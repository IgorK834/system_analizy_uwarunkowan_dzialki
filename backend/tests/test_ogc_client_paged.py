"""Tryb stronicowany klienta WFS (AU-106): równoległe strony, wznawianie z magazynu, postęp i przerwanie."""

from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path
from xml.etree import ElementTree

import httpx
import pytest
import respx

from app.modules.imports.infrastructure.ogc_client import (
    OgcCancelled,
    OgcClient,
    OgcClientConfig,
    OgcLimitError,
    OgcTransportError,
    WfsPageEvent,
)
from app.modules.imports.infrastructure.page_store import FilePageStore

WFS_URL = "https://ogc.example.gov.pl/wfs"
PUBLIC_IP = "93.184.216.34"
PAGE = 10


def _client(**overrides: object) -> OgcClient:
    defaults: dict[str, object] = {
        "allowed_hosts": frozenset({"ogc.example.gov.pl"}),
        "retries": 0,
        "backoff_seconds": 0,
        "max_response_bytes": 2_000_000,
        "max_total_bytes": 50_000_000,
    }
    defaults.update(overrides)
    return OgcClient(
        source_id="pog_app",
        config=OgcClientConfig(**defaults),  # type: ignore[arg-type]
        resolver=lambda _host: (PUBLIC_IP,),
        sleep=lambda _seconds: None,
    )


def _page(start: int, total: int, *, stamp: str = "t0", declared: str | None = None, size: int = PAGE) -> bytes:
    ids = range(start, min(start + size, total))
    members = "".join(
        f'<wfs:member><app:Feature gml:id="feature-{value:05d}"><app:n>{value}</app:n></app:Feature></wfs:member>'
        for value in ids
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" '
        'xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:app="urn:test" '
        f'numberMatched="{declared or total}" numberReturned="{len(ids)}" timeStamp="{stamp}" '
        f'next="{WFS_URL}?startIndex={start + PAGE}">'
        f"{members}</wfs:FeatureCollection>"
    ).encode()


class Server:
    """Atrapa RU: strony po 10 obiektów, ślad żądań, opcjonalne opóźnienia, awarie i licznik równoległości."""

    def __init__(self, total: int, *, stamp: str = "t0") -> None:
        self.total = total
        self.stamp = stamp
        self.requests: list[int] = []
        self.fail_at: set[int] = set()
        self.delay: dict[int, float] = {}
        self.override: dict[int, bytes] = {}
        self.short: dict[int, int] = {}
        self.in_flight = 0
        self.max_in_flight = 0
        self.lock = threading.Lock()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        start = int(request.url.params["startIndex"])
        with self.lock:
            self.requests.append(start)
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            time.sleep(self.delay.get(start, 0.0))
            if start in self.fail_at:
                return httpx.Response(503)
            body = self.override.get(start) or _page(
                start, self.total, stamp=self.stamp, size=self.short.get(start, PAGE)
            )
            return httpx.Response(200, content=body, headers={"content-type": "application/gml+xml; version=3.2"})
        finally:
            with self.lock:
                self.in_flight -= 1


def _fetch(server: Server, **kwargs: object):  # noqa: ANN202
    with respx.mock:
        respx.get(WFS_URL).mock(side_effect=server)
        with _client() as client:
            return client.fetch_wfs(WFS_URL, type_name="app:Feature", srs_name="EPSG:2180", **kwargs)


def _numbers(features: tuple[bytes, ...]) -> list[int]:
    return [int(ElementTree.fromstring(item).find("{urn:test}n").text) for item in features]


def test_parallel_fetch_returns_every_object_once_in_service_order() -> None:
    server = Server(95)
    # Późniejsze strony odpowiadają szybciej niż wcześniejsze: kolejność wyniku nie zależy od zakończenia żądań.
    server.delay = {10: 0.08, 20: 0.06, 30: 0.04}

    result = _fetch(server, concurrency=4)

    assert result.complete is True
    assert _numbers(result.features) == list(range(95))
    assert sorted(server.requests) == list(range(0, 100, PAGE))
    assert server.requests[0] == 0  # pierwsza strona ujawnia numberMatched, reszta dopiero po niej


def test_parallelism_is_bounded_by_the_politeness_limit_and_is_actually_used() -> None:
    server = Server(200)
    for start in range(10, 200, PAGE):
        server.delay[start] = 0.03

    _fetch(server, concurrency=4)

    assert server.max_in_flight == 4


