"""Współdzielony, publiczny typ układu współrzędnych (CRS).

Kanoniczny układ obliczeniowy systemu to EPSG:2180 (PUWG 1992). Ten moduł
celowo używa wyłącznie biblioteki standardowej, aby był bezpieczny do importu
z dowolnej warstwy ``domain``.
"""

from __future__ import annotations

from typing import Final

CrsCode = str

# Kanoniczny układ współrzędnych dla wszystkich geometrii domenowych.
CANONICAL_CRS: Final[CrsCode] = "EPSG:2180"

# Układy dopuszczalne jako źródłowe w kontraktach danych. Zgodne z
# app.core.data_sources.ALLOWED_SOURCE_CRS, ale bez zależności od katalogu —
# shared musi pozostać wolny od zależności aplikacyjnych.
_ALLOWED_CRS: Final[frozenset[CrsCode]] = frozenset(
    {
        "EPSG:2180",
        "EPSG:4326",
        "EPSG:3857",
        "EPSG:4258",
        "EPSG:2176",
        "EPSG:2177",
        "EPSG:2178",
        "EPSG:2179",
    }
)


def is_allowed_crs(crs: CrsCode) -> bool:
    """Zwraca True, jeżeli kod CRS należy do dozwolonego zbioru układów."""
    return crs in _ALLOWED_CRS
