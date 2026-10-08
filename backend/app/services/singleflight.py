"""Single-flight per klucz (np. ``parcel_identifier``): jedna analiza zamiast N identycznych (AU-007).

Bez blokady 6 równoległych ``POST /analyze`` dla tej samej nowej działki wykonywało 6 pełnych analiz
(6 wierszy w ``analyses``, 6× pełne odpytanie usług rządowych po 14–26 s). Teraz:

* **w procesie** pierwsze żądanie dla klucza jest liderem, kolejne czekają na jego wynik i go dostają
  (nie liczą drugi raz). Rejestr jest chroniony ``threading.Lock`` i budzi czekających przez
  ``call_soon_threadsafe``, więc nie zakłada jednej pętli zdarzeń;
* **między workerami** lider trzyma sesyjną blokadę doradczą PostgreSQL
  (``pg_try_advisory_lock(hashtextextended(klucz, 0))``) na osobnym połączeniu, odpytywaną co
  ``poll`` sekund z limitem czasu. Blokada znika razem z połączeniem, więc awaria lidera (wyjątek,
  anulowanie, zabity proces) nie zostawia jej na zawsze. Po jej zdjęciu żądanie ponownie sprawdza
  cache (``reuse``) zamiast liczyć od nowa;
* oczekiwanie ma limit (``deadline``); po przekroczeniu żądanie dostaje ``SingleFlightTimeoutError``
  (503 + ``Retry-After``), a nie wisi. Gdy lider padnie wyjątkiem lub zostanie anulowany, czekający
  nie dziedziczą jego błędu: jeden z nich przejmuje rolę lidera (ponowna próba), a po
  ``max_attempts`` nieudanych liderach błąd jest propagowany, żeby awaria nie mnożyła pracy.

Metryki: licznik ``analysis_singleflight.{leader,wait,takeover,timeout}`` w ``upstream_metrics``
oraz gauge ``analysis_singleflight_waiters`` (liczba aktualnie czekających żądań; ``/health/upstream``).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Final, Generic, Literal, TypeVar

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.logging import log_analysis_event
from app.core.metrics import upstream_metrics
from app.core.settings import settings
from app.db.session import advisory_lock_engine

logger = logging.getLogger(__name__)

T = TypeVar("T")

WAITERS_GAUGE: Final[str] = "analysis_singleflight_waiters"
_COUNTER_PREFIX: Final[str] = "analysis_singleflight"  # liczniki blokady analiz; lookup ma własny przedrostek
_LOCK_NAMESPACE: Final[str] = "analysis-singleflight:"
_RETRY_AFTER_SECONDS: Final[int] = 5


class SingleFlightTimeoutError(Exception):
    """Inna analiza tej samej działki trwa dłużej niż limit oczekiwania."""

    def __init__(self, key: str, waited_seconds: float) -> None:
        super().__init__(
            "Analiza tej działki jest już w toku i trwa dłużej niż zwykle. "
            "Spróbuj ponownie za chwilę — wynik zostanie zapisany."
        )
        self.key = key
        self.waited_seconds = waited_seconds
        self.response_headers = {"Retry-After": str(_RETRY_AFTER_SECONDS)}


class PostgresAdvisoryLock:
    """Sesyjna blokada doradcza PostgreSQL trzymana na dedykowanym połączeniu.

    Połączenie jest poza pulą (``NullPool``, ``AUTOCOMMIT``), więc długie analizy nie wyczerpują puli
    sesji żądań i blokada nie trzyma otwartej transakcji. Brak PostgreSQL albo błąd połączenia
    degraduje się do samego single-flight w procesie (z ostrzeżeniem w logu): awarię bazy zgłosi
    normalna ścieżka analizy.
    """

    def __init__(self, poll_seconds: Callable[[], float]) -> None:
        self._poll_seconds = poll_seconds

    @asynccontextmanager
    async def hold(self, key: str, timeout_seconds: float) -> AsyncIterator[None]:
        connection = await self._open()
        try:
            if connection is not None:
                await self._acquire(connection, key, timeout_seconds)
            yield
        finally:
            if connection is not None:
                self._close(connection)

    async def _open(self) -> Connection | None:
        engine = advisory_lock_engine()
        if engine.dialect.name != "postgresql":
            return None
        try:
            return await asyncio.to_thread(engine.connect)
        except Exception:  # noqa: BLE001 - blokada między workerami jest dodatkiem, nie warunkiem
            logger.warning(
                "singleflight: brak połączenia dla blokady doradczej, tylko blokada w procesie",
                exc_info=True,
            )
            upstream_metrics.increment(f"{_COUNTER_PREFIX}.lock_unavailable")
            return None

    async def _acquire(self, connection: Connection, key: str, timeout_seconds: float) -> None:
        deadline = time.monotonic() + timeout_seconds
        started = time.monotonic()
        while True:
            try:
                acquired = await asyncio.to_thread(self._try_lock, connection, key)
            except Exception:  # noqa: BLE001 - jak wyżej: degradacja zamiast awarii analizy
                logger.warning("singleflight: błąd blokady doradczej, kontynuacja bez niej", exc_info=True)
                upstream_metrics.increment(f"{_COUNTER_PREFIX}.lock_unavailable")
                return
            if acquired:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SingleFlightTimeoutError(key, time.monotonic() - started)
            await asyncio.sleep(min(self._poll_seconds(), remaining))

    @staticmethod
    def _try_lock(connection: Connection, key: str) -> bool:
        return bool(
            connection.execute(
                text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"),
                {"key": _LOCK_NAMESPACE + key},
            ).scalar()
        )

    @staticmethod
    def _close(connection: Connection) -> None:
        # Zamknięcie sesji zwalnia blokadę także wtedy, gdy zadanie zostało anulowane (bez ``await``).
        try:
            connection.close()
        except Exception:  # noqa: BLE001
            logger.warning("singleflight: nie udało się zamknąć połączenia blokady", exc_info=True)


@dataclass
class _Outcome(Generic[T]):
    kind: Literal["ok", "error", "abandoned"]
    value: T | None = None
    error: Exception | None = None


class _Flight(Generic[T]):
    def __init__(self) -> None:
        self.outcome: _Outcome[T] | None = None
        self.waiters: list[tuple[asyncio.AbstractEventLoop, asyncio.Future[None]]] = []


def _resolve(future: asyncio.Future[None]) -> None:
    if not future.done():
        future.set_result(None)


class SingleFlight:
    """Rejestr lotów: lider liczy, pozostali dostają jego wynik."""

    def __init__(
        self,
        name: str = "analysis",
        *,
        wait_seconds: Callable[[], float] = lambda: settings.analysis_singleflight_wait_seconds,
        poll_seconds: Callable[[], float] = lambda: settings.analysis_singleflight_poll_seconds,
        cross_process: Callable[[], bool] = lambda: settings.analysis_singleflight_cross_process,
        max_attempts: int = 2,
    ) -> None:
        self._name = name
        self._flights: dict[str, _Flight] = {}
        self._lock = threading.Lock()
        self._waiting = 0
        self._wait_seconds = wait_seconds
        self._cross_process = cross_process
        self._advisory = PostgresAdvisoryLock(poll_seconds)
        self._max_attempts = max_attempts

    # --- metryki ---------------------------------------------------------------

    @property
    def gauge_name(self) -> str:
        return f"{self._name}_singleflight_waiters"

    def waiters(self) -> int:
        """Liczba żądań, które w tej chwili czekają na wynik lidera (gauge ``<name>_singleflight_waiters``)."""
        with self._lock:
            return self._waiting

    def in_flight(self) -> int:
        with self._lock:
            return len(self._flights)

    # --- API -------------------------------------------------------------------

    async def run(
        self,
        key: str,
        work: Callable[[], Awaitable[T]],
        *,
        reuse: Callable[[], Awaitable[T | None]] | None = None,
        accept: Callable[[T], bool] = lambda _value: True,
        share_errors: bool = False,
        lock_across_processes: bool = True,
    ) -> T:
        """Wykonuje ``work`` raz na klucz naraz; równoległe wywołania dostają ten sam wynik.

        ``reuse`` jest wołane przez lidera po zdobyciu blokady między procesami i może zwrócić gotowy
        wynik (np. z cache zapisanego przez innego workera) zamiast liczyć od nowa. ``accept``
        decyduje, czy wynik lidera nadaje się dla czekającego (np. ``force_refresh`` wymaga wyniku
        nie starszego niż jego żądanie); odrzucony wynik oznacza ponowną próbę. Przy
        ``share_errors`` czekający dostają błąd lidera zamiast ponawiać.
        """
        started = time.monotonic()
        deadline = started + self._wait_seconds()
        failures = 0
        waited = False
        while True:
            flight, is_leader = self._join(key)
            if is_leader:
                role = "takeover" if waited else "leader"
                upstream_metrics.increment(f"{self._name}_singleflight.{role}")
                log_analysis_event(
                    "singleflight",
                    singleflight="leader",
                    parcel_identifier=key if self._name == "analysis" else None,
                    scope=self._name,
                )
                return await self._lead(
                    key, flight, work, reuse, deadline, lock_across_processes and self._cross_process()
                )

            if not waited:
                upstream_metrics.increment(f"{self._name}_singleflight.wait")
                log_analysis_event(
                    "singleflight",
                    singleflight="wait",
                    parcel_identifier=key if self._name == "analysis" else None,
                    scope=self._name,
                )
            waited = True
            outcome = await self._wait(key, flight, deadline, started)
            if outcome.kind == "ok":
                if accept(outcome.value):  # type: ignore[arg-type]
                    log_analysis_event(
                        "singleflight_shared",
                        singleflight="wait",
                        parcel_identifier=key if self._name == "analysis" else None,
                        waited_ms=int((time.monotonic() - started) * 1000),
                        scope=self._name,
                    )
                    return outcome.value  # type: ignore[return-value]
            elif outcome.kind == "error":
                if share_errors:
                    assert outcome.error is not None
                    raise outcome.error
                failures += 1
                if failures >= self._max_attempts:
                    assert outcome.error is not None
                    raise outcome.error
            # ``abandoned`` (anulowany lider) i odrzucony wynik: kolejna próba w następnym locie.

    # --- implementacja -----------------------------------------------------------

    def _join(self, key: str) -> tuple[_Flight, bool]:
        with self._lock:
            flight = self._flights.get(key)
            if flight is not None:
                return flight, False
            flight = _Flight()
            self._flights[key] = flight
            return flight, True

    async def _lead(
        self,
        key: str,
        flight: _Flight[T],
        work: Callable[[], Awaitable[T]],
        reuse: Callable[[], Awaitable[T | None]] | None,
        deadline: float,
        across_processes: bool,
    ) -> T:
        try:
            if across_processes:
                async with self._advisory.hold(key, max(deadline - time.monotonic(), 0.0)):
                    value = await self._reuse_or_work(work, reuse)
            else:
                value = await self._reuse_or_work(work, reuse)
        except SingleFlightTimeoutError as exc:
            upstream_metrics.increment(f"{self._name}_singleflight.timeout")
            self._finish(key, flight, _Outcome("error", error=exc))
            raise
        except Exception as exc:
            self._finish(key, flight, _Outcome("error", error=exc))
            raise
        except BaseException:
            # Anulowanie (rozłączony klient) nie jest błędem analizy: czekający przejmują pracę.
            self._finish(key, flight, _Outcome("abandoned"))
            raise
        self._finish(key, flight, _Outcome("ok", value=value))
        return value

    @staticmethod
    async def _reuse_or_work(
        work: Callable[[], Awaitable[T]], reuse: Callable[[], Awaitable[T | None]] | None
    ) -> T:
        if reuse is not None:
            reused = await reuse()
            if reused is not None:
                return reused
        return await work()

    async def _wait(self, key: str, flight: _Flight, deadline: float, started: float) -> _Outcome:
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[None] = loop.create_future()
        with self._lock:
            if flight.outcome is not None:
                return flight.outcome
            flight.waiters.append((loop, waiter))
            self._waiting += 1
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            await asyncio.wait_for(waiter, remaining)
        except TimeoutError:
            upstream_metrics.increment(f"{self._name}_singleflight.timeout")
            raise SingleFlightTimeoutError(key, time.monotonic() - started) from None
        finally:
            with self._lock:
                self._waiting -= 1
                try:
                    flight.waiters.remove((loop, waiter))
                except ValueError:
                    pass
        outcome = flight.outcome
        assert outcome is not None
        return outcome

    def _finish(self, key: str, flight: _Flight, outcome: _Outcome) -> None:
        with self._lock:
            flight.outcome = outcome
            if self._flights.get(key) is flight:
                del self._flights[key]
            waiters = list(flight.waiters)
        for loop, waiter in waiters:
            try:
                loop.call_soon_threadsafe(_resolve, waiter)
            except RuntimeError:  # pętla czekającego już zamknięta
                continue


analysis_single_flight = SingleFlight("analysis")
"""Blokada analiz per ``parcel_identifier``."""

lookup_single_flight = SingleFlight("lookup")
"""Współdzielenie identyfikacji działki (ULDK) przez identyczne równoległe żądania."""
