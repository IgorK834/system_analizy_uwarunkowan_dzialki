"""Prosty, ograniczony limiter zapytań w oknie stałym (in-process).

Chroni publiczne endpointy przed nadużyciem. Liczba śledzonych kluczy jest
ograniczona (eksmisja najstarszego), aby uniknąć nieograniczonej kardynalności.
Zwraca informację o dozwoleniu oraz sugerowany czas Retry-After.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import HTTPException, Query, Request

from app.core.settings import settings


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


_LIMITERS: list[FixedWindowRateLimiter] = []
_WINDOW_SECONDS = 60.0


def client_key(request: Request) -> str:
    """Klucz klienta: adres połączenia albo wpis dopisany przez zaufany proxy."""
    if settings.rate_limit_trust_forwarded_for:
        forwarded = request.headers.get("x-forwarded-for", "")
        # Ostatni wpis dodał zaufany proxy; wcześniejsze mógł podszyć klient.
        last = forwarded.split(",")[-1].strip()
        if last:
            return last
    client = request.client
    return client.host if client is not None else "unknown"


def _enforce(limiter: FixedWindowRateLimiter, request: Request) -> None:
    if not settings.rate_limit_enabled:
        return
    decision = limiter.check(client_key(request))
    if not decision.allowed:
        raise HTTPException(
            status_code=429,
            detail="Przekroczono limit zapytań. Spróbuj ponownie za chwilę.",
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )


def rate_limit(limit: int) -> Callable[[Request], Awaitable[None]]:
    """Zależność FastAPI: ``limit`` żądań na minutę na klienta (429 po przekroczeniu).

    Zależność jest ``async``, więc limiter działa wyłącznie w wątku pętli
    zdarzeń — ``FixedWindowRateLimiter`` nie jest thread-safe.
    """
    limiter = FixedWindowRateLimiter(limit=limit, window_seconds=_WINDOW_SECONDS)
    _LIMITERS.append(limiter)

    async def dependency(request: Request) -> None:
        _enforce(limiter, request)

    return dependency


def rate_limit_refresh(limit: int) -> Callable[[Request, bool], Awaitable[None]]:
    """Jak ``rate_limit``, ale liczy tylko żądania z ``force_refresh=true``."""
    limiter = FixedWindowRateLimiter(limit=limit, window_seconds=_WINDOW_SECONDS)
    _LIMITERS.append(limiter)

    async def dependency(
        request: Request,
        force_refresh: bool = Query(False, include_in_schema=False),
    ) -> None:
        if force_refresh:
            _enforce(limiter, request)

    return dependency


def reset_all_rate_limiters() -> None:
    """Czyści liczniki wszystkich limiterów utworzonych przez ``rate_limit*``."""
    for limiter in _LIMITERS:
        limiter.reset()
