"""Domena pochodnych rastra NMT (BK-302) wobec rozwiązań analitycznych.

Tolerancja deklarowana: 1e-9 dla wartości liczonych z dokładnych płaszczyzn
(metoda Horna jest dokładna dla funkcji liniowej) oraz zaokrąglenie wyniku do
1e-4 w statystykach.
"""

from __future__ import annotations

import math

import pytest

from app.modules.analysis.domain.terrain import (
    ALGORITHM_VERSION,
    FLAT_THRESHOLD_PCT,
    SLOPE_CLASSES,
    SLOPE_CLASSES_VERSION,
    ElevationGrid,
    aligned_window,
    aspect_from_gradient,
    bilinear_height,
    compute_derivatives,
    horn_gradient,
    order_profile_endpoints,
    percentile_linear,
    profile_step,
    sample_profile,
    slope_from_gradient,
    window_pixel_count,
)
from app.shared.geometry import BoundingBox

STAT_TOLERANCE = 1e-4


def plane(
    width: int,
    height: int,
    *,
    dz_east: float,
    dz_north: float,
    base: float = 100.0,
    resolution: float = 1.0,
) -> ElevationGrid:
    """Płaszczyzna z = base + a·x + b·y próbkowana w środkach pikseli."""
    origin_x, origin_y = 1000.0, 2000.0
    values = []
    for row in range(height):
        for col in range(width):
            x = origin_x + (col + 0.5) * resolution
            y = origin_y - (row + 0.5) * resolution
            values.append(base + dz_east * (x - origin_x) + dz_north * (y - origin_y))
    return ElevationGrid(width, height, origin_x, origin_y, resolution, tuple(values))


def everywhere(grid: ElevationGrid) -> tuple[bool, ...]:
    return (True,) * (grid.width * grid.height)


@pytest.mark.parametrize(
    ("dz_east", "dz_north", "expected_aspect", "direction"),
    [
        (0.1, 0.0, 270.0, "W"),  # wznosi się na wschód → spada na zachód
        (-0.1, 0.0, 90.0, "E"),
        (0.0, 0.1, 180.0, "S"),
        (0.0, -0.1, 0.0, "N"),
        (0.05, 0.05, 225.0, "SW"),
    ],
)
def test_inclined_plane_matches_analytic_slope_and_aspect(
    dz_east: float, dz_north: float, expected_aspect: float, direction: str
) -> None:
    grid = plane(12, 10, dz_east=dz_east, dz_north=dz_north, resolution=2.0)
    magnitude = math.hypot(dz_east, dz_north)

    gradient = horn_gradient(grid, 5, 5)
    assert gradient is not None
    assert gradient[0] == pytest.approx(dz_east, abs=1e-9)
    assert gradient[1] == pytest.approx(dz_north, abs=1e-9)

    result = compute_derivatives(grid, everywhere(grid))
    assert result.slope is not None
    assert result.slope.mean_pct == pytest.approx(100 * magnitude, abs=STAT_TOLERANCE)
    assert result.slope.max_pct == pytest.approx(100 * magnitude, abs=STAT_TOLERANCE)
    assert result.slope.p90_deg == pytest.approx(
        math.degrees(math.atan(magnitude)), abs=STAT_TOLERANCE
    )
    assert result.aspect is not None
    assert result.aspect.status == "defined"
    assert result.aspect.mean_azimuth_deg == pytest.approx(expected_aspect, abs=1e-3)
    assert result.aspect.resultant_length == pytest.approx(1.0, abs=1e-9)
    assert result.aspect.dominant_direction == direction
    # 12×10 pikseli, pochodne tylko w środku (10×8) — brzeg bez okna 3×3.
    assert result.parcel_pixel_count == 120
    assert result.valid_pixel_count == 80
    assert result.valid_area_share_pct == pytest.approx(66.67)


def test_horizontal_raster_has_zero_slope_and_null_aspect() -> None:
    grid = plane(6, 6, dz_east=0.0, dz_north=0.0, base=-1.25)

    result = compute_derivatives(grid, everywhere(grid))

    assert result.slope is not None
    assert result.slope.mean_deg == 0.0 and result.slope.max_pct == 0.0
    assert result.min_height_m == -1.25 == result.max_height_m
    assert [item.class_id for item in result.slope_classes if item.share_pct] == ["flat"]
    assert result.aspect is not None
    assert result.aspect.status == "flat"
    assert result.aspect.mean_azimuth_deg is None
    assert result.aspect.resultant_length is None
    assert result.aspect.dominant_direction is None
    assert aspect_from_gradient(0.0, 0.0) is None


