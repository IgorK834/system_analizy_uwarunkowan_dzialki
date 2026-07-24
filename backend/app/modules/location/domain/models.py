"""Typy domenowe wyszukiwania adresów.

Wszystkie typy są niezmienne i wolne od infrastruktury. Punkt geograficzny jest
w WGS84 (EPSG:4326) — kanoniczny układ prezentacyjny dla frontendu i GeoJSON.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum


class ResultType(str, Enum):
    """Poziom szczegółowości wyniku adresowego."""

    COUNTRY = "country"
    VOIVODESHIP = "voivodeship"
    COUNTY = "county"
    MUNICIPALITY = "municipality"
    CITY = "city"
    STREET = "street"
    HOUSE_NUMBER = "house_number"


@dataclass(frozen=True)
class GeoPoint:
    """Punkt w WGS84 (lon, lat)."""

    lon: float
    lat: float


@dataclass(frozen=True)
class AddressParts:
    """Rozłożenie adresu na części administracyjne."""

    country: str | None = None
    voivodeship: str | None = None
    county: str | None = None
    municipality: str | None = None
    city: str | None = None
    street: str | None = None
    house_number: str | None = None


@dataclass(frozen=True)
class MatchRange:
    """Zakres [start, end) w ORYGINALNYM tekście label, który pasuje do zapytania."""

    start: int
    end: int


@dataclass(frozen=True)
class BBox:
    """Prostokąt WGS84 do biasowania/ograniczania wyników."""

    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

    def contains(self, point: GeoPoint) -> bool:
        return (
            self.min_lon <= point.lon <= self.max_lon
            and self.min_lat <= point.lat <= self.max_lat
        )


@dataclass(frozen=True)
class AddressQuery:
    """Zwalidowane zapytanie wyszukiwania adresu."""

    q: str
    limit: int = 10
    bias: GeoPoint | None = None
    bbox: BBox | None = None
    type_filter: frozenset[ResultType] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not self.q.strip():
            raise ValueError("Puste zapytanie adresowe.")
        if not 1 <= self.limit <= 20:
            raise ValueError("Limit musi być w zakresie 1-20.")
        for value in self._finite_values():
            if not math.isfinite(value):
                raise ValueError("Współrzędne muszą być skończone.")

    def _finite_values(self) -> list[float]:
        values: list[float] = []
        if self.bias is not None:
            values.extend([self.bias.lon, self.bias.lat])
        if self.bbox is not None:
            values.extend(
                [self.bbox.min_lon, self.bbox.min_lat, self.bbox.max_lon, self.bbox.max_lat]
            )
        return values


@dataclass(frozen=True)
class RawAddressCandidate:
    """Surowy kandydat zwrócony przez dostawcę, przed rankingiem.

    ``source_identifier`` to identyfikator ze źródła, jeżeli kontrakt go daje;
    w przeciwnym razie None i identyfikator wyniku zostanie policzony jako
    stabilny hash danych kanonicznych.
    """

    label: str
    point: GeoPoint
    parts: AddressParts
    result_type: ResultType
    provider_confidence: float
    source_identifier: str | None = None
    source_id: str | None = None


@dataclass(frozen=True)
class RankedAddress:
    """Wynik po rankingu, gotowy do zmapowania na DTO API."""

    id: str
    label: str
    match_ranges: tuple[MatchRange, ...]
    point: GeoPoint
    parts: AddressParts
    result_type: ResultType
    confidence: float
    score: float
    source_id: str | None = None
