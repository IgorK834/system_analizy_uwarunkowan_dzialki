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


LEGAL_FORCE = "http://inspire.ec.europa.eu/codelist/ProcessStepGeneralValue/legalForce"
LAYERS = PogLayerNames(
    planning_act=("app",),
    downtown_area=("downtown",),
    ouz=("ouz",),
    planning_zones=("zones",),
)


def _feature_response(layer: str, status: str | None = LEGAL_FORCE) -> str:
    properties: dict[str, object] = {
        "numer_uchwaly": "XII/42/2026",
        "data_uchwalenia": "2026-03-12",
        "gml_url": f"https://bip.example.test/{layer}.gml",
    }
    if status is not None:
        properties["status"] = status
    return json.dumps({"features": [{"properties": properties}]})


@pytest.mark.asyncio
@respx.mock
async def test_gmina_wms_official_code_gives_binding_and_four_layer_sections() -> None:
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

    assert result.legal_status == "binding"
    assert result.status == "binding"
    assert result.source_responded is True
    assert result.legal_evidence is not None and result.legal_evidence.official
    assert result.legal_evidence.raw_value == LEGAL_FORCE
    assert result.uchwala_nr == "XII/42/2026"
    assert result.uchwala_date == "2026-03-12"
    assert result.is_discovery_only is True
    assert result.source_metadata.source_url == WMS_URL
    assert result.source_metadata.manual_review_required is True
    assert {result.planning_act.status, result.downtown_area.status} == {"found"}
    assert result.ouz.status == "found"
    assert result.planning_zones.status == "found"
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
@pytest.mark.parametrize(
    ("raw_status", "expected"),
    [
        ("w opracowaniu", "in_progress"),
        ("http://inspire.ec.europa.eu/codelist/ProcessStepGeneralValue/adoption", "project"),
        ("http://inspire.ec.europa.eu/codelist/ProcessStepGeneralValue/obsolete", "superseded"),
    ],
)
async def test_non_binding_official_status_is_preserved(raw_status: str, expected: str) -> None:
    respx.get(WMS_URL).mock(
        return_value=httpx.Response(200, text=_feature_response("pog", raw_status))
    )

    result = await discover_pog(PARCEL, PogGminaSources(WMS_URL, layer_names=LAYERS))

    assert result.legal_status == expected
    assert result.planning_act.status == "found"
    assert result.planning_act.raw_legal_status == raw_status


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("raw_status", [None, "uchwalony", "status testowy"])
async def test_feature_without_official_code_is_unknown_not_adopted(
    raw_status: str | None,
) -> None:
    respx.get(WMS_URL).mock(
        return_value=httpx.Response(200, text=_feature_response("pog", raw_status))
    )

    result = await discover_pog(PARCEL, PogGminaSources(WMS_URL, layer_names=LAYERS))

    # Obecność obiektu ani słowo „uchwalony” nie są dowodem obowiązywania.
    assert result.legal_status == "unknown"
    assert result.legal_evidence is None
    assert result.planning_act.status == "found"
    assert any(warning.code == "POG_STATUS_NOT_OFFICIAL" for warning in result.warnings)


@pytest.mark.asyncio
@respx.mock
async def test_empty_wms_response_is_empty_coverage_not_absence() -> None:
    respx.get(WMS_URL).mock(return_value=httpx.Response(200, json={"features": []}))

    result = await discover_pog(PARCEL, PogGminaSources(wms_url=WMS_URL))

    assert result.legal_status == "unknown"
    assert result.source_responded is True
    assert all(
        section.status == "empty"
        for section in (
            result.planning_act,
            result.downtown_area,
            result.ouz,
            result.planning_zones,
        )
    )
    empty = next(w for w in result.warnings if w.code == "POG_NOT_FOUND_IN_SOURCE")
    assert "nie oznacza braku planu" in empty.message


@pytest.mark.asyncio
@respx.mock
async def test_timeout_is_unknown_not_an_exception() -> None:
    respx.get(WMS_URL).mock(side_effect=httpx.TimeoutException("timeout"))

    result = await discover_pog(PARCEL, PogGminaSources(wms_url=WMS_URL))

    assert result.legal_status == "unknown"
    assert result.source_responded is False
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
        return_value=httpx.Response(200, text=_feature_response("ru", status=None))
    )

    result = await discover_pog(PARCEL, PogGminaSources(teryt="1465011"))

    # Bez atrybutu statusu urzędowy kod niesie nazwa warstwy RU.
    assert result.legal_status == "binding"
    assert result.planning_act.matched_wms_layer == "APP.POG.PrawnieWiazacyLubRealizowany"
    assert result.legal_evidence is not None
    assert result.legal_evidence.raw_value == "APP.POG.PrawnieWiazacyLubRealizowany"
    assert result.source_metadata.source_name == "REJESTR_URBANISTYCZNY_WMS"
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
    assert result.source_responded is False
    assert any(warning.code == "POG_NO_CONFIRMED_SOURCE" for warning in result.warnings)
    assert all(
        section.status == "unavailable"
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


@pytest.mark.asyncio
@respx.mock
async def test_bip_text_never_sets_legal_status() -> None:
    bip_url = "https://bip.example.test/pog"
    respx.get(bip_url).mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=(
                "<html><body>Plan ogólny uchwalony i obowiązujący "
                '<a href="/pog.gml">GML</a></body></html>'
            ),
        )
    )

    result = await discover_pog(PARCEL, PogGminaSources(bip_url=bip_url))

    assert result.legal_status == "unknown"
    assert result.source_responded is True
    assert result.links == ["https://bip.example.test/pog.gml"]
    assert any(w.code == "POG_BIP_DISCOVERY_LIMITED" for w in result.warnings)


@pytest.mark.asyncio
@respx.mock
async def test_merge_prefers_source_with_official_status() -> None:
    other_wms = "https://geo2.example.test/pog/wms"
    respx.get(WMS_URL).mock(
        return_value=httpx.Response(200, text=_feature_response("a", status=None))
    )
    respx.get(other_wms).mock(
        return_value=httpx.Response(200, text=_feature_response("b", "w opracowaniu"))
    )

    result = await discover_pog(
        PARCEL,
        [
            PogGminaSources(WMS_URL, layer_names=LAYERS),
            PogGminaSources(other_wms, layer_names=LAYERS),
        ],
    )

    assert result.legal_status == "in_progress"
    assert result.source_metadata.source_url == other_wms
    assert set(result.links) == {
        "https://bip.example.test/a.gml",
        "https://bip.example.test/b.gml",
    }
