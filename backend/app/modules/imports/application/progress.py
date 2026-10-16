"""Postęp długiego importu: liczniki, ETA i okresowy komunikat dla operatora (AU-106, ADR-024).

Import gminy z Rejestru Urbanistycznego trwał minuty i — uruchomiony w tle — kończył się bez żadnego wyjścia,
więc operator nie wiedział, czy proces żyje, jak daleko doszedł i czy warto czekać. ``ImportProgress`` zbiera
zdarzenia stron (z wielu wątków pobierających równolegle), a ``HeartbeatReporter`` drukuje czytelną linię
postępu **co najwyżej co ``interval`` sekund** — także wtedy, gdy żadna strona nie dotarła (żywotność procesu
jest informacją). Linie trafiają na stderr; wynik końcowy (JSON) zostaje na stdout.

Moduł jest czysty: nie zna HTTP, bazy ani ``OgcClient`` — zdarzenia stron mają kształt protokołu ``PageEvent``.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Final, Protocol, TextIO

DEFAULT_INTERVAL_SECONDS: Final[float] = 10.0


class PageEvent(Protocol):
    """Zdarzenie strony (``WfsPageEvent``): kształt wymagany przez licznik postępu (tylko do odczytu)."""

    @property
    def type_name(self) -> str: ...

    @property
    def start_index(self) -> int: ...

    @property
    def returned(self) -> int: ...

    @property
    def fetched(self) -> int: ...

    @property
    def matched(self) -> int | None: ...

    @property
    def pages_done(self) -> int: ...

    @property
    def pages_total(self) -> int | None: ...

    @property
    def from_store(self) -> bool: ...


@dataclass
class TypeProgress:
    """Stan pobierania jednego typu obiektów."""

    name: str
    matched: int | None = None
    fetched: int = 0
    pages_done: int = 0
    pages_total: int | None = None
    pages_from_store: int = 0
    objects_from_store: int = 0
    started_at: float | None = None
    finished_at: float | None = None

    @property
    def done(self) -> bool:
        return self.finished_at is not None


@dataclass
class ImportProgress:
    """Wątkowo-bezpieczny licznik postępu importu."""

    clock: Callable[[], float] = time.monotonic
    started_at: float = field(init=False)
    phase: str = field(default="start", init=False)
    types: dict[str, TypeProgress] = field(default_factory=dict, init=False)
    phase_started: dict[str, float] = field(default_factory=dict, init=False)
    phase_finished: dict[str, float] = field(default_factory=dict, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self.started_at = self.clock()

    # --- zdarzenia ----------------------------------------------------------------------------------------

    def set_phase(self, name: str) -> None:
        with self._lock:
            now = self.clock()
            if self.phase in self.phase_started and self.phase not in self.phase_finished:
                self.phase_finished[self.phase] = now
            self.phase = name
            self.phase_started.setdefault(name, now)

    def type_started(self, name: str) -> None:
        with self._lock:
            state = self.types.setdefault(name, TypeProgress(name))
            state.started_at = state.started_at if state.started_at is not None else self.clock()

    def type_finished(self, name: str) -> None:
        with self._lock:
            state = self.types.setdefault(name, TypeProgress(name))
            state.finished_at = self.clock()

    def on_page(self, event: PageEvent) -> None:
        with self._lock:
            state = self.types.setdefault(event.type_name, TypeProgress(event.type_name))
            if state.started_at is None:
                state.started_at = self.clock()
            state.matched = event.matched
            state.fetched = max(state.fetched, event.fetched)
            state.pages_done = max(state.pages_done, event.pages_done)
            state.pages_total = event.pages_total if event.pages_total is not None else state.pages_total
            if event.from_store:
                state.pages_from_store += 1
                state.objects_from_store += event.returned

    # --- odczyt -------------------------------------------------------------------------------------------

    def _totals(self) -> tuple[int, int | None, int]:
        fetched = sum(state.fetched for state in self.types.values())
        from_store = sum(state.objects_from_store for state in self.types.values())
        matched = [state.matched for state in self.types.values()]
        total = sum(value for value in matched if value is not None) if all(v is not None for v in matched) and matched else None
        return fetched, total, from_store

    def eta_seconds(self) -> float | None:
        """Szacunek pozostałego czasu z tempa pobierania z sieci (strony z magazynu nie liczą się do tempa).

        ``None`` — za mało danych albo nieznana liczba obiektów kolejnych typów (nie zgadujemy ich liczby).
        """
        with self._lock:
            return self._eta_unlocked()

    def _eta_unlocked(self) -> float | None:
        fetched, total, from_store = self._totals()
        network = fetched - from_store
        started = self.phase_started.get("download", self.started_at)
        elapsed = self.clock() - started
        if total is None or network <= 0 or elapsed <= 0:
            return None
        rate = network / elapsed
        return max(total - fetched, 0) / rate if rate > 0 else None

    def snapshot(self) -> dict[str, Any]:
        """Postęp w postaci bezpiecznej dla JSON (wynik końcowy CLI i ``import_runs.checkpoint``)."""
        with self._lock:
            now = self.clock()
            fetched, total, from_store = self._totals()
            return {
                "phase": self.phase,
                "elapsed_seconds": round(now - self.started_at, 1),
                "phases": {
                    name: round(self.phase_finished.get(name, now) - started, 1)
                    for name, started in self.phase_started.items()
                },
                "objects_fetched": fetched,
                "objects_total": total,
                "objects_from_store": from_store,
                "eta_seconds": None if (eta := self._eta_unlocked()) is None else round(eta, 1),
                "types": {
                    name: {
                        "matched": state.matched,
                        "fetched": state.fetched,
                        "pages_done": state.pages_done,
                        "pages_total": state.pages_total,
                        "pages_from_store": state.pages_from_store,
                        "done": state.done,
                        "seconds": None
                        if state.started_at is None
                        else round((state.finished_at or now) - state.started_at, 1),
                    }
                    for name, state in self.types.items()
                },
            }

    def line(self) -> str:
        """Jedna czytelna linia postępu (stderr)."""
        data = self.snapshot()
        parts = [f"[import] {data['phase']} {data['elapsed_seconds']:.0f} s"]
        active = [
            (name, state) for name, state in data["types"].items() if not state["done"]
        ]
        if active:
            name, state = active[-1]
            known = f"/{state['matched']}" if state["matched"] is not None else ""
            pages = (
                f", strony {state['pages_done']}/{state['pages_total']}"
                if state["pages_total"] is not None
                else f", strony {state['pages_done']}"
            )
            parts.append(f"{name} {state['fetched']}{known}{pages}")
        elif data["types"]:
            parts.append(f"typy gotowe: {sum(1 for s in data['types'].values() if s['done'])}/{len(data['types'])}")
        total = f"/{data['objects_total']}" if data["objects_total"] is not None else ""
        parts.append(f"razem {data['objects_fetched']}{total} obiektów")
        if data["objects_from_store"]:
            parts.append(f"w tym {data['objects_from_store']} z poprzedniego przebiegu")
        eta = data["eta_seconds"]
        parts.append("ETA " + (_format_seconds(eta) if eta is not None else "n/d"))
        return ", ".join(parts)


def _format_seconds(value: float) -> str:
    seconds = int(round(value))
    if seconds < 60:
        return f"{seconds} s"
    minutes, rest = divmod(seconds, 60)
    return f"{minutes} min {rest:02d} s"


class HeartbeatReporter:
    """Drukuje linię postępu co ``interval`` sekund w osobnym wątku (demon), także przy braku nowych stron."""

    def __init__(
        self,
        progress: ImportProgress,
        stream: TextIO,
        *,
        interval: float = DEFAULT_INTERVAL_SECONDS,
        on_tick: Callable[[], None] | None = None,
    ) -> None:
        if interval <= 0:
            raise ValueError("Odstęp komunikatów postępu musi być dodatni.")
        self._progress = progress
        self._stream = stream
        self._interval = interval
        # Wołane po każdym komunikacie (np. zapis checkpointu); jego błąd nie zatrzymuje raportowania.
        self.on_tick = on_tick
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def emit(self, message: str | None = None) -> None:
        text = message if message is not None else self._progress.line()
        with self._lock:
            print(text, file=self._stream, flush=True)
        if message is None and self.on_tick is not None:
            try:
                self.on_tick()
            except Exception:  # noqa: BLE001 - zapis postępu nie może przerwać importu
                pass

    def start(self) -> None:
        if self._thread is not None:
            return
        self.emit()
        self._thread = threading.Thread(target=self._run, name="import-progress", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            self.emit()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval + 1.0)
            self._thread = None
        self.emit()
