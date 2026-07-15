from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field

from app.schemas.source import SourceMetadata, WarningMessage


class MapAnalyzeRequest(BaseModel):
    method: Literal["map"] = Field(
        description="Metoda wejścia oparta o punkt kliknięty na mapie.",
        json_schema_extra={"example": "map"},
    )
    lon: float = Field(
        ge=-180.0,
        le=180.0,
        description=(
            "Długość geograficzna punktu kliknięcia w WGS84 (EPSG:4326). "
            "Wartość zostanie przeliczona do EPSG:2180 przed zapytaniem ULDK."
        ),
        json_schema_extra={"example": 19.9449799},
    )
    lat: float = Field(
        ge=-90.0,
        le=90.0,
        description="Szerokość geograficzna punktu kliknięcia w WGS84 (EPSG:4326).",
        json_schema_extra={"example": 50.0646501},
    )


class AddressAnalyzeRequest(BaseModel):
    method: Literal["address"] = Field(
        description="Metoda wejścia oparta o adres wymagający geokodowania.",
        json_schema_extra={"example": "address"},
    )
    query: str = Field(
        min_length=3,
        max_length=500,
        description=(
            "Adres w formacie czytelnym dla geokodowania EMUiA/UUG, np. ulica, "
            "numer, miejscowość."
        ),
        json_schema_extra={"example": "ul. Marszałkowska 1, Warszawa"},
    )


class ParcelIdAnalyzeRequest(BaseModel):
    method: Literal["parcel_id"] = Field(
        description="Metoda wejścia oparta o identyfikator działki ewidencyjnej.",
        json_schema_extra={"example": "parcel_id"},
    )
    parcel_identifier: str = Field(
        min_length=5,
        max_length=50,
        description=(
            "Identyfikator działki ewidencyjnej w formacie ULDK, "
            "np. 122101_1.0001.1234/2."
        ),
        json_schema_extra={"example": "122101_1.0001.1234/2"},
    )


AnalyzeRequest = Annotated[
    Union[MapAnalyzeRequest, AddressAnalyzeRequest, ParcelIdAnalyzeRequest],
    Field(discriminator="method"),
]


class AnalyzeResumeRequest(BaseModel):
    analysis_id: int = Field(
        gt=0,
        description=(
            "Identyfikator analizy oczekującej na ręczne podanie symbolu strefy "
            "(status='waiting_for_zone_symbol')."
        ),
        json_schema_extra={"example": 123},
    )
    zone_symbol: str = Field(
        min_length=1,
        max_length=20,
        description="Symbol strefy MPZP odczytany przez użytkownika z mapy rastrowej.",
        json_schema_extra={"example": "230_U"},
    )


class GeometryMetrics(BaseModel):
    area_sqm: float = Field(
        ge=0.0,
        description="Pole powierzchni działki w metrach kwadratowych.",
        json_schema_extra={"example": 1250.5},
    )
    area_ha: float = Field(
        ge=0.0,
        description="Pole powierzchni działki w hektarach.",
        json_schema_extra={"example": 0.12505},
    )
    perimeter_m: float = Field(
        ge=0.0,
        description="Obwód działki w metrach.",
        json_schema_extra={"example": 146.2},
    )
    is_valid: bool = Field(
        description="Czy geometria przeszła walidację Shapely.",
        json_schema_extra={"example": True},
    )
    geometry_repaired: bool = Field(
        description="Czy geometria była naprawiana przed obliczeniami.",
        json_schema_extra={"example": False},
    )


class ParcelGeometryResponse(BaseModel):
    parcel_identifier: str = Field(
        description="Identyfikator działki ewidencyjnej.",
        json_schema_extra={"example": "122101_1.0001.1234/2"},
    )
    geometry_geojson: dict[str, Any] = Field(
        description="Geometria działki w WGS84 (EPSG:4326) jako GeoJSON dla frontendu.",
        json_schema_extra={
            "example": {
                "type": "MultiPolygon",
                "coordinates": [[[[19.94, 50.06], [19.95, 50.06], [19.94, 50.06]]]],
            }
        },
    )
    metrics: GeometryMetrics = Field(
        description="Metryki geometryczne obliczone w EPSG:2180.",
        json_schema_extra={
            "example": {
                "area_sqm": 1250.5,
                "area_ha": 0.12505,
                "perimeter_m": 146.2,
                "is_valid": True,
                "geometry_repaired": False,
            }
        },
    )
    source: SourceMetadata = Field(
        description="Metadane źródła geometrii działki.",
        json_schema_extra={
            "example": {
                "source_name": "ULDK",
                "source_url": "https://uldk.gugik.gov.pl/",
                "fetched_at": "2026-07-02T12:00:00Z",
                "confidence": 0.95,
                "manual_review_required": False,
            }
        },
    )


