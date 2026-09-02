from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from shapely.geometry import box

from app.main import app
from app.schemas.analyze import UtilitiesPreviewResult
from app.schemas.source import SourceMetadata
from app.services.analysis_orchestrator import _check_kiut_coverage_safely
from app.services.kiut_coverage import (
    check_kiut_coverage_projected,
    clear_kiut_coverage_cache,
)
from app.services.wms_tiles import WmsPreviewSource

KIUT_URL = "https://wms.example.test/kiut"


def _source() -> WmsPreviewSource:
    return WmsPreviewSource(
        source_key="kiut",
        base_url=KIUT_URL,
        layers="przewod_wodociagowy",
        version="1.1.1",
        min_zoom=17,
        max_zoom=20,
        tile_size=512,
        fresh_ttl_s=21600,
        stale_ttl_s=172800,
        upstream_concurrency=4,
        read_timeout_s=0.5,
        source_id="kiut_wms",
        label="Uzbrojenie terenu",
        attribution="KIUT, GUGiK",
        legal_note="Podgląd poglądowy.",
        info_url=KIUT_URL,
        catalog_status="production",
    )


@pytest.fixture(autouse=True)
async def empty_coverage_cache():
    await clear_kiut_coverage_cache()
    yield
    await clear_kiut_coverage_cache()


@pytest.mark.asyncio
async def test_html_feature_means_covered_and_extracts_county() -> None:
    html = """
    <html><body><table class="featureInfo">
      <tr><th>id</th><th>Nazwa powiatu</th></tr>
      <tr><td>1</td><td>powiat krakowski</td></tr>
    </table></body></html>
    """
    formats: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        info_format = request.url.params["info_format"]
        formats.append(info_format)
        assert request.url.params["request"] == "GetFeatureInfo"
        assert request.url.params["query_layers"] == "gesut"
        assert request.url.params["srs"] == "EPSG:2180"
        assert request.url.params["bbox"] == "499999.5,199999.5,500000.5,200000.5"
        if info_format == "text/xml":
            return httpx.Response(406, text="not acceptable")
        assert info_format == "text/html"
        return httpx.Response(200, text=html)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await check_kiut_coverage_projected(
            500000,
            200000,
            client=client,
            source=_source(),
        )

    assert formats == ["text/xml", "text/html"]
    assert result.coverage_status == "covered"
    assert result.county_name == "powiat krakowski"
    assert result.layer_available is True
    assert result.source.response_status == 200
    assert "odległości" in result.note


