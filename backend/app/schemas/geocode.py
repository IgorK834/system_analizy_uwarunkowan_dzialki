from __future__ import annotations

from pydantic import BaseModel, Field


class GeocodeSuggestionResponse(BaseModel):
    label: str = Field(
        description="Sformatowany adres do wyświetlenia",
        json_schema_extra={"example": "Marki, Generała Władysława Andersa 1"},
    )
    x: float = Field(
        description="Współrzędna X w EPSG:2180 (PUWG 1992)",
        json_schema_extra={"example": 644234.29},
    )
    y: float = Field(
        description="Współrzędna Y w EPSG:2180 (PUWG 1992)",
        json_schema_extra={"example": 499514.03},
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Dokładność dopasowania adresu 0-1",
        json_schema_extra={"example": 0.44},
    )
    teryt: str = Field(
        description="Kod TERYT gminy",
        json_schema_extra={"example": "143402"},
    )


class GeocodeResponse(BaseModel):
    query: str = Field(description="Znormalizowane zapytanie adresowe")
    suggestions: list[GeocodeSuggestionResponse] = Field(
        description="Lista sugestii adresowych z punktami w EPSG:2180"
    )
    total_returned: int = Field(description="Liczba zwróconych sugestii")