class MpzpZoneResult(BaseModel):
    zone_symbol: str = Field(
        description="Symbol strefy MPZP przecinającej działkę.",
        json_schema_extra={"example": "MN"},
    )
    primary_use: str | None = Field(
        default=None,
        description="Podstawowe przeznaczenie terenu w strefie MPZP.",
        json_schema_extra={"example": "zabudowa mieszkaniowa jednorodzinna"},
    )
    supplementary_use: str | None = Field(
        default=None,
        description="Uzupełniające przeznaczenie terenu w strefie MPZP.",
        json_schema_extra={"example": "usługi nieuciążliwe"},
    )
    max_building_height_m: float | None = Field(
        default=None,
        ge=0.0,
        description="Maksymalna wysokość zabudowy w metrach.",
        json_schema_extra={"example": 9.0},
    )
    max_floors: int | None = Field(
        default=None,
        ge=0,
        description="Maksymalna liczba kondygnacji.",
        json_schema_extra={"example": 2},
    )
    min_biologically_active_pct: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Minimalny udział powierzchni biologicznie czynnej w procentach.",
        json_schema_extra={"example": 40.0},
    )
    max_floor_area_ratio: float | None = Field(
        default=None,
        ge=0.0,
        description="Maksymalny wskaźnik intensywności zabudowy.",
        json_schema_extra={"example": 0.8},
    )
    min_floor_area_ratio: float | None = Field(
        default=None,
        ge=0.0,
        description="Minimalny wskaźnik intensywności zabudowy.",
        json_schema_extra={"example": 0.01},
    )
    max_building_coverage_pct: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Maksymalna powierzchnia zabudowy w procentach powierzchni działki.",
        json_schema_extra={"example": 30.0},
    )
    intersection_area_sqm: float = Field(
        ge=0.0,
        description="Pole przecięcia działki ze strefą MPZP w metrach kwadratowych.",
        json_schema_extra={"example": 830.0},
    )
    intersection_pct: float = Field(
        ge=0.0,
        le=100.0,
        description="Udział powierzchni działki w tej strefie MPZP.",
        json_schema_extra={"example": 66.4},
    )
    is_dominant: bool = Field(
        description="Czy strefa ma największy udział powierzchniowy w działce.",
        json_schema_extra={"example": True},
    )
    source: SourceMetadata = Field(
        description="Metadane źródła danych MPZP.",
        json_schema_extra={
            "example": {
                "source_name": "KIMPZP",
                "source_url": "https://example.local/mpzp",
                "fetched_at": "2026-07-02T12:00:00Z",
                "confidence": 0.8,
                "manual_review_required": True,
            }
        },
    )


class PogResult(BaseModel):
    status: str = Field(
        description="Status dostępności Planu Ogólnego Gminy.",
        json_schema_extra={"example": "adopted"},
    )
    planning_zone: str | None = Field(
        default=None,
        description="Strefa planistyczna POG, jeżeli została ustalona.",
        json_schema_extra={"example": "SJ"},
    )
    ouz_intersection_area_sqm: float | None = Field(
        default=None,
        ge=0.0,
        description="Pole przecięcia działki z OUZ w metrach kwadratowych.",
        json_schema_extra={"example": 420.0},
    )
    ouz_intersection_pct: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Udział powierzchni działki przecinającej OUZ.",
        json_schema_extra={"example": 33.6},
    )
    touches_ouz_boundary: bool = Field(
        description="Czy działka jedynie dotyka granicy OUZ.",
        json_schema_extra={"example": False},
    )
    source: SourceMetadata | None = Field(
        default=None,
        description="Metadane źródła POG/OUZ, jeżeli dane były dostępne.",
        json_schema_extra={
            "example": {
                "source_name": "PlanyOgolneGmin",
                "source_url": "https://example.local/pog",
                "fetched_at": "2026-07-02T12:00:00Z",
                "confidence": 0.75,
                "manual_review_required": False,
            }
        },
    )


