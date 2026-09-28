"""Offline testy operacji WCS 2.0.1 wspólnego klienta OGC (BK-102/BK-302).

Odpowiedzi usługi GUGiK są zamrożone w ``tests/fixtures/terrain`` (realne
zapytania 2026-09-28) — zwykłe CI nie łączy się z internetem.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from app.modules.imports.infrastructure.ogc_client import (
    OgcClient,
    OgcClientConfig,
    OgcContractError,
    OgcExceptionReportError,
    OgcLimitError,
    OgcTransportError,
)

FIXTURES = Path(__file__).parent / "fixtures" / "terrain"
URL = "https://mapy.geoportal.gov.pl/wss/service/PZGIK/NMT/GRID1/WCS/DigitalTerrainModelFormatTIFF"
COVERAGE = "DTM_PL-KRON86-NH_TIFF"
PUBLIC_IP = "93.184.216.34"
SUBSETS = (("x", 637000.343266, 637100.343266), ("y", 486000.66941, 486100.66941))


def _client(**overrides: object) -> OgcClient:
    defaults: dict[str, object] = {
        "allowed_hosts": frozenset({"mapy.geoportal.gov.pl"}),
        "retries": 0,
        "backoff_seconds": 0,
        "max_response_bytes": 2_000_000,
        "max_total_bytes": 5_000_000,
    }
    defaults.update(overrides)
    return OgcClient(
        source_id="nmt_wcs",
        config=OgcClientConfig(**defaults),
        resolver=lambda _host: (PUBLIC_IP,),
        sleep=lambda _seconds: None,
    )


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@respx.mock
def test_describe_coverage_returns_verified_xml_with_provenance() -> None:
    route = respx.get(URL).mock(
        return_value=httpx.Response(
            200,
            content=_fixture("wcs_describecoverage.xml"),
            headers={"content-type": "text/xml; charset=UTF-8"},
        )
    )

    result = _client().fetch_wcs_description(URL, coverage_id=COVERAGE)

    params = route.calls.last.request.url.params
    assert (params["service"], params["version"], params["request"]) == (
        "WCS",
        "2.0.1",
        "DescribeCoverage",
    )
    assert params["coverageId"] == COVERAGE
    assert result.source.operation == "WCS:DescribeCoverage"
    assert result.source.content_hash is not None
    assert result.artifact == _fixture("wcs_describecoverage.xml")


@respx.mock
def test_describe_unknown_coverage_maps_exception_code() -> None:
    respx.get(URL).mock(
        return_value=httpx.Response(
            404,
            content=_fixture("wcs_exception_no_such_coverage.xml"),
            headers={"content-type": "text/xml; charset=UTF-8"},
        )
    )

    with pytest.raises(OgcExceptionReportError) as raised:
        _client().fetch_wcs_description(URL, coverage_id="NOPE")

    assert raised.value.exception_code == "NoSuchCoverage"
    assert raised.value.http_status == 404
    assert raised.value.source.error_code == "contract"
    assert raised.value.source.content_hash is not None


@respx.mock
def test_get_coverage_sends_repeated_subsets_and_returns_geotiff() -> None:
    route = respx.get(URL).mock(
        return_value=httpx.Response(
            200,
            content=_fixture("wcs_getcoverage_warszawa_aligned.tif"),
            headers={"content-type": "image/tiff"},
        )
    )

    result = _client().fetch_wcs_coverage(URL, coverage_id=COVERAGE, subsets=SUBSETS)

    request = route.calls.last.request
    assert request.url.params.get_list("subset") == [
        "x(637000.343266,637100.343266)",
        "y(486000.66941,486100.66941)",
    ]
    assert request.url.params["format"] == "image/tiff"
    assert request.url.params["request"] == "GetCoverage"
    assert result.source.operation == "WCS:GetCoverage"
    assert result.complete is True
    assert result.artifact.startswith(b"II*\x00")


@respx.mock
def test_extent_error_with_http_400_is_explicit_exception_report() -> None:
    respx.get(URL).mock(
        return_value=httpx.Response(
            400,
            content=_fixture("wcs_exception_extent.xml"),
            headers={"content-type": "text/xml; charset=UTF-8"},
        )
    )

    with pytest.raises(OgcExceptionReportError) as raised:
        _client().fetch_wcs_coverage(URL, coverage_id=COVERAGE, subsets=SUBSETS)

    assert raised.value.exception_code == "ExtentError"
    assert raised.value.http_status == 400
    assert "does not intersect" in str(raised.value)


@respx.mock
def test_exception_report_with_http_200_is_not_a_raster() -> None:
    respx.get(URL).mock(
        return_value=httpx.Response(
            200,
            content=_fixture("wcs_exception_extent.xml"),
            headers={"content-type": "application/xml"},
        )
    )

    with pytest.raises(OgcExceptionReportError):
        _client().fetch_wcs_coverage(URL, coverage_id=COVERAGE, subsets=SUBSETS)


@respx.mock
@pytest.mark.parametrize(
    ("status", "content", "content_type", "error"),
    [
        (200, b"not a tiff", "image/tiff", OgcContractError),
        (200, b"\x89PNG", "image/png", OgcContractError),
        (400, b"plain", "text/plain", OgcTransportError),
        (400, b"<html>", "text/xml", OgcContractError),
        (400, b"<root/>", "text/xml", OgcContractError),
        (500, b"", "text/plain", OgcTransportError),
    ],
)
def test_invalid_coverage_responses_are_rejected(
    status: int, content: bytes, content_type: str, error: type[Exception]
) -> None:
    respx.get(URL).mock(
        return_value=httpx.Response(status, content=content, headers={"content-type": content_type})
    )

    with pytest.raises(error):
        _client().fetch_wcs_coverage(URL, coverage_id=COVERAGE, subsets=SUBSETS)


@respx.mock
def test_describe_200_without_xml_is_contract_error() -> None:
    respx.get(URL).mock(
        return_value=httpx.Response(200, content=b"{}", headers={"content-type": "application/json"})
    )

    with pytest.raises(OgcContractError):
        _client().fetch_wcs_description(URL, coverage_id=COVERAGE)


@respx.mock
def test_describe_http_400_without_xml_is_transport_error() -> None:
    respx.get(URL).mock(
        return_value=httpx.Response(400, content=b"bad", headers={"content-type": "text/plain"})
    )

    with pytest.raises(OgcTransportError):
        _client().fetch_wcs_description(URL, coverage_id=COVERAGE)


@respx.mock
def test_coverage_byte_limit_and_timeout() -> None:
    respx.get(URL).mock(
        return_value=httpx.Response(
            200,
            content=_fixture("wcs_getcoverage_warszawa_aligned.tif"),
            headers={"content-type": "image/tiff"},
        )
    )
    with pytest.raises(OgcLimitError):
        _client(max_response_bytes=1_000).fetch_wcs_coverage(
            URL, coverage_id=COVERAGE, subsets=SUBSETS
        )

    respx.get(URL).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(OgcTransportError) as raised:
        _client().fetch_wcs_coverage(URL, coverage_id=COVERAGE, subsets=SUBSETS)
    assert isinstance(raised.value.__cause__, httpx.TimeoutException)


def test_subset_validation_and_host_allowlist() -> None:
    with pytest.raises(ValueError):
        _client().fetch_wcs_coverage(URL, coverage_id=COVERAGE, subsets=())
    with pytest.raises(ValueError):
        _client().fetch_wcs_coverage(URL, coverage_id=COVERAGE, subsets=(("x", 2.0, 1.0),))
    with pytest.raises(OgcTransportError):
        _client(allowed_hosts=frozenset({"inny.gov.pl"})).fetch_wcs_coverage(
            URL, coverage_id=COVERAGE, subsets=SUBSETS
        )
