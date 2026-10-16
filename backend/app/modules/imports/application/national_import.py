"""Import krajowy POG z GML Rejestru Urbanistycznego: wykaz → pobranie → walidacja → publikacja wersji (ADR-026).

Przypadek użycia nie zna HTTP ani bazy — dostaje porty (wykaz i pobieranie, publikacja pojedynczego aktu, rejestr,
nagrywanie przebiegu) i steruje przebiegiem:

1. pobiera wykaz bieżących wersji aktów i zapisuje go (``pog_ru_catalog``); wykaz niekompletny nie służy do dezaktywacji;
2. planuje: tryb przyrostowy pobiera tylko akty, których ``publishedActId`` nie jest tokenem aktywnego wpisu rejestru;
3. przetwarza akty równolegle (ograniczona współbieżność i budżet pamięci na parsowanie), izolując błędy per akt;
4. po imporcie dezaktywuje akty, które RU wycofał (z zabezpieczeniem przed masowym wycofaniem);
5. zwraca podsumowanie z kodem wyjścia zgodnym z ADR-024 (0 — komplet, 3 — niepełny/przerwany, 1 — błąd przebiegu).

Wznawianie: akty opublikowane w przerwanym przebiegu mają już token w rejestrze, więc ponowne uruchomienie (tryb
przyrostowy) pomija je i kończy resztę; tryb pełny (``full``) pomija akty ukończone w tym samym przebiegu na podstawie
checkpointu w ``import_runs``.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from app.modules.imports.domain.pog import PogValidationError
from app.modules.imports.domain.ru_catalog import (
    RegistryToken,
    RuListing,
    RuPublishedAct,
    SyncPlan,
    WithdrawalPlan,
    plan_sync,
    plan_withdrawals,
)

EXIT_OK: Final[int] = 0
EXIT_FAILED: Final[int] = 1
EXIT_INCOMPLETE: Final[int] = 3

TRANSIENT_CODES: Final[frozenset[str]] = frozenset({"transport", "rate_limited", "download_failed"})


class DownloadedAct(Protocol):
    @property
    def content(self) -> bytes: ...

    @property
    def filename(self) -> str | None: ...


class RuActsGateway(Protocol):
    def list_current(self) -> RuListing: ...

    def download(self, act: RuPublishedAct) -> DownloadedAct:
        """Pobiera plik GML aktu; błąd zgłasza wyjątkiem z atrybutem ``code``."""


class ActPublisher(Protocol):
    def __call__(self, act: RuPublishedAct, download: DownloadedAct, *, dry_run: bool) -> dict[str, Any]:
        """Parsuje, waliduje i publikuje akt (własna sesja/transakcja); zwraca statystyki importu."""


class RegistryGateway(Protocol):
    def sync_catalog(self, listing: RuListing, run_id: int | None) -> dict[str, Any]: ...

    def registry_tokens(self) -> list[RegistryToken]: ...

    def withdraw(self, entries: Sequence[RegistryToken], reason: str) -> dict[str, Any]: ...

    def current_state_id(self) -> int | None: ...


class RunRecorder(Protocol):
    def begin(self, key: str, identity: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Zakłada albo wznawia przebieg; zwraca ``(run_id, poprzedni checkpoint)``."""

    def save(self, run_id: int, payload: dict[str, Any], status: str | None = None) -> None: ...


@dataclass(frozen=True)
class NationalImportOptions:
    full: bool = False
    terc: tuple[str, ...] = ()
    limit: int | None = None
    concurrency: int = 3
    memory_budget_bytes: int = 160 * 1024 * 1024
    dry_run: bool = False
    plan_only: bool = False
    withdraw: bool = True
    allow_mass_withdrawal: bool = False
    max_withdraw_fraction: float = 0.1
    act_retries: int = 2
    retry_backoff_seconds: float = 5.0
    max_consecutive_failures: int | None = 25


@dataclass
class ActResult:
    published_act_id: int
    terc: str
    commune: str | None
    status: str  # succeeded | unchanged | dry_run | failed | skipped
    code: str | None = None
    message: str | None = None
    bytes: int = 0
    download_seconds: float = 0.0
    import_seconds: float = 0.0
    attempts: int = 1
    stats: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "published_act_id": self.published_act_id,
            "terc": self.terc,
            "commune": self.commune,
            "status": self.status,
            "code": self.code,
            "message": self.message,
            "bytes": self.bytes,
            "download_seconds": round(self.download_seconds, 2),
            "import_seconds": round(self.import_seconds, 2),
            "attempts": self.attempts,
        }


