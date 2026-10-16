"""Cache zapytań RU z TTL, deduplikacją i budżetem (AU-105)."""

from __future__ import annotations

import threading
import time
from dataclasses import replace

import pytest

from app.modules.planning.application.ports import RuPogFeaturesResponse
from app.modules.planning.application.ru_pog_discovery import (
    METRIC_BUDGET_EXCEEDED,
    METRIC_CACHE_HIT,
    METRIC_CACHE_MISS,
    METRIC_DEDUP_WAIT,
    METRIC_FETCH_ERROR,
    METRIC_REQUESTS,
    CachedRuPogFeaturesSource,
    ThreadSafeRequestBudget,
    TtlQueryCache,
)
from app.modules.planning.domain.pog_discovery import ERROR_BUDGET_EXCEEDED, ERROR_TRANSPORT

TILE = (503_200.0, 217_200.0, 503_300.0, 217_300.0)
OTHER_TILE = (503_300.0, 217_200.0, 503_400.0, 217_300.0)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def test_second_call_within_ttl_is_a_hit_and_does_not_fetch_again() -> None:
    cache: TtlQueryCache[str] = TtlQueryCache(max_entries=4, clock=FakeClock())
    calls: list[int] = []

    def fetch() -> str:
        calls.append(1)
        return "wynik"

    assert cache.get_or_fetch("k", fetch, ttl_of=lambda _v: 60.0) == ("wynik", "miss")
    assert cache.get_or_fetch("k", fetch, ttl_of=lambda _v: 60.0) == ("wynik", "hit")
    assert len(calls) == 1


def test_entry_expires_after_its_ttl() -> None:
    clock = FakeClock()
    cache: TtlQueryCache[str] = TtlQueryCache(max_entries=4, clock=clock)
    cache.get_or_fetch("k", lambda: "stary", ttl_of=lambda _v: 60.0)

    clock.now += 59.0
    assert cache.get_or_fetch("k", lambda: "nowy", ttl_of=lambda _v: 60.0) == ("stary", "hit")
    clock.now += 2.0
    assert cache.get_or_fetch("k", lambda: "nowy", ttl_of=lambda _v: 60.0) == ("nowy", "miss")


def test_zero_ttl_result_is_not_remembered() -> None:
    cache: TtlQueryCache[str] = TtlQueryCache(max_entries=4, clock=FakeClock())
    calls: list[int] = []

    def fetch() -> str:
        calls.append(1)
        return "niepelny"

    cache.get_or_fetch("k", fetch, ttl_of=lambda _v: 0.0)
    cache.get_or_fetch("k", fetch, ttl_of=lambda _v: 0.0)

    assert len(calls) == 2 and len(cache) == 0


def test_least_recently_used_entry_is_evicted() -> None:
    cache: TtlQueryCache[str] = TtlQueryCache(max_entries=2, clock=FakeClock())
    for key in ("a", "b"):
        cache.get_or_fetch(key, lambda key=key: key, ttl_of=lambda _v: 60.0)
    cache.get_or_fetch("a", lambda: "x", ttl_of=lambda _v: 60.0)  # "a" znów najświeższy
    cache.get_or_fetch("c", lambda: "c", ttl_of=lambda _v: 60.0)

    assert cache.get_or_fetch("a", lambda: "nowe-a", ttl_of=lambda _v: 60.0)[1] == "hit"
    assert cache.get_or_fetch("b", lambda: "nowe-b", ttl_of=lambda _v: 60.0)[1] == "miss"


def test_cache_needs_a_positive_size() -> None:
    with pytest.raises(ValueError):
        TtlQueryCache(max_entries=0)


