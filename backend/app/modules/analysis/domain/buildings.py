"""Czysta domena kontekstu istniejącej zabudowy działki (BK-304, Task 23.3).

Wynik nazywa się „kontekst istniejącej zabudowy”, a nie „możliwość zabudowy”: opisuje to, co stoi w otoczeniu
działki, i nie wnioskuje o prawnej możliwości nowej zabudowy. Wszystkie obliczenia są w EPSG:2180 i są definicjami,
nie ocenami:

* **sąsiedztwo** o promieniu ``r`` to pierścień ``buffer(działka, r) − działka`` (``quad_segs=16``, brzeg okrągły).
  Statystyki są kumulatywne: pierścień 100 m zawiera pierścień 50 m;
* budynek jest **w pierścieniu**, gdy pole jego części w pierścieniu przekracza ``AREA_EPS_SQM`` (0,01 m²). Liczymy
  unikalne identyfikatory — budynek raz, niezależnie od liczby części geometrii;
* budynek jest **na działce**, gdy pole jego części w działce przekracza ``AREA_EPS_SQM``. Taki budynek, jeśli wystaje
  poza granicę, liczy się także w pierścieniu (jego część zewnętrzna) i jest oznaczony jako ``straddling``;
* **pole zabudowy w pierścieniu** to suma pól części obrysów przyciętych do pierścienia; **pokrycie (union)** to pole
  sumy tych części (nakładające się obrysy nie liczą się podwójnie); mianownikiem udziału jest pole pierścienia;
* **odległość** to ``ST_Distance`` od poligonu działki do obrysu (0, gdy obrys przylega albo zachodzi na działkę);
* **wskaźnik pokrycia działki** = pole sumy części obrysów w działce / pole działki. To wynik obliczenia, nie ocena
  prawna.

Brak budynku w danych nie jest dowodem, że go nie ma: zasięg danych kontroluje warstwa aplikacji.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final, Literal, Sequence

from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

# Parametry definicji pierścienia — jedno źródło prawdy także dla skryptu fixtures (build_bdot10k_fixtures.py).
BUFFER_QUAD_SEGS: Final[int] = 16
AREA_EPS_SQM: Final[float] = 0.01
DEFAULT_BUFFERS_M: Final[tuple[float, ...]] = (50.0, 100.0, 200.0)
DISTANCE_REFERENCE: Final[str] = "parcel_polygon_to_building_footprint"
RING_DEFINITION: Final[str] = "buffer(parcel, r) - parcel"

BuildingContextStatus = Literal["available", "no_match", "no_coverage", "unavailable"]
BuildingRelation = Literal["on_parcel", "straddling", "adjacent", "neighbour"]

BUILDING_CONTEXT_NOTE: Final[str] = (
    "Kontekst istniejącej zabudowy z obrysów BDOT10k (w większości z EGiB): opis tego, co stoi w otoczeniu działki, "
    "a nie ocena możliwości zabudowy. Dane bez nazw i właścicieli; stan na datę wskazaną przy wydaniu danych."
)


class InvalidBuildingPlan(ValueError):
    """Plan obliczeń nie ma dodatnich, rosnących promieni buforów."""


@dataclass(frozen=True)
class BuildingSearchPlan:
    """Bufory (m) od obrysu działki, limit obiektów w tabeli i limit kandydatów z indeksu."""

    buffers_m: tuple[float, ...] = DEFAULT_BUFFERS_M
    listed_limit: int = 100
    candidate_limit: int = 20_000

    def __post_init__(self) -> None:
        if not self.buffers_m or any(radius <= 0 for radius in self.buffers_m):
            raise InvalidBuildingPlan("Bufory muszą być dodatnie.")
        if any(b <= a for a, b in zip(self.buffers_m, self.buffers_m[1:])):
            raise InvalidBuildingPlan("Bufory muszą rosnąć.")
        if self.listed_limit < 1 or self.candidate_limit < 1:
            raise InvalidBuildingPlan("Limity muszą być dodatnie.")

    @property
    def max_radius_m(self) -> float:
        return self.buffers_m[-1]

    @property
    def listed_radius_m(self) -> float:
        return self.buffers_m[0]


@dataclass(frozen=True)
class BuildingFootprint:
    """Obrys budynku z lokalnego wydania (bez nazw i danych osobowych)."""

    building_id: str
    geometry: BaseGeometry
    area_sqm: float
    general_function: str | None = None
    detailed_function: str | None = None
    predominant_function: str | None = None
    floors: int | None = None
    geometry_source: str | None = None
    feature_version: datetime | None = None


@dataclass(frozen=True)
class ParcelBuildings:
    """Budynki na działce (część obrysu w działce > ``AREA_EPS_SQM``)."""

    count: int
    union_area_in_parcel_sqm: float
    coverage_pct: float


@dataclass(frozen=True)
class BufferStats:
    """Statystyki pierścienia o promieniu ``radius_m``; ``null`` (``None``) ≠ 0 dla wielkości bez obiektów."""

    radius_m: float
    ring_area_sqm: float
    count: int
    straddling_parcel_count: int
    footprint_area_in_ring_sqm: float
    union_area_in_ring_sqm: float
    built_share_pct: float
    min_distance_m: float | None
    footprint_min_sqm: float | None
    footprint_median_sqm: float | None
    footprint_max_sqm: float | None


@dataclass(frozen=True)
class ListedBuilding:
    """Budynek do tabeli i warstwy mapy (najbliższy promień + budynki na działce)."""

    footprint: BuildingFootprint
    distance_m: float
    relation: BuildingRelation
    overlap_with_parcel_sqm: float


@dataclass(frozen=True)
class BuildingMetrics:
    parcel_area_sqm: float
    on_parcel: ParcelBuildings
    buffers: tuple[BufferStats, ...]
    listed: tuple[ListedBuilding, ...]
    listed_truncated: bool
    listed_radius_m: float
    candidates_considered: int
    duplicates_ignored: int = 0
    warnings: tuple[str, ...] = field(default_factory=tuple)


def ring_geometry(parcel: BaseGeometry, radius_m: float) -> BaseGeometry:
    """Pierścień sąsiedztwa ``buffer(parcel, r) − parcel`` w EPSG:2180."""
    return parcel.buffer(radius_m, quad_segs=BUFFER_QUAD_SEGS).difference(parcel)


def _dedupe(buildings: Sequence[BuildingFootprint]) -> tuple[list[BuildingFootprint], int]:
    seen: set[str] = set()
    unique: list[BuildingFootprint] = []
    for building in buildings:
        if building.building_id in seen:
            continue
        seen.add(building.building_id)
        unique.append(building)
    return unique, len(buildings) - len(unique)


def _relation(overlap_sqm: float, distance_m: float, straddles: bool) -> BuildingRelation:
    if overlap_sqm > AREA_EPS_SQM:
        return "straddling" if straddles else "on_parcel"
    return "adjacent" if distance_m <= 0.0 else "neighbour"


def compute_building_metrics(
    parcel: BaseGeometry,
    buildings: Sequence[BuildingFootprint],
    plan: BuildingSearchPlan,
) -> BuildingMetrics:
    """Liczności, pola i odległości zabudowy w pierścieniach i na działce; deterministyczne i bez podwójnego liczenia."""
    unique, duplicates = _dedupe(buildings)
    parcel_area = float(parcel.area)
    distances = {item.building_id: float(parcel.distance(item.geometry)) for item in unique}
    overlaps = {
        item.building_id: float(item.geometry.intersection(parcel).area) if distances[item.building_id] == 0.0 else 0.0
        for item in unique
    }
    on_parcel_items = [item for item in unique if overlaps[item.building_id] > AREA_EPS_SQM]
    parts_in_parcel = [item.geometry.intersection(parcel) for item in on_parcel_items]
    union_in_parcel = float(unary_union(parts_in_parcel).area) if parts_in_parcel else 0.0
    on_parcel = ParcelBuildings(
        count=len(on_parcel_items),
        union_area_in_parcel_sqm=round(union_in_parcel, 4),
        coverage_pct=round(100.0 * union_in_parcel / parcel_area, 4) if parcel_area > 0 else 0.0,
    )

    stats: list[BufferStats] = []
    for radius in plan.buffers_m:
        ring = ring_geometry(parcel, radius)
        ring_area = float(ring.area)
        counted: list[tuple[BuildingFootprint, BaseGeometry]] = []
        for item in unique:
            if distances[item.building_id] > radius:
                continue
            clipped = item.geometry.intersection(ring)
            if clipped.area > AREA_EPS_SQM:
                counted.append((item, clipped))
        full_areas = [float(item.geometry.area) for item, _clip in counted]
        union_area = float(unary_union([clip for _item, clip in counted]).area) if counted else 0.0
        stats.append(
            BufferStats(
                radius_m=radius,
                ring_area_sqm=round(ring_area, 4),
                count=len(counted),
                straddling_parcel_count=sum(1 for item, _c in counted if overlaps[item.building_id] > AREA_EPS_SQM),
                footprint_area_in_ring_sqm=round(sum(float(clip.area) for _item, clip in counted), 4),
                union_area_in_ring_sqm=round(union_area, 4),
                built_share_pct=round(100.0 * union_area / ring_area, 4) if ring_area > 0 else 0.0,
                min_distance_m=round(min(distances[item.building_id] for item, _c in counted), 2) if counted else None,
                footprint_min_sqm=round(min(full_areas), 4) if counted else None,
                footprint_median_sqm=round(float(statistics.median(full_areas)), 4) if counted else None,
                footprint_max_sqm=round(max(full_areas), 4) if counted else None,
            )
        )

    listed_pool = [
        item
        for item in unique
        if overlaps[item.building_id] > AREA_EPS_SQM or distances[item.building_id] <= plan.listed_radius_m
    ]
    listed_pool.sort(key=lambda item: (round(distances[item.building_id], 3), item.building_id))
    truncated = len(listed_pool) > plan.listed_limit
    listed = tuple(
        ListedBuilding(
            footprint=item,
            distance_m=round(distances[item.building_id], 2),
            relation=_relation(
                overlaps[item.building_id],
                distances[item.building_id],
                straddles=(item.geometry.difference(parcel).area > AREA_EPS_SQM),
            ),
            overlap_with_parcel_sqm=round(overlaps[item.building_id], 4),
        )
        for item in listed_pool[: plan.listed_limit]
    )
    return BuildingMetrics(
        parcel_area_sqm=round(parcel_area, 4),
        on_parcel=on_parcel,
        buffers=tuple(stats),
        listed=listed,
        listed_truncated=truncated,
        listed_radius_m=plan.listed_radius_m,
        candidates_considered=len(unique),
        duplicates_ignored=duplicates,
    )
