"""Single-flight analiz per działka na pełnym przepływie ``POST /analyze`` (AU-007, B8/R3).

Audyt: 6 równoległych ``POST /analyze`` dla tej samej nowej działki dawało 6 pełnych analiz (id 43–48),
6 wierszy w ``analyses`` i 6× pełne odpytanie usług po 14–26 s. Teraz lider liczy raz, a reszta dostaje
jego wynik. ULDK, KIMPZP i NMT to prawdziwe, zamrożone odpowiedzi usług (``respx``, z opóźnieniem, żeby
żądania naprawdę się nakładały); pozostałe źródła to podmienione szwy z licznikami wywołań.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.core.access_control import make_analysis_token
from app.core.metrics import upstream_metrics
from app.core.settings import settings
from app.db.session import SessionLocal
from app.main import app
from app.models.analysis import Analysis
from app.models.analysis_pending_document import AnalysisPendingDocument
from app.models.infrastructure import Infrastructure
from app.models.mpzp_parameter import MpzpParameter
from app.models.mpzp_zone import MpzpZone
from app.models.parcel import Parcel
from app.models.pog_data import PogData
from app.models.risk import Risk
from app.models.source_record import SourceRecord
from app.services import analysis_orchestrator
from app.services.cache import get_analysis_completed_since, get_cached_analysis
from app.services.singleflight import analysis_single_flight
from app.services.uldk import ULDK_BASE_URL
from tests.test_analysis_orchestrator import _binding_pog_discovery
from tests.test_terrain_e2e import _kiut, _kiut_section, _pog_vectors

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parent / "fixtures" / "source_contracts"
# Prawdziwa działka z korpusu referencyjnego: Warszawa 146510_8.0502.1/3 (ULDK z 2026-10-06).
PARCEL_ID = "146510_8.0502.1/3"
PARCEL_REQUEST = {"method": "parcel_id", "parcel_identifier": PARCEL_ID}
PARALLEL = 6
UPSTREAM_DELAY_S = 0.5


def _fixture(*parts: str) -> str:
    return (FIXTURES.joinpath(*parts)).read_text(encoding="utf-8")


def _cleanup() -> None:
    with SessionLocal() as db:
        parcel_ids = select(Parcel.id).where(Parcel.parcel_identifier == PARCEL_ID)
        analysis_ids = select(Analysis.id).where(Analysis.parcel_id.in_(parcel_ids))
        zone_ids = select(MpzpZone.id).where(MpzpZone.analysis_id.in_(analysis_ids))
        db.execute(delete(MpzpParameter).where(MpzpParameter.mpzp_zone_id.in_(zone_ids)))
        for model in (SourceRecord, PogData, MpzpZone, Infrastructure, Risk, AnalysisPendingDocument):
            db.execute(delete(model).where(model.analysis_id.in_(analysis_ids)))
        db.execute(delete(Analysis).where(Analysis.id.in_(analysis_ids)))
        db.execute(delete(Parcel).where(Parcel.id.in_(parcel_ids)))
        db.commit()


def _analysis_rows() -> int:
    with SessionLocal() as db:
        return int(
            db.scalar(
                select(func.count(Analysis.id))
                .join(Parcel, Analysis.parcel_id == Parcel.id)
                .where(Parcel.parcel_identifier == PARCEL_ID)
            )
            or 0
        )


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    _cleanup()
    upstream_metrics.reset()
    monkeypatch.setattr(settings, "rate_limit_enabled", False)  # 6 równoległych żądań + force_refresh
    monkeypatch.setattr(settings, "terrain_relief_enabled", False)  # bez WCS: źródła są liczone po HTTP
    monkeypatch.setattr(settings, "analysis_singleflight_wait_seconds", 60.0)
    yield
    _cleanup()


@dataclass
class Upstreams:
    """Liczniki wywołań źródeł: trasy HTTP (``respx``) i podmienione szwy."""

    router: respx.MockRouter
    routes: dict[str, respx.Route]
    seams: dict[str, AsyncMock] = field(default_factory=dict)

    def calls(self) -> dict[str, int]:
        counts = {name: route.call_count for name, route in self.routes.items()}
        counts.update({name: seam.await_count for name, seam in self.seams.items()})
        return counts

    def reset(self) -> None:
        for route in self.routes.values():
            route.reset()
        for seam in self.seams.values():
            seam.reset_mock()


def _slow_http(body: str, delay: float) -> Callable[[httpx.Request], object]:
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(delay)
        return httpx.Response(200, text=body)

    return handler


def _slow_seam(value, delay: float = UPSTREAM_DELAY_S) -> AsyncMock:
    async def call(*_args, **_kwargs):
        await asyncio.sleep(delay)
        return value

    return AsyncMock(side_effect=call)


@pytest.fixture
def upstreams() -> Iterator[Upstreams]:
    seams = {
        "kiut_network": _slow_seam(_kiut_section()),
        "isok": _slow_seam([]),
        "gdos": _slow_seam([]),
        "pog_discovery": _slow_seam(_binding_pog_discovery()),
        "pog_vectors": _slow_seam(_pog_vectors()),
        "kiut_coverage": _slow_seam(_kiut()),
    }
    with ExitStack() as stack:
        router = stack.enter_context(respx.mock(assert_all_called=False))
        routes = {
            "uldk": router.get(ULDK_BASE_URL),
            "kimpzp": router.get(settings.kimpzp_wms_base_url),
            "nmt": router.get(settings.nmt_base_url),
        }
        routes["uldk"].mock(
            side_effect=_slow_http(_fixture("uldk", "poprawna_146510_8.0502.1_3.txt"), 0.2)
        )
        routes["kimpzp"].mock(
            side_effect=_slow_http(_fixture("kimpzp", "warszawa_146510_8.0502.1_3.html"), UPSTREAM_DELAY_S)
        )
        routes["nmt"].mock(
            side_effect=_slow_http(_fixture("nmt_getminmaxbypolygon.txt"), UPSTREAM_DELAY_S)
        )
        for target, name in (
            ("app.services.context.fetch_kiut_network_section", "kiut_network"),
            ("app.services.context.fetch_flood_risk_section", "isok"),
            ("app.services.context.fetch_nature_protection_section", "gdos"),
            ("app.services.analysis_orchestrator.discover_pog", "pog_discovery"),
            ("app.services.analysis_orchestrator.fetch_pog_vector_data", "pog_vectors"),
            ("app.services.analysis_orchestrator.check_kiut_coverage_for_geometry", "kiut_coverage"),
        ):
            stack.enter_context(patch(target, new=seams[name]))
        yield Upstreams(router=router, routes=routes, seams=seams)


def _post_parallel(
    client: TestClient,
    count: int,
    *,
    path: str = "/analyze",
    payloads: list[dict] | None = None,
) -> list[httpx.Response]:
    barrier = threading.Barrier(count)
    bodies = payloads or [PARCEL_REQUEST] * count

    def request(index: int) -> httpx.Response:
        barrier.wait()
        return client.post(path, json=bodies[index])

    with ThreadPoolExecutor(max_workers=count) as pool:
        return list(pool.map(request, range(count)))


def _baseline_calls(upstreams: Upstreams, monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Ile razy jedna analiza odpytuje każde źródło (bez single-flight, jedno żądanie)."""
    monkeypatch.setattr(settings, "analysis_singleflight_enabled", False)
    with TestClient(app) as client:
        assert client.post("/analyze", json=PARCEL_REQUEST).status_code == 200
    calls = upstreams.calls()
    monkeypatch.setattr(settings, "analysis_singleflight_enabled", True)
    _cleanup()
    upstreams.reset()
    upstream_metrics.reset()
    return calls


