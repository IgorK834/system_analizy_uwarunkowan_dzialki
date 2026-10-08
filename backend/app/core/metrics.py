"""Liczniki zdarzeń w pamięci procesu (AU-002).

Projekt nie ma systemu metryk (Prometheus itp.), więc — tak jak liczniki ścieżki modelu językowego —
liczniki żyją w pamięci procesu i są wystawiane operatorowi przez ``GET /health/upstream``. Rejestr
jest lokalny dla procesu: po restarcie zaczyna od zera, a przy N procesach każdy ma własny widok.
Nazwy liczników muszą mieć ograniczoną kardynalność (stałe kody, nie tekst z odpowiedzi usługi).
"""

from __future__ import annotations

import threading
from collections import Counter


class CounterRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Counter[str] = Counter()

    def increment(self, name: str, amount: int = 1) -> None:
        if amount <= 0:
            return
        with self._lock:
            self._counters[name] += amount

    def snapshot(self) -> dict[str, int]:
        """Kopia liczników posortowana po nazwie (do diagnostyki i testów)."""
        with self._lock:
            return dict(sorted(self._counters.items()))

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()


# Liczniki odpowiedzi usług zewnętrznych, np. ``uldk.response.-1``.
upstream_metrics = CounterRegistry()