def test_parallel_requests_for_one_key_fetch_once_and_share_the_result() -> None:
    cache: TtlQueryCache[str] = TtlQueryCache(max_entries=4)
    started = threading.Event()
    release = threading.Event()
    fetches: list[int] = []
    results: list[tuple[str, str]] = []

    def fetch() -> str:
        fetches.append(1)
        started.set()
        release.wait(timeout=5)
        return "wspolny"

    def call() -> None:
        results.append(cache.get_or_fetch("k", fetch, ttl_of=lambda _v: 60.0))

    leader = threading.Thread(target=call)
    leader.start()
    assert started.wait(timeout=5)
    followers = [threading.Thread(target=call) for _ in range(5)]
    for thread in followers:
        thread.start()
    time.sleep(0.05)  # następcy czekają na lidera
    release.set()
    for thread in [leader, *followers]:
        thread.join(timeout=5)

    assert len(fetches) == 1
    assert sorted(origin for _value, origin in results) == ["miss", "shared", "shared", "shared", "shared", "shared"]
    assert {value for value, _origin in results} == {"wspolny"}


def test_leader_failure_is_not_cached_and_a_follower_takes_over() -> None:
    cache: TtlQueryCache[str] = TtlQueryCache(max_entries=4)
    started = threading.Event()
    release = threading.Event()
    attempts: list[int] = []
    outcome: dict[str, object] = {}

    def failing() -> str:
        attempts.append(1)
        started.set()
        release.wait(timeout=5)
        raise RuntimeError("awaria lidera")

    def lead() -> None:
        try:
            cache.get_or_fetch("k", failing, ttl_of=lambda _v: 60.0)
        except RuntimeError as exc:
            outcome["leader"] = str(exc)

    def follow() -> None:
        outcome["follower"] = cache.get_or_fetch("k", lambda: "z-nastepcy", ttl_of=lambda _v: 60.0)

    leader = threading.Thread(target=lead)
    leader.start()
    assert started.wait(timeout=5)
    follower = threading.Thread(target=follow)
    follower.start()
    time.sleep(0.05)
    release.set()
    leader.join(timeout=5)
    follower.join(timeout=5)

    assert outcome["leader"] == "awaria lidera"
    assert outcome["follower"] == ("z-nastepcy", "miss")


def test_follower_that_waits_too_long_fetches_by_itself() -> None:
    cache: TtlQueryCache[str] = TtlQueryCache(max_entries=4)
    started = threading.Event()
    release = threading.Event()

    def slow() -> str:
        started.set()
        release.wait(timeout=5)
        return "wolny"

    leader = threading.Thread(target=lambda: cache.get_or_fetch("k", slow, ttl_of=lambda _v: 60.0))
    leader.start()
    assert started.wait(timeout=5)

    value, origin = cache.get_or_fetch("k", lambda: "wlasny", ttl_of=lambda _v: 60.0, wait_seconds=0.05)

    release.set()
    leader.join(timeout=5)
    assert (value, origin) == ("wlasny", "miss")


# --- dekorator portu ------------------------------------------------------------------------------------


class FakeSource:
    """Źródło, które zużywa żądania z budżetu tak jak adapter i liczy wywołania."""

    def __init__(self, response: RuPogFeaturesResponse, requests_per_call: int = 1) -> None:
        self.response = response
        self.requests_per_call = requests_per_call
        self.calls = 0

    def fetch_features(self, feature_type, tile, budget=None):  # noqa: ANN001, ANN201
        self.calls += 1
        if budget is not None:
            for _ in range(self.requests_per_call):
                budget.acquire()
        return replace(self.response, requests=self.requests_per_call)


def _decorated(source: FakeSource, metrics: dict[str, int], clock: FakeClock | None = None):  # noqa: ANN202
    def sink(name: str, amount: int) -> None:
        metrics[name] = metrics.get(name, 0) + amount

    return CachedRuPogFeaturesSource(
        source,
        cache=TtlQueryCache(max_entries=8, clock=clock or FakeClock()),
        ttl_seconds=86_400.0,
        negative_ttl_seconds=60.0,
        metrics=sink,
    )


OK = RuPogFeaturesResponse(features=(), complete=True)


