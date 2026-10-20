"""Usługa harmonogramu importu POG (``pog-sync``): kolejka zleceń na żądanie + cykliczny import przyrostowy (ADR-026).

Jeden cykl: (1) uzupełnia tabele mapy aktywnych wersji, którym ich brakuje, (2) przetwarza zlecenia importu gmin z kolejki
(``import_jobs``), (3) jeśli minął interwał od ostatniego udanego przebiegu krajowego — uruchamia import przyrostowy.
**Blokada przed równoległymi przebiegami:** cały cykl działa pod sesyjną blokadą doradczą PostgreSQL
(``pg_try_advisory_lock``); druga instancja usługi (albo ręczne ``pog-sync --once``) widzi blokadę i kończy z
``status = locked`` zamiast dublować pobieranie. Wynik każdego przebiegu trafia do ``import_runs`` (``pog-national-v1``).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from app.core.settings import settings
from app.db.session import SessionLocal, engine
from app.modules.imports.application.national_import import NationalImportOptions, NationalProgress
from app.modules.imports.composition import (
    claim_next_job,
    complete_job,
    fail_job,
    pog_sync_status,
    rebuild_missing_map_tables,
    run_pog_national_command,
    worker_identity,
)

logger = logging.getLogger(__name__)

SYNC_LOCK_NAME = "pog-sync"
JOB_LEASE_SECONDS = 3600


def _is_data_defect(code: str) -> bool:
    """Kod wady danych źródła (QA albo kontrakt GML): ponowienie tego samego pliku da ten sam wynik."""
    return code == "qa_rejected" or code.startswith("gml_")


def _options_for_scheduled_run() -> NationalImportOptions:
    return NationalImportOptions(
        concurrency=settings.pog_national_concurrency,
        memory_budget_bytes=settings.pog_national_memory_budget_bytes,
        max_withdraw_fraction=settings.pog_national_max_withdraw_fraction,
    )


def process_one_job(*, with_csw: bool = True, cancel: threading.Event | None = None) -> dict[str, Any] | None:
    """Pobiera jedno zlecenie z kolejki i wykonuje import tej gminy; ``None`` — kolejka pusta."""
    worker = worker_identity()
    with SessionLocal() as session:
        job = claim_next_job(session, worker_id=worker, lease_seconds=JOB_LEASE_SECONDS)
        session.commit()
    if job is None:
        return None
    if job.kind == "bdot_county":
        from app.modules.imports.api.bdot_jobs import process_county_job
        return process_county_job(job)
    terc = job.scope_key.removeprefix("terc:")
    started = time.monotonic()
    try:
        result = run_pog_national_command(
            NationalImportOptions(
                terc=(terc,),
                concurrency=1,
                memory_budget_bytes=settings.pog_national_memory_budget_bytes,
                max_consecutive_failures=None,
                max_withdraw_fraction=1.0,
            ),
            with_csw=with_csw,
            progress=NationalProgress(),
            cancel=cancel,
        )
        payload = result.as_payload(failures_limit=10)
        outcome: dict[str, Any] = {
            "job_id": job.job_id,
            "terc": terc,
            "status": result.status,
            "counts": payload["counts"],
            "plan": payload["plan"],
            "seconds": round(time.monotonic() - started, 1),
        }
        with SessionLocal() as session:
            if result.status == "succeeded":
                complete_job(session, job.job_id, outcome)
                outcome["job_status"] = "succeeded"
            else:
                codes = payload.get("failure_codes") or {}
                only_data_defects = bool(codes) and all(_is_data_defect(str(code)) for code in codes)
                retryable = result.status in {"partial", "incomplete", "interrupted"} and not only_data_defects
                outcome["job_status"] = fail_job(
                    session, job.job_id, f"{result.status}:{payload.get('failure_codes')}"[:480], retryable=retryable
                )
            session.commit()
        return outcome
    except Exception as exc:  # noqa: BLE001 - błąd zlecenia nie zatrzymuje usługi
        logger.exception("pog_job_failed", extra={"job_id": job.job_id})
        with SessionLocal() as session:
            status = fail_job(session, job.job_id, f"{type(exc).__name__}: {exc}"[:480])
            session.commit()
        return {"job_id": job.job_id, "terc": terc, "status": "failed", "job_status": status, "error": str(exc)[:200]}


def _last_success_age_seconds() -> float | None:
    with SessionLocal() as session:
        status = pog_sync_status(session, interval_seconds=settings.pog_sync_interval_seconds)
    return status["lag_seconds"]


def run_sync_cycle(
    *, with_csw: bool = True, cancel: threading.Event | None = None, schedule: bool = True, emit: Callable[[str], None] | None = None
) -> dict[str, Any]:
    """Jeden cykl usługi; zwraca podsumowanie (JSON). Cykl nie startuje, gdy inny proces trzyma blokadę."""
    say = emit or (lambda _message: None)
    with engine.connect() as lock_connection:
        acquired = lock_connection.execute(
            text("SELECT pg_try_advisory_lock(hashtext(:name))"), {"name": SYNC_LOCK_NAME}
        ).scalar_one()
        if not acquired:
            say("[pog-sync] inny przebieg trzyma blokadę — pomijam cykl")
            return {"status": "locked"}
        try:
            summary: dict[str, Any] = {"status": "ok", "jobs": [], "scheduled": None}
            with SessionLocal() as session:
                rebuilt = rebuild_missing_map_tables(session)
                summary["map_rebuilt"] = {"zones": rebuilt.zones, "overlays": rebuilt.overlays, "acts": rebuilt.acts}
            while cancel is None or not cancel.is_set():
                job_result = process_one_job(with_csw=with_csw, cancel=cancel)
                if job_result is None:
                    break
                say(f"[pog-sync] zlecenie {job_result.get('job_id')} zakres {job_result.get('terc') or job_result.get('powiat')}: {job_result.get('job_status')}")
                summary["jobs"].append(job_result)
            if schedule and (cancel is None or not cancel.is_set()):
                age = _last_success_age_seconds()
                if age is None or age >= settings.pog_sync_interval_seconds:
                    say("[pog-sync] start importu przyrostowego (harmonogram)")
                    progress = NationalProgress()
                    result = run_pog_national_command(
                        _options_for_scheduled_run(), with_csw=with_csw, progress=progress, cancel=cancel
                    )
                    summary["scheduled"] = result.as_payload(failures_limit=20)
                    say(f"[pog-sync] import przyrostowy: {result.status}")
            summary["finished_at"] = datetime.now(timezone.utc).isoformat()
            return summary
        finally:
            lock_connection.execute(text("SELECT pg_advisory_unlock(hashtext(:name))"), {"name": SYNC_LOCK_NAME})
            lock_connection.commit()


def run_sync_forever(
    stop: threading.Event,
    *,
    with_csw: bool = True,
    schedule: bool = True,
    poll_seconds: int | None = None,
    emit: Callable[[str], None] | None = None,
) -> None:
    """Pętla usługi: cykl co ``poll_seconds``; harmonogram krajowy wewnątrz cyklu pilnuje interwału."""
    delay = poll_seconds or settings.pog_sync_queue_poll_seconds
    while not stop.is_set():
        try:
            run_sync_cycle(with_csw=with_csw, cancel=stop, schedule=schedule, emit=emit)
        except Exception:  # noqa: BLE001 - usługa ma przetrwać chwilową awarię bazy/RU
            logger.exception("pog_sync_cycle_failed")
        stop.wait(delay)
