"""Czyste typy aktu MPZP i kontrola topologii jego pełnego snapshotu."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, Mapping
import re

from shapely import from_wkt
from shapely.ops import unary_union

from app.shared.geometry import GeometryPayload


class PlanningActValidationError(ValueError):
    """Akt nie ma metadanych wymaganych do jednoznacznej publikacji."""


@dataclass(frozen=True)
class ZoneRecord:
    """Wydzielenie MPZP (teren elementarny) z geometrią w EPSG:2180.

    ``source_identifier`` to identyfikator obiektu nadany przez źródło (np. pole
    WFS). ``zone_identifier`` jest stabilnym ID publikowanym w wyniku analizy:
    identyfikator źródła albo — gdy go brak — deterministyczny skrót aktu,
    symbolu i hasha kanonicznej geometrii (patrz :func:`stable_zone_identifier`).
    """

    original_symbol: str
    normalized_symbol: str | None
    geometry: GeometryPayload
    raw_attributes: Mapping[str, Any] = field(default_factory=dict)
    source_identifier: str | None = None
    zone_identifier: str | None = None

    def with_geometry(self, geometry: GeometryPayload) -> ZoneRecord:
        return replace(self, geometry=geometry)


@dataclass(frozen=True)
class PlanningActRecord:
    act_identifier: str
    resolution_number: str
    resolution_date: date | None
    teryt: str
    name: str | None
    boundary: GeometryPayload | None
    zones: tuple[ZoneRecord, ...] = ()
    legal_status: str = "adopted"
    document_url: str | None = None

    def validate(self) -> None:
        if not self.act_identifier.strip():
            raise PlanningActValidationError("Brak identyfikatora aktu.")
        if not self.resolution_number.strip():
            raise PlanningActValidationError("Brak numeru uchwały.")
        if self.resolution_date is None or not isinstance(self.resolution_date, date):
            raise PlanningActValidationError("Brak daty uchwały.")
        if not self.teryt.strip():
            raise PlanningActValidationError("Brak kodu TERYT aktu.")


def stable_zone_identifier(
    act_identifier: str,
    zone: ZoneRecord,
    geometry_hash: str,
) -> str:
    """Stabilne ID wydzielenia niezależne od kolejności obiektów i wydania.

    Identyfikator źródła ma pierwszeństwo, ale jest kwalifikowany aktem, bo
    źródła numerują obiekty lokalnie. Bez niego ID wynika z aktu, symbolu i
    SHA-256 kanonicznej geometrii — ten sam obiekt w kolejnym imporcie dostaje
    to samo ID, a zmiana geometrii daje nowe.
    """
    if zone.source_identifier and zone.source_identifier.strip():
        return f"{act_identifier}:{zone.source_identifier.strip()}"
    return f"{act_identifier}:{zone.original_symbol}:{geometry_hash[:16]}"


@dataclass(frozen=True)
class TopologyResult:
    boundary_area_sqm: float
    zones_area_sqm: float
    union_area_sqm: float
    overlap_area_sqm: float
    gap_area_sqm: float
    outside_zone_count: int
    warnings: tuple[str, ...]


def normalize_zone_symbol(symbol: str, explicit_category: str | None) -> str | None:
    """Normalizuje typowe symbole, zachowując symbol lokalny bez zmian."""
    if explicit_category and explicit_category.strip():
        return explicit_category.strip().casefold().replace(" ", "_")
    token = re.sub(r"^[0-9.]+", "", symbol.strip().upper()).split("-", maxsplit=1)[0]
    categories = {
        "MN": "single_family_housing",
        "MW": "multi_family_housing",
        "U": "services",
        "P": "production",
        "ZP": "greenery",
        "R": "agriculture",
        "KD": "transport",
        "KDD": "transport",
        "KDL": "transport",
        "KDZ": "transport",
    }
    return categories.get(token)


def validate_topology(
    act: PlanningActRecord,
    *,
    distance_tolerance_m: float,
    area_tolerance_sqm: float,
) -> TopologyResult:
    """Mierzy szczeliny, nakładania i wyjścia stref poza granicę aktu."""
    act.validate()
    if act.boundary is None:
        return TopologyResult(0, 0, 0, 0, 0, 0, ("raster_only",))

    boundary = from_wkt(act.boundary.wkt)
    zones = [from_wkt(zone.geometry.wkt) for zone in act.zones]
    zone_union = unary_union(zones) if zones else None
    zones_area = sum(zone.area for zone in zones)
    union_area = zone_union.area if zone_union is not None else 0.0
    overlap_area = max(0.0, zones_area - union_area)
    gap_area = (
        boundary.difference(zone_union).area
        if zone_union is not None
        else boundary.area
    )
    tolerated_boundary = boundary.buffer(max(0.0, distance_tolerance_m))
    outside = sum(
        1
        for zone in zones
        if zone.difference(tolerated_boundary).area > area_tolerance_sqm
    )

    warnings: list[str] = []
    if outside:
        warnings.append("zones_outside_boundary")
    if overlap_area > area_tolerance_sqm:
        warnings.append("zone_overlaps")
    if gap_area > area_tolerance_sqm:
        warnings.append("zone_gaps")
    return TopologyResult(
        boundary_area_sqm=boundary.area,
        zones_area_sqm=zones_area,
        union_area_sqm=union_area,
        overlap_area_sqm=overlap_area,
        gap_area_sqm=gap_area,
        outside_zone_count=outside,
        warnings=tuple(warnings),
    )