def test_hit_consumes_no_budget_and_is_counted() -> None:
    metrics: dict[str, int] = {}
    source = FakeSource(OK)
    decorated = _decorated(source, metrics)
    first_budget, second_budget = ThreadSafeRequestBudget(6), ThreadSafeRequestBudget(6)

    first = decorated.fetch_features("StrefaPlanistyczna", TILE, first_budget)
    second = decorated.fetch_features("StrefaPlanistyczna", TILE, second_budget)

    assert (first.origin, first.requests) == ("network", 1)
    assert (second.origin, second.requests) == ("cache", 0)
    assert source.calls == 1 and first_budget.used == 1 and second_budget.used == 0
    assert metrics == {METRIC_CACHE_MISS: 1, METRIC_REQUESTS: 1, METRIC_CACHE_HIT: 1}


def test_other_type_or_other_tile_is_a_separate_query() -> None:
    source = FakeSource(OK)
    decorated = _decorated(source, {})

    decorated.fetch_features("StrefaPlanistyczna", TILE)
    decorated.fetch_features("AktPlanowaniaPrzestrzennego", TILE)
    decorated.fetch_features("StrefaPlanistyczna", OTHER_TILE)

    assert source.calls == 3


def test_source_error_is_remembered_briefly_to_protect_the_service() -> None:
    clock = FakeClock()
    metrics: dict[str, int] = {}
    failure = RuPogFeaturesResponse(features=(), complete=False, error_code=ERROR_TRANSPORT)
    source = FakeSource(failure)
    decorated = _decorated(source, metrics, clock)

    decorated.fetch_features("StrefaPlanistyczna", TILE)
    decorated.fetch_features("StrefaPlanistyczna", TILE)  # w oknie ujemnego TTL — bez nowego żądania
    clock.now += 61.0
    decorated.fetch_features("StrefaPlanistyczna", TILE)

    assert source.calls == 2
    assert metrics[METRIC_FETCH_ERROR] == 2


def test_budget_exhaustion_is_never_cached_for_other_analyses() -> None:
    metrics: dict[str, int] = {}
    exceeded = RuPogFeaturesResponse(features=(), complete=False, error_code=ERROR_BUDGET_EXCEEDED)
    source = FakeSource(exceeded, requests_per_call=0)
    decorated = _decorated(source, metrics)

    decorated.fetch_features("StrefaPlanistyczna", TILE)
    decorated.fetch_features("StrefaPlanistyczna", TILE)

    assert source.calls == 2  # wyczerpany budżet jednej analizy nie blokuje następnej
    assert METRIC_FETCH_ERROR not in metrics and METRIC_BUDGET_EXCEEDED not in metrics


def test_incomplete_answer_without_error_is_not_cached() -> None:
    source = FakeSource(RuPogFeaturesResponse(features=(), complete=False))
    decorated = _decorated(source, {})

    decorated.fetch_features("StrefaPlanistyczna", TILE)
    decorated.fetch_features("StrefaPlanistyczna", TILE)

    assert source.calls == 2


def test_parallel_analyses_of_the_same_tile_send_one_request() -> None:
    metrics: dict[str, int] = {}
    started = threading.Event()
    release = threading.Event()

    class SlowSource(FakeSource):
        def fetch_features(self, feature_type, tile, budget=None):  # noqa: ANN001, ANN201
            started.set()
            release.wait(timeout=5)
            return super().fetch_features(feature_type, tile, budget)

    source = SlowSource(OK)
    decorated = _decorated(source, metrics)
    budgets = [ThreadSafeRequestBudget(6) for _ in range(4)]
    results: list[RuPogFeaturesResponse] = []

    def run(budget: ThreadSafeRequestBudget) -> None:
        results.append(decorated.fetch_features("StrefaPlanistyczna", TILE, budget))

    threads = [threading.Thread(target=run, args=(budgets[0],))]
    threads[0].start()
    assert started.wait(timeout=5)
    threads += [threading.Thread(target=run, args=(budget,)) for budget in budgets[1:]]
    for thread in threads[1:]:
        thread.start()
    time.sleep(0.05)
    release.set()
    for thread in threads:
        thread.join(timeout=5)

    assert source.calls == 1
    assert sum(budget.used for budget in budgets) == 1  # tylko lider zużył budżet
    assert sorted(item.origin for item in results) == ["network", "shared", "shared", "shared"]
    assert metrics[METRIC_DEDUP_WAIT] == 3
