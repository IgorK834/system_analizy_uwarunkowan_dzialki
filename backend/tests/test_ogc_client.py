"""Offline testy wspólnego, ograniczonego klienta OGC."""

from __future__ import annotations

from xml.etree import ElementTree

import httpx
import pytest
import respx

from app.modules.imports.infrastructure.ogc_client import (
    OgcClient,
    OgcClientConfig,
    OgcContractError,
    OgcLimitError,
    OgcTransportError,
)
from app.modules.imports.infrastructure.wfs import (
    WfsFetchError,
    WfsFetcher,
    WfsResource,
)

WFS_URL = "https://ogc.example.gov.pl/wfs"
WMS_URL = "https://ogc.example.gov.pl/wms"
CSW_URL = "https://ogc.example.gov.pl/csw"
PUBLIC_IP = "93.184.216.34"


def _client(**overrides: object) -> OgcClient:
    defaults: dict[str, object] = {
        "allowed_hosts": frozenset({"ogc.example.gov.pl"}),
        "retries": 0,
        "backoff_seconds": 0,
        "max_response_bytes": 2_000_000,
        "max_total_bytes": 5_000_000,
    }
    defaults.update(overrides)
    return OgcClient(
        source_id="pog_app",
        config=OgcClientConfig(**defaults),
        resolver=lambda _host: (PUBLIC_IP,),
        sleep=lambda _seconds: None,
    )


def _wfs_page(ids: list[int], *, matched: str = "unknown") -> bytes:
    members = "".join(
        f'<wfs:member><app:Feature gml:id="feature-{value}" /></wfs:member>'
        for value in ids
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" '
        'xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:app="urn:test" '
        f'numberMatched="{matched}" numberReturned="{len(ids)}">'
        f"{members}</wfs:FeatureCollection>"
    ).encode()


def _feature_ids(features: tuple[bytes, ...]) -> set[str]:
    return {
        next(
            value
            for key, value in ElementTree.fromstring(item).attrib.items()
            if key.endswith("id")
        )
        for item in features
    }


@respx.mock
def test_wfs_server_capping_page_size_below_count_is_fully_paginated() -> None:
    """RU zawsze zwraca 10 obiektów mimo count=100; numberMatched wskazuje resztę."""
    total = 25

    def response(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params["startIndex"])
        ids = list(range(start, min(start + 10, total)))
        return httpx.Response(
            200,
            content=_wfs_page(ids, matched=str(total)),
            headers={"content-type": "application/gml+xml; version=3.2"},
        )

    route = respx.get(WFS_URL).mock(side_effect=response)
    with _client() as client:
        result = client.fetch_wfs(
            WFS_URL, type_name="app:Feature", srs_name="EPSG:2180"
        )

    assert result.complete is True
    assert len(_feature_ids(result.features)) == total
    assert [call.request.url.params["startIndex"] for call in route.calls] == [
        "0",
        "10",
        "20",
    ]


