"""Naprawa i normalizacja geometrii BDOT10k (Shapely/GEOS): linie → MultiLineString, poligony → MultiPolygon.

Geometria wchodzi i wychodzi jako WKB 2D w EPSG:2180. Obiekt nieprawidłowy jest naprawiany ``make_valid`` i zgłaszany
jako ``repaired=True``; z wyniku zostają wyłącznie części właściwego wymiaru (linie albo poligony). Pusta geometria po naprawie
i geometria niewłaściwej klasy są odrzucane z jawnym powodem, nigdy po cichu poprawiane na „coś”.
"""

from __future__ import annotations

from collections.abc import Iterator

from shapely import make_valid
from shapely.errors import GEOSException
from shapely.geometry import MultiLineString, MultiPolygon
from shapely.geometry.base import BaseGeometry
from shapely.wkb import dumps as dump_wkb
from shapely.wkb import loads as load_wkb

from app.modules.imports.application.bdot10k_import import RepairOutcome
from app.modules.imports.domain.bdot10k import (
    REJECT_EMPTY_AFTER_REPAIR,
    REJECT_NO_GEOMETRY,
    REJECT_WRONG_GEOMETRY,
)

_LINE_TYPES = ("LineString", "MultiLineString")
_POLYGON_TYPES = ("Polygon", "MultiPolygon")


def _flatten(geometry: BaseGeometry) -> Iterator[BaseGeometry]:
    if geometry.geom_type.startswith("Multi") or geometry.geom_type == "GeometryCollection":
        for part in geometry.geoms:
            yield from _flatten(part)
    else:
        yield geometry


def _load(wkb: bytes) -> BaseGeometry | None:
    try:
        geometry = load_wkb(wkb)
    except (GEOSException, ValueError, TypeError):
        return None
    return geometry


class ShapelyGeometryTools:
    """Port ``Bdot10kGeometryTools`` oparty na Shapely; bez dostępu do bazy."""

    def repair_line(self, wkb: bytes) -> RepairOutcome:
        geometry = _load(wkb)
        if geometry is None:
            return RepairOutcome(None, reject_reason=REJECT_NO_GEOMETRY)
        if geometry.geom_type not in _LINE_TYPES:
            return RepairOutcome(None, reject_reason=REJECT_WRONG_GEOMETRY)
        repaired = not geometry.is_valid
        fixed = make_valid(geometry) if repaired else geometry
        parts = [
            part
            for part in _flatten(fixed)
            if part.geom_type == "LineString" and not part.is_empty and part.length > 0
        ]
        if not parts:
            return RepairOutcome(None, repaired=repaired, reject_reason=REJECT_EMPTY_AFTER_REPAIR)
        return RepairOutcome(dump_wkb(MultiLineString(parts)), repaired=repaired)

    def repair_polygon(self, wkb: bytes) -> RepairOutcome:
        geometry = _load(wkb)
        if geometry is None:
            return RepairOutcome(None, reject_reason=REJECT_NO_GEOMETRY)
        if geometry.geom_type not in _POLYGON_TYPES:
            return RepairOutcome(None, reject_reason=REJECT_WRONG_GEOMETRY)
        repaired = not geometry.is_valid
        fixed = make_valid(geometry) if repaired else geometry
        parts = [
            part
            for part in _flatten(fixed)
            if part.geom_type == "Polygon" and not part.is_empty and part.area > 0
        ]
        if not parts:
            return RepairOutcome(None, repaired=repaired, reject_reason=REJECT_EMPTY_AFTER_REPAIR)
        merged = MultiPolygon(parts)
        return RepairOutcome(dump_wkb(merged), repaired=repaired, area_sqm=float(merged.area))

    def repair_extent(self, wkb: bytes) -> RepairOutcome:
        return self.repair_polygon(wkb)
