"""Limity zużycia modelu językowego między analizami (PV3-15, ADR-012).

Adapter ``BudgetedStructuredExtractionProvider`` implementuje port ``StructuredExtractionProvider`` i
owija właściwego dostawcę. Przed KAŻDYM żądaniem, bez wysyłania czegokolwiek, sprawdza kolejno:

1. częstotliwość w procesie (``LocalRateLimiter``, żądania na minutę) → ``local_rate_limit``;
2. współbieżność w procesie (``ConcurrencyLimiter``, krótkie czekanie) → ``concurrency_limit``;
3. twardy limit miesięczny i dobowy (tokeny i koszt szacowany) w trwałym rejestrze zużycia
   (``UsageLedger``) → ``monthly_limit_reached`` / ``daily_limit_reached``; rejestr niedostępny →
   ``usage_ledger_unavailable`` (zasada „fail closed”: bez rejestru nie ma wywołania).

Rejestr rezerwuje najgorszy przypadek (szacowane wejście + limit wyjścia) PRZED wysłaniem i rozlicza
faktyczne zużycie po odpowiedzi, więc żądania w locie i wiele procesów nie przekroczą limitu.
Sprawdzenie i rezerwacja w PostgreSQL biegną pod blokadą doradczą transakcji. Granice doby i miesiąca
liczone są w UTC. Limity w obrębie jednej analizy (żądania, tokeny na analizę/dokument/żądanie, termin
czasu) egzekwuje warstwa aplikacji (``llm_pipeline.BudgetTracker``).

Moduł zna tylko port i własny model ORM rejestru — nie domenę MPZP, nie ustawienia i nie metryki.
"""

from __future__ import annotations

import asyncio
import math
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Final, Protocol

from sqlalchemy import case, func, select, text
from sqlalchemy.orm import Session

from app.models.mpzp_llm_usage import MpzpLlmUsage
from app.modules.planning.application.ports import (
    StructuredExtractionError,
    StructuredExtractionErrorCode,
    StructuredExtractionProvider,
    StructuredExtractionRequest,
    StructuredExtractionResult,
)

Code = StructuredExtractionErrorCode
CHARS_PER_TOKEN: Final[int] = 4
# Klucz blokady doradczej PostgreSQL serializującej sprawdzenie i rezerwację budżetu.
LEDGER_LOCK_KEY: Final[int] = 2_015_020_015
# Błędy, przy których żądanie NIE dotarło do dostawcy: rezerwacja jest zwalniana, nie rozliczana.
NOT_SENT: Final[frozenset[Code]] = frozenset(
    {Code.CONFIGURATION, Code.REQUEST_TOO_LARGE, Code.CIRCUIT_OPEN, Code.REPLAY_MISS, Code.REPLAY_INTEGRITY}
)

Sleeper = Callable[[float], Awaitable[None]]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class TokenPricing:
    """Cena za 1 mln tokenów (USD) — do szacowania kosztu rezerwacji i rozliczenia."""

    input_usd_per_mtok: float = 1.50
    output_usd_per_mtok: float = 7.50

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return round((input_tokens * self.input_usd_per_mtok + output_tokens * self.output_usd_per_mtok) / 1e6, 6)


@dataclass(frozen=True)
class UsageLimits:
    """Twarde limity dobowe i miesięczne; ``None`` = brak limitu (nigdy 0 jako „brak”)."""

    daily_tokens: int | None = None
    daily_cost_usd: float | None = None
    monthly_tokens: int | None = None
    monthly_cost_usd: float | None = None

    def __post_init__(self) -> None:
        values = (self.daily_tokens, self.daily_cost_usd, self.monthly_tokens, self.monthly_cost_usd)
        if any(value is not None and value < 0 for value in values):
            raise ValueError("Limity zużycia nie mogą być ujemne.")


@dataclass(frozen=True)
class UsageTotals:
    """Zużycie okresu: rezerwacje w locie + rozliczone żądania."""

    tokens: int = 0
    cost_usd: float = 0.0


def period_starts(now: datetime) -> tuple[datetime, datetime]:
    """Początek doby i miesiąca w UTC dla chwili ``now``."""
    moment = now.astimezone(timezone.utc)
    day = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    return day, day.replace(day=1)


