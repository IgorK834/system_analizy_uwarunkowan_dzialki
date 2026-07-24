"""Ograniczony cache TTL z kanonicznymi kluczami.

Chroni przed nieograniczoną kardynalnością (twardy limit liczby kluczy z
eksmisją najstarszego wpisu) oraz przeterminowaniem (TTL). Przeznaczony do
cache'owania odpowiedzi dostawcy wyszukiwania w obrębie jednego procesu.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Generic, TypeVar

ValueT = TypeVar("ValueT")


class BoundedTTLCache(Generic[ValueT]):
    """Cache LRU z TTL i twardym limitem liczby wpisów."""

    def __init__(self, max_entries: int, ttl_seconds: float) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries musi być dodatnie.")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds musi być dodatnie.")
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._store: OrderedDict[str, tuple[float, ValueT]] = OrderedDict()

    def get(self, key: str) -> ValueT | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at < time.monotonic():
            # Wpis wygasł — usuwamy leniwie przy odczycie.
            del self._store[key]
            return None
        self._store.move_to_end(key)
        return value

    def set(self, key: str, value: ValueT) -> None:
        if key in self._store:
            self._store.move_to_end(key)
        self._store[key] = (time.monotonic() + self._ttl_seconds, value)
        while len(self._store) > self._max_entries:
            # Eksmisja najstarszego wpisu (ochrona przed nieograniczoną kardynalnością).
            self._store.popitem(last=False)

    def __len__(self) -> int:
        return len(self._store)

    def clear(self) -> None:
        self._store.clear()
