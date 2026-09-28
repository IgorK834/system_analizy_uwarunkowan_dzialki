"""Przypadek użycia BK-302 i root kompozycji ``analysis`` na portach testowych."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import pytest
from shapely.geometry import box

from app.core.data_sources import parse_catalog
from app.core.settings import settings
from app.modules.analysis import composition
from app.modules.analysis.application.terrain import (
    ElevationRasterError,
    FetchedElevationRaster,
    NativeGridSpec,
    RasterMetadata,
    ReliefLimits,
    ReliefOutcome,
    analyze_parcel_relief,
)
from app.modules.analysis.domain.terrain import ElevationGrid
from app.shared.geometry import BoundingBox
from app.shared.provenance import Provenance
from tests.terrain_fixtures import (
    COVERAGE_ID,
    SERVICE_URL,
    FakeWcsClient,
    fixture_bytes,
    requires_gdal,
)

SPEC = NativeGridSpec(coverage_id="C", origin_x=0.0, origin_y=100.0, resolution=1.0)
PROVENANCE = Provenance(source_id="nmt_wcs", content_hash="b" * 64, complete=True)


@dataclass
class FakeFootprint:
    box_bounds: BoundingBox = BoundingBox(10.0, 10.0, 14.0, 14.0)
    mask_value: bool | None = None
    line: tuple[tuple[float, float], tuple[float, float]] | None = ((10.5, 12.0), (13.5, 12.0))

    @property
    def bounds(self) -> BoundingBox:
        return self.box_bounds

    def pixel_mask(self, grid: ElevationGrid) -> tuple[bool, ...]:
        if self.mask_value is not None:
            return (self.mask_value,) * (grid.width * grid.height)
        return tuple(
            self.contains(*grid.pixel_center(col, row))
            for row in range(grid.height)
            for col in range(grid.width)
        )

    def contains(self, x: float, y: float) -> bool:
        b = self.box_bounds
        return b.min_x <= x <= b.max_x and b.min_y <= y <= b.max_y

    def profile_line(self):
        return self.line


@dataclass
class FakeSource:
    values: list[float | None] | None = None
    describe_error: ElevationRasterError | None = None
    fetch_error: ElevationRasterError | None = None
    fetched: list[BoundingBox] = field(default_factory=list)
    source_id: str = "nmt_wcs"
    service_url: str = SERVICE_URL

    def describe(self) -> NativeGridSpec:
        if self.describe_error is not None:
            raise self.describe_error
        return SPEC

    def fetch(self, window: BoundingBox, spec: NativeGridSpec, buffer_m: float) -> FetchedElevationRaster:
        self.fetched.append(window)
        if self.fetch_error is not None:
            raise self.fetch_error
        width = round(window.max_x - window.min_x)
        height = round(window.max_y - window.min_y)
        values = self.values or [100.0 + 0.1 * (i % width) for i in range(width * height)]
        grid = ElevationGrid(width, height, window.min_x, window.max_y, 1.0, tuple(values))
        metadata = RasterMetadata(
            coverage_id="C",
            resolution_m=1.0,
            width_px=width,
            height_px=height,
            bbox=window,
            buffer_m=buffer_m,
            size_bytes=1,
            nodata_value=None,
            nodata_policy="test",
            masked_pixel_count=0,
            vertical_datum=None,
            gdal_version=None,
        )
        return FetchedElevationRaster(grid=grid, metadata=metadata, provenance=PROVENANCE)


def _run(footprint: FakeFootprint, source: FakeSource, max_pixels: int = 10_000) -> ReliefOutcome:
    return analyze_parcel_relief(footprint, source, ReliefLimits(max_pixels=max_pixels))  # type: ignore[arg-type]


def test_success_computes_derivatives_profile_and_keeps_provenance() -> None:
    source = FakeSource()

    outcome = _run(FakeFootprint(), source)

    assert source.fetched == [BoundingBox(8.0, 8.0, 16.0, 16.0)]
    assert outcome.status == "available" and outcome.reason_code is None
    assert outcome.derivatives is not None
    assert outcome.derivatives.slope is not None
    assert outcome.derivatives.slope.mean_pct == pytest.approx(10.0)
    assert outcome.profile is not None and outcome.profile.length_m == 3.0
    assert outcome.provenance == PROVENANCE
    assert outcome.warnings == ()


def test_success_without_profile_line() -> None:
    outcome = _run(FakeFootprint(line=None), FakeSource())
    assert outcome.status == "available" and outcome.profile is None


def test_pixel_limit_is_checked_before_fetch() -> None:
    source = FakeSource()
    outcome = _run(FakeFootprint(), source, max_pixels=63)
    assert outcome.reason_code == "RASTER_TOO_LARGE"
    assert source.fetched == []
    assert outcome.provenance is not None and outcome.provenance.request_url == SERVICE_URL


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (ElevationRasterError("poza", reason_code="OUTSIDE_COVERAGE", provenance=PROVENANCE), "no_coverage"),
        (ElevationRasterError("timeout", reason_code="SERVICE_TIMEOUT", provenance=PROVENANCE), "unavailable"),
    ],
)
def test_fetch_and_describe_errors_keep_reason_and_provenance(
    error: ElevationRasterError, status: str
) -> None:
    for source in (FakeSource(fetch_error=error), FakeSource(describe_error=error)):
        outcome = _run(FakeFootprint(), source)
        assert (outcome.status, outcome.reason_code) == (status, error.reason_code)
        assert outcome.provenance == PROVENANCE
        assert outcome.derivatives is None


def test_parcel_below_resolution_all_nodata_and_missing_window() -> None:
    below = _run(FakeFootprint(mask_value=False), FakeSource())
    nodata = _run(FakeFootprint(), FakeSource(values=[None] * 64))
    ring = [None] * 64
    for row in range(2, 6):
        for col in range(2, 6):
            ring[row * 8 + col] = 100.0 if (row + col) % 2 else None
    window_missing = _run(FakeFootprint(), FakeSource(values=ring))

    assert (below.status, below.reason_code) == ("unavailable", "PARCEL_BELOW_RESOLUTION")
    assert (nodata.status, nodata.reason_code) == ("no_coverage", "NO_DATA_IN_PARCEL")
    assert (window_missing.status, window_missing.reason_code) == (
        "unavailable",
        "INSUFFICIENT_VALID_WINDOW",
    )
    for outcome in (below, nodata, window_missing):
        assert outcome.derivatives is None
        assert outcome.raster is not None
        assert outcome.provenance == PROVENANCE


def test_relief_limits_validation() -> None:
    with pytest.raises(ValueError):
        ReliefLimits(max_pixels=0)
    with pytest.raises(ValueError):
        ReliefLimits(max_pixels=10, buffer_px=0)


# --- Root kompozycji --------------------------------------------------------


def _catalog(**overrides: object):
    entry: dict[str, object] = {
        "source_id": "nmt_wcs",
        "name": "NMT WCS",
        "owner": "GUGiK",
        "status": "production",
        "production_ready": True,
        "contract_confirmed": True,
        "access_type": "wcs",
        "capabilities_url": SERVICE_URL,
        "layers": [COVERAGE_ID],
        "source_crs": "EPSG:2180",
        "teryt_scope": ["*"],
        "license": "Fees=none",
        "attribution": "GUGiK",
        "expected_update_interval": "not_published",
        "sla": "not_published",
        "last_manual_verification": "2026-09-28",
        "resources": [
            {
                "role": "elevation",
                "access_type": "wcs",
                "url": SERVICE_URL,
                "layer": COVERAGE_ID,
                "source_crs": "EPSG:2180",
            }
        ],
    }
    entry.update(overrides)
    return parse_catalog({"schema_version": "1.0", "sources": [entry]})


@requires_gdal
def test_run_terrain_relief_wires_catalog_contract_and_real_decoder() -> None:
    client = FakeWcsClient(coverage=fixture_bytes("wcs_getcoverage_warszawa_parcel_window.tif"))

    outcome = composition.run_terrain_relief(
        box(637000, 486000, 637100, 486100), catalog=_catalog(), client=client  # type: ignore[arg-type]
    )

    assert outcome.status == "available"
    assert outcome.raster is not None
    assert outcome.raster.coverage_id == COVERAGE_ID
    assert outcome.raster.vertical_datum == "PL-KRON86-NH"


@pytest.mark.parametrize(
    "catalog_overrides",
    [
        {"status": "research", "production_ready": False},
        {"resources": []},
    ],
)
def test_run_terrain_relief_requires_runnable_catalog_contract(catalog_overrides) -> None:
    outcome = composition.run_terrain_relief(
        box(0, 0, 10, 10), catalog=_catalog(**catalog_overrides), client=FakeWcsClient()  # type: ignore[arg-type]
    )
    assert (outcome.status, outcome.reason_code) == ("unavailable", "SOURCE_NOT_RUNNABLE")
    assert outcome.provenance is not None and outcome.provenance.error_code == "catalog"


def test_run_terrain_relief_maps_unexpected_errors() -> None:
    class Exploding(FakeWcsClient):
        def fetch_wcs_description(self, url: str, **_: object):
            raise RuntimeError("boom")

    outcome = composition.run_terrain_relief(
        box(0, 0, 10, 10), catalog=_catalog(), client=Exploding()  # type: ignore[arg-type]
    )
    assert (outcome.status, outcome.reason_code) == ("unavailable", "UNEXPECTED_ERROR")
    assert outcome.provenance is not None and outcome.provenance.error_code == "RuntimeError"


def test_vertical_datum_is_derived_from_coverage_id() -> None:
    assert composition._vertical_datum("DTM_PL-EVRF2007-NH") == "PL-EVRF2007-NH"
    assert composition._vertical_datum("OTHER") is None


@pytest.mark.asyncio
async def test_async_facade_returns_result_and_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = ReliefOutcome(status="unavailable", reason_code="X")
    monkeypatch.setattr(composition, "run_terrain_relief", lambda _geometry: expected)
    assert await composition.analyze_terrain_relief(box(0, 0, 1, 1)) is expected

    def slow(_geometry):
        time.sleep(0.3)
        return expected

    monkeypatch.setattr(composition, "run_terrain_relief", slow)
    monkeypatch.setattr(settings, "terrain_raster_timeout_seconds", 0.01)
    monkeypatch.setattr(composition, "_COMPUTE_MARGIN_SECONDS", 0.01)
    outcome = await composition.analyze_terrain_relief(box(0, 0, 1, 1))
    assert (outcome.status, outcome.reason_code) == ("unavailable", "SERVICE_TIMEOUT")
    assert outcome.provenance is not None and outcome.provenance.error_code == "timeout"
    await asyncio.sleep(0.35)