# --- Kryterium akceptacji ----------------------------------------------------------------------


def test_six_parallel_requests_for_a_new_parcel_run_one_analysis(
    upstreams: Upstreams, monkeypatch: pytest.MonkeyPatch
) -> None:
    one_analysis = _baseline_calls(upstreams, monkeypatch)
    assert all(count >= 1 for count in one_analysis.values()), one_analysis

    with TestClient(app) as client:
        responses = _post_parallel(client, PARALLEL)

    assert [response.status_code for response in responses] == [200] * PARALLEL
    bodies = [response.json() for response in responses]
    assert len({body["analysis_id"] for body in bodies}) == 1
    assert _analysis_rows() == 1
    # Każde źródło (ULDK, KIMPZP, NMT, KIUT, ISOK, GDOŚ, POG) wywołane tyle razy, co przy jednej analizie.
    assert upstreams.calls() == one_analysis
    assert upstreams.routes["uldk"].call_count == 1
    assert upstreams.routes["nmt"].call_count == 1
    # Wszystkie odpowiedzi niosą ten sam wynik i ten sam token dostępu.
    assert len({body["access_token"] for body in bodies}) == 1
    assert bodies[0]["access_token"] == make_analysis_token(bodies[0]["analysis_id"])
    assert len({body["analyzed_at"] for body in bodies}) == 1
    counters = upstream_metrics.snapshot()
    assert counters["analysis_singleflight.leader"] == 1
    assert counters["analysis_singleflight.wait"] == PARALLEL - 1
    assert analysis_single_flight.waiters() == 0 and analysis_single_flight.in_flight() == 0