def check_limits(limits: UsageLimits, day: UsageTotals, month: UsageTotals, tokens: int, cost: float) -> None:
    """Odmowa, gdy rezerwacja przekroczyłaby limit; miesięczny sprawdzany jako pierwszy."""
    if (limits.monthly_tokens is not None and month.tokens + tokens > limits.monthly_tokens) or (
        limits.monthly_cost_usd is not None and month.cost_usd + cost > limits.monthly_cost_usd + 1e-12
    ):
        raise StructuredExtractionError(Code.MONTHLY_LIMIT)
    if (limits.daily_tokens is not None and day.tokens + tokens > limits.daily_tokens) or (
        limits.daily_cost_usd is not None and day.cost_usd + cost > limits.daily_cost_usd + 1e-12
    ):
        raise StructuredExtractionError(Code.DAILY_LIMIT)


class UsageLedger(Protocol):
    def reserve(self, *, tokens: int, cost_usd: float, model_id: str, now: datetime, limits: UsageLimits) -> Any:
        """Rezerwacja albo ``StructuredExtractionError`` (limit dobowy/miesięczny)."""

    def settle(
        self, reservation: Any, *, input_tokens: int, output_tokens: int, cost_usd: float, outcome: str, now: datetime
    ) -> None:
        """Rozliczenie faktycznym (albo zachowawczo oszacowanym) zużyciem."""

    def release(self, reservation: Any, *, outcome: str, now: datetime) -> None:
        """Zwolnienie rezerwacji żądania, które nie zostało wysłane."""

    def totals(self, now: datetime) -> tuple[UsageTotals, UsageTotals]:
        """Zużycie bieżącej doby i miesiąca."""


@dataclass
class _Entry:
    created_at: datetime
    status: str
    reserved_tokens: int
    reserved_cost_usd: float
    tokens: int | None = None
    cost_usd: float | None = None
    outcome: str | None = None

    def charged(self) -> tuple[int, float]:
        if self.status == "released":
            return 0, 0.0
        if self.status == "settled":
            return self.tokens or 0, self.cost_usd or 0.0
        return self.reserved_tokens, self.reserved_cost_usd


class InMemoryUsageLedger:
    """Rejestr w pamięci procesu (testy; konfiguracja bez bazy). Te same reguły co rejestr SQL."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.entries: list[_Entry] = []

    def _totals(self, now: datetime) -> tuple[UsageTotals, UsageTotals]:
        day_start, month_start = period_starts(now)
        day_t = day_c = month_t = month_c = 0.0
        for entry in self.entries:
            tokens, cost = entry.charged()
            if entry.created_at >= month_start:
                month_t, month_c = month_t + tokens, month_c + cost
                if entry.created_at >= day_start:
                    day_t, day_c = day_t + tokens, day_c + cost
        return UsageTotals(int(day_t), round(day_c, 6)), UsageTotals(int(month_t), round(month_c, 6))

    def totals(self, now: datetime) -> tuple[UsageTotals, UsageTotals]:
        with self._lock:
            return self._totals(now)

    def reserve(self, *, tokens: int, cost_usd: float, model_id: str, now: datetime, limits: UsageLimits) -> int:
        with self._lock:
            day, month = self._totals(now)
            check_limits(limits, day, month, tokens, cost_usd)
            self.entries.append(_Entry(now, "reserved", tokens, cost_usd))
            return len(self.entries) - 1

    def settle(
        self, reservation: int, *, input_tokens: int, output_tokens: int, cost_usd: float, outcome: str, now: datetime
    ) -> None:
        with self._lock:
            entry = self.entries[reservation]
            entry.status, entry.tokens, entry.cost_usd, entry.outcome = "settled", input_tokens + output_tokens, cost_usd, outcome

    def release(self, reservation: int, *, outcome: str, now: datetime) -> None:
        with self._lock:
            entry = self.entries[reservation]
            entry.status, entry.outcome = "released", outcome


class SqlAlchemyUsageLedger:
    """Rejestr w PostgreSQL (tabela ``mpzp_llm_usage``); własne krótkie sesje, niezależne od analizy."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _period(session: Session, start: datetime) -> UsageTotals:
        tokens = func.coalesce(func.sum(_charged_tokens()), 0)
        cost = func.coalesce(func.sum(_charged_cost()), 0.0)
        row = session.execute(select(tokens, cost).where(MpzpLlmUsage.created_at >= start)).one()
        return UsageTotals(int(row[0]), round(float(row[1]), 6))

    def totals(self, now: datetime) -> tuple[UsageTotals, UsageTotals]:
        day_start, month_start = period_starts(now)
        with self._session_factory() as session:
            return self._period(session, day_start), self._period(session, month_start)

    def reserve(self, *, tokens: int, cost_usd: float, model_id: str, now: datetime, limits: UsageLimits) -> int:
        day_start, month_start = period_starts(now)
        with self._session_factory() as session:
            session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": LEDGER_LOCK_KEY})
            check_limits(limits, self._period(session, day_start), self._period(session, month_start), tokens, cost_usd)
            row = MpzpLlmUsage(
                created_at=now, model_id=model_id[:64], status="reserved", reserved_tokens=tokens,
                reserved_cost_usd=cost_usd,
            )
            session.add(row)
            session.commit()  # zwalnia blokadę transakcji
            return int(row.id)

    def settle(
        self, reservation: int, *, input_tokens: int, output_tokens: int, cost_usd: float, outcome: str, now: datetime
    ) -> None:
        with self._session_factory() as session:
            row = session.get(MpzpLlmUsage, reservation)
            if row is None:
                return
            row.status, row.settled_at = "settled", now
            row.input_tokens, row.output_tokens, row.cost_usd, row.outcome = input_tokens, output_tokens, cost_usd, outcome[:60]
            session.commit()

    def release(self, reservation: int, *, outcome: str, now: datetime) -> None:
        with self._session_factory() as session:
            row = session.get(MpzpLlmUsage, reservation)
            if row is None:
                return
            row.status, row.settled_at, row.outcome = "released", now, outcome[:60]
            session.commit()


