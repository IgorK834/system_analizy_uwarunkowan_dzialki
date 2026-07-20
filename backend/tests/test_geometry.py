import math

import pytest
from shapely import wkt as shapely_wkt
from shapely.geometry import LineString

from app.core.network_rules import NetworkRule, load_network_rules
from app.core.settings import settings
from app.services.geometry import (
    CoordinatesOutsidePolandError,
    InvalidParcelGeometryError,
    NetworkGeometryInput,
    NetworkProtectionZonesResult,
    ParcelGeometryMetrics,
    TechnicalSetbackResult,
    calculate_geometry_metrics,
    calculate_network_protection_zones,
    calculate_technical_setback,
    parse_parcel_geometry,
    to_puwg1992,
    to_wgs84,
)

TOLERANCE_M = 1.0
TOLERANCE_DEG = 1e-6
REF_LON = 19.01
REF_LAT = 49.86
REF_X = 500718.53
REF_Y = 221407.52
SQUARE_100X100_WKT = (
    "POLYGON((500000 200000, 500100 200000, 500100 200100, "
    "500000 200100, 500000 200000))"
)
TRIANGLE_WKT = "POLYGON((500000 200000, 500100 200000, 500050 200100, 500000 200000))"
MULTIPOLYGON_WKT = (
    "MULTIPOLYGON(((500000 200000, 500050 200000, 500050 200050, "
    "500000 200050, 500000 200000)), ((500100 200100, 500150 200100, "
    "500150 200150, 500100 200150, 500100 200100)))"
)
SQUARE_WITH_HOLE_WKT = (
    "POLYGON((500000 200000, 500100 200000, 500100 200100, "
    "500000 200100, 500000 200000), (500020 200020, 500080 200020, "
    "500080 200080, 500020 200080, 500020 200020))"
)
SELF_INTERSECTING_WKT = (
    "POLYGON((500000 200000, 500100 200100, 500100 200000, "
    "500000 200100, 500000 200000))"
)
INVALID_WKT_STRING = "NOT A VALID WKT"
POINT_WKT = "POINT(500000 200000)"
NARROW_5X100_WKT = (
    "POLYGON((500000 200000, 500005 200000, 500005 200100, "
    "500000 200100, 500000 200000))"
)


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


def test_parse_parcel_geometry_accepts_polygon() -> None:
    geometry = parse_parcel_geometry(SQUARE_100X100_WKT)

    assert geometry.geom_type == "Polygon"


def test_parse_parcel_geometry_accepts_multipolygon() -> None:
    geometry = parse_parcel_geometry(MULTIPOLYGON_WKT)

    assert geometry.geom_type == "MultiPolygon"


def test_parse_parcel_geometry_invalid_wkt_raises() -> None:
    with pytest.raises(InvalidParcelGeometryError):
        parse_parcel_geometry(INVALID_WKT_STRING)


def test_parse_parcel_geometry_point_raises_unsupported_type() -> None:
    with pytest.raises(InvalidParcelGeometryError):
        parse_parcel_geometry(POINT_WKT)


def test_parse_parcel_geometry_preserves_hole() -> None:
    geometry = parse_parcel_geometry(SQUARE_WITH_HOLE_WKT)

    assert len(list(geometry.interiors)) == 1


def test_calculate_geometry_metrics_square_area_sqm() -> None:
    geometry = parse_parcel_geometry(SQUARE_100X100_WKT)

    metrics = calculate_geometry_metrics(geometry)

    assert metrics.area_sqm == pytest.approx(10000.0, abs=0.01)


def test_calculate_geometry_metrics_square_area_are() -> None:
    geometry = parse_parcel_geometry(SQUARE_100X100_WKT)

    metrics = calculate_geometry_metrics(geometry)

    assert metrics.area_are == pytest.approx(100.0, abs=0.01)


def test_calculate_geometry_metrics_square_area_ha() -> None:
    geometry = parse_parcel_geometry(SQUARE_100X100_WKT)

    metrics = calculate_geometry_metrics(geometry)

    assert metrics.area_ha == pytest.approx(1.0, abs=0.0001)


def test_calculate_geometry_metrics_square_perimeter() -> None:
    geometry = parse_parcel_geometry(SQUARE_100X100_WKT)

    metrics = calculate_geometry_metrics(geometry)

    assert metrics.perimeter_m == pytest.approx(400.0, abs=0.01)


def test_calculate_geometry_metrics_square_centroid() -> None:
    geometry = parse_parcel_geometry(SQUARE_100X100_WKT)

    metrics = calculate_geometry_metrics(geometry)

    assert metrics.centroid_x == pytest.approx(500050.0, abs=0.01)
    assert metrics.centroid_y == pytest.approx(200050.0, abs=0.01)


