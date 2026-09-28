"""Czysta domena kontekstu drogowego działki (BK-305).

Model opisuje wyłącznie **fakty geometryczne** w EPSG:2180: odległość poligonu
działki od osi jezdni BDOT10k i relację ``intersects``/``touches``/``disjoint``.
Żadna wartość tego modułu nie jest oceną prawnego dostępu do drogi publicznej —
pole takiej oceny w ogóle tu nie istnieje.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

RoadRelation = Literal["intersects", "touches", "disjoint"]
RoadContextStatus = Literal[
    "available", "not_found_within_radius", "no_coverage", "unavailable"
]
RoadGeometryKind = Literal["carriageway_axis"]

# Jawne określenie, co jest mierzone: od poligonu działki (0, gdy oś przecina
# lub dotyka działki) do osi jezdni — NIE do granicy pasa drogowego.
DISTANCE_REFERENCE: Final[str] = "parcel_polygon_to_carriageway_axis"
CARRIAGEWAY_AXIS: Final[RoadGeometryKind] = "carriageway_axis"

# Kody przyczyn statusów innych niż ``available``.
REASON_SEARCH_RADIUS_EXHAUSTED: Final[str] = "SEARCH_RADIUS_EXHAUSTED"
REASON_SEARCH_AREA_PARTIALLY_OUTSIDE_COVERAGE: Final[str] = (
    "SEARCH_AREA_PARTIALLY_OUTSIDE_COVERAGE"
)
REASON_NO_ACTIVE_RELEASE: Final[str] = "NO_ACTIVE_RELEASE"
REASON_OUTSIDE_COVERAGE: Final[str] = "OUTSIDE_COVERAGE"
REASON_QUERY_TIMEOUT: Final[str] = "QUERY_TIMEOUT"
REASON_DATABASE_ERROR: Final[str] = "DATABASE_ERROR"
REASON_SOURCE_NOT_RUNNABLE: Final[str] = "SOURCE_NOT_RUNNABLE"
REASON_UNEXPECTED_ERROR: Final[str] = "UNEXPECTED_ERROR"

# Tolerancja porównań odległości (m) — wynik jest raportowany z dokładnością
# do 1 cm, więc różnice poniżej 1 mm traktujemy jako remis.
DISTANCE_TIE_TOLERANCE_M: Final[float] = 1e-3

_RELATION_RANK: Final[dict[RoadRelation, int]] = {
    "intersects": 0,
    "touches": 1,
    "disjoint": 2,
}


class InvalidRoadSearchPlan(ValueError):
    """Plan wyszukiwania nie ma dodatnich, rosnących promieni."""


def classify_relation(intersects: bool, touches: bool) -> RoadRelation:
    """Relacja osi jezdni z poligonem działki jako fakt geometryczny.

    ``touches`` — wspólny wyłącznie brzeg (oś biegnie po granicy działki);
    ``intersects`` — oś wchodzi we wnętrze działki; ``disjoint`` — brak
    wspólnego punktu.
    """
    if touches:
        return "touches"
    if intersects:
        return "intersects"
    return "disjoint"


@dataclass(frozen=True)
class RoadSearchPlan:
    """Rosnące promienie wyszukiwania; ostatni jest maksymalnym zasięgiem."""

    radii_m: tuple[float, ...]
    candidate_limit: int = 25

    def __post_init__(self) -> None:
        if not self.radii_m:
            raise InvalidRoadSearchPlan("Plan wymaga co najmniej jednego promienia.")
        if any(radius <= 0 for radius in self.radii_m):
            raise InvalidRoadSearchPlan("Promienie wyszukiwania muszą być dodatnie.")
        if any(b <= a for a, b in zip(self.radii_m, self.radii_m[1:])):
            raise InvalidRoadSearchPlan("Promienie wyszukiwania muszą rosnąć.")
        if self.candidate_limit < 1:
            raise InvalidRoadSearchPlan("Limit kandydatów musi być dodatni.")

    @property
    def max_radius_m(self) -> float:
        return self.radii_m[-1]


@dataclass(frozen=True)
class RoadCandidate:
    """Obiekt drogowy z pomiarem względem działki (EPSG:2180)."""

    road_id: str
    distance_m: float
    intersects: bool
    touches: bool
    geometry_kind: RoadGeometryKind = CARRIAGEWAY_AXIS
    category: str | None = None
    road_class: str | None = None
    road_number: str | None = None
    road_wkt: str | None = None
    link_wkt: str | None = None
    feature_version: str | None = None

    @property
    def relation(self) -> RoadRelation:
        return classify_relation(self.intersects, self.touches)


def select_nearest(candidates: list[RoadCandidate]) -> RoadCandidate | None:
    """Najbliższy obiekt; przy remisie wygrywa silniejsza relacja, potem ID.

    Kolejność jest deterministyczna, więc ten sam stan danych daje zawsze ten
    sam ``road_id`` niezależnie od kolejności zwróconej przez indeks.
    """
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda item: (
            round(item.distance_m / DISTANCE_TIE_TOLERANCE_M),
            _RELATION_RANK[item.relation],
            item.road_id,
        ),
    )


def round_distance(distance_m: float) -> float:
    """Odległość raportowana z dokładnością 1 cm; wartości ujemne są błędem."""
    if distance_m < 0:
        raise ValueError("Odległość nie może być ujemna.")
    return round(distance_m, 2)
