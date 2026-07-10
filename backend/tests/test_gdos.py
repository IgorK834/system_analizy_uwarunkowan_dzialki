import json
from datetime import UTC, datetime

import httpx
import pytest
import respx
from shapely.geometry import box

from app.core.settings import settings
from app.services.gdos import (
    GdosServiceUnavailableError,
    NatureProtectionFeature,
    _build_nature_protection_feature,
    _normalize_protection_type_value,
    _parse_zone_response,
    _ratio_based_severity,
    fetch_nature_protection_areas,
)

SQUARE_PARCEL = box(500000, 200000, 500100, 200100)


def _gml_polygon(
    bounds: tuple[float, float, float, float],
    protection_type: str,
    name: str = "Obszar testowy",
) -> str:
    minx, miny, maxx, maxy = bounds
    pos_list = (
        f"{minx} {miny} {maxx} {miny} {maxx} {maxy} "
        f"{minx} {maxy} {minx} {miny}"
    )
    return f"""
    <wfs:FeatureCollection
        xmlns:wfs="http://www.opengis.net/wfs/2.0"
        xmlns:gml="http://www.opengis.net/gml/3.2"
        xmlns:gdos="https://sdi.gdos.gov.pl">
      <wfs:member>
        <gdos:forma>
          <gdos:forma_ochrony>{protection_type}</gdos:forma_ochrony>
          <gdos:nazwa>{name}</gdos:nazwa>
          <gdos:geometry>
            <gml:Polygon srsName="EPSG:2180">
              <gml:exterior><gml:LinearRing><gml:posList>{pos_list}</gml:posList></gml:LinearRing></gml:exterior>
            </gml:Polygon>
          </gdos:geometry>
        </gdos:forma>
      </wfs:member>
    </wfs:FeatureCollection>
    """


MOCK_GML_FULL_COVERAGE = _gml_polygon(
    (499990, 199990, 500110, 200110), "Natura 2000"
)
MOCK_GML_PARTIAL_25_PERCENT = _gml_polygon(
    (500000, 200000, 500050, 200050), "park krajobrazowy"
)
MOCK_GML_DISJOINT = _gml_polygon(
    (500200, 200000, 500250, 200050), "Natura 2000"
)
MOCK_GML_UNKNOWN_TYPE = _gml_polygon(
    (500000, 200000, 500050, 200050), "XYZ_NIEZNANY"
)
MOCK_GML_RESERVE_SMALL_OVERLAP = _gml_polygon(
    (500000, 200000, 500005, 200100), "rezerwat przyrody"
)
MOCK_GML_BOUNDARY_TOUCH_RESERVE = _gml_polygon(
    (500100, 200000, 500110, 200100), "rezerwat przyrody"
)
MOCK_GEOJSON_NATURA2000_LOW = json.dumps(
    {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "forma_ochrony": "Natura 2000",
                    "nazwa": "Dolina X",
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [500000, 200000],
                            [500005, 200000],
                            [500005, 200100],
                            [500000, 200100],
                            [500000, 200000],
                        ]
                    ],
                },
            }
        ],
    }
)
EMPTY_GML = (
    '<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0">'
    "</wfs:FeatureCollection>"
)
MALFORMED_GML = "<not><valid"


def _features_from_payload(payload: str) -> list[NatureProtectionFeature]:
    features: list[NatureProtectionFeature] = []
    for geometry, properties in _parse_zone_response(payload):
        feature = _build_nature_protection_feature(
            SQUARE_PARCEL,
            geometry,
            properties,
            "https://example.test/gdos",
            datetime.now(UTC),
        )
        if feature is not None:
            features.append(feature)
    return features


def test_full_coverage_gets_severity_high() -> None:
    result = _features_from_payload(MOCK_GML_FULL_COVERAGE)

    assert result[0].severity == "high"
    assert result[0].area_ratio >= 0.99


def test_partial_intersection_contains_area_ratio_and_geometry() -> None:
    result = _features_from_payload(MOCK_GML_PARTIAL_25_PERCENT)

    assert result[0].area_ratio == pytest.approx(0.25, abs=0.01)
    assert result[0].geometry.area == pytest.approx(2500.0, abs=1.0)
    assert result[0].intersection_area_sqm == pytest.approx(2500.0, abs=1.0)
    assert result[0].severity == "medium"


def test_no_collision_returns_empty_list() -> None:
    assert _features_from_payload(MOCK_GML_DISJOINT) == []


def test_unknown_protection_type_handled_with_warning_not_exception() -> None:
    result = _features_from_payload(MOCK_GML_UNKNOWN_TYPE)

    assert result[0].protection_type == "unknown"
    assert len(result[0].warnings) >= 1
    assert result[0].source_metadata.manual_review_required is True
    assert result[0].source_metadata.confidence == 0.4


