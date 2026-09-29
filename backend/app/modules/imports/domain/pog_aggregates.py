"""Czysta logika agregatów powierzchniowych stref POG (BK-405).

Pomiary powierzchni (EPSG:2180, m²) wykonuje PostGIS podczas publikacji
wydania; ten moduł zamienia je na udziały i ocenę kompletności. Zasady:

- ``area_sqkm = area_sqm / 1e6``; ``share_pct = 100 * area / area(granicy)``;
- mianownik pochodzi wyłącznie z granicy aktu ze źródła — jego brak daje
  ``None`` (udziały też ``None``) i ``is_complete = False``, nigdy 100%;
- suma udziałów jest kontrolowana tolerancją; luka, nakładanie się stref i
  strefy poza granicą są jawnie raportowane jako przyczyny niepełności;
- ``None`` (brak danych) nie jest zamieniane na 0.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal

AGGREGATE_METHOD_VERSION: Final[str] = "pog-aggregates/1"
SQM_PER_SQKM: Final[float] = 1_000_000.0
# Tolerancja powierzchni: 0,05% mianownika, nie mniej niż 1 m² (drzazgi po
# naprawie geometrii i sumie poligonów nie mogą same oznaczać luki).
AREA_TOLERANCE_RATIO: Final[float] = 0.0005
MIN_AREA_TOLERANCE_SQM: Final[float] = 1.0
SHARE_SUM_TOLERANCE_PCT: Final[float] = 0.1

AggregateScope = Literal["act", "municipality"]
AggregateEdition = Literal["binding", "project"]
# Edycje agregatu gminy — te same grupy statusów co edycje kafli MVT (BK-401).
# Akty ``unknown``/``superseded`` mają wyłącznie agregat na poziomie aktu.
MUNICIPALITY_EDITIONS: Final[dict[str, tuple[str, ...]]] = {
    "binding": ("binding",),
    "project": ("project", "in_progress"),
}

IncompleteReason = Literal[
    "no_boundary",
    "no_zones",
    "missing_area",
    "overlapping_zones",
    "zones_outside_boundary",
    "share_sum_out_of_tolerance",
]


def edition_for_status(legal_status: str | None) -> str | None:
    """Edycja agregatu gminy dla statusu aktu albo ``None`` (tylko poziom aktu)."""
    for edition, statuses in MUNICIPALITY_EDITIONS.items():
        if legal_status in statuses:
            return edition
    return None


def area_tolerance_sqm(denominator_area_sqm: float | None) -> float:
    if denominator_area_sqm is None:
        return MIN_AREA_TOLERANCE_SQM
    return max(MIN_AREA_TOLERANCE_SQM, AREA_TOLERANCE_RATIO * denominator_area_sqm)


def sqm_to_sqkm(area_sqm: float) -> float:
    return area_sqm / SQM_PER_SQKM


@dataclass(frozen=True)
class ZoneAreaMeasurement:
    """Powierzchnia sumy stref jednego kodu (bez podwójnego liczenia w kodzie)."""

    zone_code: str
    area_sqm: float
    zone_count: int


@dataclass(frozen=True)
class AreaMeasurement:
    """Surowe pomiary PostGIS jednego zakresu (akt albo gmina + edycja).

    ``zones_union_area_sqm`` to pole sumy wszystkich stref przyciętych do
    granicy, ``zones_sum_area_sqm`` — suma pól pojedynczych stref; różnica to
    nakładanie. ``outside_area_sqm`` to pole stref poza granicą aktu.
    ``deduplicated_area_sqm`` (tylko gmina) to pole nakładania się aktów,
    przypisane jednemu aktowi zamiast liczenia go dwa razy.
    """

    denominator_area_sqm: float | None
    zones: tuple[ZoneAreaMeasurement, ...]
    zones_union_area_sqm: float
    zones_sum_area_sqm: float
    outside_area_sqm: float
    deduplicated_area_sqm: float | None = None


@dataclass(frozen=True)
class ZoneAggregate:
    zone_code: str
    area_sqm: float
    area_sqkm: float
    share_pct: float | None
    zone_count: int


@dataclass(frozen=True)
class AreaAggregate:
    zones: tuple[ZoneAggregate, ...]
    denominator_area_sqm: float | None
    zones_area_sqm: float
    missing_area_sqm: float | None
    overlap_area_sqm: float
    outside_area_sqm: float
    deduplicated_area_sqm: float | None
    share_sum_pct: float | None
    zone_count: int
    is_complete: bool
    incomplete_reasons: tuple[IncompleteReason, ...]
    area_tolerance_sqm: float
    share_tolerance_pct: float

    @property
    def denominator_area_sqkm(self) -> float | None:
        if self.denominator_area_sqm is None:
            return None
        return sqm_to_sqkm(self.denominator_area_sqm)


def _non_negative(value: float) -> float:
    return value if value > 0 else 0.0


def aggregate_areas(measurement: AreaMeasurement) -> AreaAggregate:
    """Udziały i ocena kompletności z pomiarów jednego zakresu."""
    denominator = measurement.denominator_area_sqm
    if denominator is not None and denominator <= 0:
        denominator = None
    tolerance = area_tolerance_sqm(denominator)

    zones = tuple(
        ZoneAggregate(
            zone_code=zone.zone_code,
            area_sqm=_non_negative(zone.area_sqm),
            area_sqkm=sqm_to_sqkm(_non_negative(zone.area_sqm)),
            share_pct=(
                100.0 * _non_negative(zone.area_sqm) / denominator
                if denominator is not None
                else None
            ),
            zone_count=zone.zone_count,
        )
        for zone in sorted(
            measurement.zones, key=lambda item: (-item.area_sqm, item.zone_code)
        )
    )
    union_area = _non_negative(measurement.zones_union_area_sqm)
    overlap = _non_negative(measurement.zones_sum_area_sqm - union_area)
    outside = _non_negative(measurement.outside_area_sqm)
    missing = _non_negative(denominator - union_area) if denominator is not None else None
    share_sum = (
        sum(zone.share_pct for zone in zones if zone.share_pct is not None)
        if denominator is not None
        else None
    )

    reasons: list[IncompleteReason] = []
    if denominator is None:
        reasons.append("no_boundary")
    if not zones:
        reasons.append("no_zones")
    if missing is not None and missing > tolerance:
        reasons.append("missing_area")
    if overlap > tolerance:
        reasons.append("overlapping_zones")
    if outside > tolerance:
        reasons.append("zones_outside_boundary")
    if share_sum is not None and abs(share_sum - 100.0) > SHARE_SUM_TOLERANCE_PCT:
        reasons.append("share_sum_out_of_tolerance")

    return AreaAggregate(
        zones=zones,
        denominator_area_sqm=denominator,
        zones_area_sqm=union_area,
        missing_area_sqm=missing,
        overlap_area_sqm=overlap,
        outside_area_sqm=outside,
        deduplicated_area_sqm=(
            _non_negative(measurement.deduplicated_area_sqm)
            if measurement.deduplicated_area_sqm is not None
            else None
        ),
        share_sum_pct=share_sum,
        zone_count=sum(zone.zone_count for zone in zones),
        is_complete=not reasons,
        incomplete_reasons=tuple(reasons),
        area_tolerance_sqm=tolerance,
        share_tolerance_pct=SHARE_SUM_TOLERANCE_PCT,
    )


@dataclass(frozen=True)
class ActForAggregation:
    """Wersja aktu w wydaniu; kolejność priorytetu decyduje przy nakładaniu."""

    version_id: int
    act_identifier: str
    act_version: str | None
    teryt: str | None
    legal_status: str | None
    priority_key: tuple[str, str, int]


def priority_ordered(acts: Sequence[ActForAggregation]) -> tuple[ActForAggregation, ...]:
    """Akty gminy od najwyższego priorytetu: najnowsza wersja publikacji wygrywa.

    Obszar pokryty przez akt o wyższym priorytecie nie jest ponownie liczony
    dla aktu o niższym priorytecie (brak dublowania nakładających się aktów).
    """
    return tuple(sorted(acts, key=lambda act: act.priority_key, reverse=True))


def aggregate_warning(scope_key: str, aggregate: AreaAggregate) -> str | None:
    """Ostrzeżenie importu dla niepełnego agregatu (``None`` dla pełnego)."""
    if aggregate.is_complete:
        return None
    return f"pog_aggregate_incomplete:{scope_key}:{','.join(aggregate.incomplete_reasons)}"
