"""Prosty, ograniczony limiter zapytań w oknie stałym (in-process).

Chroni publiczne endpointy przed nadużyciem. Liczba śledzonych kluczy jest
ograniczona (eksmisja najstarszego), aby uniknąć nieograniczonej kardynalności.
Zwraca informację o dozwoleniu oraz sugerowany czas Retry-After.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int


class FixedWindowRateLimiter:
    """Limiter w oknie stałym: max ``limit`` żądań na ``window_seconds`` na klucz."""

    def __init__(
        self, limit: int, window_seconds: float, max_tracked_keys: int = 10_000
    ) -> None:
        if limit <= 0 or window_seconds <= 0:
            raise ValueError("limit i window_seconds muszą być dodatnie.")
        self._limit = limit
        self._window = window_seconds
        self._max_tracked_keys = max_tracked_keys
        # klucz -> (początek_okna, licznik)
        self._windows: OrderedDict[str, tuple[float, int]] = OrderedDict()

    def check(self, key: str, now: float | None = None) -> RateLimitDecision:
        current = now if now is not None else time.monotonic()
        window_start, count = self._windows.get(key, (current, 0))

        if current - window_start >= self._window:
            # Nowe okno.
            window_start, count = current, 0

        if count >= self._limit:
            retry_after = max(1, int(self._window - (current - window_start)) + 1)
            self._windows[key] = (window_start, count)
            self._windows.move_to_end(key)
            return RateLimitDecision(allowed=False, retry_after_seconds=retry_after)

        self._windows[key] = (window_start, count + 1)
        self._windows.move_to_end(key)
        self._evict_if_needed()
        return RateLimitDecision(allowed=True, retry_after_seconds=0)

    def _evict_if_needed(self) -> None:
        while len(self._windows) > self._max_tracked_keys:
            self._windows.popitem(last=False)

    def reset(self) -> None:
        self._windows.clear()
