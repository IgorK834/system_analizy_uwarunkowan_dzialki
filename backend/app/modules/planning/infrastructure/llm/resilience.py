"""Ponowienia z wykładniczym opóźnieniem i losowaniem oraz wyłącznik awaryjny (PV3-10).

Moduł jest niezależny od HTTP i od dostawcy; zegar, losowanie i uśpienie wstrzykuje wywołujący,
więc testy nie czekają i są deterministyczne.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Final, Literal

BreakerState = Literal["closed", "open", "half_open"]


@dataclass(frozen=True)
class RetryPolicy:
    """``max_retries`` to liczba PONOWIEŃ (łącznie ``max_retries + 1`` prób).

    Opóźnienie przed ponowieniem ``n`` (od 0): ``min(max_delay, base * 2**n)`` pomniejszone
    losowo o do ``jitter`` (pełne losowanie nie zsynchronizuje klientów). ``Retry-After`` jest
    dolną granicą opóźnienia; gdy żąda czekania dłuższego niż ``max_retry_after``, nie czekamy
    i kończymy (opóźnienie dłuższe niż budżet czasu analizy jest gorsze niż błąd).
    """

    max_retries: int = 2
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 20.0
    max_retry_after_seconds: float = 30.0
    jitter: float = 0.5

    def __post_init__(self) -> None:
        if self.max_retries < 0 or self.base_delay_seconds < 0 or self.max_delay_seconds < 0:
            raise ValueError("Parametry ponowień nie mogą być ujemne.")
        if not 0.0 <= self.jitter <= 1.0:
            raise ValueError("jitter musi mieścić się w 0–1.")


def backoff_delay(attempt: int, policy: RetryPolicy, rng: random.Random) -> float:
    """Opóźnienie przed ponowieniem po nieudanej próbie ``attempt`` (0 = pierwsza próba)."""
    ceiling = min(policy.max_delay_seconds, policy.base_delay_seconds * (2**attempt))
    return ceiling * (1.0 - policy.jitter * rng.random())


def retry_wait(
    attempt: int, policy: RetryPolicy, rng: random.Random, retry_after: float | None
) -> float | None:
    """Ile czekać przed ponowieniem albo ``None``, gdy ``Retry-After`` przekracza dopuszczalne czekanie."""
    delay = backoff_delay(attempt, policy, rng)
    if retry_after is None:
        return delay
    if retry_after > policy.max_retry_after_seconds:
        return None
    return max(delay, retry_after)


_MAX_RETRY_AFTER_PARSE: Final[float] = 24 * 3600.0


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """``Retry-After`` jako liczba sekund (cała liczba albo data HTTP); błędny zapis → ``None``."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if text.isdigit():
        return min(float(text), _MAX_RETRY_AFTER_PARSE)
    try:
        moment = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return max(0.0, min((moment - current).total_seconds(), _MAX_RETRY_AFTER_PARSE))


class CircuitBreaker:
    """Wyłącznik awaryjny: po ``failure_threshold`` kolejnych awariach dostępności otwiera się na
    ``cooldown_seconds``; potem przepuszcza jedną próbę (half-open) — sukces zamyka, porażka
    otwiera ponownie. Błędy treści odpowiedzi (zły JSON, naruszenie schematu) nie są awariami
    dostępności i nie liczą się."""

    def __init__(
        self,
        failure_threshold: int = 5,
        cooldown_seconds: float = 60.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if failure_threshold < 1 or cooldown_seconds < 0:
            raise ValueError("Niepoprawne parametry wyłącznika awaryjnego.")
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._clock = clock or time.monotonic
        self._failures = 0
        self._opened_at: float | None = None
        self._probe_in_flight = False

    @property
    def state(self) -> BreakerState:
        if self._opened_at is None:
            return "closed"
        if self._clock() - self._opened_at >= self.cooldown_seconds:
            return "half_open"
        return "open"

    @property
    def consecutive_failures(self) -> int:
        return self._failures

    def allow(self) -> bool:
        """Czy wolno wysłać żądanie; w stanie half-open przepuszcza dokładnie jedną próbę naraz."""
        state = self.state
        if state == "closed":
            return True
        if state == "open":
            return False
        if self._probe_in_flight:
            return False
        self._probe_in_flight = True
        return True

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None
        self._probe_in_flight = False

    def record_failure(self) -> None:
        self._probe_in_flight = False
        self._failures += 1
        if self._failures >= self.failure_threshold or self._opened_at is not None:
            self._opened_at = self._clock()

    def release_probe(self) -> None:
        """Zwalnia próbę half-open po wyniku niebędącym awarią dostępności ani sukcesem."""
        self._probe_in_flight = False
