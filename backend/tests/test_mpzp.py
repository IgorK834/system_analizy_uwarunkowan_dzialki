import json

import httpx
import pytest
import respx
from shapely.geometry import MultiPolygon, Polygon

from app.core.settings import settings
from pathlib import Path

from app.modules.planning.domain.kimpzp_discovery import KimpzpAct, KimpzpPointResult
from app.services.mpzp import (
    MpzpDiscoveryResult,
    _aggregate_point_results,
    _build_sample_points,
    _grid_sample_points,
    _parse_get_feature_info_response,
    _parse_html_get_feature_info_response,
    discover_mpzp,
    discovery_section,
)

KIMPZP_FIXTURES = Path(__file__).parent / "fixtures" / "source_contracts" / "kimpzp"


def _fixture(name: str) -> str:
    return (KIMPZP_FIXTURES / name).read_text(encoding="utf-8")

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
    assert result.status == "available"
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
    assert result.status == "no_match"
    assert any("ręczna weryfikacja" in warning for warning in result.warnings)


@pytest.mark.asyncio
@respx.mock
async def test_point_outside_mpzp_returns_status_no_mpzp_with_warning() -> None:
    respx.get(settings.kimpzp_wms_base_url).mock(
        return_value=httpx.Response(200, text=EMPTY_VECTOR_AVAILABLE)
    )

    result = await discover_mpzp(SMALL_SQUARE)

    assert result.status == "no_match"
    assert result.brak_wektorow is False
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

    assert result.status == "available"
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

    assert result.status == "available"
    assert result.failed_points == 1
    assert any("nie powiodło się" in warning for warning in result.warnings)
    assert "KIMPZP_PARTIAL_SERVICE_ERROR" in [issue.code for issue in result.issues]


@pytest.mark.asyncio
@respx.mock
async def test_all_points_fail_returns_gracefully_not_exception() -> None:
    respx.get(settings.kimpzp_wms_base_url).mock(
        side_effect=httpx.TimeoutException("timeout")
    )

    result = await discover_mpzp(SMALL_SQUARE)

    # Błąd źródła nigdy nie jest „brakiem planu”.
    assert result.status == "unavailable"
    assert result.failed_points == result.sampled_points
    assert [issue.code for issue in result.issues] == ["MPZP_DISCOVERY_UNAVAILABLE"]
    assert not any("Nie znaleziono" in warning for warning in result.warnings)


@pytest.mark.asyncio
@respx.mock
async def test_malformed_json_is_unknown_not_no_match() -> None:
    respx.get(settings.kimpzp_wms_base_url).mock(
        return_value=httpx.Response(200, text="{not-json")
    )

    result = await discover_mpzp(SMALL_SQUARE)

    assert result.status == "unknown"
    assert "KIMPZP_UNRECOGNIZED_RESPONSE" in result.reason_codes
    assert [issue.code for issue in result.issues] == ["MPZP_DISCOVERY_UNKNOWN"]


@pytest.mark.asyncio
@respx.mock
async def test_http_error_is_a_warning_not_discovery_exception() -> None:
    respx.get(settings.kimpzp_wms_base_url).mock(return_value=httpx.Response(503))

    result = await discover_mpzp(SMALL_SQUARE)

    assert result.status == "unavailable"
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

    assert result.status == "available"
    assert result.candidate_zone_symbols == ["MN", "U"]
    # Różne akty w różnych punktach: bez cichego wyboru pierwszego planu.
    assert result.plan_id is None and result.uchwala_url is None
    assert result.multiple_acts_on_parcel is True
    assert result.multiple_acts_at_point is False
    issue = next(issue for issue in result.issues if issue.code == "MPZP_MULTIPLE_ACTS_ON_PARCEL")
    assert "różne plany" in issue.message


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
    assert result.status == "no_match"
    assert result.vector_available is True


def test_parse_empty_features_raster_only() -> None:
    result = _parse_get_feature_info_response(EMPTY_RASTER_ONLY)

    assert result.found is False
    assert result.vector_available is False


def test_parse_empty_response_is_unknown_not_no_match() -> None:
    result = _parse_get_feature_info_response("  ")

    assert result.found is False
    assert result.status == "unknown"
    assert result.reason_codes == ("KIMPZP_EMPTY_RESPONSE",)


def test_parse_response_with_feature_and_alternate_attribute_names() -> None:
    response = json.dumps(
        {
            "features": [
                {
                    "properties": {
                        "ID_PLANU": " P/7/2020 ",
                        "SYMBOL_STREFY": " MW ",
                        "LINK": " https://example.local/p7.pdf ",
                    }
                }
            ]
        }
    )

    result = _parse_get_feature_info_response(response)

    assert result.found is True
    assert [act.resolution_number for act in result.acts] == ["P/7/2020"]
    assert result.zone_symbols == ("MW",)
    assert result.acts[0].document_url == "https://example.local/p7.pdf"


