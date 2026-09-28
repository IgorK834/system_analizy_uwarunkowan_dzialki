"""Adapter rastra NMT (WCS + GDAL + Shapely) na zamrożonych kontraktach (BK-302).

Scenariusze kontrolne: płaszczyzna o zadanym gradiencie i raster poziomy dają
wyniki analityczne; NoData, zły CRS, za duży raster i timeout nie wytwarzają
statystyk ze sztucznych zer; realny raster Warszawy odtwarza wynik z przebiegu
na żywej usłudze (SHA-256 artefaktu identyczny).
"""

from __future__ import annotations

import hashlib
import math
import subprocess
import tempfile
from pathlib import Path

import httpx
import pytest
from shapely.geometry import Polygon, box
from shapely.affinity import rotate

from app.modules.analysis.application.terrain import (
    ReliefLimits,
    analyze_parcel_relief,
)
from app.modules.analysis.domain.terrain import ElevationGrid
from app.modules.analysis.infrastructure.terrain_raster import (
    ShapelyParcelFootprint,
    WcsCoverageContract,
    WcsElevationRasterSource,
    clear_native_grid_cache,
    parse_describe_coverage,
)
from app.modules.imports.composition import (
    GdalRasterProcessor,
    OgcContractError,
    OgcExceptionReportError,
    OgcLimitError,
    OgcTransportError,
    RasterProcessingError,
)
from app.shared.provenance import Provenance
from tests.terrain_fixtures import (
    COVERAGE_ID,
    SERVICE_URL,
    FakeWcsClient,
    fixture_bytes,
    make_geotiff,
    plane_values,
    requires_gdal,
)

WARSAW = Polygon.from_bounds(637000, 486000, 637100, 486100)
ORIGIN_X, ORIGIN_Y = 160828.343266, 796521.669410
FAIL = Provenance(source_id="nmt_wcs", request_url=SERVICE_URL, complete=False, error_code="x")


def _source(client: FakeWcsClient, *, max_bytes: int = 8 * 1024 * 1024) -> WcsElevationRasterSource:
    return WcsElevationRasterSource(
        WcsCoverageContract(
            service_url=SERVICE_URL, coverage_id=COVERAGE_ID, vertical_datum="PL-KRON86-NH"
        ),
        client,  # type: ignore[arg-type]
        GdalRasterProcessor(command_timeout_s=60),
        max_bytes=max_bytes,
    )


def _run(parcel: Polygon, client: FakeWcsClient, *, max_pixels: int = 1_000_000, **kwargs):
    return analyze_parcel_relief(
        ShapelyParcelFootprint(parcel), _source(client, **kwargs), ReliefLimits(max_pixels=max_pixels)
    )


def _window_geotiff(values_for, *, epsg: int = 2180, nodata: float | None = None):
    """Buduje GeoTIFF dokładnie w oknie żądanym przez adapter (jak serwer WCS)."""

    def build(subsets):
        (_, min_x, max_x), (_, min_y, max_y) = subsets
        width, height = round(max_x - min_x), round(max_y - min_y)
        return make_geotiff(
            values_for(width, height, min_x, max_y),
            origin_x=min_x,
            origin_y=max_y,
            epsg=epsg,
            nodata=nodata,
        )

    return build


# --- DescribeCoverage ------------------------------------------------------


