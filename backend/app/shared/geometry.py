"""Współdzielone, lekkie typy geometrii niezależne od bibliotek GIS.

Typy przenoszą geometrię jako dane (WKT/GeoJSON) z jawnym CRS, bez zależności od
Shapely, GeoAlchemy2 czy PostGIS. Dzięki temu warstwy ``domain`` mogą operować
na geometrii jako wartości, a konkretne biblioteki GIS pozostają w
``infrastructure``.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.shared.crs import CANONICAL_CRS, CrsCode


@dataclass(frozen=True)
class GeometryPayload:
    """Geometria jako niezmienna wartość z jawnym układem współrzędnych.

    ``wkt`` przechowuje geometrię w reprezentacji tekstowej WKT. ``crs`` jest
    jawny, aby żaden odbiorca nie zakładał domyślnego układu.
    """

    wkt: str
    crs: CrsCode = CANONICAL_CRS

    def is_canonical(self) -> bool:
        """Czy geometria jest już w kanonicznym układzie EPSG:2180."""
        return self.crs == CANONICAL_CRS


@dataclass(frozen=True)
class BoundingBox:
    """Prostokąt otaczający w jednostkach danego CRS."""

    min_x: float
    min_y: float
    max_x: float
    max_y: float
    crs: CrsCode = CANONICAL_CRS

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.min_x, self.min_y, self.max_x, self.max_y)