def test_calculate_geometry_metrics_square_is_valid_true_not_repaired() -> None:
    geometry = parse_parcel_geometry(SQUARE_100X100_WKT)

    metrics = calculate_geometry_metrics(geometry)

    assert metrics.is_valid is True
    assert metrics.geometry_repaired is False
    assert metrics.repair_warning is None


def test_calculate_geometry_metrics_triangle_area() -> None:
    geometry = parse_parcel_geometry(TRIANGLE_WKT)

    metrics = calculate_geometry_metrics(geometry)

    assert metrics.area_sqm == pytest.approx(5000.0, abs=1.0)


def test_calculate_geometry_metrics_triangle_perimeter() -> None:
    geometry = parse_parcel_geometry(TRIANGLE_WKT)

    metrics = calculate_geometry_metrics(geometry)

    assert metrics.perimeter_m == pytest.approx(323.607, abs=1.0)


def test_calculate_geometry_metrics_multipolygon_area() -> None:
    geometry = parse_parcel_geometry(MULTIPOLYGON_WKT)

    metrics = calculate_geometry_metrics(geometry)

    assert metrics.area_sqm == pytest.approx(5000.0, abs=0.01)


def test_calculate_geometry_metrics_multipolygon_perimeter() -> None:
    geometry = parse_parcel_geometry(MULTIPOLYGON_WKT)

    metrics = calculate_geometry_metrics(geometry)

    assert metrics.perimeter_m == pytest.approx(400.0, abs=0.01)


def test_calculate_geometry_metrics_polygon_with_hole_subtracts_area() -> None:
    geometry = parse_parcel_geometry(SQUARE_WITH_HOLE_WKT)

    metrics = calculate_geometry_metrics(geometry)

    assert metrics.area_sqm == pytest.approx(6400.0, abs=0.01)


def test_calculate_geometry_metrics_self_intersecting_is_repaired() -> None:
    geometry = shapely_wkt.loads(SELF_INTERSECTING_WKT)

    metrics = calculate_geometry_metrics(geometry)

    assert metrics.is_valid is False
    assert metrics.geometry_repaired is True
    assert metrics.repair_warning is not None
    assert "napraw" in metrics.repair_warning.lower()


def test_calculate_geometry_metrics_repaired_geometry_has_positive_area() -> None:
    geometry = shapely_wkt.loads(SELF_INTERSECTING_WKT)

    metrics = calculate_geometry_metrics(geometry)

    assert metrics.area_sqm > 0


def test_calculate_geometry_metrics_returns_dataclass_instance() -> None:
    geometry = parse_parcel_geometry(SQUARE_100X100_WKT)

    metrics = calculate_geometry_metrics(geometry)

    assert isinstance(metrics, ParcelGeometryMetrics)


def test_calculate_geometry_metrics_area_units_consistency() -> None:
    geometry = parse_parcel_geometry(SQUARE_100X100_WKT)

    metrics = calculate_geometry_metrics(geometry)

    assert metrics.area_are == pytest.approx(metrics.area_sqm / 100.0)
    assert metrics.area_ha == pytest.approx(metrics.area_sqm / 10000.0)


def test_calculate_technical_setback_default_square_area() -> None:
    geometry = parse_parcel_geometry(SQUARE_100X100_WKT)

    result = calculate_technical_setback(geometry)

    assert result.buildable_area_sqm == pytest.approx(8464.0, abs=1.0)


def test_calculate_technical_setback_default_uses_settings_value() -> None:
    geometry = parse_parcel_geometry(SQUARE_100X100_WKT)

    result = calculate_technical_setback(geometry)

    assert settings.default_technical_setback_m == 4.0
    assert result.setback_m == settings.default_technical_setback_m


def test_calculate_technical_setback_is_marked_as_technical_approximation() -> None:
    geometry = parse_parcel_geometry(SQUARE_100X100_WKT)

    result = calculate_technical_setback(geometry)

    assert result.is_technical_approximation is True


def test_calculate_technical_setback_normal_case_has_no_warning() -> None:
    geometry = parse_parcel_geometry(SQUARE_100X100_WKT)

    result = calculate_technical_setback(geometry)

    assert result.warning is None


def test_calculate_technical_setback_narrow_parcel_returns_zero_without_error() -> None:
    geometry = parse_parcel_geometry(NARROW_5X100_WKT)

    result = calculate_technical_setback(geometry)

    assert result.buildable_area_sqm == 0.0


def test_calculate_technical_setback_narrow_parcel_warning_mentions_width() -> None:
    geometry = parse_parcel_geometry(NARROW_5X100_WKT)

    result = calculate_technical_setback(geometry)

    assert result.warning is not None
    assert "wąska" in result.warning or "zbyt wąsk" in result.warning


