"""Kontrakt sekcji z lokalnego wydania BDOT10k: ``road_context`` (BK-305) i ``neighbour_buildings`` (BK-304).

Obie sekcje mają statusy rozłączne ``available | no_match | no_coverage | unavailable | unknown``. Brak danych nie jest
brakiem drogi ani budynków, ``null`` nie jest zerem, a błąd źródła nigdy nie staje się „brakiem”. W kontrakcie nie ma
pola prawnego dostępu do drogi publicznej — wartość taka nie wynika z geometrii (test architektoniczny to pilnuje).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, model_validator

from app.modules.analysis.domain.buildings import (
    BUILDING_CONTEXT_NOTE,
    DISTANCE_REFERENCE as BUILDING_DISTANCE_REFERENCE,
    RING_DEFINITION,
)
from app.modules.analysis.domain.roads import DISTANCE_REFERENCE as ROAD_DISTANCE_REFERENCE
from app.modules.analysis.domain.roads import ROAD_ACCESS_DISCLAIMER
from app.schemas.source import SourceMetadata

ROAD_RESULT_SCHEMA_VERSION = "1.0"
BUILDINGS_RESULT_SCHEMA_VERSION = "1.1"

BdotContextStatus = Literal["available", "no_match", "no_coverage", "unavailable", "unknown"]
_STATUS_DESCRIPTION = (
    "available — obliczono z lokalnego wydania BDOT10k; no_match — nic nie znaleziono w ograniczonym zasięgu "
    "(nie dowodzi braku obiektu); no_coverage — działka poza zasięgiem zaimportowanych powiatów (to NIE jest "
    "brak drogi ani budynków); unavailable — timeout lub błąd zapytania; unknown — zapis sprzed tej sekcji."
)


class BdotReleaseInfo(BaseModel):
    """Wydanie danych (paczka powiatowa) przypięte do wyniku — odtwarzalność i data stanu danych."""

    data_release_id: int = Field(gt=0)
    version_label: str
    teryt_powiat: str = Field(description="Czterocyfrowy TERYT powiatu paczki.")
    data_as_of: date | None = Field(
        default=None,
        description="Najnowsza wersja obiektu w paczce — data, na którą dane są aktualne (nie data pobrania).",
    )
    package_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    imported_at: datetime | None = Field(default=None, description="Chwila importu wydania do bazy.")


class RoadNearestResult(BaseModel):
    """Najbliższa oś jezdni: fakty geometryczne i opis ze źródła (kategoria, klasa) bez oceny prawnej."""

    road_id: str = Field(description="lokalnyId obiektu OT_SKJZ_L.")
    distance_m: float = Field(ge=0.0, description="Odległość od poligonu działki do osi jezdni, m (0 — przecina/dotyka).")
    relation: Literal["intersects", "touches", "disjoint"]
    geometry_kind: Literal["carriageway_axis"] = "carriageway_axis"
    category: str | None = Field(default=None, description="Kategoria zarządzania drogą ze źródła (np. gminna, wewnętrzna).")
    road_class: str | None = Field(default=None, description="Klasa drogi ze źródła (np. droga lokalna).")
    road_number: str | None = None
    street_name: str | None = None
    surface_material: str | None = None
    surface_width_m: float | None = Field(default=None, gt=0.0)
    position: str | None = Field(default=None, description="Położenie ze źródła (np. na powierzchni gruntu).")
    feature_version: str | None = Field(default=None, description="Wersja obiektu w źródle (ISO 8601, UTC).")
    road_geojson: dict[str, Any] | None = Field(default=None, description="Oś jezdni jako GeoJSON Feature (EPSG:4326).")
    link_geojson: dict[str, Any] | None = Field(
        default=None, description="Najkrótszy odcinek działka → oś jezdni (null, gdy odległość to 0)."
    )


class RoadContextResult(BaseModel):
    """Kontekst drogowy działki z lokalnego wydania BDOT10k."""

    schema_version: str = ROAD_RESULT_SCHEMA_VERSION
    status: BdotContextStatus = Field(description=_STATUS_DESCRIPTION)
    reason_code: str | None = Field(default=None, description="Kod przyczyny statusu innego niż available.")
    distance_reference: str = Field(
        default=ROAD_DISTANCE_REFERENCE,
        description="Co jest mierzone: od poligonu działki do osi jezdni (nie do granicy pasa drogowego).",
    )
    nearest: RoadNearestResult | None = None
    search_radius_m: float | None = Field(
        default=None,
        gt=0.0,
        description="Promień, w którym znaleziono drogę (available) albo największy sprawdzony (no_match).",
    )
    searched_radii_m: list[float] = Field(default_factory=list)
    max_search_radius_m: float | None = Field(default=None, gt=0.0)
    candidate_limit: int | None = Field(default=None, ge=1)
    search_area_within_coverage: bool | None = Field(
        default=None,
        description="Czy obszar wyszukiwania leży w całości w zasięgu wydania (granica powiatu); false — wynik częściowy.",
    )
    intersecting_road_count: int | None = Field(default=None, ge=0)
    touching_road_count: int | None = Field(default=None, ge=0)
    release: BdotReleaseInfo | None = None
    interpretation_note: str = Field(
        default=ROAD_ACCESS_DISCLAIMER,
        description="Obowiązkowa uwaga interpretacyjna — ta sama w API, UI, PDF i pakiecie audytowym.",
    )
    source: SourceMetadata | None = Field(
        default=None, description="Provenance zapytania; null tylko dla statusu unknown."
    )
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_consistency(self) -> Self:
        if self.status == "available":
            if self.nearest is None:
                raise ValueError("Wynik available wymaga najbliższej drogi.")
        elif self.nearest is not None:
            raise ValueError("Bez ustalonej drogi pole nearest musi być null — brak danych to nie brak drogi.")
        if self.status != "unknown" and self.source is None:
            raise ValueError("Wynik sekcji drogowej musi zachować provenance źródła.")
        if self.status != "available" and self.reason_code is None and self.status != "unknown":
            raise ValueError("Status inny niż available wymaga reason_code.")
        return self


BuildingRelationValue = Literal["on_parcel", "straddling", "adjacent", "neighbour"]


class BuildingBufferResult(BaseModel):
    """Statystyki pierścienia ``buffer(działka, r) − działka`` (kumulatywne); ``null`` ≠ 0."""

    radius_m: float = Field(gt=0.0)
    ring_area_sqm: float = Field(ge=0.0)
    count: int = Field(ge=0, description="Unikalne budynki z częścią w pierścieniu > 0,01 m².")
    straddling_parcel_count: int = Field(ge=0, description="Z nich: budynki zachodzące także na działkę.")
    footprint_area_in_ring_sqm: float = Field(ge=0.0, description="Suma pól części obrysów przyciętych do pierścienia.")
    union_area_in_ring_sqm: float = Field(ge=0.0, description="Pole sumy części obrysów (bez podwójnego liczenia).")
    built_share_pct: float = Field(ge=0.0, le=100.0001, description="union / pole pierścienia, %.")
    min_distance_m: float | None = Field(default=None, ge=0.0)
    footprint_min_sqm: float | None = Field(default=None, ge=0.0)
    footprint_median_sqm: float | None = Field(default=None, ge=0.0)
    footprint_max_sqm: float | None = Field(default=None, ge=0.0)


class ParcelBuildingsResult(BaseModel):
    """Budynki na działce: część obrysu w działce > 0,01 m²."""

    count: int = Field(ge=0)
    union_area_in_parcel_sqm: float = Field(ge=0.0)
    coverage_pct: float = Field(ge=0.0, le=100.0001, description="Pole zabudowy na działce / pole działki, % (wynik obliczenia).")


class NeighbourBuildingItem(BaseModel):
    """Budynek w tabeli i na mapie — bez nazw, uwag i właścicieli."""

    building_id: str = Field(description="lokalnyId obiektu OT_BUBD_A.")
    distance_m: float = Field(ge=0.0)
    relation: BuildingRelationValue
    overlap_with_parcel_sqm: float = Field(ge=0.0)
    footprint_sqm: float = Field(gt=0.0)
    general_function: str | None = None
    detailed_function: str | None = None
    predominant_function: str | None = None
    floors: int | None = Field(default=None, ge=0)
    geometry_source: str | None = None
    feature_version: str | None = None
    geometry_geojson: dict[str, Any] | None = Field(default=None, description="Obrys jako GeoJSON Feature (EPSG:4326).")


class NeighbourBuildingsResult(BaseModel):
    """Kontekst istniejącej zabudowy w otoczeniu działki (nie „możliwość zabudowy”)."""

    schema_version: str = BUILDINGS_RESULT_SCHEMA_VERSION
    status: BdotContextStatus = Field(description=_STATUS_DESCRIPTION)
    reason_code: str | None = None
    neighbour_parcel_indicators_status: Literal["unknown"] = "unknown"
    neighbour_parcel_indicators_reason: str = "NEIGHBOUR_PARCEL_GEOMETRY_NOT_AVAILABLE"
    ring_definition: str = Field(default=RING_DEFINITION, description="Definicja sąsiedztwa o promieniu r.")
    distance_reference: str = Field(default=BUILDING_DISTANCE_REFERENCE)
    buffers_m: list[float] = Field(default_factory=list)
    parcel_area_sqm: float | None = Field(default=None, gt=0.0)
    on_parcel: ParcelBuildingsResult | None = None
    buffers: list[BuildingBufferResult] = Field(default_factory=list)
    buildings: list[NeighbourBuildingItem] = Field(default_factory=list)
    buildings_truncated: bool = False
    listed_radius_m: float | None = Field(default=None, gt=0.0)
    candidates_considered: int | None = Field(default=None, ge=0)
    search_area_within_coverage: bool | None = None
    release: BdotReleaseInfo | None = None
    interpretation_note: str = Field(default=BUILDING_CONTEXT_NOTE)
    source: SourceMetadata | None = None
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_consistency(self) -> Self:
        measured = self.on_parcel is not None or bool(self.buffers)
        if self.status == "available":
            if self.on_parcel is None or len(self.buffers) != len(self.buffers_m):
                raise ValueError("Wynik available wymaga obliczeń na działce i dla każdego bufora.")
        elif self.status != "no_match" and measured:
            raise ValueError("Bez obliczeń statystyki muszą być null/puste — brak danych to nie brak budynków.")
        if self.status != "unknown" and self.source is None:
            raise ValueError("Wynik sekcji zabudowy musi zachować provenance źródła.")
        if self.status not in {"available", "unknown"} and self.reason_code is None:
            raise ValueError("Status inny niż available wymaga reason_code.")
        return self