def test_requests_after_the_leader_finished_are_cache_hits(upstreams: Upstreams) -> None:
    with TestClient(app) as client:
        first = client.post("/analyze", json=PARCEL_REQUEST)
        after = upstreams.calls()
        again = _post_parallel(client, 3)

    assert first.status_code == 200
    assert {response.json()["analysis_id"] for response in again} == {first.json()["analysis_id"]}
    # Trafienie w cache wymaga tylko identyfikacji działki: jedno wspólne zapytanie ULDK dla trzech żądań.
    assert upstreams.calls() == dict(after, uldk=after["uldk"] + 1)
    assert _analysis_rows() == 1


def test_requests_for_the_same_parcel_by_different_methods_still_run_one_analysis(
    upstreams: Upstreams, monkeypatch: pytest.MonkeyPatch
) -> None:
    one_analysis = _baseline_calls(upstreams, monkeypatch)
    payloads = [PARCEL_REQUEST, {"method": "map", "lon": 21.0, "lat": 52.2}] * 2

    with TestClient(app) as client:
        responses = _post_parallel(client, 4, payloads=payloads)

    assert [response.status_code for response in responses] == [200] * 4
    assert len({response.json()["analysis_id"] for response in responses}) == 1
    assert _analysis_rows() == 1
    expected = dict(one_analysis, uldk=2)  # dwa różne rodzaje żądań = dwa zapytania identyfikujące
    assert upstreams.calls() == expected


def test_six_parallel_force_refresh_requests_share_one_new_analysis(upstreams: Upstreams) -> None:
    with TestClient(app) as client:
        baseline = client.post("/analyze", json=PARCEL_REQUEST)
        assert baseline.status_code == 200
        upstreams.reset()
        responses = _post_parallel(client, PARALLEL, path="/analyze?force_refresh=true")

    assert [response.status_code for response in responses] == [200] * PARALLEL
    ids = {response.json()["analysis_id"] for response in responses}
    assert len(ids) == 1 and ids != {baseline.json()["analysis_id"]}  # świeża analiza, jedna
    assert _analysis_rows() == 2  # historia zachowana: poprzednia + jedna nowa
    assert upstreams.routes["nmt"].call_count == 1
    assert upstreams.routes["kimpzp"].call_count >= 1
    assert upstreams.routes["uldk"].call_count == 1


def test_force_refresh_is_not_served_the_older_cache_entry_by_a_waiting_leader(
    upstreams: Upstreams,
) -> None:
    """``force_refresh`` po zakończonym locie liczy od nowa (wynik lidera jest starszy niż żądanie)."""
    with TestClient(app) as client:
        first = client.post("/analyze", json=PARCEL_REQUEST)
        second = client.post("/analyze?force_refresh=true", json=PARCEL_REQUEST)

    assert first.json()["analysis_id"] != second.json()["analysis_id"]
    assert _analysis_rows() == 2


# --- Awaria lidera, timeout, wyłączenie --------------------------------------------------------


