import json
import socket

import httpx
import pytest
import respx
from shapely.geometry import Polygon

from app.core.data_sources import SourceNotRunnableError, get_catalog
from app.services.pog import (
    PogGminaSources,
    PogLayerNames,
    _build_get_feature_info_params,
    _parse_wms_response,
    discover_pog,
)

PARCEL = Polygon([(0, 0), (30, 0), (30, 10), (10, 10), (10, 30), (0, 30), (0, 0)])
WMS_URL = "https://geo.example.test/pog/wms"
RU_WMS_URL = next(
    resource.url
    for resource in get_catalog().get("pog_app").resources
    if resource.role == "ru_wms_preview"
)


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port: [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))
        ],
    )


def _feature_response(layer: str, status: str = "uchwalony") -> str:
    return json.dumps(
        {
            "features": [
                {
                    "properties": {
                        "status": status,
                        "numer_uchwaly": "XII/42/2026",
                        "data_uchwalenia": "2026-03-12",
                        "gml_url": f"https://bip.example.test/{layer}.gml",
                    }
                }
            ]
        }
    )


@pytest.mark.asyncio
@respx.mock
async def test_gmina_wms_returns_adopted_and_four_layer_sections() -> None:
    route = respx.get(WMS_URL).mock(
        side_effect=lambda request: httpx.Response(
            200,
            text=_feature_response(request.url.params["layers"]),
        )
    )
    layers = PogLayerNames(
        planning_act=("app",),
        downtown_area=("downtown",),
        ouz=("ouz",),
        planning_zones=("zones",),
    )

    result = await discover_pog(PARCEL, PogGminaSources(WMS_URL, layer_names=layers))

    assert result.status == "adopted"
    assert result.uchwala_nr == "XII/42/2026"
    assert result.uchwala_date == "2026-03-12"
    assert result.is_discovery_only is True
    assert result.source_metadata.source_url == WMS_URL
    assert result.source_metadata.manual_review_required is True
    assert {result.planning_act.status, result.downtown_area.status} == {"adopted"}
    assert result.ouz.status == "adopted"
    assert result.planning_zones.status == "adopted"
    assert {call.request.url.params["layers"] for call in route.calls} == {
        "app",
        "downtown",
        "ouz",
        "zones",
    }


@pytest.mark.asyncio
@respx.mock
async def test_centroid_is_not_the_only_sample_for_concave_parcel() -> None:
    route = respx.get(WMS_URL).mock(
        return_value=httpx.Response(200, json={"features": []})
    )
    layers = PogLayerNames(
        planning_act=("app",),
        downtown_area=("downtown",),
        ouz=("ouz",),
        planning_zones=("zones",),
    )

    await discover_pog(PARCEL, PogGminaSources(WMS_URL, layer_names=layers))

    bboxes = {call.request.url.params["bbox"] for call in route.calls}
    assert len(bboxes) >= 2


@pytest.mark.asyncio
@respx.mock
async def test_in_progress_status_is_preserved() -> None:
    respx.get(WMS_URL).mock(
        return_value=httpx.Response(200, text=_feature_response("pog", "projekt"))
    )
    one_candidate = PogLayerNames(
        planning_act=("app",),
        downtown_area=("downtown",),
        ouz=("ouz",),
        planning_zones=("zones",),
    )

    result = await discover_pog(
        PARCEL, PogGminaSources(WMS_URL, layer_names=one_candidate)
    )

    assert result.status == "in_progress"
    assert result.planning_act.status == "in_progress"


@pytest.mark.asyncio
@respx.mock
async def test_empty_wms_response_returns_not_available() -> None:
    respx.get(WMS_URL).mock(return_value=httpx.Response(200, json={"features": []}))

    result = await discover_pog(PARCEL, PogGminaSources(wms_url=WMS_URL))

    assert result.status == "not_available"
    assert all(
        section.status == "not_available"
        for section in (
            result.planning_act,
            result.downtown_area,
            result.ouz,
            result.planning_zones,
        )
    )


@pytest.mark.asyncio
@respx.mock
async def test_timeout_is_unknown_not_an_exception() -> None:
    respx.get(WMS_URL).mock(side_effect=httpx.TimeoutException("timeout"))

    result = await discover_pog(PARCEL, PogGminaSources(wms_url=WMS_URL))

    assert result.status == "unknown"
    assert any(warning.code == "POG_WMS_UNAVAILABLE" for warning in result.warnings)


@pytest.mark.asyncio
@respx.mock
async def test_malformed_wms_response_is_unknown() -> None:
    respx.get(WMS_URL).mock(return_value=httpx.Response(200, text="not-json"))

    result = await discover_pog(PARCEL, PogGminaSources(wms_url=WMS_URL))

    assert result.status == "unknown"


@pytest.mark.asyncio
@respx.mock
async def test_no_gmina_source_uses_confirmed_catalog_wms() -> None:
    route = respx.get(RU_WMS_URL).mock(
        return_value=httpx.Response(200, text=_feature_response("ru"))
    )

    result = await discover_pog(PARCEL, PogGminaSources(teryt="1465011"))

    assert result.status == "adopted"
    assert route.called
    assert result.source_metadata.source_url == RU_WMS_URL
    assert any(warning.code == "POG_RU_CATALOG_SOURCE" for warning in result.warnings)


@pytest.mark.asyncio
@respx.mock
async def test_catalog_guard_failure_is_unknown_not_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.pog.ensure_source_runnable",
        lambda _source_id: (_ for _ in ()).throw(SourceNotRunnableError("blocked")),
    )

    result = await discover_pog(PARCEL, PogGminaSources(teryt="1465011"))

    assert result.status == "unknown"
    assert result.source_metadata.source_url is None
    assert any(warning.code == "POG_NO_CONFIRMED_SOURCE" for warning in result.warnings)
    assert all(
        section.status == "unknown"
        for section in (
            result.planning_act,
            result.downtown_area,
            result.ouz,
            result.planning_zones,
        )
    )


def test_get_feature_info_uses_requested_layer_and_epsg_2180() -> None:
    params = _build_get_feature_info_params("custom_ouz", 100.0, 200.0)

    assert params["layers"] == "custom_ouz"
    assert params["query_layers"] == "custom_ouz"
    assert params["crs"] == "EPSG:2180"


def test_parse_unexpected_features_type_raises_controlled_parse_error() -> None:
    with pytest.raises(TypeError):
        _parse_wms_response('{"features": {}}')