def test_single_concurrency_with_progress_keeps_requests_sequential() -> None:
    server = Server(60)
    for start in range(10, 60, PAGE):
        server.delay[start] = 0.01

    _fetch(server, on_page=lambda _event: None)

    assert server.max_in_flight == 1 and server.requests == list(range(0, 60, PAGE))


def test_concurrency_is_capped_at_eight() -> None:
    server = Server(400)
    for start in range(10, 400, PAGE):
        server.delay[start] = 0.02

    _fetch(server, concurrency=50)

    assert server.max_in_flight <= 8


def test_artifact_is_content_addressed_and_ignores_volatile_response_attributes() -> None:
    """Dwa pobrania tych samych danych w różnych chwilach (inne ``timeStamp`` i ``next``) dają ten sam artefakt."""
    first = _fetch(Server(45, stamp="2026-10-12T11:00:00Z"), concurrency=4)
    second = _fetch(Server(45, stamp="2026-10-12T11:07:31Z"))  # tryb sekwencyjny, inny czas
    paged_other = _fetch(Server(45, stamp="2026-10-13T00:00:00Z"), concurrency=3)

    assert hashlib.sha256(first.artifact).hexdigest() == hashlib.sha256(second.artifact).hexdigest()
    assert first.artifact == paged_other.artifact
    root = ElementTree.fromstring(first.artifact)
    assert "timeStamp" not in root.attrib and "next" not in root.attrib
    assert root.attrib["numberMatched"] == root.attrib["numberReturned"] == "45"


def test_progress_events_report_pages_and_totals() -> None:
    events: list[WfsPageEvent] = []
    lock = threading.Lock()

    def collect(event: WfsPageEvent) -> None:
        with lock:
            events.append(event)

    _fetch(Server(35), concurrency=2, on_page=collect)

    assert len(events) == 4
    assert {event.pages_total for event in events} == {4}
    assert all(event.matched == 35 and event.type_name == "app:Feature" for event in events)
    assert max(event.fetched for event in events) == 35
    assert max(event.pages_done for event in events) == 4
    assert not any(event.from_store for event in events)


def test_failure_of_one_page_raises_the_real_cause_and_stops_scheduling_new_pages() -> None:
    server = Server(300)
    server.fail_at = {40}
    for start in range(10, 300, PAGE):
        server.delay[start] = 0.01

    with pytest.raises(OgcTransportError, match="HTTP 503"):
        _fetch(server, concurrency=4)

    assert len(server.requests) < 30  # po błędzie nie pobieramy reszty setek stron


def test_short_page_in_the_middle_falls_back_to_sequential_continuation() -> None:
    server = Server(40)
    server.short[10] = 7  # strona od indeksu 10 zwraca tylko 7 obiektów (10..16); kolejny obiekt ma indeks 17

    result = _fetch(server, concurrency=4)

    assert result.complete is True
    assert _numbers(result.features) == list(range(40))  # bez luk i bez duplikatów
    assert 17 in server.requests  # dociągnięcie reszty od faktycznego indeksu


def test_repeated_page_makes_the_result_incomplete_instead_of_duplicating_objects() -> None:
    server = Server(50)
    server.override[30] = _page(20, 50)  # serwer zwraca drugi raz stronę 20

    result = _fetch(server, concurrency=4)

    assert result.complete is False
    assert len(set(_numbers(result.features))) == len(result.features)


def test_unknown_number_matched_falls_back_to_the_sequential_mode() -> None:
    def response(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params["startIndex"])
        ids = list(range(start, min(start + 100, 201)))
        members = "".join(
            f'<wfs:member><app:Feature gml:id="feature-{value}"/></wfs:member>' for value in ids
        )
        body = (
            '<?xml version="1.0"?><wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" '
            'xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:app="urn:test" numberMatched="unknown" '
            f'numberReturned="{len(ids)}">{members}</wfs:FeatureCollection>'
        ).encode()
        return httpx.Response(200, content=body, headers={"content-type": "application/gml+xml"})

    with respx.mock:
        respx.get(WFS_URL).mock(side_effect=response)
        with _client() as client:
            result = client.fetch_wfs(WFS_URL, type_name="app:Feature", srs_name="EPSG:2180", concurrency=4)

    assert result.complete is True and len(result.features) == 201