def test_reserve_small_overlap_still_high() -> None:
    result = _features_from_payload(MOCK_GML_RESERVE_SMALL_OVERLAP)

    assert result[0].area_ratio == pytest.approx(0.05)
    assert result[0].severity == "high"


def test_reserve_boundary_touch_overrides_to_low() -> None:
    result = _features_from_payload(MOCK_GML_BOUNDARY_TOUCH_RESERVE)

    assert result[0].severity == "low"
    assert any("boundary_touch" in warning for warning in result[0].warnings)


def test_natura2000_low_ratio_is_low() -> None:
    result = _features_from_payload(MOCK_GEOJSON_NATURA2000_LOW)

    assert result[0].severity == "low"
    assert result[0].name == "Dolina X"
    assert result[0].protection_type == "natura2000"


@pytest.mark.parametrize(
    ("ratio", "expected"),
    [(0.3, "medium"), (0.6, "high"), (0.05, "low")],
)
def test_ratio_based_severity_thresholds(ratio: float, expected: str) -> None:
    assert _ratio_based_severity(ratio) == expected


def test_normalize_protection_type_recognizes_multiple_keywords() -> None:
    assert (
        _normalize_protection_type_value("Rezerwat przyrody Bór")
        == "rezerwat_przyrody"
    )
    assert (
        _normalize_protection_type_value("Park  Krajobrazowy Dolina")
        == "park_krajobrazowy"
    )
    assert _normalize_protection_type_value("coś zupełnie innego") == "unknown"


def test_empty_response_returns_empty_list() -> None:
    assert _parse_zone_response(EMPTY_GML) == []
    assert _parse_zone_response("   ") == []


def test_geojson_without_geometry_is_ignored() -> None:
    payload = json.dumps(
        {"type": "FeatureCollection", "features": [{"properties": {}}]}
    )

    assert _parse_zone_response(payload) == []


def test_geojson_non_polygon_geometry_is_ignored() -> None:
    payload = json.dumps(
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "properties": {},
                    "geometry": {"type": "Point", "coordinates": [1, 2]},
                }
            ],
        }
    )

    assert _parse_zone_response(payload) == []


@respx.mock
@pytest.mark.asyncio
async def test_fetch_timeout_raises_gdos_service_unavailable() -> None:
    respx.get(settings.gdos_wfs_base_url).mock(
        side_effect=httpx.TimeoutException("timeout")
    )

    with pytest.raises(GdosServiceUnavailableError):
        await fetch_nature_protection_areas(SQUARE_PARCEL)


@respx.mock
@pytest.mark.asyncio
async def test_fetch_http_500_raises_gdos_service_unavailable() -> None:
    respx.get(settings.gdos_wfs_base_url).mock(return_value=httpx.Response(500))

    with pytest.raises(GdosServiceUnavailableError):
        await fetch_nature_protection_areas(SQUARE_PARCEL)


@respx.mock
@pytest.mark.asyncio
async def test_fetch_malformed_response_raises_not_empty_list() -> None:
    respx.get(settings.gdos_wfs_base_url).mock(
        return_value=httpx.Response(200, text=MALFORMED_GML)
    )

    with pytest.raises(GdosServiceUnavailableError):
        await fetch_nature_protection_areas(SQUARE_PARCEL)


@pytest.mark.parametrize(
    "payload", [MOCK_GML_PARTIAL_25_PERCENT, MOCK_GEOJSON_NATURA2000_LOW]
)
@respx.mock
@pytest.mark.asyncio
async def test_fetch_success_via_respx_gml_and_geojson(payload: str) -> None:
    respx.get(settings.gdos_wfs_base_url).mock(
        return_value=httpx.Response(200, text=payload)
    )

    result = await fetch_nature_protection_areas(SQUARE_PARCEL)

    assert len(result) == 1
    assert isinstance(result[0], NatureProtectionFeature)


@respx.mock
@pytest.mark.asyncio
async def test_fetch_sends_bbox_with_epsg2180() -> None:
    route = respx.get(settings.gdos_wfs_base_url).mock(
        return_value=httpx.Response(200, text=EMPTY_GML)
    )

    await fetch_nature_protection_areas(SQUARE_PARCEL)

    assert "EPSG:2180" in route.calls.last.request.url.params["bbox"]


@respx.mock
@pytest.mark.asyncio
async def test_risk_feature_has_source_metadata_gdos() -> None:
    respx.get(settings.gdos_wfs_base_url).mock(
        return_value=httpx.Response(200, text=MOCK_GML_FULL_COVERAGE)
    )

    result = await fetch_nature_protection_areas(SQUARE_PARCEL)

    assert result[0].source_metadata.source_name == "GDOS"
    assert result[0].source_metadata.manual_review_required is False
