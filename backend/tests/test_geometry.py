import math

import pytest

from app.services.geometry import (
    CoordinatesOutsidePolandError,
    to_puwg1992,
    to_wgs84,
)


TOLERANCE_M = 1.0
TOLERANCE_DEG = 1e-6
REF_LON = 19.01
REF_LAT = 49.86
REF_X = 500718.53
REF_Y = 221407.52


def test_to_puwg1992_reference_point_x_within_1m_tolerance() -> None:
    x, _ = to_puwg1992(REF_LON, REF_LAT)

    assert abs(x - REF_X) <= TOLERANCE_M


def test_to_puwg1992_reference_point_y_within_1m_tolerance() -> None:
    _, y = to_puwg1992(REF_LON, REF_LAT)

    assert abs(y - REF_Y) <= TOLERANCE_M


def test_to_wgs84_roundtrip_lon_within_1e6_degrees() -> None:
    x, y = to_puwg1992(REF_LON, REF_LAT)
    lon_back, _ = to_wgs84(x, y)

    assert abs(lon_back - REF_LON) < TOLERANCE_DEG


def test_to_wgs84_roundtrip_lat_within_1e6_degrees() -> None:
    x, y = to_puwg1992(REF_LON, REF_LAT)
    _, lat_back = to_wgs84(x, y)

    assert abs(lat_back - REF_LAT) < TOLERANCE_DEG


def test_to_puwg1992_returns_tuple_of_two_floats() -> None:
    result = to_puwg1992(REF_LON, REF_LAT)

    assert isinstance(result, tuple)
    assert len(result) == 2
    assert all(isinstance(value, float) for value in result)


def test_to_wgs84_returns_tuple_of_two_floats() -> None:
    result = to_wgs84(REF_X, REF_Y)

    assert isinstance(result, tuple)
    assert len(result) == 2
    assert all(isinstance(value, float) for value in result)


def test_to_puwg1992_warsaw_x_near_634000() -> None:
    x, _ = to_puwg1992(21.0, 52.2)

    assert 580000 < x < 680000


def test_to_puwg1992_gdansk_y_greater_than_bielsko_y() -> None:
    _, y_gdansk = to_puwg1992(18.65, 54.35)

    assert y_gdansk > REF_Y


def test_to_puwg1992_outside_poland_west_raises() -> None:
    with pytest.raises(CoordinatesOutsidePolandError):
        to_puwg1992(10.0, 52.0)


def test_to_puwg1992_outside_poland_east_raises() -> None:
    with pytest.raises(CoordinatesOutsidePolandError):
        to_puwg1992(30.0, 52.0)


def test_to_puwg1992_outside_poland_south_raises() -> None:
    with pytest.raises(CoordinatesOutsidePolandError):
        to_puwg1992(20.0, 45.0)


def test_to_puwg1992_outside_poland_north_raises() -> None:
    with pytest.raises(CoordinatesOutsidePolandError):
        to_puwg1992(20.0, 60.0)


def test_error_message_contains_offending_longitude() -> None:
    with pytest.raises(CoordinatesOutsidePolandError, match="10.0000"):
        to_puwg1992(10.0, 52.0)


def test_error_is_subclass_of_value_error() -> None:
    assert issubclass(CoordinatesOutsidePolandError, ValueError)


def test_to_wgs84_invalid_epsg2180_coords_raises() -> None:
    with pytest.raises(CoordinatesOutsidePolandError):
        to_wgs84(0.0, 0.0)


def test_to_puwg1992_border_point_accepted() -> None:
    result = to_puwg1992(14.5, 50.0)

    assert isinstance(result, tuple)
    assert len(result) == 2


def test_to_puwg1992_cracow_reference() -> None:
    x, y = to_puwg1992(19.944, 50.065)

    assert 560000 < x < 580000
    assert 230000 < y < 270000


def test_reference_roundtrip_distance_is_small() -> None:
    x, y = to_puwg1992(REF_LON, REF_LAT)
    lon_back, lat_back = to_wgs84(x, y)
    delta = math.hypot(lon_back - REF_LON, lat_back - REF_LAT)

    assert delta < TOLERANCE_DEG