def _charged_tokens() -> Any:
    return case(
        (MpzpLlmUsage.status == "released", 0),
        (MpzpLlmUsage.status == "settled",
         func.coalesce(MpzpLlmUsage.input_tokens, 0) + func.coalesce(MpzpLlmUsage.output_tokens, 0)),
        else_=MpzpLlmUsage.reserved_tokens,
    )


def _charged_cost() -> Any:
    return case(
        (MpzpLlmUsage.status == "released", 0.0),
        (MpzpLlmUsage.status == "settled", func.coalesce(MpzpLlmUsage.cost_usd, 0.0)),
        else_=MpzpLlmUsage.reserved_cost_usd,
    )


# --- limity w procesie ------------------------------------------------------------------------------


class ConcurrencyLimiter:
    """Limit równoległych żądań w procesie (niezależny od pętli zdarzeń, bezpieczny wątkowo)."""

    def __init__(self, limit: int, *, poll_seconds: float = 0.02) -> None:
        if limit < 1:
            raise ValueError("Limit współbieżności musi wynosić co najmniej 1.")
        self.limit = limit
        self._poll = poll_seconds
        self._lock = threading.Lock()
        self.in_flight = 0

    def try_acquire(self) -> bool:
        with self._lock:
            if self.in_flight >= self.limit:
                return False
            self.in_flight += 1
            return True

    async def acquire(self, wait_seconds: float, sleep: Sleeper, clock: Callable[[], float]) -> bool:
        give_up = clock() + max(0.0, wait_seconds)
        while not self.try_acquire():
            if clock() >= give_up:
                return False
            await sleep(self._poll)
        return True

    def release(self) -> None:
        with self._lock:
            self.in_flight = max(0, self.in_flight - 1)


class LocalRateLimiter:
    """Okno przesuwne: najwyżej ``per_minute`` żądań w ostatnich 60 s w tym procesie."""

    def __init__(self, per_minute: int, *, clock: Callable[[], float] = time.monotonic) -> None:
        if per_minute < 1:
            raise ValueError("Limit częstotliwości musi wynosić co najmniej 1.")
        self.per_minute = per_minute
        self._clock = clock
        self._lock = threading.Lock()
        self._sent: deque[float] = deque()

    def try_acquire(self) -> bool:
        now = self._clock()
        with self._lock:
            while self._sent and now - self._sent[0] >= 60.0:
                self._sent.popleft()
            if len(self._sent) >= self.per_minute:
                return False
            self._sent.append(now)
            return True


# --- dekorator portu ------------------------------------------------------------------------------------


def estimate_tokens(request: StructuredExtractionRequest) -> int:
    return math.ceil((len(request.system_instruction) + len(request.user_text)) / CHARS_PER_TOKEN)


