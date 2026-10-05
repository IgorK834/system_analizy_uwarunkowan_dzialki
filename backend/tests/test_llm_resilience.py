"""Ponowienia, ``Retry-After`` i wyłącznik awaryjny (PV3-10): logika bez HTTP i bez uśpień."""

from __future__ import annotations

import random
from datetime import datetime, timezone

import pytest

from app.modules.planning.infrastructure.llm.resilience import (
    CircuitBreaker,
    RetryPolicy,
    backoff_delay,
    parse_retry_after,
    retry_wait,
)


def test_backoff_grows_exponentially_up_to_the_ceiling_with_bounded_jitter() -> None:
    policy = RetryPolicy(base_delay_seconds=1.0, max_delay_seconds=10.0, jitter=0.5)
    rng = random.Random(3)
    for attempt, ceiling in [(0, 1.0), (1, 2.0), (2, 4.0), (3, 8.0), (4, 10.0), (9, 10.0)]:
        delays = [backoff_delay(attempt, policy, rng) for _ in range(200)]
        assert all(ceiling * 0.5 <= delay <= ceiling for delay in delays)
        assert len({round(delay, 6) for delay in delays}) > 50  # jest losowanie, nie stała


def test_zero_jitter_is_deterministic_and_full_jitter_can_reach_zero() -> None:
    assert backoff_delay(2, RetryPolicy(base_delay_seconds=1.0, jitter=0.0), random.Random(1)) == 4.0
    low = min(backoff_delay(1, RetryPolicy(jitter=1.0), random.Random(seed)) for seed in range(300))
    assert low < 0.1


def test_retry_after_is_a_lower_bound_and_an_excessive_one_aborts_the_wait() -> None:
    policy = RetryPolicy(base_delay_seconds=1.0, max_delay_seconds=4.0, max_retry_after_seconds=30.0, jitter=0.0)
    rng = random.Random(1)
    assert retry_wait(0, policy, rng, None) == 1.0
    assert retry_wait(0, policy, rng, 7.0) == 7.0  # Retry-After dłuższe niż backoff
    assert retry_wait(3, policy, rng, 2.0) == 4.0  # backoff dłuższy niż Retry-After
    assert retry_wait(0, policy, rng, 30.0) == 30.0
    assert retry_wait(0, policy, rng, 31.0) is None  # powyżej limitu czekania: nie czekamy


@pytest.mark.parametrize(
    "kwargs", [{"max_retries": -1}, {"base_delay_seconds": -1.0}, {"max_delay_seconds": -1.0}, {"jitter": 1.5}, {"jitter": -0.1}]
)
def test_invalid_retry_policy_is_rejected(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        RetryPolicy(**kwargs)


NOW = datetime(2026, 10, 3, 12, 0, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ("   ", None),
        ("5", 5.0),
        (" 12 ", 12.0),
        ("0", 0.0),
        ("999999999", 86400.0),  # górne ograniczenie
        ("-3", None),
        ("1.5", None),
        ("soon", None),
        ("Sat, 03 Oct 2026 12:00:30 GMT", 30.0),
        ("Sat, 03 Oct 2026 11:00:00 GMT", 0.0),  # data z przeszłości
        ("Mon, 05 Oct 2026 12:00:00 GMT", 86400.0),
        ("Sat, 03 Oct 2026 12:01:00", 60.0),  # bez strefy: traktowana jako UTC
    ],
)
def test_retry_after_parsing(value: str | None, expected: float | None) -> None:
    assert parse_retry_after(value, now=NOW) == expected


def test_retry_after_without_explicit_clock_uses_the_current_time() -> None:
    assert parse_retry_after("Thu, 01 Jan 1970 00:00:00 GMT") == 0.0


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_breaker_state_machine() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=10.0, clock=clock)
    assert breaker.state == "closed" and breaker.allow()
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == "closed" and breaker.consecutive_failures == 2
    breaker.record_failure()
    assert breaker.state == "open" and not breaker.allow()
    clock.now = 9.9
    assert breaker.state == "open" and not breaker.allow()
    clock.now = 10.0
    assert breaker.state == "half_open"
    assert breaker.allow() is True  # jedna próba
    assert breaker.allow() is False  # druga równolegle nie przechodzi
    breaker.record_failure()  # próba nieudana → znów otwarty od teraz
    assert breaker.state == "open" and not breaker.allow()
    clock.now = 21.0
    assert breaker.allow() is True
    breaker.record_success()
    assert breaker.state == "closed" and breaker.consecutive_failures == 0 and breaker.allow()


def test_released_probe_allows_another_one() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=5.0, clock=clock)
    breaker.record_failure()
    clock.now = 6.0
    assert breaker.allow() and not breaker.allow()
    breaker.release_probe()
    assert breaker.allow()


def test_breaker_uses_the_monotonic_clock_by_default() -> None:
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=3600.0)
    breaker.record_failure()
    assert breaker.state == "open"


@pytest.mark.parametrize("args", [(0, 1.0), (1, -1.0)])
def test_invalid_breaker_parameters(args: tuple[int, float]) -> None:
    with pytest.raises(ValueError):
        CircuitBreaker(*args)