class NationalProgress:
    """Wątkowo-bezpieczny licznik postępu z ETA; ma ``line()`` i ``snapshot()`` jak ``ImportProgress``."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._started = clock()
        self._lock = threading.Lock()
        self.phase = "start"
        self.total = 0
        self.done = 0
        self.failed = 0
        self.unchanged = 0
        self.bytes_downloaded = 0
        self.in_flight: dict[int, str] = {}
        self.catalog_rows = 0

    def set_phase(self, name: str) -> None:
        with self._lock:
            self.phase = name

    def set_total(self, total: int) -> None:
        with self._lock:
            self.total = total

    def started(self, act: RuPublishedAct) -> None:
        with self._lock:
            self.in_flight[act.published_act_id] = act.commune or act.terc

    def finished(self, result: ActResult) -> None:
        with self._lock:
            self.in_flight.pop(result.published_act_id, None)
            self.done += 1
            self.bytes_downloaded += result.bytes
            if result.status == "failed":
                self.failed += 1
            elif result.status == "unchanged":
                self.unchanged += 1

    def eta_seconds(self) -> float | None:
        with self._lock:
            return self._eta_unlocked()

    def _eta_unlocked(self) -> float | None:
        elapsed = self._clock() - self._started
        if self.done <= 0 or self.total <= self.done or elapsed <= 0:
            return None if self.done <= 0 else 0.0
        return (self.total - self.done) * (elapsed / self.done)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            elapsed = self._clock() - self._started
            eta = self._eta_unlocked()
            return {
                "phase": self.phase,
                "elapsed_seconds": round(elapsed, 1),
                "acts_total": self.total,
                "acts_done": self.done,
                "acts_failed": self.failed,
                "acts_unchanged": self.unchanged,
                "bytes_downloaded": self.bytes_downloaded,
                "in_flight": sorted(self.in_flight.values()),
                "eta_seconds": None if eta is None else round(eta, 1),
            }

    def line(self) -> str:
        data = self.snapshot()
        eta = data["eta_seconds"]
        eta_text = "n/d" if eta is None else _format_seconds(eta)
        flight = ", ".join(data["in_flight"][:3]) or "-"
        return (
            f"[import-krajowy] {data['phase']} {data['elapsed_seconds']:.0f} s, akty {data['acts_done']}/{data['acts_total']}"
            f" (błędy {data['acts_failed']}), pobrano {data['bytes_downloaded'] / 1e6:.1f} MB, w toku: {flight}, ETA {eta_text}"
        )


def _format_seconds(value: float) -> str:
    seconds = int(round(value))
    if seconds < 60:
        return f"{seconds} s"
    minutes, rest = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} min {rest:02d} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min"


class _WeightedBudget:
    """Budżet pamięci na parsowanie: akt zajmuje tyle jednostek, ile bajtów ma plik GML (pojedynczy większy — samotnie)."""

    def __init__(self, capacity: int) -> None:
        self._capacity = max(capacity, 1)
        self._used = 0
        self._condition = threading.Condition()

    def acquire(self, weight: int, cancel: threading.Event | None = None) -> int:
        weight = min(max(weight, 1), self._capacity)
        with self._condition:
            while self._used + weight > self._capacity and self._used > 0:
                self._condition.wait(timeout=1.0)
                if cancel is not None and cancel.is_set():
                    break
            self._used += weight
        return weight

    def release(self, weight: int) -> None:
        with self._condition:
            self._used -= weight
            self._condition.notify_all()


@dataclass
class NationalImportResult:
    status: str
    exit_code: int
    plan: dict[str, Any]
    results: list[ActResult]
    catalog: dict[str, Any]
    withdrawal: dict[str, Any]
    timing: dict[str, Any]
    registry_state_before: int | None
    registry_state_after: int | None
    run_id: int | None
    error: str | None = None
    resume: dict[str, Any] | None = None

    def as_payload(self, *, failures_limit: int = 100) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for item in self.results:
            counts[item.status] = counts.get(item.status, 0) + 1
        failures = [item.as_dict() for item in self.results if item.status == "failed"]
        codes: dict[str, int] = {}
        for failure in failures:
            codes[str(failure["code"])] = codes.get(str(failure["code"]), 0) + 1
        payload: dict[str, Any] = {
            "status": self.status,
            "plan": self.plan,
            "counts": counts,
            "failure_codes": codes,
            "failures": failures[:failures_limit],
            "catalog": self.catalog,
            "withdrawal": self.withdrawal,
            "timing": self.timing,
            "registry_state_before": self.registry_state_before,
            "registry_state_after": self.registry_state_after,
            "import_run_id": self.run_id,
            "exit_code": self.exit_code,
        }
        if self.error:
            payload["error"] = self.error
        if self.resume:
            payload["resume"] = self.resume
        return payload


def run_national_import(
    gateway: RuActsGateway,
    registry: RegistryGateway,
    publish: ActPublisher,
    recorder: RunRecorder | None,
    options: NationalImportOptions,
    *,
    progress: NationalProgress | None = None,
    cancel: threading.Event | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> NationalImportResult:
    started = clock()
    progress = progress or NationalProgress(clock)
    cancel = cancel or threading.Event()
    state_before = registry.current_state_id()
    run_key = "|".join(
        [
            "full" if options.full else "incremental",
            ",".join(options.terc),
            str(options.limit),
            "dry" if options.dry_run else "live",
        ]
    )
    run_id: int | None = None
    previous: dict[str, Any] = {}
    if recorder is not None and not options.plan_only:
        run_id, previous = recorder.begin(run_key, {"full": options.full, "terc": list(options.terc)})

    progress.set_phase("wykaz")
    try:
        listing = gateway.list_current()
    except Exception as exc:  # noqa: BLE001 - brak wykazu = import niepełny, rejestr nietknięty
        result = NationalImportResult(
            "incomplete",
            EXIT_INCOMPLETE,
            {},
            [],
            {"error": f"{type(exc).__name__}: {exc}"},
            {},
            {"total_seconds": round(clock() - started, 1)},
            state_before,
            state_before,
            run_id,
            error=f"RU_LISTING_UNAVAILABLE: {type(exc).__name__}: {exc}",
            resume={"hint": "Wykaz RU niedostępny; rejestr nie został zmieniony. Uruchom polecenie ponownie."},
        )
        if recorder is not None and run_id is not None:
            recorder.save(run_id, {"error": result.error}, status="failed")
        return result

    catalog_info: dict[str, Any] = (
        registry.sync_catalog(listing, run_id) if not options.plan_only else {"skipped": "plan_only"}
    )
    catalog_info.update(
        {
            "rows": len(listing.acts),
            "declared_total": listing.declared_total,
            "complete": listing.complete,
            "incomplete_reason": listing.incomplete_reason,
        }
    )
    tokens = registry.registry_tokens()
    plan: SyncPlan = plan_sync(
        listing, tokens, full=options.full, terc_filter=options.terc or None, limit=options.limit
    )
    done_before = set(previous.get("completed_ids", [])) if options.full else set()
    queue = [act for act in plan.to_import if act.published_act_id not in done_before]
    plan_payload = {
        "listing_rows": len(listing.acts),
        "to_import": len(plan.to_import),
        "already_completed_in_run": len(plan.to_import) - len(queue),
        "unchanged": len(plan.unchanged),
        "skipped_by_filter": plan.skipped_by_filter,
        "mode": "full" if options.full else "incremental",
    }
    progress.set_total(len(queue))
    if options.plan_only:
        return NationalImportResult(
            "plan_only",
            EXIT_OK,
            {**plan_payload, "acts": [act.published_act_id for act in queue][:500]},
            [],
            catalog_info,
            {},
            {"total_seconds": round(clock() - started, 1)},
            state_before,
            state_before,
            None,
        )

    results: list[ActResult] = []
    completed_ids: list[int] = sorted(done_before)
    lock = threading.Lock()
    budget = _WeightedBudget(options.memory_budget_bytes)
    consecutive_failures = 0
    aborted = False

    def process(act: RuPublishedAct) -> ActResult:
        progress.started(act)
        attempts = 0
        download_seconds = 0.0
        outcome: ActResult | None = None
        while attempts <= options.act_retries:
            attempts += 1
            if cancel.is_set():
                outcome = ActResult(act.published_act_id, act.terc, act.commune, "skipped", "cancelled")
                break
            t0 = clock()
            try:
                downloaded = gateway.download(act)
            except Exception as exc:  # noqa: BLE001 - błąd pobrania izolowany per akt
                code = str(getattr(exc, "code", None) or type(exc).__name__)
                download_seconds += clock() - t0
                outcome = ActResult(
                    act.published_act_id, act.terc, act.commune, "failed", code, str(exc)[:300],
                    download_seconds=download_seconds, attempts=attempts,
                )
                if code in TRANSIENT_CODES and attempts <= options.act_retries:
                    sleep(options.retry_backoff_seconds * attempts)
                    continue
                break
            download_seconds += clock() - t0
            size = len(downloaded.content)
            weight = budget.acquire(size, cancel)
            t1 = clock()
            try:
                stats = publish(act, downloaded, dry_run=options.dry_run)
                status = "dry_run" if options.dry_run else (
                    "unchanged" if int(stats.get("new", 0)) + int(stats.get("changed", 0)) == 0 else "succeeded"
                )
                outcome = ActResult(
                    act.published_act_id, act.terc, act.commune, status,
                    bytes=size, download_seconds=download_seconds, import_seconds=clock() - t1,
                    attempts=attempts, stats=stats,
                )
            except Exception as exc:  # noqa: BLE001 - błąd walidacji/publikacji izolowany per akt
                # QA odrzucające dane źródła ma stały kod (wada danych RU, nie błąd importera); reszta — nazwa wyjątku.
                code = str(
                    getattr(exc, "code", None) or ("qa_rejected" if isinstance(exc, PogValidationError) else type(exc).__name__)
                )
                outcome = ActResult(
                    act.published_act_id, act.terc, act.commune, "failed", code, str(exc)[:300],
                    bytes=size, download_seconds=download_seconds, import_seconds=clock() - t1, attempts=attempts,
                )
            finally:
                budget.release(weight)
            break
        assert outcome is not None
        progress.finished(outcome)
        return outcome

    progress.set_phase("import")
    in_flight: set[Future[ActResult]] = set()
    pending = iter(queue)
    with ThreadPoolExecutor(max_workers=max(options.concurrency, 1), thread_name_prefix="pog-nat") as pool:
        exhausted = False
        while True:
            while not exhausted and not cancel.is_set() and not aborted and len(in_flight) < options.concurrency * 2:
                act = next(pending, None)
                if act is None:
                    exhausted = True
                    break
                in_flight.add(pool.submit(process, act))
            if not in_flight:
                break
            finished, in_flight = wait(in_flight, timeout=1.0, return_when=FIRST_COMPLETED)
            for future in finished:
                item = future.result()
                with lock:
                    results.append(item)
                    if item.status in {"succeeded", "unchanged", "dry_run"}:
                        completed_ids.append(item.published_act_id)
                        consecutive_failures = 0
                    elif item.status == "failed":
                        consecutive_failures += 1
                if (
                    options.max_consecutive_failures is not None
                    and consecutive_failures >= options.max_consecutive_failures
                ):
                    aborted = True
            if recorder is not None and run_id is not None:
                recorder.save(run_id, {"completed_ids": completed_ids[-5000:], "progress": progress.snapshot()})

    # Dezaktywacja: dopiero po imporcie (nowa wersja zastępuje starą), tylko przy pełnym przebiegu i kompletnej liście.
    withdrawal_payload: dict[str, Any] = {"planned": 0, "withdrawn": 0, "blocked": None}
    interrupted = cancel.is_set()
    if options.withdraw and not options.dry_run and not interrupted and not aborted:
        progress.set_phase("dezaktywacja")
        fresh = registry.registry_tokens()
        withdrawal: WithdrawalPlan = plan_withdrawals(
            listing,
            fresh,
            terc_filter=options.terc or None,
            max_withdraw_fraction=options.max_withdraw_fraction,
            allow_mass_withdrawal=options.allow_mass_withdrawal,
        )
        withdrawal_payload["planned"] = len(withdrawal.to_withdraw)
        withdrawal_payload["blocked"] = withdrawal.blocked
        if withdrawal.to_withdraw:
            outcome_w = registry.withdraw(withdrawal.to_withdraw, "not_current")
            withdrawal_payload.update(outcome_w)
            withdrawal_payload["acts"] = [item.act_identifier for item in withdrawal.to_withdraw][:100]

    failed = [item for item in results if item.status == "failed"]
    state_after = registry.current_state_id()
    if interrupted:
        status, exit_code = "interrupted", EXIT_INCOMPLETE
    elif aborted:
        status, exit_code = "aborted", EXIT_INCOMPLETE
    elif failed or withdrawal_payload.get("blocked") or not listing.complete:
        status, exit_code = "partial", EXIT_INCOMPLETE
    else:
        status, exit_code = "succeeded", EXIT_OK
    progress.set_phase("done")
    timing = {
        "total_seconds": round(clock() - started, 1),
        "download_seconds_sum": round(sum(item.download_seconds for item in results), 1),
        "import_seconds_sum": round(sum(item.import_seconds for item in results), 1),
        "bytes_downloaded": sum(item.bytes for item in results),
    }
    resume = None
    if status != "succeeded":
        resume = {
            "hint": (
                "Uruchom to samo polecenie ponownie: akty z tokenem w rejestrze są pomijane, a niedokończone "
                "i nieudane zostaną ponowione."
            )
        }
    final = NationalImportResult(
        status, exit_code, plan_payload, results, catalog_info, withdrawal_payload, timing,
        state_before, state_after, run_id, resume=resume,
    )
    if recorder is not None and run_id is not None:
        recorder.save(
            run_id,
            {"completed_ids": completed_ids[-5000:], "progress": progress.snapshot(), "summary": final.as_payload(failures_limit=20)},
            status={"succeeded": "succeeded", "partial": "failed"}.get(status, "interrupted"),
        )
    return final
