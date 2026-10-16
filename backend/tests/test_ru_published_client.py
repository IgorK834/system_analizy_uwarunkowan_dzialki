"""Klient publicznego API RU (wykaz + GML): paginacja, przepustnica, 429, limity i kody przyczyn (ADR-026)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.modules.imports.infrastructure.ogc_client import OgcClient, OgcClientConfig, OgcContractError
from app.modules.imports.infrastructure.ru_published import (
    RuDownloadError,
    RuPublishedClient,
    RuRateLimitedError,
    Throttle,
)

FIXTURES = Path(__file__).parent / "fixtures" / "ru_national"
BASE = "https://rejestr-urbanistyczny.gov.pl/api/public/published"


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _client(handler, clock: Clock, *, max_bytes: int = 8 * 1024 * 1024, interval: float = 1.2) -> RuPublishedClient:  # noqa: ANN001
    http = OgcClient(
        source_id="pog_app",
        config=OgcClientConfig(
            allowed_hosts=frozenset({"rejestr-urbanistyczny.gov.pl"}), max_response_bytes=max_bytes, retries=1,
            backoff_seconds=0.0,
        ),
        client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False),
        resolver=lambda _host: ["93.184.216.34"],
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    return RuPublishedClient(
        http, base_url=BASE, min_interval_seconds=interval, max_gml_bytes=max_bytes, sleep=clock.sleep,
        clock=clock.monotonic, rate_limit_backoff=(5.0, 15.0),
    )


def _listing_handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    number = body["page"]["number"]
    assert body["criteria"]["planTypeList"] == ["MUNICIPAL_GENERAL_PLAN"]
    assert body["criteria"]["publishedActVersions"] == "CURRENT" and body["page"]["size"] == 100
    return httpx.Response(200, content=(FIXTURES / f"listing_current_page_{number}.json").read_bytes())


def test_listing_walks_all_pages_with_spacing_between_requests() -> None:
    clock = Clock()
    listing = _client(_listing_handler, clock).list_acts("CURRENT")

    assert listing.complete and len(listing.acts) == 465 and listing.pages_fetched == 5
    # Pięć żądań rozstawionych co ≥ 1,2 s (pierwsze bez czekania).
    assert len(clock.sleeps) == 4 and all(value == pytest.approx(1.2) for value in clock.sleeps)


def test_listing_marks_a_truncated_server_answer_as_incomplete() -> None:
    clock = Clock()

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads((FIXTURES / "listing_current_page_1.json").read_bytes())
        payload["pageInfo"]["totalPages"] = 1
        payload["publishedActs"] = payload["publishedActs"][:50]
        return httpx.Response(200, json=payload)

    listing = _client(handler, clock).list_acts("CURRENT")
    assert not listing.complete and listing.incomplete_reason == "row_count_mismatch"


def test_listing_with_a_malformed_body_is_a_contract_error() -> None:
    clock = Clock()
    with pytest.raises(OgcContractError):
        _client(lambda request: httpx.Response(200, content=b"<html>"), clock).list_acts("CURRENT")


def test_rate_limit_is_waited_out_with_growing_backoff_and_counted() -> None:
    clock = Clock()
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= 2:
            return httpx.Response(429, text="Request blocked Exceeded requests/sec limit.")
        return httpx.Response(
            200, content=b"<gml/>", headers={"content-disposition": 'attachment; filename="a.gml"'}
        )

    client = _client(handler, clock)
    download = client.download_gml(42)

    assert download.filename == "a.gml" and download.content == b"<gml/>" and len(download.sha256) == 64
    assert download.url == f"{BASE}/42/download"
    assert client.stats == {"requests": 3, "rate_limited": 2}
    assert [value for value in clock.sleeps if value >= 5.0] == [5.0, 15.0]


def test_persistent_rate_limit_is_reported_as_rate_limited() -> None:
    clock = Clock()
    client = _client(lambda request: httpx.Response(429, text="blocked"), clock)
    with pytest.raises(RuDownloadError) as error:
        client.download_gml(1)
    assert error.value.code == "rate_limited"
    with pytest.raises(RuRateLimitedError):
        client.list_acts("CURRENT")


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(500), "transport"),
        (httpx.Response(404), "transport"),
        (httpx.Response(200, content=b"%PDF-1.4"), "not_xml"),
        (httpx.Response(200, content=b"<gml>" + b"x" * 2048 + b"</gml>"), "too_large"),
    ],
)
def test_download_failures_have_stable_codes(response: httpx.Response, code: str) -> None:
    clock = Clock()
    client = _client(lambda request: response, clock, max_bytes=1024)
    with pytest.raises(RuDownloadError) as error:
        client.download_gml(1)
    assert error.value.code == code


def test_download_accepts_a_utf8_bom_before_the_xml_declaration() -> None:
    # Realny przypadek z RU (Jejkowice, 2026-10-13): plik GML zaczyna się od BOM UTF-8 i jest poprawnym dokumentem.
    body = b"\xef\xbb\xbf<?xml version=\"1.0\" encoding=\"utf-8\"?><wfs:FeatureCollection/>"
    client = _client(lambda request: httpx.Response(200, content=body), Clock())
    assert client.download_gml(1152).content == body
    # BOM bez dokumentu za nim nadal nie jest XML.
    bom_only = _client(lambda request: httpx.Response(200, content=b"\xef\xbb\xbf%PDF"), Clock())
    with pytest.raises(RuDownloadError) as error:
        bom_only.download_gml(1)
    assert error.value.code == "not_xml"


def test_download_rejects_non_positive_identifier_and_foreign_host() -> None:
    clock = Clock()
    client = _client(lambda request: httpx.Response(200, content=b"<gml/>"), clock)
    with pytest.raises(ValueError):
        client.download_gml(0)
    foreign = RuPublishedClient(
        client._client, base_url="https://example.com/api/public/published", sleep=clock.sleep, clock=clock.monotonic
    )
    with pytest.raises(RuDownloadError) as error:
        foreign.download_gml(1)
    assert error.value.code == "transport"  # host spoza allowlisty


def test_throttle_spaces_concurrent_starts_without_losing_the_first() -> None:
    clock = Clock()
    throttle = Throttle(1.0, clock=clock.monotonic, sleep=clock.sleep)
    for _ in range(3):
        throttle.wait()
    assert clock.sleeps == [pytest.approx(1.0), pytest.approx(1.0)]