def test_calculate_technical_setback_custom_two_meter_area() -> None:
    geometry = parse_parcel_geometry(SQUARE_100X100_WKT)

    result = calculate_technical_setback(geometry, setback_m=2.0)

    assert result.buildable_area_sqm == pytest.approx(9216.0, abs=1.0)


def test_calculate_technical_setback_larger_setback_has_smaller_area() -> None:
    geometry = parse_parcel_geometry(SQUARE_100X100_WKT)

    smaller_setback = calculate_technical_setback(geometry, setback_m=2.0)
    larger_setback = calculate_technical_setback(geometry, setback_m=10.0)

    assert larger_setback.buildable_area_sqm < smaller_setback.buildable_area_sqm


def test_calculate_technical_setback_zero_setback_equals_original_area() -> None:
    geometry = parse_parcel_geometry(SQUARE_100X100_WKT)

    result = calculate_technical_setback(geometry, setback_m=0.0)

    assert result.buildable_area_sqm == pytest.approx(10000.0, abs=0.01)


def test_calculate_technical_setback_returns_dataclass_instance() -> None:
    geometry = parse_parcel_geometry(SQUARE_100X100_WKT)

    result = calculate_technical_setback(geometry)

    assert isinstance(result, TechnicalSetbackResult)


def test_calculate_technical_setback_multipolygon_area() -> None:
    geometry = parse_parcel_geometry(MULTIPOLYGON_WKT)

    result = calculate_technical_setback(geometry, setback_m=4.0)

    assert result.buildable_area_sqm == pytest.approx(3528.0, abs=1.0)


def test_calculate_technical_setback_uses_monkeypatched_default_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    geometry = parse_parcel_geometry(SQUARE_100X100_WKT)
    monkeypatch.setattr(settings, "default_technical_setback_m", 2.0)

    result = calculate_technical_setback(geometry)

    assert result.buildable_area_sqm == pytest.approx(9216.0, abs=1.0)
    assert result.setback_m == 2.0


def test_calculate_technical_setback_warning_mentions_technical_and_mpzp() -> None:
    geometry = parse_parcel_geometry(NARROW_5X100_WKT)

    result = calculate_technical_setback(geometry)

    assert result.warning is not None
    assert "techniczne" in result.warning
    assert (
        "MPZP" in result.warning
        or "linii zabudowy" in result.warning
        or "planistyczn" in result.warning
    )


# --- calculate_network_protection_zones -------------------------------------

# Osobny zestaw reguł testowych z okrągłymi wartościami, niezależny od
# produkcyjnego network_rules.json, dla deterministycznych obliczeń.
CUSTOM_TEST_RULES = {
    "water": NetworkRule(
        network_type="water",
        default_buffer_m=2.0,
        source="test",
        confidence=0.9,
        note="test",
        apply_even_outside_parcel=False,
    ),
    "gas": NetworkRule(
        network_type="gas",
        default_buffer_m=2.0,
        source="test",
        confidence=0.9,
        note="test",
        apply_even_outside_parcel=True,
    ),
}


def test_calculate_network_protection_zones_line_through_center_reduces_area_predictably() -> None:
    parcel = parse_parcel_geometry(SQUARE_100X100_WKT)
    network = NetworkGeometryInput(
        network_type="water",
        geometry=LineString([(500000, 200050), (500100, 200050)]),
    )

    result = calculate_network_protection_zones(
        parcel=parcel,
        buildable_area=parcel,
        networks=[network],
        rules=CUSTOM_TEST_RULES,
    )

    # Bufor 2m wokół linii na pełną szerokość działki (flat caps) = pasek
    # 100m x 4m = 400 m², w całości wewnątrz działki.
    assert result.net_buildable_area_sqm == pytest.approx(10000.0 - 400.0, abs=1.0)
    assert len(result.zones) == 1
    assert result.zones[0].zone_area_sqm == pytest.approx(400.0, abs=1.0)


def test_calculate_network_protection_zones_network_outside_parcel_does_not_reduce_area() -> None:
    parcel = parse_parcel_geometry(SQUARE_100X100_WKT)
    network = NetworkGeometryInput(
        network_type="water",
        geometry=LineString([(500200, 200050), (500300, 200050)]),
    )

    result = calculate_network_protection_zones(
        parcel=parcel,
        buildable_area=parcel,
        networks=[network],
        rules=CUSTOM_TEST_RULES,
    )

    assert result.net_buildable_area_sqm == pytest.approx(10000.0, abs=0.01)
    assert result.zones == []