@pytest.mark.asyncio
async def test_xml_feature_means_covered_and_extracts_county() -> None:
    formats: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        formats.append(request.url.params["info_format"])
        return httpx.Response(
            200,
            text=(
                '<?xml version="1.0"?>'
                '<msGMLOutput xmlns:gml="http://www.opengis.net/gml">'
                "<gesut_layer><gesut_feature>"
                "<nazwa_powiatu>powiat bielski</nazwa_powiatu>"
                "</gesut_feature></gesut_layer></msGMLOutput>"
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await check_kiut_coverage_projected(
            500000,
            200000,
            client=client,
            source=_source(),
        )

    assert formats == ["text/xml"]
    assert result.coverage_status == "covered"
    assert result.county_name == "powiat bielski"


@pytest.mark.asyncio
async def test_xml_payload_on_html_format_is_parsed_as_xml() -> None:
    """KIUT na info_format=text/html bywa zwraca XML — parser HTML tego nie czyta."""

    formats: list[str] = []
    xml = (
        '<?xml version="1.0"?>'
        '<msGMLOutput xmlns:gml="http://www.opengis.net/gml">'
        "<gesut_layer><gesut_feature>"
        "<nazwa_powiatu>powiat cieszyński</nazwa_powiatu>"
        "</gesut_feature></gesut_layer></msGMLOutput>"
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        info_format = request.url.params["info_format"]
        formats.append(info_format)
        if info_format == "text/xml":
            return httpx.Response(400, text="unsupported format")
        return httpx.Response(200, text=xml)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await check_kiut_coverage_projected(
            500000,
            200000,
            client=client,
            source=_source(),
        )

    assert formats == ["text/xml", "text/html"]
    assert result.coverage_status == "covered"
    assert result.county_name == "powiat cieszyński"


@pytest.mark.asyncio
async def test_empty_success_response_means_not_covered() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await check_kiut_coverage_projected(
            500000,
            200000,
            client=client,
            source=_source(),
        )

    assert result.coverage_status == "not_covered"
    assert result.layer_available is False
    assert "nie jest dowodem braku sieci" in result.note


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "server_error"])
async def test_transport_failure_never_means_not_covered(failure: str) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if failure == "timeout":
            raise httpx.ReadTimeout("timeout", request=request)
        return httpx.Response(503, text="upstream error")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await check_kiut_coverage_projected(
            500000,
            200000,
            client=client,
            source=_source(),
        )

    assert result.coverage_status == "unknown"
    assert result.layer_available is False
    assert result.source.manual_review_required is True


@pytest.mark.asyncio
async def test_unrecognized_xml_falls_back_to_html() -> None:
    formats: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        info_format = request.url.params["info_format"]
        formats.append(info_format)
        if info_format == "text/xml":
            return httpx.Response(200, text="<SomethingElse>unusable</SomethingElse>")
        return httpx.Response(
            200,
            text="""
            <html><body><table class="featureInfo">
              <tr><th>Nazwa powiatu</th></tr>
              <tr><td>powiat poznański</td></tr>
            </table></body></html>
            """,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await check_kiut_coverage_projected(
            500000,
            200000,
            client=client,
            source=_source(),
        )

    assert formats == ["text/xml", "text/html"]
    assert result.coverage_status == "covered"
    assert result.county_name == "powiat poznański"


@pytest.mark.asyncio
async def test_malformed_fallback_response_means_unknown() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["info_format"] == "text/xml":
            return httpx.Response(200, text="not xml")
        return httpx.Response(200, text="<html><body>KIUT</body></html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await check_kiut_coverage_projected(
            500000,
            200000,
            client=client,
            source=_source(),
        )

    assert result.coverage_status == "unknown"


@pytest.mark.asyncio
async def test_county_teryt_cache_is_shared_inside_county() -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, text="")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        first = await check_kiut_coverage_projected(
            500000,
            200000,
            county_teryt="146101",
            client=client,
            source=_source(),
        )
        second = await check_kiut_coverage_projected(
            510000,
            210000,
            county_teryt="146199",
            client=client,
            source=_source(),
        )

    assert calls == 1
    assert first == second


@pytest.mark.asyncio
async def test_coverage_endpoint_exposes_three_state_contract() -> None:
    expected = UtilitiesPreviewResult(
        coverage_status="covered",
        county_name="powiat krakowski",
        layer_available=True,
        note="Podgląd poglądowy; brak obiektów nie oznacza braku sieci.",
        source=SourceMetadata(
            source_name="KIUT (GUGiK)",
            source_url=KIUT_URL,
            confidence=0.9,
            manual_review_required=False,
        ),
    )
    with patch(
        "app.routers.map_tiles.check_kiut_coverage_wgs84",
        new=AsyncMock(return_value=expected),
    ) as coverage_mock:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await client.get(
                "/api/v1/map/coverage/kiut",
                params={"lon": 19.94, "lat": 50.06},
            )

    assert response.status_code == 200
    assert response.json()["coverage_status"] == "covered"
    coverage_mock.assert_awaited_once_with(19.94, 50.06)


@pytest.mark.asyncio
async def test_coverage_endpoint_validates_coordinates_without_calling_upstream() -> (
    None
):
    with patch(
        "app.routers.map_tiles.check_kiut_coverage_wgs84",
        new=AsyncMock(),
    ) as coverage_mock:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await client.get(
                "/api/v1/map/coverage/kiut",
                params={"lon": 190, "lat": 50.06},
            )

    assert response.status_code == 422
    coverage_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_orchestrator_boundary_maps_unexpected_error_to_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coverage = AsyncMock(side_effect=RuntimeError("parser failure"))
    monkeypatch.setattr(
        "app.services.analysis_orchestrator.check_kiut_coverage_for_geometry",
        coverage,
    )

    result = await _check_kiut_coverage_safely(
        box(500000, 200000, 500100, 200100),
        "126101",
    )

    assert result.coverage_status == "unknown"
    assert result.layer_available is False
    coverage.assert_awaited_once()
    assert coverage.await_args.kwargs["county_teryt"] == "126101"
