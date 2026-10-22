"""Wspólny model pomiaru odległości do obiektów środowiskowych.

Model jest niezależny od HTTP, Pydantic i warstwy prezentacji. Adaptery ISOK
i GDOŚ zwracają te same trzy rozłączne wyniki wyszukiwania w EPSG:2180:
przecięcie, najbliższy obiekt w promieniu albo potwierdzony brak obiektu w
ograniczonym promieniu. Brak obiektu w promieniu nie jest brakiem ograniczenia
poza tym promieniem.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ProximityStatus = Literal["intersection", "near", "none_within_radius"]


@dataclass(frozen=True)
class EnvironmentalProximity:
    """Najbliższy wynik jednej klasy/warstwy, zmierzony w EPSG:2180."""

    category: str
    label: str
    layer_name: str
    status: ProximityStatus
    search_radius_m: float
    distance_m: float | None = None
    feature_id: str | None = None
    name: str | None = None
    probability_class: str | None = None
    return_period_years: int | None = None
    informational_only: bool = False
