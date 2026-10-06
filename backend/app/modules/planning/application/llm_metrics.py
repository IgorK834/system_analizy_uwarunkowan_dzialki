"""Liczniki ścieżki modelu językowego w procesie (PV3-12–14, monitoring PV3-19).

Projekt nie ma systemu metryk (Prometheus itp.), więc liczniki są trzymane w pamięci procesu i
logowane jako zdarzenia z samymi kodami i liczbami — bez treści dokumentu, cytatów, żądań i
identyfikatorów działki. ``snapshot()`` zwraca kopię do diagnostyki i testów; ``window_totals()``
sumuje te same liczniki w oknie czasowym (odsetki alarmowe nie mogą zależeć od historii całego
życia procesu). Rejestr jest lokalny dla procesu: po restarcie liczniki zaczynają od zera, a przy N
procesach każdy ma własny widok (twarde limity kosztu żyją w bazie — ``mpzp_llm_usage``).

Nazwy liczników:

- ``llm.verifier.rejected.G1`` … ``G8`` — odrzucenia per bramka (PV3-12), ``llm.verifier.accepted``;
- ``llm.verifier.code.<kod>`` — odrzucenia per kod;
- ``llm.calls`` (żądania do dostawcy), ``llm.cache.hit`` / ``llm.cache.miss`` (PV3-13);
- ``llm.latency.count``, ``llm.latency.sum_ms``, ``llm.latency.le_<próg>ms`` (histogram skumulowany),
  gauge ``llm.latency.max_ms`` — opóźnienia żądań do dostawcy (PV3-19);
- ``llm.tokens.input`` / ``llm.tokens.output`` oraz ``llm.cost.estimated_microusd`` (PV3-19);
- ``llm.analyses`` — dokumenty, dla których w trybie ``hybrid`` próbowano ścieżki modelu, i
  ``llm.degraded`` — z nich te zakończone wynikiem deterministycznym z ostrzeżeniem (PV3-14/19);
- ``llm.unavailable.<powód>`` — degradacje do wyniku deterministycznego (PV3-14);
- ``llm.shadow.<wynik>`` — porównania trybu cienia (``agree``, ``disagree``, ``llm_only``, ``det_only``).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter, deque
from collections.abc import Callable, Iterable, Mapping
from typing import Final

logger = logging.getLogger("app.mpzp_llm")

# Progi skumulowanego histogramu opóźnień [ms]: 30 s to próg p95 z ADR-012 (budżet czasu ścieżki).
LATENCY_BUCKETS_MS: Final[tuple[int, ...]] = (1_000, 2_500, 5_000, 10_000, 30_000)
LATENCY_COUNT: Final[str] = "llm.latency.count"
LATENCY_SUM: Final[str] = "llm.latency.sum_ms"
LATENCY_MAX: Final[str] = "llm.latency.max_ms"
# Odrzucenia jakościowe to G1–G7; G8 to deduplikacja (ta sama wartość z nakładających się części bloku).
QUALITY_GATES: Final[tuple[str, ...]] = ("G1", "G2", "G3", "G4", "G5", "G6", "G7")
# Okno zdarzeń: najwyżej tyle wpisów i najwyżej tyle sekund wstecz (pamięć procesu jest ograniczona).
MAX_WINDOW_EVENTS: Final[int] = 50_000
MAX_WINDOW_SECONDS: Final[float] = 24 * 3600.0


def latency_bucket_name(upper_ms: int) -> str:
    return f"llm.latency.le_{upper_ms}ms"


class LlmMetrics:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._lock = threading.Lock()
        self._clock = clock
        self._counters: Counter[str] = Counter()
        self._gauges: dict[str, float] = {}
        # (czas, nazwa, wartość): ten sam strumień co liczniki, do sum w oknie czasowym.
        self._events: deque[tuple[float, str, int]] = deque(maxlen=MAX_WINDOW_EVENTS)

    def increment(self, name: str, amount: int = 1) -> None:
        if amount <= 0:
            return
        with self._lock:
            self._counters[name] += amount
            self._events.append((self._clock(), name, amount))

    def add_all(self, values: Mapping[str, int]) -> None:
        for name, amount in values.items():
            self.increment(name, amount)

    def observe_latency(self, milliseconds: float) -> None:
        """Opóźnienie jednego żądania do dostawcy: licznik, suma, histogram skumulowany i maksimum."""
        value = max(0.0, float(milliseconds))
        self.increment(LATENCY_COUNT)
        self.increment(LATENCY_SUM, round(value))
        for upper in LATENCY_BUCKETS_MS:
            if value <= upper:
                self.increment(latency_bucket_name(upper))
        with self._lock:
            self._gauges[LATENCY_MAX] = max(self._gauges.get(LATENCY_MAX, 0.0), value)

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(sorted(self._counters.items()))

    def now(self) -> float:
        return self._clock()

    def set_gauge(self, name: str, value: float) -> None:
        with self._lock:
            self._gauges[name] = value

    def gauges(self) -> dict[str, float]:
        with self._lock:
            return dict(sorted(self._gauges.items()))

    def window_totals(self, window_seconds: float, *, now: float | None = None) -> dict[str, int]:
        """Sumy liczników z ostatnich ``window_seconds`` (nie więcej niż doba i nie więcej niż bufor)."""
        horizon = (self._clock() if now is None else now) - min(max(window_seconds, 0.0), MAX_WINDOW_SECONDS)
        totals: Counter[str] = Counter()
        with self._lock:
            for moment, name, amount in self._events:
                if moment >= horizon:
                    totals[name] += amount
        return dict(sorted(totals.items()))

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._events.clear()


metrics = LlmMetrics()


# --- odsetki (PV3-19): liczone z liczników, nigdy z treści ----------------------------------------------


def rejected_quality_count(totals: Mapping[str, int]) -> int:
    return sum(totals.get(f"llm.verifier.rejected.{gate}", 0) for gate in QUALITY_GATES)


def rejection_rate(totals: Mapping[str, int]) -> tuple[float | None, int]:
    """(odsetek odrzuceń bramek G1–G7 wśród ocenionych kandydatów, liczba ocenionych); ``None`` bez próby."""
    rejected = rejected_quality_count(totals)
    evaluated = rejected + totals.get("llm.verifier.accepted", 0)
    return (rejected / evaluated if evaluated else None), evaluated


def rejection_rate_by_gate(totals: Mapping[str, int]) -> dict[str, float | None]:
    evaluated = rejection_rate(totals)[1]
    return {
        gate: (totals.get(f"llm.verifier.rejected.{gate}", 0) / evaluated if evaluated else None)
        for gate in QUALITY_GATES
    }


def degradation_rate(totals: Mapping[str, int]) -> tuple[float | None, int]:
    """(odsetek analiz zakończonych degradacją do wyniku deterministycznego, liczba analiz); ``None`` bez próby."""
    analyses = totals.get("llm.analyses", 0)
    return (totals.get("llm.degraded", 0) / analyses if analyses else None), analyses


def cache_hit_rate(totals: Mapping[str, int]) -> tuple[float | None, int]:
    hits, misses = totals.get("llm.cache.hit", 0), totals.get("llm.cache.miss", 0)
    return (hits / (hits + misses) if hits + misses else None), hits + misses


def estimated_cost_usd(totals: Mapping[str, int]) -> float:
    return round(totals.get("llm.cost.estimated_microusd", 0) / 1_000_000, 6)


def mean_latency_ms(totals: Mapping[str, int]) -> float | None:
    count = totals.get(LATENCY_COUNT, 0)
    return round(totals.get(LATENCY_SUM, 0) / count, 1) if count else None


LAST_RUN_OK: Final[str] = "llm.last_run_ok"
LAST_RUN_AT: Final[str] = "llm.last_run_at"


def record_run(ok: bool) -> None:
    """Wynik ostatniego przebiegu ścieżki modelu: po sukcesie stan „model niedostępny” wraca do normy."""
    metrics.set_gauge(LAST_RUN_OK, 1.0 if ok else 0.0)
    metrics.set_gauge(LAST_RUN_AT, metrics.now())


def last_run() -> tuple[bool | None, float | None]:
    """(czy ostatni przebieg się udał, ile sekund temu) albo ``(None, None)`` bez przebiegu."""
    gauges = metrics.gauges()
    if LAST_RUN_OK not in gauges:
        return None, None
    return gauges[LAST_RUN_OK] >= 1.0, max(0.0, metrics.now() - gauges.get(LAST_RUN_AT, 0.0))


def log_event(event: str, **fields: object) -> None:
    """Zdarzenie ścieżki modelu: tylko liczby, kody i skróty (pole o innej wartości jest odrzucane)."""
    safe = {key: value for key, value in fields.items() if isinstance(value, (int, float, bool)) or (
        isinstance(value, str) and len(value) <= 80 and "\n" not in value
    )}
    logger.info("mpzp_llm event=%s %s", event, " ".join(f"{key}={value}" for key, value in sorted(safe.items())))


def observe_latencies(values: Iterable[float]) -> None:
    for value in values:
        metrics.observe_latency(value)