class BudgetedStructuredExtractionProvider:
    """Port ekstrakcji z limitami między analizami: częstotliwość, współbieżność, doba i miesiąc."""

    def __init__(
        self,
        inner: StructuredExtractionProvider,
        *,
        ledger: UsageLedger,
        limits: UsageLimits,
        pricing: TokenPricing | None = None,
        max_output_tokens: int = 8192,
        concurrency: ConcurrencyLimiter | None = None,
        rate: LocalRateLimiter | None = None,
        concurrency_wait_seconds: float = 2.0,
        wall_clock: Callable[[], datetime] = _utc_now,
        clock: Callable[[], float] = time.monotonic,
        sleep: Sleeper = asyncio.sleep,
    ) -> None:
        self.inner = inner
        self.provider_name = inner.provider_name
        self.model = inner.model
        self.ledger = ledger
        self.limits = limits
        self.pricing = pricing or TokenPricing()
        self.max_output_tokens = max_output_tokens
        self.concurrency = concurrency
        self.rate = rate
        self.concurrency_wait_seconds = concurrency_wait_seconds
        self._wall_clock = wall_clock
        self._clock = clock
        self._sleep = sleep

    def __repr__(self) -> str:
        return f"BudgetedStructuredExtractionProvider(inner={self.inner!r})"

    async def aclose(self) -> None:
        close = getattr(self.inner, "aclose", None)
        if close is not None:
            await close()

    async def _reserve(self, input_estimate: int) -> Any:
        output_reserve = self.max_output_tokens
        try:
            return await asyncio.to_thread(
                self.ledger.reserve,
                tokens=input_estimate + output_reserve,
                cost_usd=self.pricing.cost(input_estimate, output_reserve),
                model_id=self.model,
                now=self._wall_clock(),
                limits=self.limits,
            )
        except StructuredExtractionError:
            raise
        except Exception:  # noqa: BLE001 - bez rejestru nie ma wywołania (fail closed)
            raise StructuredExtractionError(Code.USAGE_LEDGER_UNAVAILABLE) from None

    def _settle_sync(self, reservation: Any, input_tokens: int, output_tokens: int, outcome: str) -> None:
        try:
            self.ledger.settle(
                reservation, input_tokens=input_tokens, output_tokens=output_tokens,
                cost_usd=self.pricing.cost(input_tokens, output_tokens), outcome=outcome, now=self._wall_clock(),
            )
        except Exception:  # noqa: BLE001 - rezerwacja zostaje (zachowawczo liczona jako zużycie)
            pass

    def _release_sync(self, reservation: Any, outcome: str) -> None:
        try:
            self.ledger.release(reservation, outcome=outcome, now=self._wall_clock())
        except Exception:  # noqa: BLE001
            pass

    async def extract_structured(self, request: StructuredExtractionRequest) -> StructuredExtractionResult:
        if self.rate is not None and not self.rate.try_acquire():
            raise StructuredExtractionError(Code.LOCAL_RATE_LIMIT)
        if self.concurrency is not None and not await self.concurrency.acquire(
            self.concurrency_wait_seconds, self._sleep, self._clock
        ):
            raise StructuredExtractionError(Code.CONCURRENCY_LIMIT)
        try:
            input_estimate = estimate_tokens(request)
            reservation = await self._reserve(input_estimate)
            try:
                result = await self.inner.extract_structured(request)
            except StructuredExtractionError as error:
                if error.code in NOT_SENT:
                    self._release_sync(reservation, error.code.value)
                else:
                    # Żądanie mogło zostać przetworzone i rozliczone przez dostawcę: liczymy wejście.
                    self._settle_sync(reservation, input_estimate, 0, error.code.value)
                raise
            except BaseException:
                # Przerwanie (termin analizy, anulowanie): zachowawczo jak żądanie wysłane.
                self._settle_sync(reservation, input_estimate, 0, "cancelled")
                raise
            output = (result.output_tokens or 0) + (result.thinking_tokens or 0)
            unknown = result.input_tokens is None and result.output_tokens is None and result.thinking_tokens is None
            self._settle_sync(
                reservation,
                result.input_tokens if result.input_tokens is not None else input_estimate,
                self.max_output_tokens if unknown else output,  # brak zgłoszenia ≠ 0
                "ok",
            )
            return result
        finally:
            if self.concurrency is not None:
                self.concurrency.release()
