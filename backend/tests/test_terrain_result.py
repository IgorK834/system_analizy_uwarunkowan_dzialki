"""Kontrakt ``TerrainResult`` i mapowanie sekcji NMT (BK-301/BK-302)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.modules.analysis.application.terrain import RasterMetadata, ReliefOutcome
from app.modules.analysis.domain.terrain import (
    ElevationGrid,
    compute_derivatives,
    sample_profile,
)
from app.schemas.analyze import (
    TerrainAspectResult,
    TerrainReliefResult,
    TerrainResult,
)
from app.schemas.source import SourceMetadata
from app.services.context import ContextSectionResult
from app.services.nmt import TerrainExtremes, TerrainNoCoverage
from app.services.terrain import (
    LEGACY_TERRAIN_WARNING,
    build_terrain_result,
    relief_result_from_outcome,
    terrain_from_snapshot,
    terrain_sources,
    terrain_warnings,
)
from app.shared.geometry import BoundingBox
from app.shared.provenance import Provenance

NOW = datetime(2026, 9, 28, 10, 0, tzinfo=timezone.utc)
SHA = "a" * 64


def _source(**overrides: object) -> SourceMetadata:
    values: dict[str, object] = {
        "source_id": "nmt",
        "source_name": "NMT",
        "source_url": "https://services.gugik.gov.pl/nmt/?request=GetMinMaxByPolygon",
        "fetched_at": NOW,
        "response_status": 200,
        "confidence": 0.9,
        "manual_review_required": False,
    }
    values.update(overrides)
    return SourceMetadata(**values)  # type: ignore[arg-type]


def _available(**overrides: object) -> TerrainResult:
    values: dict[str, object] = {
        "status": "available",
        "min_height_m": 112.3,
        "max_height_m": 115.7,
        "height_difference_m": 3.4,
        "grid_size_m": 4.0,
        "sampled_points": 676,
        "source": _source(),
    }
    values.update(overrides)
    return TerrainResult(**values)  # type: ignore[arg-type]


# --- Kontrakt ---------------------------------------------------------------


def test_control_measurement_is_valid_and_serializes_units() -> None:
    terrain = _available()
    dumped = terrain.model_dump(mode="json")
    assert dumped["height_difference_m"] == 3.4
    assert dumped["schema_version"] == "1.0"
    assert TerrainResult.model_validate(dumped) == terrain


@pytest.mark.parametrize(
    "overrides",
    [
        {"min_height_m": None},
        {"height_difference_m": None},
        {"min_height_m": 116.0, "max_height_m": 115.7, "height_difference_m": 0.3},
        {"height_difference_m": 3.5},
        {"source": None},
    ],
)
def test_available_measurement_rules(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _available(**overrides)


def test_difference_tolerance_and_negative_heights() -> None:
    # Szum zmiennoprzecinkowy mieści się w tolerancji; ujemne wysokości są realne.
    assert _available(height_difference_m=3.4001).height_difference_m == 3.4001
    negative = _available(min_height_m=-1.8, max_height_m=0.4, height_difference_m=2.2)
    assert negative.min_height_m == -1.8
    flat = _available(min_height_m=101.2, max_height_m=101.2, height_difference_m=0.0)
    assert flat.height_difference_m == 0.0


@pytest.mark.parametrize("status", ["no_coverage", "unavailable", "unknown"])
def test_missing_measurement_keeps_null_not_zero(status: str) -> None:
    with pytest.raises(ValidationError):
        TerrainResult(status=status, height_difference_m=0.0, source=_source())
    with pytest.raises(ValidationError):
        TerrainResult(status=status, min_height_m=0.0, source=_source())


def test_empty_results_require_provenance_except_unknown() -> None:
    with pytest.raises(ValidationError):
        TerrainResult(status="no_coverage")
    with pytest.raises(ValidationError):
        TerrainResult(status="unavailable")
    assert TerrainResult(status="unknown").source is None


def test_aspect_contract_keeps_null_for_flat() -> None:
    with pytest.raises(ValidationError):
        TerrainAspectResult(
            status="flat",
            mean_azimuth_deg=0.0,
            non_flat_share_pct=0.0,
            flat_threshold_pct=2.0,
        )
    with pytest.raises(ValidationError):
        TerrainAspectResult(status="defined", non_flat_share_pct=50.0, flat_threshold_pct=2.0)
    with pytest.raises(ValidationError):
        TerrainAspectResult(
            status="dispersed",
            mean_azimuth_deg=10.0,
            resultant_length=0.1,
            dominant_direction="N",
            non_flat_share_pct=50.0,
            flat_threshold_pct=2.0,
        )


def test_relief_contract_forbids_statistics_without_measurement() -> None:
    base = {
        "algorithm_version": "horn1981-3x3-v1",
        "slope_classes_version": "slope-classes-pl-v1",
    }
    with pytest.raises(ValidationError):
        TerrainReliefResult(status="unavailable", min_height_m=0.0, **base)
    with pytest.raises(ValidationError):
        TerrainReliefResult(status="no_coverage", **base)  # brak provenance
    with pytest.raises(ValidationError):
        TerrainReliefResult(status="available", **base)
    assert TerrainReliefResult(status="unknown", **base).slope is None


# --- Mapowanie sekcji -------------------------------------------------------


def _extremes() -> TerrainExtremes:
    return TerrainExtremes(
        min_height_m=112.3,
        max_height_m=115.7,
        height_difference_m=3.4,
        grid_size_m=4.0,
        sampled_points=676,
        source_metadata=_source(),
    )


def test_four_behaviours_are_distinct() -> None:
    available = build_terrain_result(
        ContextSectionResult(section="nmt", status="available", data=[_extremes()]),
        None,
    )
    no_coverage = build_terrain_result(
        ContextSectionResult(
            section="nmt",
            status="available",
            data=[TerrainNoCoverage(4.0, 676, _source(), ["brak danych"])],
        ),
        None,
    )
    timeout = build_terrain_result(
        ContextSectionResult(
            section="nmt",
            status="unavailable",
            source_metadata=_source(confidence=0.0, manual_review_required=True, response_status=None),
            reason_code="SERVICE_TIMEOUT",
            warnings=["Usługa NMT jest tymczasowo niedostępna"],
        ),
        None,
    )
    legacy = terrain_from_snapshot(None)
    flat = build_terrain_result(
        ContextSectionResult(
            section="nmt",
            status="available",
            data=[
                TerrainExtremes(101.2, 101.2, 0.0, 4.0, 676, _source()),
            ],
        ),
        None,
    )

    assert [item.status for item in (available, no_coverage, timeout, legacy, flat)] == [
        "available",
        "no_coverage",
        "unavailable",
        "unknown",
        "available",
    ]
    assert available.height_difference_m == 3.4
    assert flat.height_difference_m == 0.0
    for missing in (no_coverage, timeout, legacy):
        assert missing.height_difference_m is None
        assert missing.min_height_m is None
    assert no_coverage.reason_code == "NO_COVERAGE_SENTINEL"
    assert no_coverage.grid_size_m == 4.0 and no_coverage.source is not None
    assert timeout.reason_code == "SERVICE_TIMEOUT"
    assert timeout.source is not None and timeout.source.manual_review_required
    assert legacy.reason_code == "LEGACY_SNAPSHOT"
    assert legacy.warnings == [LEGACY_TERRAIN_WARNING]


def test_unexpected_error_and_missing_measurement_fall_back_to_attempt_source() -> None:
    error = build_terrain_result(
        ContextSectionResult(section="nmt", status="error", warnings=["boom"]), None
    )
    empty = build_terrain_result(ContextSectionResult(section="nmt", status="available"), None)

    assert error.status == "unavailable" and error.reason_code == "UNEXPECTED_ERROR"
    assert empty.status == "unavailable" and empty.reason_code == "MISSING_MEASUREMENT"
    for item in (error, empty):
        assert item.source is not None
        assert item.source.source_name == "NMT"
        assert item.source.confidence == 0.0


def test_snapshot_roundtrip_is_identical() -> None:
    terrain = _available()
    assert terrain_from_snapshot(terrain.model_dump(mode="json")) == terrain


# --- Pochodne rastra -------------------------------------------------------


def _relief_outcome(*, offset: float = 0.0, partial: bool = False) -> ReliefOutcome:
    values = [110.0 + offset + 0.06 * col for _row in range(6) for col in range(6)]
    if partial:
        values[1 * 6 + 1] = None  # type: ignore[call-overload]
    grid = ElevationGrid(6, 6, 1000.0, 2006.0, 1.0, tuple(values))
    # Działka leży wewnątrz okna — adapter zawsze pobiera bufor kernela.
    interior = tuple(
        1 <= row <= 4 and 1 <= col <= 4 for row in range(6) for col in range(6)
    )
    derivatives = compute_derivatives(grid, interior)
    profile = sample_profile(grid, (1000.5, 2003.0), (1005.5, 2003.0), lambda *_: True)
    return ReliefOutcome(
        status="available",
        reason_code=None,
        derivatives=derivatives,
        profile=profile,
        raster=RasterMetadata(
            coverage_id="DTM_PL-KRON86-NH_TIFF",
            resolution_m=1.0,
            width_px=6,
            height_px=6,
            bbox=BoundingBox(1000.0, 2000.0, 1006.0, 2006.0),
            buffer_m=2.0,
            size_bytes=1234,
            nodata_value=None,
            nodata_policy="0.0 = NoData",
            masked_pixel_count=0,
            vertical_datum="PL-KRON86-NH",
            gdal_version="GDAL 3.10.3",
        ),
        provenance=Provenance(
            source_id="nmt_wcs",
            fetched_at=NOW,
            content_hash=SHA,
            request_url="https://mapy.geoportal.gov.pl/wcs?request=GetCoverage",
            operation="WCS:GetCoverage",
            complete=True,
        ),
        warnings=("część bez danych",) if partial else (),
    )


def test_relief_mapping_keeps_resolution_classes_profile_and_source() -> None:
    relief = relief_result_from_outcome(_relief_outcome())

    assert relief.status == "available"
    assert relief.resolution_m == 1.0
    assert relief.slope is not None and relief.slope.mean_pct == pytest.approx(6.0)
    assert [item.class_id for item in relief.slope_classes if item.share_pct] == ["moderate"]
    assert relief.profile is not None
    assert relief.profile.samples[0].height_m is not None
    assert relief.profile.line_geojson is not None
    assert relief.profile.line_geojson["geometry"]["type"] == "LineString"
    assert relief.raster is not None and relief.raster.gdal_version == "GDAL 3.10.3"
    assert relief.source is not None
    assert relief.source.source_name == "NMT_WCS"
    assert relief.source.artifact_sha256 == SHA
    assert relief.source.source_version == "DTM_PL-KRON86-NH_TIFF (WCS 2.0.1)"
    assert relief.source.manual_review_required is False
    assert TerrainReliefResult.model_validate(relief.model_dump(mode="json")) == relief


def test_partial_relief_requires_review_and_emits_warning() -> None:
    relief = relief_result_from_outcome(_relief_outcome(partial=True))

    assert relief.source is not None
    assert relief.source.manual_review_required is True
    assert relief.source.confidence == 0.6
    terrain = _available(relief=relief)
    [warning] = terrain_warnings(terrain)
    assert warning.code == "NMT_RELIEF_WARNING" and warning.severity == "warning"


def test_failed_relief_has_provenance_but_no_statistics() -> None:
    outcome = ReliefOutcome(
        status="unavailable",
        reason_code="RASTER_TOO_LARGE",
        provenance=Provenance(source_id="nmt_wcs", complete=False, error_code="limit"),
        warnings=("za duży raster",),
    )
    relief = relief_result_from_outcome(outcome)

    assert relief.slope is None and relief.slope_classes == []
    assert relief.source is not None and relief.source.confidence == 0.0
    assert relief.source.manual_review_required is True
    terrain = _available(relief=relief)
    assert terrain_warnings(terrain)[0].severity == "info"
    assert [item.source_name for item in terrain_sources(terrain)] == ["NMT", "NMT_WCS"]
    assert terrain_warnings(_available()) == []


def test_source_inconsistency_between_services_is_flagged() -> None:
    far = build_terrain_result(
        ContextSectionResult(section="nmt", status="available", data=[_extremes()]),
        _relief_outcome(offset=-7.0),
    )
    near = build_terrain_result(
        ContextSectionResult(
            section="nmt",
            status="available",
            data=[TerrainExtremes(112.3, 112.6, 0.3, 4.0, 676, _source())],
        ),
        _relief_outcome(offset=2.5),
    )

    assert far.relief is not None
    assert any("różnią się od usługi" in item for item in far.relief.warnings)
    assert near.relief is not None
    assert not any("różnią się od usługi" in item for item in near.relief.warnings)