def test_parse_json_feature_without_valid_plan_number_keeps_only_the_symbol() -> None:
    response = json.dumps({"features": [{"properties": {"plan_id": "P-7", "symbol": "MN"}}]})

    result = _parse_get_feature_info_response(response)

    assert result.status == "available"
    assert result.acts == ()
    assert result.unassigned_zone_symbols == ("MN",)


def test_parse_official_kimpzp_html_vector_response() -> None:
    response = """
    <html><body><table>
      <tr><th>Uchwalenie</th><th>Oznaczenie</th><th>WWW</th></tr>
      <tr><td>LIII/1464/21</td><td>KDLT.2</td>
          <td>https://example.local/plan</td></tr>
    </table></body></html>
    """

    result = _parse_get_feature_info_response(response)

    assert result.found is True
    assert [act.resolution_number for act in result.acts] == ["LIII/1464/21"]
    assert result.zone_symbols == ("KDLT.2",)
    assert result.acts[0].www_url == "https://example.local/plan"
    assert result.vector_available is True


def test_parse_official_kimpzp_html_raster_response() -> None:
    response = """
    <html><body><table>
      <tr><th>Uchwała</th><td>XVII/136/96</td></tr>
      <tr><th>Poziom informatyzacji</th><td>rastrowy</td></tr>
      <tr><th>Treść uchwały</th><td>https://example.local/plan.pdf</td></tr>
    </table></body></html>
    """

    result = _parse_get_feature_info_response(response)

    assert result.found is True
    assert result.acts[0].resolution_number == "XVII/136/96"
    assert result.acts[0].text_url == "https://example.local/plan.pdf"
    assert result.zone_symbols == ()
    assert result.vector_available is False


def _point(*acts: KimpzpAct, status: str = "available") -> KimpzpPointResult:
    return KimpzpPointResult(status=status, acts=tuple(acts))  # type: ignore[arg-type]


def test_aggregate_point_results_ignores_exceptions_gracefully() -> None:
    results = [
        httpx.TimeoutException("x"),
        _point(KimpzpAct(resolution_number="P/1/2020", zone_symbols=("MN",))),
    ]

    summary, warnings = _aggregate_point_results(results)

    assert summary.zone_symbols == ("MN",)
    assert summary.single_act is not None
    assert summary.single_act.resolution_number == "P/1/2020"
    assert summary.status == "available"
    assert summary.failed_points == 1
    assert len(warnings) == 1


def test_aggregate_raster_only_results_sets_vector_flag() -> None:
    result = KimpzpPointResult(status="no_match", vector_available=False)

    summary, _ = _aggregate_point_results([result])

    assert summary.saw_vector_unavailable is True
    assert summary.status == "no_match"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_sends_get_feature_info_request_params() -> None:
    route = respx.get(settings.kimpzp_wms_base_url).mock(
        return_value=httpx.Response(200, text=EMPTY_VECTOR_AVAILABLE)
    )

    await discover_mpzp(SMALL_SQUARE)

    params = route.calls.last.request.url.params
    assert params["request"] == "GetFeatureInfo"
    assert params["version"] == "1.1.1"
    assert params["srs"] == "EPSG:2180"
    assert params["layers"] == "plany_granice"
    assert params["width"] == "2"
    assert params["x"] == "1"


# --- AU-004: zamrożone, rzeczywiste odpowiedzi KIMPZP -------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_gora_kalwaria_parcel_lists_both_acts_with_text_links_without_choosing() -> None:
    """Regresja audytu (B4/R3): działka 141801_4.0701.23/8 z raportu OnGeo."""
    respx.get(settings.kimpzp_wms_base_url).mock(
        return_value=httpx.Response(200, text=_fixture("gora_kalwaria_141801_4.0701.23_8.html"))
    )

    result = await discover_mpzp(SMALL_SQUARE)

    assert result.status == "available"
    acts = {act.resolution_number: act for act in result.acts}
    assert list(acts) == ["IV/30/2024", "576/XLVII/2010"]  # malejąco wg „obowiązuje od”
    assert acts["IV/30/2024"].text_url == "http://mpzp.gorakalwaria.pl/portal/mpzp/uch/IV_30_2024.pdf"
    assert acts["576/XLVII/2010"].text_url == (
        "http://mpzp.gorakalwaria.pl/portal/mpzp/uch/576_XLVII_2010.pdf"
    )
    # Uchwała zmieniająca jest wyłącznie zmianą planu 576/XLVII/2010.
    assert "LIV/467/2021" not in acts
    assert "LIV/467/2021" in [a.resolution_number for a in acts["576/XLVII/2010"].amendments]
    assert result.multiple_acts_at_point is True
    assert result.plan_id is None and result.uchwala_url is None
    assert "MPZP_MULTIPLE_ACTS_AT_POINT" in result.reason_codes
    issue = next(issue for issue in result.issues if issue.code == "MPZP_MULTIPLE_ACTS_AT_POINT")
    assert "IV/30/2024" in issue.message and "576/XLVII/2010" in issue.message


