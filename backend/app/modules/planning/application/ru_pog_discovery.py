"""Budżet żądań, cache zapytań i deduplikacja dla discovery POG w Rejestrze Urbanistycznym (AU-105, ADR-023).

Jedna analiza nie może wysłać do ``rejestr-urbanistyczny.gov.pl`` więcej niż ``RequestBudget.limit`` żądań HTTP
(przed AU-105: 46–288, w logach 384 żądania na 2 analizy). Dwa mechanizmy tego pilnują:

* **budżet** — licznik zasilany przed każdym żądaniem HTTP (także ponowieniem). Po wyczerpaniu żądanie nie jest
  wysyłane, a odpowiedź jest niepełna (``complete=False``) — wynik discovery nie może wtedy twierdzić, że obiektów
  nie ma;
* **cache zapytań** per (typ obiektu, kafel ``bbox``) z TTL oraz **deduplikacja** — gdy dwie analizy potrzebują tego
  samego kafla w tym samym czasie, żądanie wysyła jedna, a druga czeka na jej wynik. Błąd źródła jest
  zapamiętywany krótko (ujemny TTL), żeby awaria RU nie powodowała lawiny ponowień; odpowiedź niepełna nie jest
  zapamiętywana wcale.

Cache żyje w pamięci procesu (jak ``InMemoryPogTileCache``): po restarcie zaczyna od zera, a przy N procesach każdy ma
własny widok. Analizy tej samej działki są i tak serializowane przez single-flight (AU-007).
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Final, Generic, Literal, TypeVar

from app.modules.planning.application.ports import (
    RequestBudget,
    RequestBudgetExceeded,
    RuPogFeaturesResponse,
    RuPogFeaturesSource,
)
from app.modules.planning.domain.pog_discovery import ERROR_BUDGET_EXCEEDED, Bbox, PogFeatureType, tile_key

T = TypeVar("T")
MetricsSink = Callable[[str, int], None]
CacheOrigin = Literal["hit", "miss", "shared"]

# Licznik dla ``/health/upstream`` (nazwy o stałej kardynalności).
METRIC_REQUESTS: Final[str] = "ru_pog.requests"
METRIC_CACHE_HIT: Final[str] = "ru_pog.cache_hit"
METRIC_CACHE_MISS: Final[str] = "ru_pog.cache_miss"
METRIC_DEDUP_WAIT: Final[str] = "ru_pog.dedup_wait"
METRIC_BUDGET_EXCEEDED: Final[str] = "ru_pog.budget_exceeded"
METRIC_FETCH_ERROR: Final[str] = "ru_pog.fetch_error"

# Najdłuższe oczekiwanie na wynik cudzego żądania; po nim wołający pobiera dane sam (z własnym budżetem).
DEDUP_WAIT_SECONDS: Final[float] = 60.0


class ThreadSafeRequestBudget:
    """Budżet żądań jednej analizy. Bezpieczny wątkowo: cztery typy obiektów są pobierane równolegle."""

    def __init__(self, limit: int) -> None:
        if limit < 1:
            raise ValueError("Budżet żądań musi wynosić co najmniej 1.")
        self._limit = limit
        self._used = 0
        self._denied = 0
        self._lock = threading.Lock()

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def used(self) -> int:
        with self._lock:
            return self._used

    @property
    def denied(self) -> int:
        """Ile razy odmówiono żądania po wyczerpaniu budżetu (alarm, gdy > 0)."""
        with self._lock:
            return self._denied

    def acquire(self) -> None:
        with self._lock:
            if self._used >= self._limit:
                self._denied += 1
                raise RequestBudgetExceeded(f"Wyczerpano budżet {self._limit} żądań HTTP do Rejestru Urbanistycznego.")
            self._used += 1


@dataclass
class _Entry(Generic[T]):
    value: T
    expires_at: float


class _InFlight:
    def __init__(self) -> None:
        self.done = threading.Event()


class TtlQueryCache(Generic[T]):
    """Cache TTL z deduplikacją równoległych pobrań tego samego klucza (wątkowo-bezpieczny, LRU po liczbie wpisów)."""

    def __init__(
        self,
        *,
        max_entries: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_entries < 1:
            raise ValueError("Cache wymaga co najmniej jednego wpisu.")
        self._max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[str, _Entry[T]] = OrderedDict()
        self._inflight: dict[str, _InFlight] = {}
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def get_or_fetch(
        self,
        key: str,
        fetch: Callable[[], T],
        *,
        ttl_of: Callable[[T], float],
        wait_seconds: float = DEDUP_WAIT_SECONDS,
    ) -> tuple[T, CacheOrigin]:
        """Wartość z cache, wynik cudzego pobrania albo własne pobranie.

        ``ttl_of`` zwraca czas życia wyniku w sekundach; ``0`` oznacza „nie zapamiętuj”. Wyjątek z ``fetch`` nie jest
        zapamiętywany, a czekający pobierają wtedy dane sami (jeden z nich zostaje liderem).
        """
        deadline = self._clock() + wait_seconds
        while True:
            with self._lock:
                entry = self._entries.get(key)
                if entry is not None and entry.expires_at > self._clock():
                    self._entries.move_to_end(key)
                    return entry.value, "hit"
                if entry is not None:
                    del self._entries[key]
                inflight = self._inflight.get(key)
                if inflight is None:
                    inflight = _InFlight()
                    self._inflight[key] = inflight
                    leader = True
                else:
                    leader = False
            if leader:
                try:
                    value = fetch()
                    ttl = ttl_of(value)
                    if ttl > 0:
                        with self._lock:
                            self._entries[key] = _Entry(value, self._clock() + ttl)
                            self._entries.move_to_end(key)
                            while len(self._entries) > self._max_entries:
                                self._entries.popitem(last=False)
                    return value, "miss"
                finally:
                    with self._lock:
                        self._inflight.pop(key, None)
                    inflight.done.set()
            remaining = deadline - self._clock()
            if remaining <= 0 or not inflight.done.wait(timeout=remaining):
                # Lider nie odpowiedział w czasie — pobierz sam, bez wpisywania się jako lider (nie psuje cache).
                return fetch(), "miss"
            with self._lock:
                entry = self._entries.get(key)
                if entry is not None and entry.expires_at > self._clock():
                    return entry.value, "shared"
            # Lider padł albo wynik nie nadaje się do zapamiętania (odpowiedź niepełna) — nowa runda.


class CachedRuPogFeaturesSource:
    """Dekorator portu ``RuPogFeaturesSource``: cache zapytań (typ, kafel), deduplikacja, budżet i liczniki."""

    def __init__(
        self,
        inner: RuPogFeaturesSource,
        *,
        cache: TtlQueryCache[RuPogFeaturesResponse],
        ttl_seconds: float,
        negative_ttl_seconds: float,
        metrics: MetricsSink | None = None,
    ) -> None:
        self._inner = inner
        self._cache = cache
        self._ttl = ttl_seconds
        self._negative_ttl = negative_ttl_seconds
        self._metrics = metrics or (lambda _name, _amount: None)

    def _ttl_of(self, response: RuPogFeaturesResponse) -> float:
        if response.error_code is not None:
            # Błąd źródła chroni RU przed lawiną ponowień, ale budżet analizy nie jest błędem usługi.
            return 0.0 if response.error_code == ERROR_BUDGET_EXCEEDED else self._negative_ttl
        return self._ttl if response.complete else 0.0

    def fetch_features(
        self, feature_type: PogFeatureType, tile: Bbox, budget: RequestBudget | None = None
    ) -> RuPogFeaturesResponse:
        response, origin = self._cache.get_or_fetch(
            tile_key(feature_type, tile),
            lambda: self._inner.fetch_features(feature_type, tile, budget),
            ttl_of=self._ttl_of,
        )
        if origin == "miss":
            # Liczba żądań to licznik samego wywołania adaptera (budżet jest wspólny dla równoległych typów).
            consumed = max(response.requests, 0)
            self._metrics(METRIC_CACHE_MISS, 1)
            self._metrics(METRIC_REQUESTS, consumed)
            if response.error_code is not None and response.error_code != ERROR_BUDGET_EXCEEDED:
                self._metrics(METRIC_FETCH_ERROR, 1)
            return replace(response, requests=consumed, origin="network")
        self._metrics(METRIC_CACHE_HIT if origin == "hit" else METRIC_DEDUP_WAIT, 1)
        return replace(response, requests=0, origin="cache" if origin == "hit" else "shared")
