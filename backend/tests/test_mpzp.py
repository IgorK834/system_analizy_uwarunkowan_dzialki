import json

import httpx
import pytest
import respx
from shapely.geometry import MultiPolygon, Polygon

from app.core.settings import settings
from app.services.mpzp import (
    MpzpDiscoveryResult,
    _PointQueryResult,
    _aggregate_point_results,
    _build_sample_points,
    _grid_sample_points,
    _parse_get_feature_info_response,
    discover_mpzp,
)

SMALL_SQUARE = Polygon.from_bounds(500000, 200000, 500010, 200010)
LARGE_SQUARE = Polygon.from_bounds(500000, 200000, 500100, 200100)
MULTI_PARCEL = MultiPolygon(
    [
        Polygon.from_bounds(500000, 200000, 500010, 200010),
        Polygon.from_bounds(500100, 200100, 500110, 200110),
    ]
)

# W tej geometrii centroid leży poza wnętrzem litery L, a representative_point
# wewnątrz niej, dzięki czemu test rozróżnia oba dowody bez sztucznych duplikatów.
CONCAVE_PARCEL = Polygon(
    [(0, 0), (30, 0), (30, 10), (10, 10), (10, 30), (0, 30), (0, 0)]
)

FOUND_RESPONSE_MN = json.dumps(
    {
        "features": [
            {
                "properties": {
                    "plan_id": "MPZP/2020/1",
                    "symbol": "MN",
                    "uchwala_url": "https://example.local/uchwala.pdf",
                }
            }
        ],
        "vector_available": True,
    }
)
FOUND_RESPONSE_U = json.dumps(
    {
        "features": [
            {
                "properties": {
                    "plan_id": "MPZP/2020/2",
                    "symbol": "U",
                    "uchwala_url": "https://example.local/uchwala2.pdf",
                }
            }
        ],
        "vector_available": True,
    }
)
EMPTY_VECTOR_AVAILABLE = json.dumps({"features": [], "vector_available": True})
EMPTY_RASTER_ONLY = json.dumps({"features": [], "vector_available": False})


@pytest.mark.asyncio
@respx.mock
async def test_vector_gmina_returns_candidate_symbol_and_uchwala_url() -> None:
    respx.get(settings.kimpzp_wms_base_url).mock(
        return_value=httpx.Response(200, text=FOUND_RESPONSE_MN)
    )

    result = await discover_mpzp(SMALL_SQUARE)

    assert isinstance(result, MpzpDiscoveryResult)
    assert result.status == "found"
    assert "MN" in result.candidate_zone_symbols
    assert result.uchwala_url == "https://example.local/uchwala.pdf"
    assert result.plan_id == "MPZP/2020/1"


@pytest.mark.asyncio
@respx.mock
async def test_raster_gmina_returns_brak_wektorow_true_without_raising() -> None:
    respx.get(settings.kimpzp_wms_base_url).mock(
        return_value=httpx.Response(200, text=EMPTY_RASTER_ONLY)
    )

    result = await discover_mpzp(SMALL_SQUARE)

    assert result.brak_wektorow is True
    assert result.status == "raster_only"
    assert any("ręczna weryfikacja" in warning for warning in result.warnings)


@pytest.mark.asyncio
@respx.mock
async def test_point_outside_mpzp_returns_status_no_mpzp_with_warning() -> None:
    respx.get(settings.kimpzp_wms_base_url).mock(
        return_value=httpx.Response(200, text=EMPTY_VECTOR_AVAILABLE)
    )

    result = await discover_mpzp(SMALL_SQUARE)

    assert result.status == "no_mpzp"
    assert any(
        "plan" in warning.lower() or "mpzp" in warning.lower()
        for warning in result.warnings
    )


@pytest.mark.asyncio
@respx.mock
async def test_centroid_alone_is_not_sole_evidence_of_zone() -> None:
    assert len(_build_sample_points(CONCAVE_PARCEL)) == 2
    respx.get(settings.kimpzp_wms_base_url).mock(
        side_effect=[
            httpx.Response(200, text=EMPTY_VECTOR_AVAILABLE),
            httpx.Response(200, text=FOUND_RESPONSE_MN),
        ]
    )

    result = await discover_mpzp(CONCAVE_PARCEL)

    assert result.status == "found"
    assert "MN" in result.candidate_zone_symbols


@pytest.mark.asyncio
@respx.mock
async def test_one_point_query_failure_does_not_fail_whole_discovery() -> None:
    respx.get(settings.kimpzp_wms_base_url).mock(
        side_effect=[
            httpx.TimeoutException("timeout"),
            httpx.Response(200, text=FOUND_RESPONSE_MN),
        ]
    )

    result = await discover_mpzp(CONCAVE_PARCEL)

    assert result.status == "found"
    assert any("nie powiodło się" in warning for warning in result.warnings)


@pytest.mark.asyncio
@respx.mock
async def test_all_points_fail_returns_gracefully_not_exception() -> None:
    respx.get(settings.kimpzp_wms_base_url).mock(
        side_effect=httpx.TimeoutException("timeout")
    )

    result = await discover_mpzp(SMALL_SQUARE)

    assert result.status == "no_mpzp"
    assert len(result.warnings) >= 2


@pytest.mark.asyncio
@respx.mock
async def test_malformed_json_is_a_warning_not_discovery_exception() -> None:
    respx.get(settings.kimpzp_wms_base_url).mock(
        return_value=httpx.Response(200, text="{not-json")
    )

    result = await discover_mpzp(SMALL_SQUARE)

    assert result.status == "no_mpzp"
    assert any("nie powiodło się" in warning for warning in result.warnings)


