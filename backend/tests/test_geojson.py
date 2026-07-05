import json

import pytest
from pyproj import Transformer
from shapely.geometry import shape
from shapely.ops import transform as shapely_transform

from app.services.geometry import calculate_geometry_metrics, parse_parcel_geometry
from app.services.geojson import (
    buildable_area_geometry_to_geojson,
    geometries_to_geojson_feature_collection,
    geometry_to_geojson_feature,
    parcel_geometry_to_geojson,
    transform_geometry_to_wgs84,
)

SMALL_SQUARE_WKT = (
    "POLYGON((500000 200000, 500010 200000, 500010 200010, "
    "500000 200010, 500000 200000))"
)
MULTIPOLYGON_WKT = (
    "MULTIPOLYGON(((500000 200000, 500010 200000, 500010 200010, "
    "500000 200010, 500000 200000)), ((500100 200100, 500110 200100, "
    "500110 200110, 500100 200110, 500100 200100)))"
)


def test_transform_geometry_to_wgs84_returns_same_geom_type_polygon() -> None:
    geometry = parse_parcel_geometry(SMALL_SQUARE_WKT)

    result = transform_geometry_to_wgs84(geometry)

    assert result.geom_type == "Polygon"


def test_transform_geometry_to_wgs84_returns_same_geom_type_multipolygon() -> None:
    geometry = parse_parcel_geometry(MULTIPOLYGON_WKT)

    result = transform_geometry_to_wgs84(geometry)

    assert result.geom_type == "MultiPolygon"


def test_transform_geometry_to_wgs84_coordinates_within_poland_bounds() -> None:
    geometry = parse_parcel_geometry(SMALL_SQUARE_WKT)

    result = transform_geometry_to_wgs84(geometry)
    min_lon, min_lat, max_lon, max_lat = result.bounds

    assert 14.0 <= min_lon <= 25.0
    assert 14.0 <= max_lon <= 25.0
    assert 48.9 <= min_lat <= 55.0
    assert 48.9 <= max_lat <= 55.0


def test_transform_geometry_to_wgs84_roundtrip_preserves_shape() -> None:
    original = parse_parcel_geometry(SMALL_SQUARE_WKT)
    to_puwg = Transformer.from_crs("EPSG:4326", "EPSG:2180", always_xy=True)

    wgs84 = transform_geometry_to_wgs84(original)

    def _project_back(lon, lat, z=None):
        x, y = to_puwg.transform(lon, lat)
        return (x, y) if z is None else (x, y, z)

    back = shapely_transform(_project_back, wgs84)

    assert abs(back.area - original.area) < 0.01


def test_geometry_to_geojson_feature_has_correct_type() -> None:
    geometry = transform_geometry_to_wgs84(parse_parcel_geometry(SMALL_SQUARE_WKT))

    feature = geometry_to_geojson_feature(geometry)

    assert feature["type"] == "Feature"


def test_geometry_to_geojson_feature_geometry_is_valid_geojson_polygon() -> None:
    geometry = transform_geometry_to_wgs84(parse_parcel_geometry(SMALL_SQUARE_WKT))

    feature = geometry_to_geojson_feature(geometry)

    assert feature["geometry"]["type"] == "Polygon"
    assert isinstance(feature["geometry"]["coordinates"], list)
    assert shape(feature["geometry"]).geom_type == "Polygon"


def test_geometry_to_geojson_feature_multipolygon_type() -> None:
    geometry = transform_geometry_to_wgs84(parse_parcel_geometry(MULTIPOLYGON_WKT))

    feature = geometry_to_geojson_feature(geometry)

    assert feature["geometry"]["type"] == "MultiPolygon"


def test_geometry_to_geojson_feature_coordinates_are_lon_lat_order() -> None:
    geometry = transform_geometry_to_wgs84(parse_parcel_geometry(SMALL_SQUARE_WKT))

    feature = geometry_to_geojson_feature(geometry)
    lon, lat = feature["geometry"]["coordinates"][0][0]

    assert 14.0 <= lon <= 25.0
    assert 48.9 <= lat <= 55.0
    assert lon < lat


def test_geometry_to_geojson_feature_default_properties_empty_dict() -> None:
    geometry = transform_geometry_to_wgs84(parse_parcel_geometry(SMALL_SQUARE_WKT))

    feature = geometry_to_geojson_feature(geometry)

    assert feature["properties"] == {}


def test_geometry_to_geojson_feature_custom_properties_preserved() -> None:
    geometry = transform_geometry_to_wgs84(parse_parcel_geometry(SMALL_SQUARE_WKT))

    feature = geometry_to_geojson_feature(geometry, properties={"foo": "bar"})

    assert feature["properties"] == {"foo": "bar"}


def test_geometries_to_geojson_feature_collection_structure() -> None:
    geometry = transform_geometry_to_wgs84(parse_parcel_geometry(SMALL_SQUARE_WKT))
    feature1 = geometry_to_geojson_feature(geometry, properties={"layer": "first"})
    feature2 = geometry_to_geojson_feature(geometry, properties={"layer": "second"})

    result = geometries_to_geojson_feature_collection([feature1, feature2])

    assert result["type"] == "FeatureCollection"
    assert len(result["features"]) == 2


def test_geometries_to_geojson_feature_collection_empty_list() -> None:
    result = geometries_to_geojson_feature_collection([])

    assert result["features"] == []


def test_parcel_geometry_to_geojson_has_parcel_identifier_property() -> None:
    geometry = parse_parcel_geometry(SMALL_SQUARE_WKT)

    feature = parcel_geometry_to_geojson(geometry, "122101_1.0001.1")

    assert feature["properties"]["parcel_identifier"] == "122101_1.0001.1"
    assert feature["properties"]["layer"] == "parcel"


def test_buildable_area_geometry_to_geojson_has_setback_property() -> None:
    geometry = parse_parcel_geometry(SMALL_SQUARE_WKT).buffer(-1.0)

    feature = buildable_area_geometry_to_geojson(geometry, setback_m=1.0)

    assert feature["properties"]["setback_m"] == 1.0
    assert feature["properties"]["is_technical_approximation"] is True
    assert feature["properties"]["layer"] == "buildable_area"


def test_transform_does_not_mutate_area_sqm_calculation() -> None:
    original = parse_parcel_geometry(SMALL_SQUARE_WKT)

    metrics = calculate_geometry_metrics(original)
    wgs84 = transform_geometry_to_wgs84(original)

    assert metrics.area_sqm == pytest.approx(100.0, abs=0.01)
    assert wgs84.area != metrics.area_sqm


def test_valid_geojson_feature_is_serializable_to_json() -> None:
    geometry = transform_geometry_to_wgs84(parse_parcel_geometry(SMALL_SQUARE_WKT))
    feature = geometry_to_geojson_feature(geometry)

    serialized = json.dumps(feature)

    assert json.loads(serialized)["type"] == "Feature"