@pytest.mark.asyncio
@respx.mock
async def test_no_service_for_area_is_no_coverage_not_not_found() -> None:
    respx.get(settings.kimpzp_wms_base_url).mock(
        return_value=httpx.Response(200, text=_fixture("dygowo_321606_2.0029.362.html"))
    )

    result = await discover_mpzp(SMALL_SQUARE)

    assert result.status == "no_coverage"
    assert result.reason_codes == ["KIMPZP_NO_SERVICE_FOR_AREA"]
    assert [issue.code for issue in result.issues] == ["KIMPZP_NO_SERVICE_FOR_AREA"]
    assert not any("Nie znaleziono" in warning for warning in result.warnings)
    assert result.brak_wektorow is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fixture",
    ["warszawa_146510_8.0502.1_3.html", "bielsko_biala_246101_1.0056.155_3.html"],
)
@respx.mock
async def test_municipal_service_error_with_http_200_is_unavailable(fixture: str) -> None:
    respx.get(settings.kimpzp_wms_base_url).mock(
        return_value=httpx.Response(200, text=_fixture(fixture))
    )

    result = await discover_mpzp(SMALL_SQUARE)

    assert result.status == "unavailable"
    assert result.acts == []
    assert [issue.code for issue in result.issues] == ["MPZP_DISCOVERY_UNAVAILABLE"]
    assert result.source_metadata.confidence == 0.3


@pytest.mark.asyncio
@respx.mock
async def test_single_act_with_zone_symbol_is_selected_for_the_analysis() -> None:
    respx.get(settings.kimpzp_wms_base_url).mock(
        return_value=httpx.Response(200, text=_fixture("krakow_126105_9.0001.580_4.html"))
    )

    result = await discover_mpzp(SMALL_SQUARE)

    assert result.plan_id == "XII/131/11"
    assert result.uchwala_url == "http://www.bip.krakow.pl/?bip_id=1&dok_id=45241&sub_dok_id=45241"
    assert result.candidate_zone_symbols == ["KP.1"]
    assert result.multiple_acts_at_point is False
    assert result.issues == []


@pytest.mark.asyncio
@respx.mock
async def test_discovery_section_maps_acts_amendments_and_link_verification() -> None:
    respx.get(settings.kimpzp_wms_base_url).mock(
        return_value=httpx.Response(200, text=_fixture("gora_kalwaria_141801_4.0701.23_8.html"))
    )

    section = discovery_section(await discover_mpzp(SMALL_SQUARE))

    assert section.status == "available"
    assert section.selected_act is None
    assert section.multiple_acts_at_point is True
    newest, oldest = section.acts
    assert newest.resolution_number == "IV/30/2024"
    assert str(newest.valid_from) == "2024-06-26"
    assert newest.legal_status == "binding"
    assert newest.legend_url == "http://mpzp.gorakalwaria.pl/portal/mpzp/leg/IV_30_2024_leg.pdf"
    # Linki HTTP nie są klikalne; BIP po HTTPS — tak.
    assert newest.verified_links == ["bip_url"]
    kinds = [(amendment.kind, amendment.resolution_number) for amendment in oldest.amendments]
    assert kinds == [("note", None), ("text_change", "LIV/467/2021"), ("text_change", "XXXIX/366/2017")]
    assert oldest.amendments[1].document_url_verified is True


def test_discovery_section_for_failed_discovery_is_unknown() -> None:
    section = discovery_section(None)

    assert section.status == "unknown"
    assert section.reason_codes == ["MPZP_DISCOVERY_ERROR"]
    assert section.acts == []


def test_html_parser_entry_point_delegates_to_the_planning_adapter() -> None:
    result = _parse_html_get_feature_info_response(_fixture("legnica_026201_1.0009.1319_4.html"))

    assert [act.resolution_number for act in result.acts] == ["XXVI/277/04"]
    assert result.zone_symbols == ("22 KD G1/2(Z1/4)",)
