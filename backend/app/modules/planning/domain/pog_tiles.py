"""Kontrakt wektorowych kafli POG (MVT) z krajowego stanu rejestru (BK-401, ADR-026).

Kafel jest adresowany przez ``(state_id, z, x, y, edition)`` i jest niezmienny
dla danej wersji schematu atrybutów: numer stanu krajowego oznacza stały zbiór
wersji aktów (rejestr jest czasowy), a styl (kolory, progi) jest stosowany po
stronie klienta. Dzięki temu klucz cache i ETag mogą być wyliczone wyłącznie z
adresu kafla oraz jego treści, a URL przypięty do stanu odtwarza dokładnie ten
sam stan mapy — także po późniejszych importach innych gmin.

Atrybuty są celowo „chude”: kafel nie publikuje surowego XML/``raw_attributes``
ani dużych pól, tylko to, czego potrzebuje mapa tematyczna i inspektor cechy.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal

from app.modules.planning.domain.pog_features import (
    POG_PARAMETER_NAMES,
    PogFeaturePresentation,
    pog_feature_presentation,
    release_feature_attributes,
)
from app.shared.planning_status import canonical_legal_status

# Wersja schematu atrybutów kafla. Zmiana nazw/znaczenia atrybutów albo sposobu
# kodowania wymaga podniesienia wersji — unieważnia to klucze cache i ETagi.
POG_TILE_SCHEMA_VERSION: Final[str] = "pog-mvt/2"
POG_TILE_MEDIA_TYPE: Final[str] = "application/vnd.mapbox-vector-tile"
MVT_EXTENT: Final[int] = 4096
MVT_BUFFER: Final[int] = 64
# Absolutny zakres adresowania kafli; konfiguracja może go jedynie zawęzić.
ABSOLUTE_MIN_ZOOM: Final[int] = 0
ABSOLUTE_MAX_ZOOM: Final[int] = 22

# Progi zoomu warstw (jedno źródło prawdy dla serwera i klienta; klient czyta je z metadanych stanu).
# Granice aktów (kolor statusu) są dostępne od najmniejszego zoomu, strefy od z11, OUZ/OZS/OSDIS od z12; pełne
# etykiety i kody profili są w kaflu dopiero od z13 (przy małym zoomie nie są czytelne, a dominują rozmiar kafla).
LAYER_MIN_ZOOM: Final[dict[str, int]] = {
    "act_boundary": 0,
    "zones": 11,
    "ouz": 12,
    "downtown": 12,
    "social_infrastructure_standard": 12,
}
TEXT_ATTRIBUTES_MIN_ZOOM: Final[int] = 13
# Stopnie uproszczenia geometrii zależne od zoomu: kolumna tabeli ``pog_map_*`` używana przy danym z.
ZONE_GEOMETRY_BANDS: Final[tuple[tuple[int, str], ...]] = ((12, "geom_low"), (14, "geom_mid"))
ACT_GEOMETRY_BANDS: Final[tuple[tuple[int, str], ...]] = ((8, "geom_overview"), (12, "geom_low"))


def geometry_column(layer: str, z: int) -> str:
    """Kolumna geometrii o stopniu uproszczenia właściwym dla zoomu ``z`` (``geom_full`` powyżej ostatniego pasma)."""
    bands = ACT_GEOMETRY_BANDS if layer == "act_boundary" else ZONE_GEOMETRY_BANDS
    for max_zoom, column in bands:
        if z <= max_zoom:
            return column
    return "geom_full"

PogTileLayer = Literal[
    "zones", "ouz", "downtown", "social_infrastructure_standard", "act_boundary"
]
POG_TILE_LAYERS: Final[tuple[PogTileLayer, ...]] = (
    "zones",
    "ouz",
    "downtown",
    "social_infrastructure_standard",
    "act_boundary",
)
# Typ warstwy w ``planning_features`` → logiczna warstwa kafla.
FEATURE_TYPE_LAYERS: Final[dict[str, PogTileLayer]] = {
    "planning_zone": "zones",
    "ouz": "ouz",
    "downtown_area": "downtown",
    "social_infrastructure_standard": "social_infrastructure_standard",
}

PogTileEdition = Literal["all", "binding", "project"]
# Edycja kafla rozdziela akty wiążące od projektów/aktów w toku. Filtr działa
# na kanonicznym statusie prawnym aktu (BK-106), nie na obecności geometrii.
POG_TILE_EDITIONS: Final[dict[str, tuple[str, ...] | None]] = {
    "all": None,
    "binding": ("binding",),
    "project": ("project", "in_progress"),
}
DEFAULT_POG_TILE_EDITION: Final[PogTileEdition] = "all"

MAX_LABEL_LENGTH: Final[int] = 200
MAX_PROFILE_CODES_LENGTH: Final[int] = 256


class InvalidPogTileRequestError(ValueError):
    """Adres kafla jest spoza dozwolonego zakresu (HTTP 422)."""


@dataclass(frozen=True)
class PogTileRequest:
    state_id: int
    z: int
    x: int
    y: int
    edition: str = DEFAULT_POG_TILE_EDITION

    def validate(self, *, min_zoom: int, max_zoom: int) -> None:
        """Waliduje z/x/y i edycję; ``x``/``y`` muszą należeć do ``[0, 2^z)``."""
        if self.state_id <= 0:
            raise InvalidPogTileRequestError("Numer stanu krajowego musi być dodatni.")
        if not min_zoom <= self.z <= max_zoom:
            raise InvalidPogTileRequestError(
                f"Poziom zoom {self.z} jest poza zakresem {min_zoom}–{max_zoom}."
            )
        limit = 1 << self.z
        if not (0 <= self.x < limit and 0 <= self.y < limit):
            raise InvalidPogTileRequestError(
                f"Współrzędne kafla muszą spełniać 0 ≤ x, y < {limit} dla z={self.z}."
            )
        if self.edition not in POG_TILE_EDITIONS:
            allowed = ", ".join(POG_TILE_EDITIONS)
            raise InvalidPogTileRequestError(
                f"Nieznana edycja {self.edition!r}; dozwolone: {allowed}."
            )

    @property
    def legal_statuses(self) -> tuple[str, ...] | None:
        return POG_TILE_EDITIONS[self.edition]

    @property
    def cache_key(self) -> str:
        """Klucz cache: schemat atrybutów, stan krajowy, edycja i adres kafla."""
        return (
            f"{POG_TILE_SCHEMA_VERSION}|state={self.state_id}|"
            f"edition={self.edition}|{self.z}/{self.x}/{self.y}"
        )


def tile_etag(cache_key: str, content: bytes) -> str:
    """Silny ETag zależny od klucza kafla i jego bajtów."""
    key_digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()[:16]
    content_digest = hashlib.sha256(content).hexdigest()[:16]
    return f'"{key_digest}-{content_digest}"'


@dataclass(frozen=True)
class PogTileCandidate:
    """Obiekt warstwy POG przecinający kafel (dane wejściowe atrybutów)."""

    pk: int
    feature_type: str
    feature_identifier: str | None
    feature_version: str | None
    symbol: str | None
    label: str | None
    parameters: Mapping[str, object] | None
    primary_profiles: object
    additional_profiles: object
    raw_attributes: Mapping[str, object] | None
    act_identifier: str
    teryt: str | None
    legal_status: str | None


def _truncate(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _profile_codes(profiles: tuple[dict[str, str | None], ...]) -> str | None:
    codes = [str(profile["code"]) for profile in profiles if profile.get("code")]
    return _truncate(",".join(codes), MAX_PROFILE_CODES_LENGTH) if codes else None


def candidate_presentation(candidate: PogTileCandidate) -> PogFeaturePresentation:
    """Te same reguły co analiza lokalnego wydania (``release_feature_attributes``)."""
    attributes = release_feature_attributes(
        candidate.raw_attributes,
        feature_identifier=candidate.feature_identifier,
        feature_version=candidate.feature_version,
        symbol=candidate.symbol,
        label=candidate.label,
        primary_profiles=candidate.primary_profiles,
        additional_profiles=candidate.additional_profiles,
        parameters=candidate.parameters,
    )
    return pog_feature_presentation(attributes)


def tile_feature_properties(candidate: PogTileCandidate) -> dict[str, object]:
    """Chude atrybuty cechy kafla; ``None`` jest pomijane w MVT (≠ 0).

    ``pk`` służy wyłącznie jako identyfikator cechy MVT (feature-state), a
    ``layer`` wybiera warstwę logiczną — oba nie są atrybutami cechy.
    """
    layer = FEATURE_TYPE_LAYERS.get(candidate.feature_type)
    if layer is None:
        raise ValueError(f"Nieobsługiwany typ obiektu POG: {candidate.feature_type!r}.")
    presentation = candidate_presentation(candidate)
    properties: dict[str, object] = {
        "pk": candidate.pk,
        "layer": layer,
        "feature_id": presentation.feature_id or f"planning_feature:{candidate.pk}",
        "feature_version": presentation.feature_version,
        "symbol": _truncate(presentation.symbol, 60),
        "designation": _truncate(presentation.designation, 60),
        "label": _truncate(presentation.label, MAX_LABEL_LENGTH),
        "legal_status": canonical_legal_status(candidate.legal_status),
        "teryt": candidate.teryt,
        "act_id": candidate.act_identifier,
    }
    if layer == "zones":
        properties["zone_code"] = presentation.zone_code
        for name in POG_PARAMETER_NAMES:
            properties[name] = presentation.parameter(name)
        properties["parameters_informational"] = (
            True if presentation.parameters_informational else None
        )
        properties["primary_profiles"] = _profile_codes(presentation.primary_profiles)
        properties["additional_profiles"] = _profile_codes(
            presentation.additional_profiles
        )
    return properties


@dataclass(frozen=True)
class PogTile:
    content: bytes
    etag: str
    feature_count: int
    cache_status: Literal["HIT", "MISS"]
    state_id: int
    edition: str


@dataclass(frozen=True)
class PogCoverageArea:
    """Zasięg danych jednego aktu w stanie krajowym (BK-406).

    Pokrycie mapy jest oceniane z tych metadanych obszaru, a nie z pustego
    kafla: widok poza wszystkimi zasięgami to ``no_coverage`` (brak danych w
    stanie — nie brak planu), a akt bez granicy albo z niepełnym agregatem
    stref (BK-405) daje stan ``partial``.
    """

    act_id: str
    teryt: str | None
    legal_status: str
    bounds: tuple[float, float, float, float] | None
    has_boundary: bool
    # ``None`` — agregat nie został policzony (wersja sprzed BK-405).
    is_complete: bool | None
    incomplete_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class PogStateInfo:
    """Metadane stanu krajowego POG, do którego przypięte są kafle."""

    state_id: int
    source_id: str
    kind: str
    created_at: object
    is_current: bool
    act_count: int
    bounds: tuple[float, float, float, float] | None
    acts_by_legal_status: Mapping[str, int]
    coverage_areas: tuple[PogCoverageArea, ...] = ()
    # Liczba aktywnych wersji, którym brakuje wierszy mapy (np. przed ``pog-map-rebuild``); > 0 = mapa niepełna.
    versions_without_map_rows: int = 0