@pytest.mark.asyncio
@respx.mock
async def test_http_error_is_a_warning_not_discovery_exception() -> None:
    respx.get(settings.kimpzp_wms_base_url).mock(return_value=httpx.Response(503))

    result = await discover_mpzp(SMALL_SQUARE)

    assert result.status == "no_mpzp"
    assert any("nie powiodło się" in warning for warning in result.warnings)


@pytest.mark.asyncio
@respx.mock
async def test_conflicting_plan_ids_across_points_add_warning() -> None:
    respx.get(settings.kimpzp_wms_base_url).mock(
        side_effect=[
            httpx.Response(200, text=FOUND_RESPONSE_MN),
            httpx.Response(200, text=FOUND_RESPONSE_U),
        ]
    )

    result = await discover_mpzp(CONCAVE_PARCEL)

    assert result.status == "found"
    assert result.candidate_zone_symbols == ["MN", "U"]
    assert any("różne plany" in warning for warning in result.warnings)


@pytest.mark.asyncio
@respx.mock
async def test_result_is_always_discovery_only_and_manual_review_required() -> None:
    respx.get(settings.kimpzp_wms_base_url).mock(
        return_value=httpx.Response(200, text=FOUND_RESPONSE_MN)
    )

    result = await discover_mpzp(SMALL_SQUARE)

    assert result.is_discovery_only is True
    assert result.source_metadata.manual_review_required is True
    assert result.source_metadata.source_name == "KIMPZP"
    assert result.source_metadata.confidence == 0.6


def test_build_sample_points_includes_centroid_and_representative_point() -> None:
    points = _build_sample_points(SMALL_SQUARE)
    centroid = SMALL_SQUARE.centroid
    representative = SMALL_SQUARE.representative_point()

    assert (round(centroid.x, 3), round(centroid.y, 3)) in points
    assert (round(representative.x, 3), round(representative.y, 3)) in points


def test_build_sample_points_no_duplicates() -> None:
    points = _build_sample_points(SMALL_SQUARE)

    assert len(points) == len(set(points))


def test_large_parcel_samples_more_points_than_small_parcel() -> None:
    assert len(_build_sample_points(LARGE_SQUARE)) > len(
        _build_sample_points(SMALL_SQUARE)
    )


def test_grid_sample_points_returns_four_quadrant_points() -> None:
    points = _grid_sample_points(LARGE_SQUARE)

    assert len(points) == 4
    assert len(points) == len(set(points))


def test_multipolygon_samples_each_part() -> None:
    points = _build_sample_points(MULTI_PARCEL)
    part_points = {
        (
            round(part.representative_point().x, 3),
            round(part.representative_point().y, 3),
        )
        for part in MULTI_PARCEL.geoms
    }

    assert len(points) >= 3
    assert part_points.issubset(set(points))


def test_parse_empty_features_vector_available_true() -> None:
    result = _parse_get_feature_info_response(EMPTY_VECTOR_AVAILABLE)

    assert result.found is False
    assert result.vector_available is True


def test_parse_empty_features_raster_only() -> None:
    result = _parse_get_feature_info_response(EMPTY_RASTER_ONLY)

    assert result.found is False
    assert result.vector_available is False


def test_parse_empty_response_defaults_to_no_feature_with_vector() -> None:
    result = _parse_get_feature_info_response("  ")

    assert result.found is False
    assert result.vector_available is True


def test_parse_response_with_feature_and_alternate_attribute_names() -> None:
    response = json.dumps(
        {
            "features": [
                {
                    "properties": {
                        "ID_PLANU": " P-7 ",
                        "SYMBOL_STREFY": " MW ",
                        "LINK": " https://example.local/p7.pdf ",
                    }
                }
            ]
        }
    )

    result = _parse_get_feature_info_response(response)

    assert result.found is True
    assert result.plan_id == "P-7"
    assert result.zone_symbol == "MW"
    assert result.uchwala_url == "https://example.local/p7.pdf"


def test_aggregate_point_results_ignores_exceptions_gracefully() -> None:
    results = [
        httpx.TimeoutException("x"),
        _PointQueryResult(
            found=True,
            plan_id="P1",
            zone_symbol="MN",
            uchwala_url=None,
            vector_available=True,
        ),
    ]

    symbols, plan_id, _, brak_wektorow, saw_feature, warnings = (
        _aggregate_point_results(results)
    )

    assert symbols == ["MN"]
    assert plan_id == "P1"
    assert brak_wektorow is False
    assert saw_feature is True
    assert len(warnings) == 1


def test_aggregate_raster_only_results_sets_brak_wektorow() -> None:
    result = _PointQueryResult(
        found=False,
        plan_id=None,
        zone_symbol=None,
        uchwala_url=None,
        vector_available=False,
    )

    _, _, _, brak_wektorow, saw_feature, _ = _aggregate_point_results([result])

    assert brak_wektorow is True
    assert saw_feature is False


@pytest.mark.asyncio
@respx.mock
async def test_fetch_sends_get_feature_info_request_params() -> None:
    route = respx.get(settings.kimpzp_wms_base_url).mock(
        return_value=httpx.Response(200, text=EMPTY_VECTOR_AVAILABLE)
    )

    await discover_mpzp(SMALL_SQUARE)

    params = route.calls.last.request.url.params
    assert params["request"] == "GetFeatureInfo"
    assert params["crs"] == "EPSG:2180"
    assert params["width"] == "2"
    assert params["i"] == "1"
