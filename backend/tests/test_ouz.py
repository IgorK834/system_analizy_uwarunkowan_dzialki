import pytest
from shapely.geometry import MultiPolygon, Polygon

from app.services.ouz import calculate_ouz_status


def test_parcel_fully_inside_ouz() -> None:
    parcel = Polygon.from_bounds(0, 0, 10, 10)
    ouz = Polygon.from_bounds(-5, -5, 15, 15)

    result = calculate_ouz_status(parcel, [ouz])

    assert result.status == "available"
    assert result.in_ouz is True
    assert result.intersection_area_sqm == pytest.approx(100.0)
    assert result.area_ratio == pytest.approx(100.0)
    assert result.touches_ouz_boundary is False
    assert "informacyjny" in result.legal_disclaimer


def test_parcel_partially_inside_ouz_has_percent_ratio() -> None:
    parcel = Polygon.from_bounds(0, 0, 20, 10)
    ouz = Polygon.from_bounds(0, 0, 5, 10)

    result = calculate_ouz_status(parcel, [ouz])

    assert result.in_ouz is True
    assert result.intersection_area_sqm == pytest.approx(50.0)
    assert result.area_ratio == pytest.approx(25.0)


def test_boundary_touch_does_not_mean_in_ouz() -> None:
    parcel = Polygon.from_bounds(0, 0, 10, 10)
    ouz = Polygon.from_bounds(10, 0, 20, 10)

    result = calculate_ouz_status(parcel, [ouz])

    assert result.in_ouz is False
    assert result.intersection_area_sqm == 0.0
    assert result.touches_ouz_boundary is True
    assert result.manual_review_required is True
    assert result.warnings[0].code == "OUZ_BOUNDARY_TOUCH"


def test_parcel_outside_ouz_is_available_and_false() -> None:
    parcel = Polygon.from_bounds(0, 0, 10, 10)
    ouz = Polygon.from_bounds(20, 20, 30, 30)

    result = calculate_ouz_status(parcel, [ouz])

    assert result.status == "available"
    assert result.in_ouz is False
    assert result.touches_ouz_boundary is False
    assert result.area_ratio == 0.0


def test_multipolygon_ouz_sums_disjoint_intersections() -> None:
    parcel = Polygon.from_bounds(0, 0, 20, 10)
    ouz = MultiPolygon(
        [
            Polygon.from_bounds(0, 0, 5, 10),
            Polygon.from_bounds(15, 0, 20, 10),
        ]
    )

    result = calculate_ouz_status(parcel, [ouz])

    assert result.intersection_area_sqm == pytest.approx(100.0)
    assert result.area_ratio == pytest.approx(50.0)
    assert result.in_ouz is True


@pytest.mark.parametrize("geometries", [None, []])
def test_missing_ouz_data_is_unknown_not_false_confirmation(geometries) -> None:
    result = calculate_ouz_status(Polygon.from_bounds(0, 0, 10, 10), geometries)

    assert result.status == "unknown"
    assert result.in_ouz is False
    assert result.manual_review_required is True
    assert result.warnings[0].code == "OUZ_DATA_UNAVAILABLE"


def test_small_absolute_intersection_can_be_significant_for_small_parcel() -> None:
    parcel = Polygon.from_bounds(0, 0, 10, 10)
    ouz = Polygon.from_bounds(0, 0, 0.05, 10)

    result = calculate_ouz_status(parcel, [ouz])

    assert result.intersection_area_sqm == pytest.approx(0.5)
    assert result.area_ratio == pytest.approx(0.5)
    assert result.in_ouz is True


def test_subthreshold_sliver_is_only_boundary_touch() -> None:
    parcel = Polygon.from_bounds(0, 0, 100, 100)
    ouz = Polygon.from_bounds(0, 0, 0.005, 100)

    result = calculate_ouz_status(parcel, [ouz])

    assert result.intersection_area_sqm == pytest.approx(0.5)
    assert result.area_ratio == pytest.approx(0.005)
    assert result.in_ouz is False
    assert result.touches_ouz_boundary is True


def test_overlapping_ouz_geometries_are_not_double_counted() -> None:
    parcel = Polygon.from_bounds(0, 0, 10, 10)
    overlapping = [
        Polygon.from_bounds(0, 0, 7, 10),
        Polygon.from_bounds(3, 0, 10, 10),
    ]

    result = calculate_ouz_status(parcel, overlapping)

    assert result.intersection_area_sqm == pytest.approx(100.0)
    assert result.area_ratio == pytest.approx(100.0)