@respx.mock
def test_wfs_page_retries_transient_http_400() -> None:
    calls = {"n": 0}

    def response(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 2:
            return httpx.Response(400, content=b"Invalid request")
        start = int(request.url.params["startIndex"])
        ids = list(range(start, min(start + 10, 25)))
        return httpx.Response(
            200,
            content=_wfs_page(ids, matched="25"),
            headers={"content-type": "application/gml+xml; version=3.2"},
        )

    respx.get(WFS_URL).mock(side_effect=response)
    with _client(retries=2) as client:
        result = client.fetch_wfs(
            WFS_URL, type_name="app:Feature", srs_name="EPSG:2180"
        )

    assert result.complete is True
    assert len(_feature_ids(result.features)) == 25


@respx.mock
def test_wfs_201_features_uses_three_pages_and_returns_unique_ids() -> None:
    def response(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params["startIndex"])
        ids = list(range(start, min(start + 100, 201)))
        return httpx.Response(
            200,
            content=_wfs_page(ids),
            headers={"content-type": "application/gml+xml; version=3.2"},
        )

    route = respx.get(WFS_URL).mock(side_effect=response)
    with _client() as client:
        result = client.fetch_wfs(
            WFS_URL, type_name="app:Feature", srs_name="EPSG:2180"
        )

    assert result.complete is True
    assert len(result.features) == 201
    assert len(_feature_ids(result.features)) == 201
    assert [call.request.url.params["startIndex"] for call in route.calls] == [
        "0",
        "100",
        "200",
    ]
    assert all(call.request.url.params["count"] == "100" for call in route.calls)
    assert all(call.request.url.params["version"] == "2.0.0" for call in route.calls)
    assert result.source.complete is True
    assert result.source.operation == "WFS:GetFeature"


@respx.mock
def test_repeated_wfs_page_stops_as_incomplete() -> None:
    page = _wfs_page(list(range(100)))
    route = respx.get(WFS_URL).mock(
        return_value=httpx.Response(
            200, content=page, headers={"content-type": "application/xml"}
        )
    )

    with _client() as client:
        result = client.fetch_wfs(
            WFS_URL, type_name="app:Feature", srs_name="EPSG:2180"
        )

    assert result.complete is False
    assert len(result.features) == 100
    assert route.call_count == 2
    assert result.source.complete is False


@respx.mock
def test_timeout_is_retried_and_preserves_error_provenance() -> None:
    route = respx.get(WFS_URL).mock(side_effect=httpx.ReadTimeout("too slow"))

    with _client(retries=1) as client, pytest.raises(OgcTransportError) as caught:
        client.fetch_wfs(WFS_URL, type_name="app:Feature", srs_name="EPSG:2180")

    assert route.call_count == 2
    assert caught.value.source.source_id == "pog_app"
    assert caught.value.source.error_code == "transport"
    assert caught.value.source.complete is False


@respx.mock
def test_http_5xx_is_retried_then_reported() -> None:
    route = respx.get(WFS_URL).mock(return_value=httpx.Response(503))

    with _client(retries=1) as client, pytest.raises(OgcTransportError, match="503"):
        client.fetch_wfs(WFS_URL, type_name="app:Feature", srs_name="EPSG:2180")

    assert route.call_count == 2


@respx.mock
def test_http_200_exception_report_is_contract_error() -> None:
    report = b"""<?xml version='1.0'?>
    <ows:ExceptionReport xmlns:ows='http://www.opengis.net/ows/1.1'>
      <ows:Exception><ows:ExceptionText>bad filter</ows:ExceptionText></ows:Exception>
    </ows:ExceptionReport>"""
    respx.get(WFS_URL).mock(
        return_value=httpx.Response(
            200, content=report, headers={"content-type": "application/xml"}
        )
    )

    with _client() as client, pytest.raises(OgcContractError, match="bad filter"):
        client.fetch_wfs(WFS_URL, type_name="app:Feature", srs_name="EPSG:2180")


@pytest.mark.parametrize(
    ("content", "content_type", "message"),
    [
        (b"<broken>", "application/xml", "Niepoprawny XML"),
        (_wfs_page([]), "text/html", "Content-Type"),
        (
            b"<!DOCTYPE x [<!ENTITY e 'boom'>]><x>&e;</x>",
            "application/xml",
            "DTD lub encji",
        ),
    ],
)
@respx.mock
def test_invalid_xml_mime_and_entity_are_rejected(
    content: bytes, content_type: str, message: str
) -> None:
    respx.get(WFS_URL).mock(
        return_value=httpx.Response(
            200, content=content, headers={"content-type": content_type}
        )
    )

    with _client() as client, pytest.raises(OgcContractError, match=message):
        client.fetch_wfs(WFS_URL, type_name="app:Feature", srs_name="EPSG:2180")


@respx.mock
def test_byte_limit_raises_typed_error() -> None:
    respx.get(WFS_URL).mock(
        return_value=httpx.Response(
            200,
            content=_wfs_page(list(range(20))),
            headers={"content-type": "application/xml"},
        )
    )

    with _client(max_response_bytes=100) as client, pytest.raises(OgcLimitError):
        client.fetch_wfs(WFS_URL, type_name="app:Feature", srs_name="EPSG:2180")


@respx.mock
def test_xml_depth_limit_is_enforced() -> None:
    respx.get(WFS_URL).mock(
        return_value=httpx.Response(
            200,
            content=b"<a><b><c><d /></c></b></a>",
            headers={"content-type": "application/xml"},
        )
    )

    with (
        _client(max_xml_depth=3) as client,
        pytest.raises(OgcContractError, match="głębokości"),
    ):
        client.fetch_wfs(WFS_URL, type_name="app:Feature", srs_name="EPSG:2180")


@respx.mock
def test_page_limit_has_incomplete_partial_result() -> None:
    def response(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params["startIndex"])
        return httpx.Response(
            200,
            content=_wfs_page(list(range(start, start + 100))),
            headers={"content-type": "application/xml"},
        )

    route = respx.get(WFS_URL).mock(side_effect=response)
    with _client(max_pages=2) as client, pytest.raises(OgcLimitError) as caught:
        client.fetch_wfs(WFS_URL, type_name="app:Feature", srs_name="EPSG:2180")

    assert route.call_count == 2
    assert caught.value.partial is not None
    assert caught.value.partial.complete is False
    assert len(caught.value.partial.features) == 200


@respx.mock
def test_redirect_to_disallowed_host_is_rejected_before_following() -> None:
    initial = respx.get(WFS_URL).mock(
        return_value=httpx.Response(
            302, headers={"location": "https://evil.example.test/steal"}
        )
    )
    foreign = respx.get("https://evil.example.test/steal").mock(
        return_value=httpx.Response(200, content=_wfs_page([]))
    )

    with _client() as client, pytest.raises(OgcTransportError, match="allowliście"):
        client.fetch_wfs(WFS_URL, type_name="app:Feature", srs_name="EPSG:2180")

    assert initial.call_count == 1
    assert foreign.call_count == 0


def test_private_dns_address_is_rejected() -> None:
    client = OgcClient(
        source_id="pog_app",
        config=OgcClientConfig(allowed_hosts=frozenset({"ogc.example.gov.pl"})),
        resolver=lambda _host: ("127.0.0.1",),
    )
    with client, pytest.raises(OgcTransportError, match="niedozwolonego IP"):
        client.fetch_wfs(WFS_URL, type_name="app:Feature", srs_name="EPSG:2180")


@respx.mock
def test_wms_uses_explicit_version_and_axis_table() -> None:
    route = respx.get(WMS_URL).mock(
        return_value=httpx.Response(
            200, content=b"png", headers={"content-type": "image/png"}
        )
    )
    with _client() as client:
        result = client.fetch_wms(
            WMS_URL,
            layers=("APP.POG.SW.PrawnieWiazacyLubRealizowany",),
            bbox=(10.0, 20.0, 30.0, 40.0),
            crs="EPSG:4326",
            width=256,
            height=256,
        )

    assert result.artifact == b"png"
    assert route.calls.last.request.url.params["version"] == "1.3.0"
    assert route.calls.last.request.url.params["bbox"] == "20,10,40,30"
    assert OgcClient.wms_bbox("CRS:84", (10, 20, 30, 40)) == "10,20,30,40"
    assert OgcClient.wms_bbox("EPSG:2180", (10, 20, 30, 40)) == "10,20,30,40"
    with pytest.raises(ValueError, match="reguły osi"):
        OgcClient.wms_bbox("EPSG:3857", (10, 20, 30, 40))


@respx.mock
def test_csw_uses_202_and_follows_next_record() -> None:
    def response(request: httpx.Request) -> httpx.Response:
        start = request.url.params["startPosition"]
        if start == "1":
            records = "<csw:Record><dc:identifier>a</dc:identifier></csw:Record>"
            next_record = "2"
        else:
            records = "<csw:Record><dc:identifier>b</dc:identifier></csw:Record>"
            next_record = "0"
        body = (
            '<csw:GetRecordsResponse xmlns:csw="http://www.opengis.net/cat/csw/2.0.2" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f'<csw:SearchResults nextRecord="{next_record}" '
            f'numberOfRecordsReturned="1">{records}</csw:SearchResults>'
            "</csw:GetRecordsResponse>"
        )
        return httpx.Response(
            200, content=body.encode(), headers={"content-type": "application/xml"}
        )

    route = respx.get(CSW_URL).mock(side_effect=response)
    with _client() as client:
        result = client.fetch_csw(CSW_URL)

    assert result.complete is True
    assert len(result.features) == 2
    assert [call.request.url.params["startPosition"] for call in route.calls] == [
        "1",
        "2",
    ]
    assert all(call.request.url.params["version"] == "2.0.2" for call in route.calls)


@respx.mock
def test_wfs_fetcher_propagates_incomplete_as_explicit_failure() -> None:
    page = _wfs_page(list(range(100)))
    respx.get(WFS_URL).mock(
        return_value=httpx.Response(
            200, content=page, headers={"content-type": "application/xml"}
        )
    )
    resource = WfsResource("zones", WFS_URL, "app:Feature", "EPSG:2180", {}, "pog_app")

    with _client() as client, pytest.raises(WfsFetchError) as caught:
        WfsFetcher(ogc_client=client).fetch((resource,))

    assert caught.value.partial is not None
    assert caught.value.partial.complete is False
    assert caught.value.source is not None
