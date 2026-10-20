"""Port i przypadek użycia lokalnej analizy pokrycia BDOT10k (AU-204)."""

from dataclasses import dataclass, field
from typing import Protocol

from app.modules.analysis.application.bdot_ports import BdotIndexError, BdotReleaseLocator
from app.modules.analysis.domain.bdot import BdotReleaseRef
from app.modules.analysis.domain.landcover import CoverMeasurement, ForestMeasurement, coverage_is_complete


class LandCoverIndex(BdotReleaseLocator, Protocol):
    def land_cover_ready(self, release: BdotReleaseRef) -> bool: ...
    def cover(
        self, release: BdotReleaseRef, parcel_wkt: str, radius_m: float, limit: int
    ) -> list[CoverMeasurement]: ...
    def forest(self, release: BdotReleaseRef, parcel_wkt: str, radius_m: float) -> ForestMeasurement | None: ...


@dataclass
class LandCoverOutcome:
    status: str
    reason_code: str | None = None
    release: BdotReleaseRef | None = None
    measurements: list[CoverMeasurement] = field(default_factory=list)
    search_area_within_coverage: bool | None = None
    forest: ForestMeasurement | None = None
    forest_status: str = "unknown"
    forest_reason_code: str | None = None
    forest_search_radius_m: float = 5000


def find_land_cover(
    parcel_wkt: str,
    index: LandCoverIndex,
    *,
    radius_m: float = 200,
    forest_radius_m: float = 5000,
    candidate_limit: int = 20000,
) -> LandCoverOutcome:
    result = LandCoverOutcome("unavailable", forest_search_radius_m=forest_radius_m)
    try:
        result.release = release = index.release_for(parcel_wkt)
        if release is None:
            result.status = "no_coverage"
            result.reason_code = "OUTSIDE_COVERAGE" if index.has_active_releases() else "NO_ACTIVE_RELEASE"
            return result
        if not index.land_cover_ready(release):
            result.status, result.reason_code = "no_coverage", "LAND_COVER_NOT_IMPORTED"
            return result
        coverage = index.coverage(release, parcel_wkt, radius_m)
        result.search_area_within_coverage = coverage.parcel_covered and coverage.search_area_covered
        if not result.search_area_within_coverage:
            result.status, result.reason_code = "no_coverage", "SEARCH_AREA_PARTIALLY_OUTSIDE_COVERAGE"
            return result
        result.measurements = index.cover(release, parcel_wkt, radius_m, candidate_limit)
        if coverage_is_complete(result.measurements):
            result.status = "available"
        else:
            result.status, result.reason_code = "unknown", "LAND_COVER_GEOMETRY_GAPS_OR_OVERLAPS"
        result.forest = index.forest(release, parcel_wkt, forest_radius_m)
        # Sprawdzenie całego mniejszego promienia dowodzi, że bliższy las nie został pominięty za granicą wydania.
        check_radius = result.forest.distance_m if result.forest else forest_radius_m
        if index.coverage(release, parcel_wkt, max(16, check_radius)).search_area_covered:
            result.forest_status = "available" if result.forest else "no_match"
            result.forest_reason_code = None if result.forest else "SEARCH_RADIUS_EXHAUSTED"
        else:
            result.forest = None
            result.forest_status, result.forest_reason_code = "no_coverage", "SEARCH_AREA_PARTIALLY_OUTSIDE_COVERAGE"
        return result
    except BdotIndexError as exc:
        result.status, result.reason_code = "unavailable", exc.reason_code
        result.measurements, result.forest = [], None
        result.forest_status, result.forest_reason_code = "unavailable", exc.reason_code
        return result
