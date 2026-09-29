"""Szczegóły obiektu POG dla inspektora mapy (BK-404).

Kafel MVT niesie „chude” atrybuty (kody profili, skrócona etykieta), dzięki
czemu inspektor pokazuje strefę natychmiast po kliknięciu. Szczegóły, których
kafel nie mieści — pełna etykieta, nazwy profili ze słownika, dane aktu
(nazwa, uchwała, okres obowiązywania, urzędowy kod statusu) — są pobierane
osobnym, wersjonowanym żądaniem przypiętym do ``release_id``.

Atrybuty kanoniczne (typ strefy, symbol, cztery parametry) liczy ta sama
funkcja co analiza i kafel (``candidate_presentation``), więc inspektor, mapa
i wynik analizy mają z konstrukcji te same wartości. ``None`` pozostaje
``None`` — parametr bez wartości nie staje się zerem.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Final

from app.modules.planning.domain.pog_features import POG_PARAMETER_NAMES
from app.modules.planning.domain.pog_tiles import (
    FEATURE_TYPE_LAYERS,
    POG_TILE_SCHEMA_VERSION,
    PogTileCandidate,
    PogTileLayer,
    candidate_presentation,
)
from app.shared.planning_status import canonical_legal_status

# Kafel publikuje ``planning_feature:<pk>``, gdy obiekt nie ma identyfikatora
# APP; ten sam zapis jest akceptowany przez endpoint szczegółów.
FEATURE_PK_PREFIX: Final[str] = "planning_feature:"
MAX_FEATURE_ID_LENGTH: Final[int] = 500
INSPECTOR_SCHEMA_VERSION: Final[str] = "pog-inspector/1"


class InvalidPogFeatureIdError(ValueError):
    """Identyfikator obiektu jest pusty, za długi albo niepoprawny (HTTP 422)."""


@dataclass(frozen=True)
class PogFeatureRef:
    """Identyfikator APP obiektu albo klucz wiersza wydania (``planning_feature:N``)."""

    feature_id: str | None
    pk: int | None


def parse_feature_ref(value: str) -> PogFeatureRef:
    text = value.strip()
    if not text or len(text) > MAX_FEATURE_ID_LENGTH:
        raise InvalidPogFeatureIdError(
            f"Identyfikator obiektu musi mieć od 1 do {MAX_FEATURE_ID_LENGTH} znaków."
        )
    if text.startswith(FEATURE_PK_PREFIX):
        suffix = text.removeprefix(FEATURE_PK_PREFIX)
        if not suffix.isdigit() or int(suffix) <= 0:
            raise InvalidPogFeatureIdError(
                f"Po prefiksie {FEATURE_PK_PREFIX!r} wymagana jest dodatnia liczba."
            )
        return PogFeatureRef(feature_id=None, pk=int(suffix))
    return PogFeatureRef(feature_id=text, pk=None)


@dataclass(frozen=True)
class PogActDetails:
    act_id: str
    act_version: str | None
    name: str | None
    teryt: str | None
    legal_status: str
    # Urzędowy kod statusu (URI INSPIRE/RU) — dowód dla ``legal_status``.
    legal_status_code: str | None
    resolution_number: str | None
    resolution_date: date | None
    legal_valid_from: date | None
    legal_valid_to: date | None
    publication_id: str | None
    manual_review_required: bool


@dataclass(frozen=True)
class PogReleaseHeader:
    release_id: int
    version_label: str
    published_at: object
    is_active: bool
    artifact_sha256: str | None


@dataclass(frozen=True)
class PogFeatureDetailsRow:
    """Wiersz wydania: kandydat kafla + dane aktu i pochodzenia."""

    candidate: PogTileCandidate
    act: PogActDetails
    source_reference: str | None


@dataclass(frozen=True)
class PogFeatureDetails:
    release: PogReleaseHeader
    feature_pk: int
    feature_id: str
    feature_version: str | None
    layer: PogTileLayer
    feature_type: str
    symbol: str | None
    label: str | None
    zone_code: str | None
    source_zone_type: str | None
    parameters: Mapping[str, float | None]
    parameters_informational: bool
    primary_profiles: tuple[dict[str, str | None], ...]
    additional_profiles: tuple[dict[str, str | None], ...]
    act: PogActDetails
    source_reference: str | None
    schema: str = INSPECTOR_SCHEMA_VERSION
    tile_schema: str = POG_TILE_SCHEMA_VERSION


def feature_details(row: PogFeatureDetailsRow, release: PogReleaseHeader) -> PogFeatureDetails:
    """Pełne szczegóły obiektu — te same reguły atrybutów co kafel i analiza."""
    candidate = row.candidate
    layer = FEATURE_TYPE_LAYERS.get(candidate.feature_type)
    if layer is None:
        raise ValueError(f"Nieobsługiwany typ obiektu POG: {candidate.feature_type!r}.")
    presentation = candidate_presentation(candidate)
    is_zone = layer == "zones"
    return PogFeatureDetails(
        release=release,
        feature_pk=candidate.pk,
        feature_id=presentation.feature_id or f"{FEATURE_PK_PREFIX}{candidate.pk}",
        feature_version=presentation.feature_version,
        layer=layer,
        feature_type=candidate.feature_type,
        symbol=presentation.symbol,
        label=presentation.label,
        zone_code=presentation.zone_code if is_zone else None,
        source_zone_type=presentation.source_zone_type if is_zone else None,
        parameters=(
            {name: presentation.parameter(name) for name in POG_PARAMETER_NAMES}
            if is_zone
            else {}
        ),
        parameters_informational=presentation.parameters_informational if is_zone else False,
        primary_profiles=presentation.primary_profiles if is_zone else (),
        additional_profiles=presentation.additional_profiles if is_zone else (),
        act=PogActDetails(
            **{**row.act.__dict__, "legal_status": canonical_legal_status(row.act.legal_status)}
        ),
        source_reference=row.source_reference,
    )


def payload_etag(kind: str, payload: object) -> str:
    """Silny ETag odpowiedzi wyliczony z jej kanonicznej postaci JSON.

    Wydanie nie zmienia się po publikacji, więc ETag jest stały dla
    ``release_id``; zmiana kontraktu odpowiedzi zmienia ``kind``.
    """
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return f'"{kind}-{hashlib.sha256(encoded).hexdigest()[:32]}"'
