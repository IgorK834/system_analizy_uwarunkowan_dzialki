"""Wersjonowany kontrakt AU-204 — null nie oznacza zera."""

from typing import Any, Self
from pydantic import BaseModel, Field, model_validator
from app.schemas.bdot_context import BdotContextStatus, BdotReleaseInfo
from app.schemas.source import SourceMetadata
from app.modules.analysis.domain.landcover import LAND_COVER_NOTE

LAND_COVER_RESULT_SCHEMA_VERSION = "1.0"


class LandCoverShare(BaseModel):
    class_code: str
    label: str
    area_sqm: float = Field(ge=0)
    share_pct: float = Field(ge=0, le=100.0001)


class LandCoverZone(BaseModel):
    area_sqm: float = Field(gt=0)
    classified_share_pct: float = Field(ge=0)
    classes: list[LandCoverShare]


class ForestBuffer(BaseModel):
    radius_m: float = Field(gt=0)
    overlap_with_parcel_sqm: float = Field(ge=0)
    geometry_geojson: dict[str, Any] | None = None


class ForestContext(BaseModel):
    status: BdotContextStatus = "unknown"
    reason_code: str | None = None
    distance_m: float | None = Field(default=None, ge=0)
    forest_id: str | None = None
    search_radius_m: float = Field(default=5000, gt=0)
    distance_reference: str = "parcel_polygon_to_forest_polygon"
    buffers: list[ForestBuffer] = Field(default_factory=list)
    geometry_geojson: dict[str, Any] | None = None


class LandCoverResult(BaseModel):
    schema_version: str = LAND_COVER_RESULT_SCHEMA_VERSION
    status: BdotContextStatus
    reason_code: str | None = None
    radius_m: float = Field(default=200, gt=0)
    surroundings_definition: str = "buffer(parcel, 200 m) - parcel"
    parcel: LandCoverZone | None = None
    surroundings: LandCoverZone | None = None
    forest: ForestContext = Field(default_factory=ForestContext)
    search_area_within_coverage: bool | None = None
    release: BdotReleaseInfo | None = None
    source: SourceMetadata | None = None
    interpretation_note: str = LAND_COVER_NOTE
    features_geojson: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.status == "available" and (self.parcel is None or self.surroundings is None):
            raise ValueError("Wynik available wymaga obliczeń obu obszarów.")
        if self.status != "unknown" and self.source is None:
            raise ValueError("Sekcja wymaga provenance źródła.")
        if self.status not in {"available", "unknown"} and not self.reason_code:
            raise ValueError("Brak wyniku wymaga reason_code.")
        return self
