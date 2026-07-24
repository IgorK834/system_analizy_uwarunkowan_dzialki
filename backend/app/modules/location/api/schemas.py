"""Schematy transportowe (DTO) endpointu wyszukiwania adresów."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.modules.location.domain.models import RankedAddress


class PointGeoJSON(BaseModel):
    type: str = Field(default="Point")
    coordinates: list[float] = Field(
        description="Współrzędne [lon, lat] w WGS84 (EPSG:4326).",
        json_schema_extra={"example": [21.0122, 52.2297]},
    )


class MatchRangeSchema(BaseModel):
    start: int = Field(ge=0, description="Początek zakresu dopasowania w label.")
    end: int = Field(ge=0, description="Koniec (wyłączny) zakresu dopasowania w label.")


class AddressPartsSchema(BaseModel):
    country: str | None = None
    voivodeship: str | None = None
    county: str | None = None
    municipality: str | None = None
    city: str | None = None
    street: str | None = None
    house_number: str | None = None


class SourceInfo(BaseModel):
    source_id: str = Field(description="Identyfikator źródła zgodny z katalogiem.")
    attribution: str = Field(description="Wymagana atrybucja źródła.")


class AddressSearchResult(BaseModel):
    id: str = Field(description="Jednoznaczny, deterministyczny identyfikator wyniku.")
    label: str = Field(description="Oryginalny tekst adresu do wyświetlenia.")
    match_ranges: list[MatchRangeSchema] = Field(
        description="Zakresy dopasowania odnoszące się do indeksów w label."
    )
    point: PointGeoJSON = Field(description="Punkt wyniku jako GeoJSON w WGS84.")
    address_parts: AddressPartsSchema
    result_type: str = Field(description="Poziom szczegółowości wyniku.")
    confidence: float = Field(ge=0.0, le=1.0)
    source: SourceInfo


class AddressSearchResponse(BaseModel):
    query: str
    results: list[AddressSearchResult]
    total_returned: int


class AddressIndexStatusResponse(BaseModel):
    ready: bool
    source: SourceInfo
    release_id: int | None = None
    version_label: str | None = None
    published_at: str | None = None
    entry_count: int = 0
    scopes: list[str] = Field(
        default_factory=list,
        description="Zakresy TERYT opublikowane w aktywnym wydaniu.",
    )


def to_result_dto(ranked: RankedAddress, source: SourceInfo) -> AddressSearchResult:
    """Mapuje wynik domenowy na DTO API (bez ujawniania kontraktu dostawcy)."""
    return AddressSearchResult(
        id=ranked.id,
        label=ranked.label,
        match_ranges=[
            MatchRangeSchema(start=match.start, end=match.end)
            for match in ranked.match_ranges
        ],
        point=PointGeoJSON(coordinates=[ranked.point.lon, ranked.point.lat]),
        address_parts=AddressPartsSchema(
            country=ranked.parts.country,
            voivodeship=ranked.parts.voivodeship,
            county=ranked.parts.county,
            municipality=ranked.parts.municipality,
            city=ranked.parts.city,
            street=ranked.parts.street,
            house_number=ranked.parts.house_number,
        ),
        result_type=ranked.result_type.value,
        confidence=ranked.confidence,
        source=source,
    )
