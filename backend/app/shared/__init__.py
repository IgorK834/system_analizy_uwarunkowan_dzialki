"""Pakiet współdzielonych, publicznych typów modularnego monolitu.

``app.shared`` zawiera wyłącznie stabilne, publiczne typy wartości używane przez
warstwy ``domain`` wielu modułów: kanoniczny układ współrzędnych (CRS),
lekkie typy geometrii oraz metadane pochodzenia (provenance). Pakiet NIE może
importować FastAPI, SQLAlchemy, GeoAlchemy2 ani httpx — jest bezpieczny do
importu z każdej warstwy domenowej (patrz docs/adr/ADR-001-modular-monolith.md).
"""

from app.shared.crs import CANONICAL_CRS, CrsCode, is_allowed_crs
from app.shared.geometry import BoundingBox, GeometryPayload
from app.shared.provenance import Provenance

__all__ = [
    "CANONICAL_CRS",
    "CrsCode",
    "is_allowed_crs",
    "BoundingBox",
    "GeometryPayload",
    "Provenance",
]
