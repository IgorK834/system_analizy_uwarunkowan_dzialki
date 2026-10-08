"""Single-flight per klucz: rejestr w procesie i sesyjna blokada doradcza PostgreSQL (AU-007).

Testy bez HTTP — sam mechanizm: lider liczy raz, czekający dostają jego wynik, awaria/anulowanie lidera
zwalnia blokadę, a oczekiwanie ma limit. Blokada doradcza jest sprawdzana na prawdziwym PostgreSQL.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from app.core.metrics import upstream_metrics
from app.core.settings import settings
from app.db.session import advisory_lock_engine
from app.services.singleflight import (
    PostgresAdvisoryLock,
    SingleFlight,
    SingleFlightTimeoutError,
    analysis_single_flight,
)

pytestmark = pytest.mark.integration

KEY = "SINGLEFLIGHT_TEST_146510_8.0502.1/3"


def _flight(*, wait: float = 5.0, cross_process: bool = False, poll: float = 0.02, name: str = "analysis") -> SingleFlight:
    return SingleFlight(
        name,
        wait_seconds=lambda: wait,
        poll_seconds=lambda: poll,
        cross_process=lambda: cross_process,
    )


class _Counter:
    def __init__(self, delay: float = 0.2, fail_first: int = 0) -> None:
        self.calls = 0
        self.delay = delay
        self.fail_first = fail_first

    async def __call__(self) -> str:
        self.calls += 1
        number = self.calls
        await asyncio.sleep(self.delay)
        if number <= self.fail_first:
            raise RuntimeError(f"awaria lidera nr {number}")
        return f"wynik-{number}"


@pytest.fixture(autouse=True)
def _reset_metrics() -> None:
    upstream_metrics.reset()


# --- Rejestr w procesie ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parallel_calls_run_the_work_once_and_share_the_result() -> None:
    flight, work = _flight(), _Counter()

    results = await asyncio.gather(*(flight.run(KEY, work) for _ in range(6)))

    assert work.calls == 1
    assert results == ["wynik-1"] * 6
    assert flight.in_flight() == 0 and flight.waiters() == 0
    counters = upstream_metrics.snapshot()
    assert counters["analysis_singleflight.leader"] == 1
    assert counters["analysis_singleflight.wait"] == 5


@pytest.mark.asyncio
async def test_waiters_gauge_counts_the_waiting_requests_and_returns_to_zero() -> None:
    flight, work = _flight(), _Counter(delay=0.3)

    tasks = [asyncio.create_task(flight.run(KEY, work)) for _ in range(4)]
    await asyncio.sleep(0.1)
    during = flight.waiters()
    await asyncio.gather(*tasks)

    assert during == 3
    assert flight.waiters() == 0
    assert flight.gauge_name == "analysis_singleflight_waiters"


@pytest.mark.asyncio
async def test_different_keys_do_not_wait_for_each_other() -> None:
    flight, work = _flight(), _Counter(delay=0.3)
    started = time.monotonic()

    await asyncio.gather(flight.run("A", work), flight.run("B", work), flight.run("C", work))

    assert work.calls == 3
    assert time.monotonic() - started < 0.8  # równolegle, nie 3 × 0.3 s sekwencyjnie


@pytest.mark.asyncio
async def test_sequential_calls_after_completion_start_a_new_flight() -> None:
    flight, work = _flight(), _Counter(delay=0.0)

    first = await flight.run(KEY, work)
    second = await flight.run(KEY, work)

    assert (first, second) == ("wynik-1", "wynik-2")


@pytest.mark.asyncio
async def test_leader_failure_releases_the_flight_and_one_waiter_takes_over() -> None:
    flight, work = _flight(), _Counter(delay=0.2, fail_first=1)

    outcomes = await asyncio.gather(*(flight.run(KEY, work) for _ in range(4)), return_exceptions=True)

    assert isinstance(outcomes[0], RuntimeError)  # lider dostaje własny błąd
    assert outcomes[1:] == ["wynik-2"] * 3  # reszta: jeden przejmuje, pozostali dzielą jego wynik
    assert work.calls == 2
    assert flight.in_flight() == 0 and flight.waiters() == 0
    assert upstream_metrics.snapshot()["analysis_singleflight.takeover"] == 1


@pytest.mark.asyncio
async def test_repeated_leader_failures_are_propagated_instead_of_multiplying_work() -> None:
    flight, work = _flight(), _Counter(delay=0.1, fail_first=99)

    outcomes = await asyncio.gather(*(flight.run(KEY, work) for _ in range(6)), return_exceptions=True)

    assert all(isinstance(outcome, RuntimeError) for outcome in outcomes)
    assert work.calls == 2  # lider i jeden następca; reszta dostaje błąd, nie liczy 6 razy
    assert flight.in_flight() == 0


@pytest.mark.asyncio
async def test_cancelled_leader_hands_over_without_failing_the_waiters() -> None:
    flight, work = _flight(), _Counter(delay=0.3)
    leader = asyncio.create_task(flight.run(KEY, work))
    await asyncio.sleep(0.05)
    followers = [asyncio.create_task(flight.run(KEY, work)) for _ in range(2)]
    await asyncio.sleep(0.05)

    leader.cancel()
    results = await asyncio.gather(*followers)

    with pytest.raises(asyncio.CancelledError):
        await leader
    assert results == ["wynik-2", "wynik-2"]
    assert work.calls == 2
    assert flight.in_flight() == 0 and flight.waiters() == 0


@pytest.mark.asyncio
async def test_waiter_times_out_without_hanging_and_leaves_the_leader_untouched() -> None:
    flight, work = _flight(wait=0.2), _Counter(delay=0.6)
    leader = asyncio.create_task(flight.run(KEY, work))
    await asyncio.sleep(0.05)

    with pytest.raises(SingleFlightTimeoutError) as caught:
        await flight.run(KEY, work)

    assert caught.value.response_headers == {"Retry-After": "5"}
    assert flight.waiters() == 0
    assert await leader == "wynik-1"  # lider kończy swoją pracę mimo timeoutu czekającego
    assert work.calls == 1
    assert upstream_metrics.snapshot()["analysis_singleflight.timeout"] == 1


@pytest.mark.asyncio
async def test_cancelled_waiter_is_removed_and_does_not_affect_others() -> None:
    flight, work = _flight(), _Counter(delay=0.3)
    leader = asyncio.create_task(flight.run(KEY, work))
    await asyncio.sleep(0.05)
    cancelled = asyncio.create_task(flight.run(KEY, work))
    kept = asyncio.create_task(flight.run(KEY, work))
    await asyncio.sleep(0.05)
    assert flight.waiters() == 2

    cancelled.cancel()
    results = await asyncio.gather(leader, kept)

    assert results == ["wynik-1", "wynik-1"]
    assert flight.waiters() == 0


@pytest.mark.asyncio
async def test_rejected_result_is_retried_instead_of_returned() -> None:
    """``force_refresh`` nie przyjmuje wyniku starszego niż jego żądanie."""
    flight, work = _flight(), _Counter(delay=0.15)
    leader = asyncio.create_task(flight.run(KEY, work))
    await asyncio.sleep(0.03)

    picky = await flight.run(KEY, work, accept=lambda value: value != "wynik-1")

    assert await leader == "wynik-1"
    assert picky == "wynik-2"
    assert work.calls == 2


@pytest.mark.asyncio
async def test_share_errors_gives_waiters_the_leader_error_without_retrying() -> None:
    flight, work = _flight(), _Counter(delay=0.1, fail_first=99)

    outcomes = await asyncio.gather(
        *(flight.run(KEY, work, share_errors=True) for _ in range(5)), return_exceptions=True
    )

    assert all(isinstance(outcome, RuntimeError) for outcome in outcomes)
    assert work.calls == 1


@pytest.mark.asyncio
async def test_reuse_result_short_circuits_the_work() -> None:
    flight, work = _flight(), _Counter()

    async def reuse() -> str:
        return "z-cache"

    assert await flight.run(KEY, work, reuse=reuse) == "z-cache"
    assert work.calls == 0


def test_flights_started_from_different_event_loops_and_threads_still_share_one_run() -> None:
    flight = _flight()
    calls: list[int] = []
    results: list[str] = []
    barrier = threading.Barrier(6)

    async def work() -> str:
        calls.append(1)
        await asyncio.sleep(0.4)
        return "wynik"

    def request() -> None:
        barrier.wait()
        results.append(asyncio.run(flight.run(KEY, work)))

    threads = [threading.Thread(target=request) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert results == ["wynik"] * 6
    assert len(calls) == 1
    assert flight.in_flight() == 0 and flight.waiters() == 0


def test_module_singletons_use_the_configured_wait_and_start_idle() -> None:
    assert analysis_single_flight.in_flight() == 0
    assert settings.analysis_singleflight_wait_seconds > 0
    assert settings.analysis_singleflight_enabled is True


# --- Blokada doradcza PostgreSQL (między workerami) --------------------------------------------


def _advisory_locks_held() -> int:
    engine = create_engine(settings.database_url, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            return int(
                connection.execute(
                    text("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND granted")
                ).scalar_one()
            )
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_advisory_lock_excludes_a_second_holder_until_the_first_releases() -> None:
    lock = PostgresAdvisoryLock(lambda: 0.02)
    order: list[str] = []

    async def first() -> None:
        async with lock.hold(KEY, 5.0):
            order.append("first-in")
            await asyncio.sleep(0.4)
            order.append("first-out")

    async def second() -> None:
        await asyncio.sleep(0.1)
        async with lock.hold(KEY, 5.0):
            order.append("second-in")

    await asyncio.gather(first(), second())

    assert order == ["first-in", "first-out", "second-in"]


@pytest.mark.asyncio
async def test_advisory_lock_times_out_instead_of_hanging() -> None:
    lock = PostgresAdvisoryLock(lambda: 0.02)

    async with lock.hold(KEY, 5.0):
        started = time.monotonic()
        with pytest.raises(SingleFlightTimeoutError):
            async with lock.hold(KEY, 0.3):
                pytest.fail("blokada nie powinna zostać zdobyta")
        assert 0.25 <= time.monotonic() - started < 2.0


@pytest.mark.asyncio
async def test_advisory_locks_of_different_keys_are_independent() -> None:
    lock = PostgresAdvisoryLock(lambda: 0.02)

    async with lock.hold("SINGLEFLIGHT_TEST_A", 1.0):
        async with lock.hold("SINGLEFLIGHT_TEST_B", 1.0):
            assert _advisory_locks_held() >= 2


@pytest.mark.asyncio
async def test_advisory_lock_is_released_after_an_exception_in_the_leader() -> None:
    lock = PostgresAdvisoryLock(lambda: 0.02)
    before = _advisory_locks_held()

    with pytest.raises(RuntimeError):
        async with lock.hold(KEY, 1.0):
            raise RuntimeError("awaria lidera")

    assert _advisory_locks_held() == before
    async with lock.hold(KEY, 0.5):  # od razu do zdobycia
        pass


@pytest.mark.asyncio
async def test_advisory_lock_is_released_when_the_leader_is_cancelled() -> None:
    lock = PostgresAdvisoryLock(lambda: 0.02)
    before = _advisory_locks_held()
    entered = asyncio.Event()

    async def leader() -> None:
        async with lock.hold(KEY, 1.0):
            entered.set()
            await asyncio.sleep(30)

    task = asyncio.create_task(leader())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert _advisory_locks_held() == before


@pytest.mark.asyncio
async def test_advisory_lock_disappears_with_the_session_of_a_dead_worker() -> None:
    """Zabity proces zamyka połączenie — serwer sam zdejmuje blokadę (brak martwej blokady)."""
    engine = advisory_lock_engine()
    dead_worker = engine.connect()
    dead_worker.execute(text("SELECT pg_advisory_lock(hashtextextended(:key, 0))"), {"key": "analysis-singleflight:" + KEY})
    lock = PostgresAdvisoryLock(lambda: 0.02)

    with pytest.raises(SingleFlightTimeoutError):
        async with lock.hold(KEY, 0.2):
            pass
    dead_worker.close()  # symulacja zakończenia procesu

    async with lock.hold(KEY, 1.0):
        pass


@pytest.mark.asyncio
async def test_two_workers_run_the_work_once_and_the_second_reuses_the_stored_result() -> None:
    """Dwa rejestry = dwa procesy: wspólna jest tylko blokada w PostgreSQL i „cache” (tu: słownik)."""
    worker_a = _flight(cross_process=True, name="analysis")
    worker_b = _flight(cross_process=True, name="analysis")
    store: dict[str, str] = {}
    calls: list[str] = []

    async def work() -> str:
        calls.append("work")
        await asyncio.sleep(0.4)
        store["analysis"] = "wynik-lidera"
        return store["analysis"]

    async def reuse() -> str | None:
        return store.get("analysis")

    results = await asyncio.gather(
        worker_a.run(KEY, work, reuse=reuse),
        worker_b.run(KEY, work, reuse=reuse),
    )

    assert results == ["wynik-lidera", "wynik-lidera"]
    assert calls == ["work"]
    assert worker_a.in_flight() == worker_b.in_flight() == 0


@pytest.mark.asyncio
async def test_cross_process_wait_times_out_when_the_other_worker_is_too_slow() -> None:
    slow_worker = _flight(cross_process=True)
    impatient = _flight(cross_process=True, wait=0.3)
    release = asyncio.Event()

    async def slow() -> str:
        await release.wait()
        return "późno"

    leader = asyncio.create_task(slow_worker.run(KEY, slow))
    await asyncio.sleep(0.2)

    with pytest.raises(SingleFlightTimeoutError):
        await impatient.run(KEY, _Counter(delay=0.0))

    release.set()
    assert await leader == "późno"
    assert impatient.in_flight() == 0


@pytest.mark.asyncio
async def test_lock_connections_are_not_leaked_by_many_sequential_flights() -> None:
    flight = _flight(cross_process=True)
    before = _advisory_locks_held()

    for index in range(15):
        await flight.run(f"{KEY}/{index}", _Counter(delay=0.0))

    assert _advisory_locks_held() == before


@pytest.mark.asyncio
async def test_unreachable_lock_connection_degrades_to_the_in_process_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    broken = create_engine(
        "postgresql+psycopg2://nikt:zle@127.0.0.1:1/brak", poolclass=NullPool, isolation_level="AUTOCOMMIT"
    )
    monkeypatch.setattr("app.services.singleflight.advisory_lock_engine", lambda: broken)
    flight, work = _flight(cross_process=True), _Counter(delay=0.1)

    results = await asyncio.gather(*(flight.run(KEY, work) for _ in range(3)))

    assert results == ["wynik-1"] * 3  # nadal jeden przebieg dzięki blokadzie w procesie
    assert upstream_metrics.snapshot()["analysis_singleflight.lock_unavailable"] >= 1