class InfrastructureResult(BaseModel):
    network_type: str = Field(
        description="Typ sieci uzbrojenia terenu.",
        json_schema_extra={"example": "water"},
    )
    buffer_m: float = Field(
        ge=0.0,
        description="Bufor techniczny wokół sieci w metrach.",
        json_schema_extra={"example": 4.0},
    )
    source: SourceMetadata = Field(
        description="Metadane źródła danych o uzbrojeniu terenu.",
        json_schema_extra={
            "example": {
                "source_name": "KIUT",
                "source_url": "https://example.local/kiut",
                "fetched_at": "2026-07-02T12:00:00Z",
                "confidence": 0.7,
                "manual_review_required": True,
            }
        },
    )


class RiskResult(BaseModel):
    risk_type: str = Field(
        description="Typ ryzyka środowiskowego lub przestrzennego.",
        json_schema_extra={"example": "flood_zone"},
    )
    description: str = Field(
        description="Czytelny opis ryzyka dla użytkownika.",
        json_schema_extra={"example": "Część działki znajduje się w obszarze zagrożenia powodziowego."},
    )
    source: SourceMetadata = Field(
        description="Metadane źródła danych o ryzyku.",
        json_schema_extra={
            "example": {
                "source_name": "ISOK",
                "source_url": "https://example.local/isok",
                "fetched_at": "2026-07-02T12:00:00Z",
                "confidence": 0.85,
                "manual_review_required": False,
            }
        },
    )


class AnalyzeResponse(BaseModel):
    analysis_id: int | None = Field(
        default=None,
        description="Identyfikator analizy, None gdy wynik nie został zapisany.",
        json_schema_extra={"example": 123},
    )
    status: str = Field(
        description="Status wykonania analizy.",
        json_schema_extra={"example": "partial"},
    )
    analyzed_at: datetime = Field(
        description="Data i czas wykonania analizy.",
        json_schema_extra={"example": "2026-07-02T12:00:00Z"},
    )
    parcel: ParcelGeometryResponse | None = Field(
        default=None,
        description="Geometria i metryki działki, jeżeli udało się ją ustalić.",
        json_schema_extra={"example": None},
    )
    mpzp_zones: list[MpzpZoneResult] = Field(
        description="Lista stref MPZP przecinających działkę.",
        json_schema_extra={"example": []},
    )
    pog: PogResult | None = Field(
        default=None,
        description="Wynik analizy POG i OUZ, jeżeli dane są dostępne.",
        json_schema_extra={"example": None},
    )
    infrastructure: list[InfrastructureResult] = Field(
        description="Lista wykrytych sieci uzbrojenia terenu.",
        json_schema_extra={"example": []},
    )
    risks: list[RiskResult] = Field(
        description="Lista ryzyk środowiskowych i przestrzennych.",
        json_schema_extra={"example": []},
    )
    buildable_area_sqm: float | None = Field(
        default=None,
        ge=0.0,
        description="Szacowana powierzchnia możliwa do zabudowy w metrach kwadratowych.",
        json_schema_extra={"example": 560.0},
    )
    manual_zone_required: bool = Field(
        default=False,
        description=(
            "Czy analiza wymaga ręcznego podania symbolu strefy z mapy "
            "rastrowej (gmina nie udostępnia wektorowych danych MPZP). Jeżeli "
            "True, wznów analizę przez POST /analyze/resume z analysis_id "
            "i odczytanym symbolem strefy."
        ),
        json_schema_extra={"example": False},
    )
    warnings: list[WarningMessage] = Field(
        description="Ostrzeżenia o niepewności lub brakujących sekcjach analizy.",
        json_schema_extra={"example": []},
    )
    sources: list[SourceMetadata] = Field(
        description="Wszystkie zewnętrzne źródła danych użyte w analizie.",
        json_schema_extra={"example": []},
    )


class ErrorResponse(BaseModel):
    error: str = Field(
        description="Krótki kod błędu API.",
        json_schema_extra={"example": "VALIDATION_ERROR"},
    )
    detail: str = Field(
        description="Czytelny komunikat dla użytkownika, bez stack trace.",
        json_schema_extra={"example": "Nie udało się zwalidować danych wejściowych."},
    )
    section: str | None = Field(
        default=None,
        description="Sekcja analizy, której dotyczy błąd.",
        json_schema_extra={"example": "parcel"},
    )