def test_describe_coverage_contract_gives_native_grid_edges() -> None:
    spec = parse_describe_coverage(fixture_bytes("wcs_describecoverage.xml"), COVERAGE_ID)

    assert spec.coverage_id == COVERAGE_ID
    assert (spec.origin_x, spec.origin_y) == (ORIGIN_X, ORIGIN_Y)
    assert spec.resolution == 1.0
    assert spec.crs == "EPSG:2180"


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("<wcs:CoverageId>DTM_PL-KRON86-NH_TIFF</wcs:CoverageId>", "<wcs:CoverageId>X</wcs:CoverageId>"),
        ('EPSG/0/2180" axisLabels', 'EPSG/0/4326" axisLabels'),
        ("0 1.000000</gml:offsetVector>", "0.2 1.000000</gml:offsetVector>"),
        ("-1.000000 0</gml:offsetVector>", "-2.000000 0</gml:offsetVector>"),
        (
            "<gml:pos>796521.169410 160828.843266</gml:pos>",
            "<gml:pos>160828.843266 796521.169410</gml:pos>",
        ),
        ("<gml:pos>796521.169410 160828.843266</gml:pos>", "<gml:pos>abc</gml:pos>"),
        ("<gml:RectifiedGrid", "<gml:OtherGrid"),
    ],
)
def test_describe_coverage_rejects_ambiguous_or_rotated_grid(old: str, new: str) -> None:
    content = fixture_bytes("wcs_describecoverage.xml").decode()
    assert old in content
    with pytest.raises(Exception) as raised:
        parse_describe_coverage(
            content.replace(old, new).encode(), COVERAGE_ID, provenance=FAIL
        )
    assert getattr(raised.value, "reason_code", None) == "CONTRACT_MISMATCH"


def test_describe_coverage_rejects_invalid_xml() -> None:
    with pytest.raises(Exception) as raised:
        parse_describe_coverage(b"<not-closed", COVERAGE_ID)
    assert getattr(raised.value, "reason_code", None) == "CONTRACT_MISMATCH"


def test_describe_is_cached_per_service_and_coverage() -> None:
    client = FakeWcsClient()
    source = _source(client)
    assert source.describe() == source.describe()
    assert client.describe_calls == 1
    clear_native_grid_cache()
    source.describe()
    assert client.describe_calls == 2


# --- Realny raster i rozwiązania analityczne --------------------------------


@requires_gdal
def test_real_warsaw_window_reproduces_live_run() -> None:
    tiff = fixture_bytes("wcs_getcoverage_warszawa_parcel_window.tif")
    client = FakeWcsClient(coverage=tiff)

    outcome = _run(WARSAW, client)

    assert client.coverage_calls == [
        (("x", 636997.343266, 637102.343266), ("y", 485997.66941, 486102.66941))
    ]
    assert outcome.status == "available"
    derivatives = outcome.derivatives
    assert derivatives is not None
    assert (derivatives.parcel_pixel_count, derivatives.valid_pixel_count) == (10000, 10000)
    assert (derivatives.min_height_m, derivatives.max_height_m) == (105.19, 116.22)
    assert derivatives.slope is not None
    assert derivatives.slope.mean_deg == pytest.approx(4.2218, abs=1e-4)
    assert derivatives.slope.p90_pct == pytest.approx(16.0119, abs=1e-4)
    assert [item.share_pct for item in derivatives.slope_classes] == [
        36.48, 38.58, 11.28, 3.16, 4.35, 6.15,
    ]
    assert derivatives.aspect is not None and derivatives.aspect.status == "dispersed"
    assert outcome.raster is not None
    assert (outcome.raster.width_px, outcome.raster.height_px) == (105, 105)
    assert outcome.raster.resolution_m == 1.0 and outcome.raster.buffer_m == 2.0
    assert outcome.raster.vertical_datum == "PL-KRON86-NH"
    assert outcome.raster.gdal_version and outcome.raster.gdal_version.startswith("GDAL ")
    assert outcome.provenance is not None
    assert outcome.provenance.content_hash == hashlib.sha256(tiff).hexdigest()
    assert outcome.provenance.content_hash.startswith("603a9ddd")
    assert outcome.profile is not None
    assert outcome.profile.start == (637000.0, 486050.0)
    assert outcome.profile.end == (637100.0, 486050.0)
    assert len(outcome.profile.samples) == 101


