"""Liczniki ścieżki modelu językowego w procesie (PV3-12–14).

Projekt nie ma systemu metryk (Prometheus itp.), więc liczniki są trzymane w pamięci procesu i
logowane jako zdarzenia z samymi kodami i liczbami — bez treści dokumentu, cytatów i
identyfikatorów działki. ``snapshot()`` zwraca kopię do diagnostyki i testów; po wdrożeniu
systemu metryk (Task 20.19) wystarczy podpiąć eksporter pod ten sam rejestr.

Nazwy liczników:

- ``llm.verifier.rejected.G1`` … ``G8`` — odrzucenia per bramka (PV3-12), ``llm.verifier.accepted``;
- ``llm.verifier.code.<kod>`` — odrzucenia per kod;
- ``llm.calls`` (żądania do dostawcy), ``llm.cache.hit`` / ``llm.cache.miss`` (PV3-13);
- ``llm.unavailable.<powód>`` — degradacje do wyniku deterministycznego (PV3-14);
- ``llm.shadow.<wynik>`` — porównania trybu cienia (``agree``, ``disagree``, ``llm_only``, ``det_only``).
"""

from __future__ import annotations

import logging
import threading
from collections import Counter
from collections.abc import Mapping

logger = logging.getLogger("app.mpzp_llm")


class LlmMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Counter[str] = Counter()

    def increment(self, name: str, amount: int = 1) -> None:
        if amount <= 0:
            return
        with self._lock:
            self._counters[name] += amount

    def add_all(self, values: Mapping[str, int]) -> None:
        for name, amount in values.items():
            self.increment(name, amount)

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(sorted(self._counters.items()))

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()


metrics = LlmMetrics()


def log_event(event: str, **fields: object) -> None:
    """Zdarzenie ścieżki modelu: tylko liczby, kody i skróty (pole o innej wartości jest odrzucane)."""
    safe = {key: value for key, value in fields.items() if isinstance(value, (int, float, bool)) or (
        isinstance(value, str) and len(value) <= 80 and "\n" not in value
    )}
    logger.info("mpzp_llm event=%s %s", event, " ".join(f"{key}={value}" for key, value in sorted(safe.items())))
