"""Discovery POG w Rejestrze Urbanistycznym przez WFS: kafle zapytań, rekordy obiektów i obserwacja warstw (AU-105).

Moduł jest czystą logiką (bez sieci i XML). Adapter RU (``infrastructure/ru_pog_features``) dostarcza
``PogFeatureRecord`` z geometrią w EPSG:2180 (x = wschód, y = północ) przyciętą do kafla zapytania, a ta
domena rozstrzyga, które obiekty leżą na działce:

* zapytanie WFS dotyczy **kafla** — obwiedni działki zaokrąglonej na zewnątrz do siatki (``TILE_SIZE_M``).
  Kafel jest kluczem cache zapytań (ten sam kafel obsługuje sąsiednie działki i równoległe analizy), a
  dokładna przynależność obiektu do działki jest rozstrzygana lokalnie, przecięciem w EPSG:2180;
* RU filtruje po **obwiedni** obiektu, więc obiekt zwrócony dla kafla może go nie dotykać wielokątem —
  obiekt obecny na działce to taki, którego przecięcie z działką ma co najmniej ``MIN_PARCEL_OVERLAP_SQM``;
* pusta odpowiedź działającej usługi to warstwa ``empty``, a błąd, niepełna odpowiedź albo obiekt bez
  użytecznej geometrii — ``unavailable``: nigdy „brak planu”.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Final, Literal

import shapely
from shapely.geometry.base import BaseGeometry

from app.shared.planning_status import LegalStatus, official_status_code

PogFeatureType = Literal[
    "AktPlanowaniaPrzestrzennego",
    "ObszarZabudowySrodmiejskiej",
    "ObszarUzupelnieniaZabudowy",
    "StrefaPlanistyczna",
]
PogLogicalLayerName = Literal["planning_act", "downtown_area", "ouz", "planning_zones"]
PogLayerState = Literal["found", "empty", "unavailable"]

# Kolejność = kolejność pierwszeństwa statusu aktu przy wyborze kodu statusu (akt przed strefami).
POG_FEATURE_TYPES: Final[tuple[PogFeatureType, ...]] = (
    "AktPlanowaniaPrzestrzennego",
    "ObszarZabudowySrodmiejskiej",
    "ObszarUzupelnieniaZabudowy",
    "StrefaPlanistyczna",
)
LOGICAL_LAYER_BY_TYPE: Final[dict[PogFeatureType, PogLogicalLayerName]] = {
    "AktPlanowaniaPrzestrzennego": "planning_act",
    "ObszarZabudowySrodmiejskiej": "downtown_area",
    "ObszarUzupelnieniaZabudowy": "ouz",
    "StrefaPlanistyczna": "planning_zones",
}
FEATURE_TYPE_BY_LAYER: Final[dict[PogLogicalLayerName, PogFeatureType]] = {
    layer: feature_type for feature_type, layer in LOGICAL_LAYER_BY_TYPE.items()
}

# Bok siatki kafli [m]. Kompromis: mały kafel = mało obiektów na stronę (RU zwraca strony po 10), duży kafel =
# lepsze trafienia cache. Wartość nie wpływa na wynik — tylko na liczbę żądań i rozmiar odpowiedzi.
TILE_SIZE_M: Final[float] = 50.0
# Przecięcie z działką mniejsze niż ta powierzchnia to szum digitalizacji granicy, a nie obecność obiektu.
MIN_PARCEL_OVERLAP_SQM: Final[float] = 0.5

# Kody problemów (stałe, o ograniczonej kardynalności — trafiają do ostrzeżeń i liczników).
GEOMETRY_AXIS_SUSPECT: Final[str] = "POG_RU_GEOMETRY_AXIS_SUSPECT"
GEOMETRY_UNUSABLE: Final[str] = "POG_RU_GEOMETRY_UNUSABLE"
RESPONSE_PARTIAL: Final[str] = "POG_RU_PARTIAL_RESPONSE"

# Kody błędów kanału (odpowiedź z ``error_code`` NIE jest „brakiem obiektów”).
ERROR_TRANSPORT: Final[str] = "POG_RU_TRANSPORT_ERROR"
ERROR_SERVICE_EXCEPTION: Final[str] = "POG_RU_SERVICE_EXCEPTION"
ERROR_CONTRACT: Final[str] = "POG_RU_CONTRACT_ERROR"
ERROR_LIMIT_EXCEEDED: Final[str] = "POG_RU_LIMIT_EXCEEDED"
ERROR_BUDGET_EXCEEDED: Final[str] = "POG_RU_BUDGET_EXCEEDED"

Bbox = tuple[float, float, float, float]

_STATUS_RANK: Final[dict[LegalStatus, int]] = {
    "unknown": 0,
    "superseded": 1,
    "in_progress": 2,
    "project": 3,
    "binding": 4,
}


@dataclass(frozen=True)
class PogFeatureRecord:
    """Obiekt APP 3.0 zwrócony przez WFS ``app-pog`` (tylko pola potrzebne do discovery).

    ``geometry`` jest przycięta do kafla zapytania (pamięć cache jest ograniczona rozmiarem kafla, a nie obiektu —
    granica aktu Bielska-Białej ma ponad 300 kB GML). ``None`` oznacza geometrię nieczytelną albo podejrzaną
    o zamienione osie; takiego obiektu nie wolno policzyć ani jako obecnego, ani jako nieobecnego na działce.
    """

    feature_type: PogFeatureType
    identifier: str
    version_id: str | None = None
    status_href: str | None = None
    status_title: str | None = None
    plan_ref: str | None = None
    title: str | None = None
    valid_from: date | None = None
    geometry: BaseGeometry | None = None
    geometry_issue: str | None = None
    # Obwiednia obiektu nachodzi na kafel (tak filtruje RU), ale sam wielokąt go nie dotyka — obiekt nie ma wpływu
    # na żadną działkę w kaflu i nie jest ani znaleziskiem, ani problemem.
    outside_tile: bool = False


@dataclass(frozen=True)
class LayerObservation:
    """Wynik jednej warstwy logicznej dla działki (odpowiednik sekcji discovery WMS)."""

    logical_layer: PogLogicalLayerName
    feature_type: PogFeatureType
    state: PogLayerState
    feature_count: int = 0
    raw_legal_status: str | None = None
    plan_refs: tuple[str, ...] = ()
    titles: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()
    on_parcel: tuple[PogFeatureRecord, ...] = field(default=(), repr=False)


def snap_bbox_outward(bbox: Bbox, tile_size: float = TILE_SIZE_M) -> Bbox:
    """Obwiednia zaokrąglona na zewnątrz do siatki ``tile_size`` (EPSG:2180, x = wschód, y = północ).

    Wynik zawsze zawiera ``bbox`` i ma dodatnie wymiary. Granica leżąca dokładnie na linii siatki nie jest
    rozszerzana o kolejny kafel.
    """
    if tile_size <= 0 or not all(math.isfinite(value) for value in bbox):
        raise ValueError("Kafel wymaga dodatniego boku i skończonych współrzędnych.")
    min_x, min_y, max_x, max_y = bbox
    snapped_min_x = math.floor(min_x / tile_size) * tile_size
    snapped_min_y = math.floor(min_y / tile_size) * tile_size
    snapped_max_x = math.ceil(max_x / tile_size) * tile_size
    snapped_max_y = math.ceil(max_y / tile_size) * tile_size
    if snapped_max_x <= snapped_min_x:
        snapped_max_x = snapped_min_x + tile_size
    if snapped_max_y <= snapped_min_y:
        snapped_max_y = snapped_min_y + tile_size
    return snapped_min_x, snapped_min_y, snapped_max_x, snapped_max_y


def tile_key(feature_type: str, tile: Bbox) -> str:
    """Klucz cache zapytania: typ obiektu i kafel (współrzędne z milimetrową dokładnością)."""
    return f"{feature_type}|" + ",".join(f"{value:.3f}" for value in tile)


def preferred_status_href(values: Iterable[str | None]) -> str | None:
    """Kod statusu o najwyższej randze prawnej; nierozpoznane kody na końcu.

    Zwraca oryginalny zapis (URI INSPIRE), a nie znormalizowany token — to on jest dowodem w provenance.
    """
    candidates = [value for value in values if value]
    official = [(official_status_code(value), value) for value in candidates]
    recognised = [(status, value) for status, value in official if status is not None]
    if recognised:
        return max(recognised, key=lambda item: _STATUS_RANK[item[0]])[1]  # type: ignore[index]
    return candidates[0] if candidates else None


def clip_to_tile(geometry: BaseGeometry, tile: Bbox) -> BaseGeometry | None:
    """Przycina geometrię do kafla; ``None`` gdy po przycięciu nic nie zostaje (obwiednia trafiła, wielokąt nie)."""
    min_x, min_y, max_x, max_y = tile
    candidate = geometry if geometry.is_valid else shapely.make_valid(geometry)
    clipped = shapely.clip_by_rect(candidate, min_x, min_y, max_x, max_y)
    if clipped.is_empty:
        return None
    return clipped


def envelope_overlaps(geometry: BaseGeometry, bbox: Bbox) -> bool:
    """Czy obwiednia geometrii nachodzi na ``bbox`` — kontrola, czy usługa nie zamieniła osi."""
    min_x, min_y, max_x, max_y = bbox
    g_min_x, g_min_y, g_max_x, g_max_y = geometry.bounds
    return g_min_x <= max_x and g_max_x >= min_x and g_min_y <= max_y and g_max_y >= min_y


def observe_layer(
    logical_layer: PogLogicalLayerName,
    records: Sequence[PogFeatureRecord] | None,
    parcel: BaseGeometry,
    *,
    complete: bool = True,
) -> LayerObservation:
    """Które obiekty warstwy leżą na działce.

    ``records is None`` — zapytanie się nie powiodło (``unavailable``). ``complete=False`` — odpowiedź niepełna
    (przerwana paginacja, wyczerpany budżet): obiekty znalezione do tej pory są raportowane, ale brak obiektu
    nie jest dowodem ich braku, więc warstwa bez trafień jest ``unavailable``, a nie ``empty``.
    """
    feature_type = FEATURE_TYPE_BY_LAYER[logical_layer]
    if records is None:
        return LayerObservation(logical_layer, feature_type, "unavailable")
    if not parcel.is_valid:
        # Przecięcie na nieprawidłowej topologii (np. nachodzące części MultiPolygonu) kończy się błędem GEOS.
        parcel = shapely.make_valid(parcel)
    issues: list[str] = []
    on_parcel: list[PogFeatureRecord] = []
    unlocatable = 0
    for record in records:
        if record.outside_tile:
            continue
        if record.geometry is None:
            unlocatable += 1
            if record.geometry_issue:
                issues.append(record.geometry_issue)
            continue
        overlap = record.geometry.intersection(parcel) if record.geometry.intersects(parcel) else None
        if overlap is not None and overlap.area >= min(MIN_PARCEL_OVERLAP_SQM, parcel.area * 0.5):
            on_parcel.append(record)
    if not complete:
        issues.append(RESPONSE_PARTIAL)
    if on_parcel:
        state: PogLayerState = "found"
    elif unlocatable or not complete:
        state = "unavailable"
        if unlocatable and GEOMETRY_AXIS_SUSPECT not in issues:
            issues.append(GEOMETRY_UNUSABLE)
    else:
        state = "empty"
    return LayerObservation(
        logical_layer=logical_layer,
        feature_type=feature_type,
        state=state,
        feature_count=len(on_parcel),
        raw_legal_status=preferred_status_href(record.status_href for record in on_parcel),
        plan_refs=tuple(dict.fromkeys(record.plan_ref for record in on_parcel if record.plan_ref)),
        titles=tuple(dict.fromkeys(record.title for record in on_parcel if record.title)),
        issues=tuple(dict.fromkeys(issues)),
        on_parcel=tuple(on_parcel),
    )