def test_failed_leader_releases_the_lock_and_waiters_get_a_result(upstreams: Upstreams) -> None:
    real_save = analysis_orchestrator.save_analysis
    attempts: list[int] = []

    def flaky_save(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("awaria zapisu lidera")
        return real_save(*args, **kwargs)

    with patch("app.services.analysis_orchestrator.save_analysis", new=flaky_save):
        with TestClient(app, raise_server_exceptions=False) as client:
            responses = _post_parallel(client, 4)

    statuses = sorted(response.status_code for response in responses)
    assert statuses == [200, 200, 200, 500]  # lider dostaje własny błąd, reszta wynik następcy
    failed = next(response for response in responses if response.status_code == 500)
    assert failed.json()["error"] == "INTERNAL_ERROR"
    ids = {response.json()["analysis_id"] for response in responses if response.status_code == 200}
    assert len(ids) == 1
    assert _analysis_rows() == 1
    assert len(attempts) == 2
    assert upstreams.routes["uldk"].call_count == 1
    assert analysis_single_flight.waiters() == 0 and analysis_single_flight.in_flight() == 0


def test_waiter_over_the_wait_limit_gets_503_with_retry_after_and_the_leader_still_finishes(
    upstreams: Upstreams, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "analysis_singleflight_wait_seconds", 0.8)
    results: dict[str, httpx.Response] = {}

    with TestClient(app) as client:
        leader = threading.Thread(
            target=lambda: results.__setitem__("leader", client.post("/analyze", json=PARCEL_REQUEST))
        )
        leader.start()
        threading.Event().wait(1.0)  # lider jest już w trakcie (KIMPZP/NMT/… trwają ≥ 0,5 s)
        results["waiter"] = client.post("/analyze", json=PARCEL_REQUEST)
        leader.join(timeout=60)
        after = client.post("/analyze", json=PARCEL_REQUEST)

    waiter = results["waiter"]
    assert waiter.status_code == 503
    body = waiter.json()
    assert body["error"] == "ANALYSIS_IN_PROGRESS" and body["request_id"]
    assert int(waiter.headers["Retry-After"]) >= 1
    assert results["leader"].status_code == 200  # lider nie ucierpiał
    assert after.json()["analysis_id"] == results["leader"].json()["analysis_id"]  # ponowienie = cache
    assert _analysis_rows() == 1


def test_cache_hits_never_enter_the_single_flight(upstreams: Upstreams) -> None:
    with TestClient(app) as client:
        first = client.post("/analyze", json=PARCEL_REQUEST)
        with patch.object(
            analysis_single_flight, "run", AsyncMock(side_effect=AssertionError("cache nie czeka na blokadę"))
        ):
            again = client.post("/analyze", json=PARCEL_REQUEST)

    assert again.status_code == 200 and again.json()["analysis_id"] == first.json()["analysis_id"]


def test_single_flight_can_be_disabled(upstreams: Upstreams, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "analysis_singleflight_enabled", False)

    with TestClient(app) as client:
        responses = _post_parallel(client, 3)

    assert [response.status_code for response in responses] == [200] * 3
    assert _analysis_rows() == 3  # zachowanie sprzed AU-007
    assert upstreams.routes["uldk"].call_count == 3


def test_metrics_are_visible_to_the_operator(upstreams: Upstreams, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "admin_api_keys", "ops:klucz-ops")

    with TestClient(app) as client:
        _post_parallel(client, 3)
        metrics = client.get("/health/upstream", headers={"X-Admin-Key": "klucz-ops"}).json()

    assert metrics["counters"]["analysis_singleflight.leader"] == 1
    assert metrics["counters"]["analysis_singleflight.wait"] == 2
    assert metrics["gauges"]["analysis_singleflight_waiters"] == 0


# --- Cache po single-flight --------------------------------------------------------------------


def test_analysis_completed_since_returns_only_results_saved_after_the_request(
    upstreams: Upstreams,
) -> None:
    before = datetime.now(timezone.utc)
    with TestClient(app) as client:
        response = client.post("/analyze", json=PARCEL_REQUEST)
    assert response.status_code == 200

    with SessionLocal() as db:
        fresh = get_analysis_completed_since(PARCEL_ID, db, before)
        stale = get_analysis_completed_since(PARCEL_ID, db, datetime.now(timezone.utc) + timedelta(seconds=5))
        cached = get_cached_analysis(PARCEL_ID, db)

    assert fresh is not None and fresh.id == response.json()["analysis_id"]
    assert stale is None
    assert cached is not None and cached.id == fresh.id


# --- Przekazanie wyniku między workerami (reuse po zdobyciu blokady) ---------------------------


def test_result_saved_by_another_worker_while_waiting_for_the_lock_is_reused(
    upstreams: Upstreams,
) -> None:
    """Pre-check cache jest pusty, ale po blokadzie wynik innego workera już jest: brak drugiej analizy."""
    with TestClient(app) as client:
        first = client.post("/analyze", json=PARCEL_REQUEST)
        assert first.status_code == 200
        sources_after_first = upstreams.calls()
        real = analysis_orchestrator.get_cached_analysis
        calls: list[int] = []

        def precheck_misses_once(*args, **kwargs):
            calls.append(1)
            return None if len(calls) == 1 else real(*args, **kwargs)

        with patch("app.services.analysis_orchestrator.get_cached_analysis", new=precheck_misses_once):
            second = client.post("/analyze", json=PARCEL_REQUEST)

    assert second.status_code == 200
    assert second.json()["analysis_id"] == first.json()["analysis_id"]
    assert len(calls) == 2  # wstępne sprawdzenie + ponowne po wejściu do blokady
    assert _analysis_rows() == 1
    assert upstreams.calls() == dict(sources_after_first, uldk=sources_after_first["uldk"] + 1)


def test_force_refresh_reuses_a_result_saved_after_the_request_by_another_worker(
    upstreams: Upstreams,
) -> None:
    with TestClient(app) as client:
        first = client.post("/analyze", json=PARCEL_REQUEST)
        sources_after_first = upstreams.calls()
        real_since = analysis_orchestrator.get_analysis_completed_since
        # „Inny worker” zapisał wynik już po żądaniu: okno obejmuje istniejący wiersz.
        with patch(
            "app.services.analysis_orchestrator.get_analysis_completed_since",
            new=lambda parcel, db, since: real_since(parcel, db, since - timedelta(hours=1)),
        ):
            second = client.post("/analyze?force_refresh=true", json=PARCEL_REQUEST)

    assert second.json()["analysis_id"] == first.json()["analysis_id"]
    assert _analysis_rows() == 1
    assert upstreams.calls() == dict(sources_after_first, uldk=sources_after_first["uldk"] + 1)