@requires_gdal
@pytest.mark.parametrize(("dz_east", "dz_north"), [(0.12, 0.0), (0.03, -0.04), (0.0, 0.0)])
def test_synthetic_plane_through_gdal_matches_analytic_solution(
    dz_east: float, dz_north: float
) -> None:
    parcel = box(637010, 486010, 637030, 486025)

    def values(width, height, _min_x, _max_y):
        return plane_values(
            width, height, origin_x=0, origin_y=0, dz_east=dz_east, dz_north=dz_north
        )

    outcome = _run(parcel, FakeWcsClient(coverage=_window_geotiff(values)))

    assert outcome.status == "available"
    slope = outcome.derivatives.slope  # type: ignore[union-attr]
    magnitude = math.hypot(dz_east, dz_north)
    # Float32 GeoTIFF: tolerancja 0,01 pp spadku.
    assert slope.mean_pct == pytest.approx(100 * magnitude, abs=0.01)
    assert slope.max_pct == pytest.approx(100 * magnitude, abs=0.01)
    aspect = outcome.derivatives.aspect  # type: ignore[union-attr]
    if magnitude == 0:
        assert aspect.status == "flat" and aspect.mean_azimuth_deg is None
        assert slope.max_deg == pytest.approx(0.0, abs=1e-3)
    else:
        expected = math.degrees(math.atan2(-dz_east, -dz_north)) % 360
        assert aspect.status == "defined"
        assert aspect.mean_azimuth_deg == pytest.approx(expected, abs=0.05)


@requires_gdal
def test_negative_heights_are_measured_not_masked() -> None:
    parcel = box(637010, 486010, 637020, 486020)

    def values(width, height, _min_x, _max_y):
        return plane_values(
            width, height, origin_x=0, origin_y=0, dz_east=0.01, dz_north=0.0, base=-2.5
        )

    outcome = _run(parcel, FakeWcsClient(coverage=_window_geotiff(values)))

    assert outcome.status == "available"
    assert outcome.derivatives.min_height_m < 0  # type: ignore[union-attr]
    assert outcome.derivatives.nodata_pixel_count == 0  # type: ignore[union-attr]


# --- NoData, CRS, limity, błędy usługi -------------------------------------


@requires_gdal
def test_undeclared_zero_outside_data_is_no_coverage_not_flat_terrain() -> None:
    zeros = lambda width, height, *_: [[0.0] * width for _ in range(height)]  # noqa: E731

    outcome = _run(box(170000, 450000, 170020, 450020), FakeWcsClient(coverage=_window_geotiff(zeros)))

    assert outcome.status == "no_coverage"
    assert outcome.reason_code == "NO_DATA_IN_PARCEL"
    assert outcome.derivatives is None
    assert outcome.raster is not None and outcome.raster.masked_pixel_count > 0
    assert outcome.provenance is not None
    assert any("nie oznacza płaskiego terenu" in item for item in outcome.warnings)