def test_feature_limit_raises_a_typed_error_with_partial_result() -> None:
    server = Server(300)
    with respx.mock:
        respx.get(WFS_URL).mock(side_effect=server)
        with _client(max_features=50) as client:
            with pytest.raises(OgcLimitError) as raised:
                client.fetch_wfs(WFS_URL, type_name="app:Feature", srs_name="EPSG:2180", concurrency=4)

    assert raised.value.partial is not None and raised.value.partial.complete is False


def test_total_byte_limit_is_enforced_across_parallel_pages() -> None:
    server = Server(300)
    with respx.mock:
        respx.get(WFS_URL).mock(side_effect=server)
        with _client(max_total_bytes=6_000) as client:
            with pytest.raises(OgcLimitError):
                client.fetch_wfs(WFS_URL, type_name="app:Feature", srs_name="EPSG:2180", concurrency=4)


# --- wznawianie ---------------------------------------------------------------------------------------------


def test_interrupted_fetch_resumes_from_stored_pages_and_gives_the_same_artifact(tmp_path: Path) -> None:
    uninterrupted = _fetch(Server(120, stamp="A"), concurrency=4)

    store = FilePageStore(tmp_path / "zones")
    broken = Server(120, stamp="B")
    broken.fail_at = {80}  # awaria w połowie importu
    with pytest.raises(OgcTransportError):
        _fetch(broken, concurrency=1 + 3, page_store=store)
    saved = store.stored_pages()
    assert 1 <= saved < 12

    resumed_server = Server(120, stamp="C")  # inny czas niż w przerwanym przebiegu
    events: list[WfsPageEvent] = []
    resumed = _fetch(resumed_server, concurrency=4, page_store=store, on_page=events.append)

    assert resumed.complete is True
    assert resumed.artifact == uninterrupted.artifact
    assert hashlib.sha256(resumed.artifact).hexdigest() == hashlib.sha256(uninterrupted.artifact).hexdigest()
    # Wznowienie pobrało z sieci tylko brakujące strony (plus pierwszą, która waliduje stan usługi).
    fetched_now = set(resumed_server.requests)
    stored_before = {event.start_index for event in events if event.from_store}
    assert 0 in fetched_now and stored_before and not (stored_before & fetched_now)
    assert len(fetched_now) + len(stored_before) == 12
    assert sum(event.returned for event in events if event.from_store) == 10 * len(stored_before)


def test_resume_discards_pages_when_the_service_state_changed(tmp_path: Path) -> None:
    store = FilePageStore(tmp_path / "zones")
    _fetch(Server(60), concurrency=4, page_store=store)

    changed = Server(70)  # w rejestrze przybyło obiektów od poprzedniego przebiegu
    result = _fetch(changed, concurrency=4, page_store=store)

    assert result.complete is True and len(result.features) == 70
    assert sorted(changed.requests) == list(range(0, 70, PAGE))  # nic z magazynu — inny numberMatched


def test_corrupted_stored_page_is_downloaded_again(tmp_path: Path) -> None:
    store = FilePageStore(tmp_path / "zones")
    first = _fetch(Server(50), concurrency=4, page_store=store)
    next(store.directory.glob("page-0000000020.xml")).write_bytes(b"<uszkodzone")

    server = Server(50)
    resumed = _fetch(server, concurrency=4, page_store=store)

    assert resumed.artifact == first.artifact
    assert 20 in server.requests and server.requests.count(20) == 1


def test_cancel_stops_the_download_and_keeps_pages_for_a_later_resume(tmp_path: Path) -> None:
    store = FilePageStore(tmp_path / "zones")
    cancel = threading.Event()
    server = Server(500)
    for start in range(10, 500, PAGE):
        server.delay[start] = 0.01
    seen = 0

    def stop_after_a_few(event: WfsPageEvent) -> None:
        nonlocal seen
        seen += 1
        if seen == 5:
            cancel.set()

    with pytest.raises(OgcCancelled):
        _fetch(server, concurrency=2, page_store=store, on_page=stop_after_a_few, cancel=cancel)

    assert 5 <= store.stored_pages() < 50  # część stron zachowana, reszty nie pobrano

    resumed_server = Server(500)
    resumed = _fetch(resumed_server, concurrency=4, page_store=store)
    assert resumed.complete is True and len(resumed.features) == 500
    assert len(resumed_server.requests) < 50


def test_cancel_before_start_sends_nothing() -> None:
    cancel = threading.Event()
    cancel.set()
    server = Server(30)

    with pytest.raises(OgcCancelled):
        _fetch(server, concurrency=4, cancel=cancel)

    assert server.requests == []