def test_nodata_is_masked_and_never_becomes_zero() -> None:
    base = plane(7, 7, dz_east=0.1, dz_north=0.0)
    values = list(base.values)
    values[3 * 7 + 3] = None  # środek
    grid = ElevationGrid(7, 7, base.origin_x, base.origin_y, 1.0, tuple(values))

    result = compute_derivatives(grid, everywhere(grid))

    assert result.nodata_pixel_count == 1
    # Każdy piksel sąsiadujący z NoData traci okno 3×3 — nie dostaje spadku 0.
    assert result.valid_pixel_count == 25 - 9
    assert result.slope is not None
    assert result.slope.mean_pct == pytest.approx(10.0, abs=STAT_TOLERANCE)
    assert horn_gradient(grid, 2, 2) is None


def test_all_nodata_produces_no_statistics() -> None:
    grid = ElevationGrid(4, 4, 0.0, 4.0, 1.0, (None,) * 16)

    result = compute_derivatives(grid, everywhere(grid))

    assert result.slope is None
    assert result.aspect is None
    assert result.slope_classes == ()
    assert result.min_height_m is None and result.mean_height_m is None
    assert result.nodata_pixel_count == 16


def test_mask_limits_statistics_to_parcel_pixels() -> None:
    grid = plane(6, 6, dz_east=0.2, dz_north=0.0)
    mask = [False] * 36
    mask[2 * 6 + 2] = True

    result = compute_derivatives(grid, mask)

    assert result.parcel_pixel_count == 1
    assert result.valid_pixel_count == 1
    with pytest.raises(ValueError):
        compute_derivatives(grid, mask[:-1])


def test_slope_classes_are_versioned_and_share_sums_to_100() -> None:
    assert ALGORITHM_VERSION == "horn1981-3x3-v1"
    assert SLOPE_CLASSES_VERSION == "slope-classes-pl-v1"
    assert [(c.min_pct, c.max_pct) for c in SLOPE_CLASSES] == [
        (0.0, 2.0),
        (2.0, 5.0),
        (5.0, 10.0),
        (10.0, 15.0),
        (15.0, 30.0),
        (30.0, None),
    ]
    # Granice: dolna włącznie, górna rozłącznie.
    assert SLOPE_CLASSES[1].contains(2.0) and not SLOPE_CLASSES[0].contains(2.0)
    assert SLOPE_CLASSES[-1].contains(500.0)

    grid = plane(6, 6, dz_east=0.07, dz_north=0.0, resolution=0.5)
    result = compute_derivatives(grid, everywhere(grid))
    shares = {item.class_id: item for item in result.slope_classes}
    assert shares["moderate"].share_pct == 100.0
    assert shares["moderate"].area_sqm == pytest.approx(16 * 0.25)
    assert sum(item.share_pct for item in result.slope_classes) == pytest.approx(100.0)


def test_aspect_dispersed_and_flat_share_rules() -> None:
    # Dolina symetryczna (V): połowa spada na wschód, połowa na zachód.
    width, height = 10, 6
    values = []
    for _row in range(height):
        for col in range(width):
            values.append(100.0 + 0.2 * abs(col - 4.5))
    grid = ElevationGrid(width, height, 0.0, float(height), 1.0, tuple(values))

    result = compute_derivatives(grid, everywhere(grid))

    assert result.aspect is not None
    assert result.aspect.status == "dispersed"
    assert result.aspect.dominant_direction is None
    assert result.aspect.mean_azimuth_deg is not None
    assert set(result.aspect.sector_shares_pct) == {"N", "NE", "E", "SE", "S", "SW", "W", "NW"}
    assert result.aspect.flat_threshold_pct == FLAT_THRESHOLD_PCT


def test_small_non_flat_share_is_reported_as_flat() -> None:
    values = [100.0] * 100
    values[5 * 10 + 5] = 100.3  # pojedynczy garb — ~7% pikseli nachylonych
    grid = ElevationGrid(10, 10, 0.0, 10.0, 1.0, tuple(values))

    result = compute_derivatives(grid, everywhere(grid))

    assert result.aspect is not None
    assert result.aspect.status == "flat"
    assert 0 < result.aspect.non_flat_share_pct < 25


def test_percentile_linear_matches_r7() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 10.0]
    assert percentile_linear(values, 0.5) == 3.0
    assert percentile_linear(values, 0.9) == pytest.approx(7.6)
    assert percentile_linear([5.0], 0.9) == 5.0
    with pytest.raises(ValueError):
        percentile_linear([], 0.5)
    with pytest.raises(ValueError):
        percentile_linear(values, 1.5)


