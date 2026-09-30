"""Sekcja „Rzeźba terenu (NMT)” raportu PDF i ścieżka błędu orkiestratora."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.settings import settings
from app.schemas.analyze import (
    AnalyzeResponse,
    TerrainAspectResult,
    TerrainProfileResult,
    TerrainProfileSample,
    TerrainReliefResult,
    TerrainResult,
    TerrainSlopeClass,
    TerrainSlopeStatistics,
)
from app.schemas.source import SourceMetadata
from app.services import analysis_orchestrator
from app.services.report import (
    _build_limitations,
    _build_report_context,
    _format_trimmed,
    _profile_context,
    _render_report_html,
    _terrain_context,
)

NOW = datetime(2026, 9, 28, 10, 0, tzinfo=timezone.utc)
SOURCE = SourceMetadata(
    source_id="nmt_wcs",
    source_name="NMT_WCS",
    fetched_at=NOW,
    confidence=0.9,
    manual_review_required=False,
)
BASE = {"algorithm_version": "horn1981-3x3-v1", "slope_classes_version": "slope-classes-pl-v1"}


def _profile(heights: list[float | None]) -> TerrainProfileResult:
    return TerrainProfileResult(
        method="parcel_long_axis_through_rectangle_center",
        start=(0.0, 0.0),
        end=(float(len(heights) - 1), 0.0),
        length_m=float(len(heights) - 1),
        step_m=1.0,
        samples=[
            TerrainProfileSample(distance_m=float(i), x=float(i), y=0.0, height_m=h, inside_parcel=True)
            for i, h in enumerate(heights)
        ],
    )


def _relief(aspect: TerrainAspectResult, profile: TerrainProfileResult | None = None) -> TerrainReliefResult:
    return TerrainReliefResult(
        status="available",
        resolution_m=1.0,
        parcel_pixel_count=10,
        valid_pixel_count=10,
        nodata_pixel_count=0,
        valid_area_share_pct=100.0,
        min_height_m=100.0,
        max_height_m=101.0,
        mean_height_m=100.5,
        slope=TerrainSlopeStatistics(
            mean_deg=1, median_deg=1, p90_deg=2, max_deg=3, mean_pct=2, median_pct=2, p90_pct=3, max_pct=5
        ),
        slope_classes=[
            TerrainSlopeClass(class_id="flat", label="płaski (< 2%)", min_pct=0, max_pct=2, pixel_count=10, area_sqm=10, share_pct=100)
        ],
        aspect=aspect,
        profile=profile,
        source=SOURCE,
        raster={
            "coverage_id": "DTM_PL-KRON86-NH_TIFF",
            "resolution_m": 1.0,
            "width_px": 5,
            "height_px": 5,
            "bbox": (0.0, 0.0, 5.0, 5.0),
            "buffer_m": 2.0,
            "size_bytes": 10,
            "nodata_policy": "0.0 = NoData",
        },
        **BASE,
    )


def test_format_trimmed_is_polish_and_drops_zeros() -> None:
    assert _format_trimmed(112.3) == "112,3"
    assert _format_trimmed(3.4) == "3,4"
    assert _format_trimmed(0.0) == "0"
    assert _format_trimmed(-0.0001) == "0"
    assert _format_trimmed(1234.5) == "1 234,5"
    assert _format_trimmed(None) is None


def test_profile_context_breaks_line_on_nodata_and_handles_empty() -> None:
    gap = _profile_context(_relief(_flat_aspect(), _profile([100.0, 101.0, None, 100.5, 100.2])))
    empty = _profile_context(_relief(_flat_aspect(), _profile([None, None])))

    assert gap is not None and len(gap["segments"]) == 2
    assert gap["missing_count"] == 1
    assert gap["max_label"] == "101 m" and gap["min_label"] == "100 m"
    assert empty is not None and empty["segments"] == [] and empty["min_label"] is None
    assert _profile_context(_relief(_flat_aspect())) is None


def _flat_aspect() -> TerrainAspectResult:
    return TerrainAspectResult(status="flat", non_flat_share_pct=10.0, flat_threshold_pct=2.0)


@pytest.mark.parametrize(
    ("aspect", "expected"),
    [
        (_flat_aspect(), "nie wyznaczono — teren płaski"),
        (
            TerrainAspectResult(
                status="dispersed", mean_azimuth_deg=12.0, resultant_length=0.1,
                non_flat_share_pct=80.0, flat_threshold_pct=2.0,
            ),
            "rozproszona — brak dominującego kierunku",
        ),
        (
            TerrainAspectResult(
                status="defined", mean_azimuth_deg=180.0, resultant_length=0.9,
                dominant_direction="S", non_flat_share_pct=90.0, flat_threshold_pct=2.0,
            ),
            "południowa",
        ),
    ],
)
def test_aspect_wording_in_html(aspect: TerrainAspectResult, expected: str) -> None:
    terrain = TerrainResult(
        status="available",
        min_height_m=100.0,
        max_height_m=101.0,
        height_difference_m=1.0,
        source=SOURCE,
        relief=_relief(aspect, _profile([100.0, 100.5, 101.0])),
    )
    response = AnalyzeResponse(
        status="partial", analyzed_at=NOW, mpzp_zones=[], infrastructure=[], risks=[],
        warnings=[], sources=[], terrain=terrain,
    )

    html = _render_report_html(_build_report_context(response))

    assert expected in html
    assert "<polyline" in html
    assert any("metodą Horna" in item for item in _build_limitations(response))


def test_missing_relief_and_failed_relief_are_explicit() -> None:
    context = _terrain_context(None)
    assert context["status"] == "unknown" and context["relief"] is None

    failed = TerrainResult(
        status="no_coverage",
        source=SOURCE,
        relief=TerrainReliefResult(status="unavailable", reason_code="RASTER_TOO_LARGE", source=SOURCE, **BASE),
    )
    response = AnalyzeResponse(
        status="partial", analyzed_at=NOW, mpzp_zones=[], infrastructure=[], risks=[],
        warnings=[], sources=[], terrain=failed,
    )
    limitations = _build_limitations(response)
    html = _render_report_html(_build_report_context(response))

    assert any("RASTER_TOO_LARGE" in item for item in limitations)
    assert any("Brak pokrycia danymi NMT" in item for item in limitations)
    assert "Deniwelacja (Hmax − Hmin)" not in html
    legacy = AnalyzeResponse(
        status="partial", analyzed_at=NOW, mpzp_zones=[], infrastructure=[], risks=[], warnings=[], sources=[],
    )
    assert "Nie liczono pochodnych rastra NMT" in _render_report_html(_build_report_context(legacy))


@pytest.mark.asyncio
async def test_orchestrator_relief_is_optional_and_never_breaks_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    from shapely.geometry import box

    assert await analysis_orchestrator._analyze_terrain_relief_safely(box(0, 0, 1, 1)) is None

    async def boom(_geometry):
        raise RuntimeError("wcs")

    monkeypatch.setattr(settings, "terrain_relief_enabled", True)
    monkeypatch.setattr(analysis_orchestrator, "analyze_terrain_relief", boom)
    outcome = await analysis_orchestrator._analyze_terrain_relief_safely(box(0, 0, 1, 1))

    assert outcome is not None
    assert (outcome.status, outcome.reason_code) == ("unavailable", "UNEXPECTED_ERROR")
    assert outcome.provenance is not None and outcome.provenance.error_code == "RuntimeError"