@requires_gdal
def test_declared_nodata_and_partial_coverage_are_masked() -> None:
    def values(width, height, *_):
        grid = plane_values(width, height, origin_x=0, origin_y=0, dz_east=0.05, dz_north=0.0)
        for row in range(height):
            for col in range(width // 2):
                grid[row][col] = -9999.0
        return grid

    outcome = _run(
        box(637010, 486010, 637030, 486020),
        FakeWcsClient(coverage=_window_geotiff(values, nodata=-9999.0)),
    )

    assert outcome.status == "available"
    derivatives = outcome.derivatives
    assert derivatives is not None and derivatives.nodata_pixel_count > 0
    assert derivatives.min_height_m is not None and derivatives.min_height_m > 0
    assert derivatives.slope is not None
    assert derivatives.slope.mean_pct == pytest.approx(5.0, abs=0.01)
    assert outcome.raster is not None and outcome.raster.nodata_value == -9999.0
    assert any("nie jest wliczana" in item for item in outcome.warnings)


@requires_gdal
def test_wrong_crs_is_rejected_without_statistics() -> None:
    flat = lambda width, height, *_: [[100.0] * width for _ in range(height)]  # noqa: E731

    outcome = _run(WARSAW, FakeWcsClient(coverage=_window_geotiff(flat, epsg=4326)))

    assert outcome.status == "unavailable"
    assert outcome.reason_code == "RASTER_CRS_MISMATCH"
    assert outcome.derivatives is None
    assert outcome.provenance is not None and outcome.provenance.error_code == "crs"


@requires_gdal
def test_raster_not_matching_requested_window_is_rejected() -> None:
    client = FakeWcsClient(coverage=fixture_bytes("wcs_getcoverage_warszawa_aligned.tif"))

    outcome = _run(WARSAW, client)

    assert outcome.status == "unavailable"
    assert outcome.reason_code == "RASTER_GRID_INVALID"


def test_too_many_pixels_stop_before_download() -> None:
    client = FakeWcsClient(coverage=None)

    outcome = _run(WARSAW, client, max_pixels=10_000)

    assert outcome.status == "unavailable"
    assert outcome.reason_code == "RASTER_TOO_LARGE"
    assert client.coverage_calls == []
    assert outcome.provenance is not None and outcome.provenance.error_code == "limit"
    assert outcome.derivatives is None


@requires_gdal
def test_decoder_byte_limit_and_non_tiff_payload() -> None:
    tiff = fixture_bytes("wcs_getcoverage_warszawa_parcel_window.tif")
    too_big = _run(WARSAW, FakeWcsClient(coverage=tiff), max_bytes=1_000)
    garbage = _run(WARSAW, FakeWcsClient(coverage=b"II*\x00garbage"))

    assert too_big.reason_code == "RASTER_TOO_LARGE"
    assert garbage.reason_code == "RASTER_DECODE_ERROR"
    for outcome in (too_big, garbage):
        assert outcome.status == "unavailable"
        assert outcome.provenance is not None and outcome.provenance.complete is False


@pytest.mark.parametrize(
    ("error", "status", "reason"),
    [
        (
            OgcExceptionReportError("extent", source=FAIL, exception_code="ExtentError", http_status=400),
            "no_coverage",
            "OUTSIDE_COVERAGE",
        ),
        (
            OgcExceptionReportError("x", source=FAIL, exception_code="InvalidSubsetting", http_status=400),
            "unavailable",
            "SERVICE_ERROR",
        ),
        (OgcLimitError("limit", source=FAIL), "unavailable", "RASTER_TOO_LARGE"),
        (OgcTransportError("Usługa OGC przekroczyła całkowity timeout.", source=FAIL), "unavailable", "SERVICE_TIMEOUT"),
        (OgcTransportError("HTTP 503", source=FAIL), "unavailable", "SERVICE_ERROR"),
        (OgcContractError("mime", source=FAIL), "unavailable", "CONTRACT_MISMATCH"),
    ],
)
def test_service_failures_map_to_explicit_status(error, status: str, reason: str) -> None:
    outcome = _run(WARSAW, FakeWcsClient(coverage=error))

    assert (outcome.status, outcome.reason_code) == (status, reason)
    assert outcome.derivatives is None and outcome.profile is None
    assert outcome.provenance == FAIL
    assert outcome.warnings


def test_timeout_detected_from_httpx_cause_and_describe_failure() -> None:
    error = OgcTransportError("Nie udało się odczytać usługi OGC", source=FAIL)
    error.__cause__ = httpx.ReadTimeout("slow")
    assert _run(WARSAW, FakeWcsClient(coverage=error)).reason_code == "SERVICE_TIMEOUT"

    clear_native_grid_cache()
    describe_failure = FakeWcsClient(describe=OgcTransportError("HTTP 502", source=FAIL))
    outcome = _run(WARSAW, describe_failure)
    assert (outcome.status, outcome.reason_code) == ("unavailable", "SERVICE_ERROR")


# --- Dekoder GDAL ----------------------------------------------------------


@requires_gdal
def test_gdal_decoder_reads_float_band_and_version() -> None:
    decoder = GdalRasterProcessor(command_timeout_s=60)
    tiff = fixture_bytes("wcs_getcoverage_warszawa_aligned.tif")

    band = decoder.read_float_band(tiff, max_bytes=len(tiff))

    assert (band.width, band.height, band.epsg, band.band_type) == (100, 100, 2180, "Float32")
    assert band.geotransform == (637000.343266, 1.0, 0.0, 486100.66941, 0.0, -1.0)
    assert band.nodata is None
    assert all(100.0 < value < 120.0 for value in band.values)
    assert decoder.gdal_version().startswith("GDAL ")


@requires_gdal
def test_gdal_decoder_rejects_non_strict_geotiff() -> None:
    decoder = GdalRasterProcessor(command_timeout_s=60)
    grid = [[1.0, 2.0], [3.0, 4.0]]
    with pytest.raises(RasterProcessingError):
        decoder.read_float_band(b"\x89PNG\r\n", max_bytes=100)
    with pytest.raises(RasterProcessingError):
        decoder.read_float_band(
            make_geotiff(grid, origin_x=0, origin_y=2, bands=2), max_bytes=10**6
        )
    with pytest.raises(RasterProcessingError):
        decoder.read_float_band(
            make_geotiff(grid, origin_x=0, origin_y=2, output_type="Int16"), max_bytes=10**6
        )
    with pytest.raises(RasterProcessingError):
        decoder.read_float_band(_rotated_geotiff(), max_bytes=10**6)


def _rotated_geotiff() -> bytes:
    source = make_geotiff([[1.0, 2.0], [3.0, 4.0]], origin_x=0, origin_y=2)
    with tempfile.TemporaryDirectory(prefix="rotated-") as workdir:
        work = Path(workdir)
        (work / "src.tif").write_bytes(source)
        (work / "rot.vrt").write_text(
            '<VRTDataset rasterXSize="2" rasterYSize="2">'
            "<SRS>EPSG:2180</SRS>"
            "<GeoTransform>0, 1, 0.5, 2, 0.5, -1</GeoTransform>"
            '<VRTRasterBand dataType="Float32" band="1"><SimpleSource>'
            '<SourceFilename relativeToVRT="1">src.tif</SourceFilename>'
            "<SourceBand>1</SourceBand></SimpleSource></VRTRasterBand></VRTDataset>",
            encoding="utf-8",
        )
        subprocess.run(  # noqa: S603
            ["gdal_translate", "-q", "-of", "GTiff", str(work / "rot.vrt"), str(work / "rot.tif")],
            check=True,
            capture_output=True,
            timeout=60,
        )
        return (work / "rot.tif").read_bytes()


# --- Obrys działki ----------------------------------------------------------


def test_pixel_mask_uses_pixel_centres_inside_parcel() -> None:
    footprint = ShapelyParcelFootprint(
        Polygon([(0, 0), (4, 0), (4, 2), (2, 2), (2, 4), (0, 4)])
    )
    grid = ElevationGrid(4, 4, 0.0, 4.0, 1.0, (1.0,) * 16)

    mask = footprint.pixel_mask(grid)

    assert sum(mask) == 12
    assert mask[0] is True and mask[3] is False  # NW w środku, NE poza „L”
    assert footprint.contains(1.0, 1.0) and not footprint.contains(3.5, 3.5)
    assert footprint.bounds.as_tuple() == (0.0, 0.0, 4.0, 4.0)


def test_profile_line_follows_long_axis_and_is_deterministic() -> None:
    rectangle = rotate(box(0, 0, 80, 20), 30, origin=(0, 0))
    first = ShapelyParcelFootprint(rectangle).profile_line()
    second = ShapelyParcelFootprint(rotate(box(0, 0, 80, 20), 30, origin=(0, 0))).profile_line()

    assert first == second
    assert first is not None
    (x0, y0), (x1, y1) = first
    assert math.hypot(x1 - x0, y1 - y0) == pytest.approx(80.0, abs=1e-2)
    assert math.degrees(math.atan2(y1 - y0, x1 - x0)) == pytest.approx(30.0, abs=1e-3)
    assert x0 <= x1


def test_profile_line_square_prefers_west_east_and_concave_is_clipped() -> None:
    square = ShapelyParcelFootprint(box(0, 0, 10, 10)).profile_line()
    assert square == ((0.0, 5.0), (10.0, 5.0))

    u_shape = Polygon([(0, 0), (30, 0), (30, 10), (20, 10), (20, 4), (10, 4), (10, 10), (0, 10)])
    line = ShapelyParcelFootprint(u_shape).profile_line()
    assert line is not None
    assert line[0][0] == pytest.approx(0.0) and line[1][0] == pytest.approx(30.0)


def test_profile_line_is_none_for_degenerate_geometry() -> None:
    assert ShapelyParcelFootprint(Polygon()).profile_line() is None