def test_slope_from_gradient_units() -> None:
    degrees, percent = slope_from_gradient(1.0, 0.0)
    assert degrees == pytest.approx(45.0)
    assert percent == pytest.approx(100.0)


def test_bilinear_height_is_exact_on_plane_and_none_outside_or_nodata() -> None:
    grid = plane(5, 5, dz_east=0.3, dz_north=-0.1)
    x, y = 1002.2, 1997.7
    expected = 100.0 + 0.3 * (x - 1000.0) - 0.1 * (y - 2000.0)
    assert bilinear_height(grid, x, y) == pytest.approx(expected, abs=1e-9)
    # Środek skrajnego piksela jest poprawny.
    assert bilinear_height(grid, 1004.5, 1995.5) is not None
    assert bilinear_height(grid, 1000.1, 1999.9) is None
    values = list(grid.values)
    values[1 * 5 + 2] = None
    holey = ElevationGrid(5, 5, 1000.0, 2000.0, 1.0, tuple(values))
    assert bilinear_height(holey, 1002.2, 1998.2) is None
    single = ElevationGrid(1, 1, 0.0, 1.0, 1.0, (7.0,))
    assert bilinear_height(single, 0.5, 0.5) == 7.0


def test_profile_is_deterministic_with_recorded_ends_step_and_samples() -> None:
    grid = plane(30, 12, dz_east=0.1, dz_north=0.0)
    start, end = (1002.0, 1994.0), (1027.5, 1994.0)

    first = sample_profile(grid, start, end, lambda x, _y: x < 1020.0)
    second = sample_profile(grid, start, end, lambda x, _y: x < 1020.0)

    assert first == second
    assert first.start == start and first.end == end
    assert first.length_m == 25.5
    assert first.step_m == 1.0
    assert len(first.samples) == 27  # 0..25 co 1 m + koniec 25,5 m
    assert first.samples[-1].distance_m == 25.5
    for sample in first.samples:
        assert sample.height_m == pytest.approx(100.0 + 0.1 * (sample.x - 1000.0), abs=1e-3)
    assert first.samples[0].inside_parcel and not first.samples[-1].inside_parcel


def test_profile_step_grows_for_long_lines_and_handles_zero_length() -> None:
    assert profile_step(100.0, 1.0) == 1.0
    assert profile_step(4000.0, 1.0) == pytest.approx(10.0)
    assert profile_step(0.0, 2.0) == 2.0
    grid = plane(4, 4, dz_east=0.0, dz_north=0.0)
    point = sample_profile(grid, (1002.0, 1998.0), (1002.0, 1998.0), lambda *_: True)
    assert point.length_m == 0.0 and len(point.samples) == 1


def test_order_profile_endpoints_west_first() -> None:
    assert order_profile_endpoints((5.0, 1.0), (1.0, 9.0)) == ((1.0, 9.0), (5.0, 1.0))
    assert order_profile_endpoints((1.0, 2.0), (1.0, 1.0)) == ((1.0, 1.0), (1.0, 2.0))


def test_aligned_window_snaps_outward_to_native_grid_with_buffer() -> None:
    bounds = BoundingBox(637000.0, 486000.0, 637100.0, 486100.0)

    window = aligned_window(
        bounds,
        grid_origin_x=160828.343266,
        grid_origin_y=796521.669410,
        resolution=1.0,
        buffer_px=2,
    )

    assert window == BoundingBox(636997.343266, 485997.66941, 637102.343266, 486102.66941)
    assert window_pixel_count(window, 1.0) == 105 * 105
    with pytest.raises(ValueError):
        aligned_window(bounds, grid_origin_x=0, grid_origin_y=0, resolution=0, buffer_px=1)


def test_elevation_grid_validation() -> None:
    with pytest.raises(ValueError):
        ElevationGrid(0, 1, 0.0, 0.0, 1.0, ())
    with pytest.raises(ValueError):
        ElevationGrid(1, 1, 0.0, 0.0, -1.0, (1.0,))
    with pytest.raises(ValueError):
        ElevationGrid(2, 1, 0.0, 0.0, 1.0, (1.0,))
    with pytest.raises(ValueError):
        ElevationGrid(1, 1, 0.0, 0.0, 1.0, (math.nan,))
    grid = ElevationGrid(2, 3, 10.0, 30.0, 2.0, (1.0,) * 6)
    assert grid.bounds == BoundingBox(10.0, 24.0, 14.0, 30.0)
    assert grid.pixel_area == 4.0