def test_calculate_network_protection_zones_gas_rule_applies_even_outside_parcel() -> None:
    parcel = parse_parcel_geometry(SQUARE_100X100_WKT)
    network = NetworkGeometryInput(
        network_type="gas",
        geometry=LineString([(500101, 200000), (500101, 200100)]),
    )

    result = calculate_network_protection_zones(
        parcel=parcel,
        buildable_area=parcel,
        networks=[network],
        rules=CUSTOM_TEST_RULES,
    )

    assert result.net_buildable_area_sqm < 10000.0
    assert len(result.zones) == 1
    assert result.zones[0].network_type == "gas"


def test_calculate_network_protection_zones_water_rule_ignores_network_just_outside_without_override() -> None:
    parcel = parse_parcel_geometry(SQUARE_100X100_WKT)
    network = NetworkGeometryInput(
        network_type="water",
        geometry=LineString([(500101, 200000), (500101, 200100)]),
    )

    result = calculate_network_protection_zones(
        parcel=parcel,
        buildable_area=parcel,
        networks=[network],
        rules=CUSTOM_TEST_RULES,
    )

    assert result.net_buildable_area_sqm == pytest.approx(10000.0, abs=0.01)
    assert result.zones == []


def test_calculate_network_protection_zones_unknown_type_skipped_with_warning() -> None:
    parcel = parse_parcel_geometry(SQUARE_100X100_WKT)
    network = NetworkGeometryInput(
        network_type="unknown",
        geometry=LineString([(500000, 200050), (500100, 200050)]),
    )

    result = calculate_network_protection_zones(
        parcel=parcel,
        buildable_area=parcel,
        networks=[network],
        rules=CUSTOM_TEST_RULES,
    )

    assert result.net_buildable_area_sqm == pytest.approx(10000.0, abs=0.01)
    assert len(result.warnings) == 1
    assert "unknown" in result.warnings[0] or "nieznan" in result.warnings[0].lower()


def test_calculate_network_protection_zones_each_zone_has_source_and_confidence() -> None:
    parcel = parse_parcel_geometry(SQUARE_100X100_WKT)
    network = NetworkGeometryInput(
        network_type="water",
        geometry=LineString([(500000, 200050), (500100, 200050)]),
    )

    result = calculate_network_protection_zones(
        parcel=parcel,
        buildable_area=parcel,
        networks=[network],
        rules=CUSTOM_TEST_RULES,
    )

    assert result.zones[0].source == "test"
    assert result.zones[0].confidence == 0.9


def test_calculate_network_protection_zones_empty_networks_list_returns_full_area() -> None:
    parcel = parse_parcel_geometry(SQUARE_100X100_WKT)

    result = calculate_network_protection_zones(
        parcel=parcel,
        buildable_area=parcel,
        networks=[],
        rules=CUSTOM_TEST_RULES,
    )

    assert result.net_buildable_area_sqm == pytest.approx(10000.0, abs=0.01)


def test_calculate_network_protection_zones_multiple_networks_sum_correctly() -> None:
    parcel = parse_parcel_geometry(SQUARE_100X100_WKT)
    # Dwie równoległe linie wodociągowe w różnych częściach działki,
    # nieprzecinające swoich buforów (bufor 2m, linie oddalone o 20m).
    network_a = NetworkGeometryInput(
        network_type="water",
        geometry=LineString([(500000, 200020), (500100, 200020)]),
    )
    network_b = NetworkGeometryInput(
        network_type="water",
        geometry=LineString([(500000, 200080), (500100, 200080)]),
    )

    result = calculate_network_protection_zones(
        parcel=parcel,
        buildable_area=parcel,
        networks=[network_a, network_b],
        rules=CUSTOM_TEST_RULES,
    )

    # Mniej niż redukcja tylko jednej sieci (9600), bo dwie strefy się sumują.
    assert result.net_buildable_area_sqm < 10000.0 - 400.0
    assert sum(zone.zone_area_sqm for zone in result.zones) == pytest.approx(
        parcel.area - result.net_buildable_area_sqm
    )


def test_calculate_network_protection_zones_uses_default_rules_when_none_passed() -> None:
    parcel = parse_parcel_geometry(SQUARE_100X100_WKT)
    network = NetworkGeometryInput(
        network_type="water",
        geometry=LineString([(500000, 200050), (500100, 200050)]),
    )

    result = calculate_network_protection_zones(
        parcel=parcel,
        buildable_area=parcel,
        networks=[network],
    )

    assert result.zones[0].buffer_m == load_network_rules()["water"].default_buffer_m


def test_calculate_network_protection_zones_returns_dataclass_instance() -> None:
    parcel = parse_parcel_geometry(SQUARE_100X100_WKT)

    result = calculate_network_protection_zones(
        parcel=parcel,
        buildable_area=parcel,
        networks=[],
        rules=CUSTOM_TEST_RULES,
    )

    assert isinstance(result, NetworkProtectionZonesResult)
