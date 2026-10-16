"""Kolejka zleceń importu w PostgreSQL (``import_jobs``, ``FOR UPDATE SKIP LOCKED``) — ADR-026, issue 300.

* **Deduplikacja:** częściowy indeks unikalny ``(kind, scope_key)`` dla statusów ``queued``/``running`` — kolejne
  żądania importu tej samej gminy dostają to samo zlecenie, bez dubli.
* **Dzierżawa:** zlecenie ``running`` z wygasłą dzierżawą (proces roboczy zginął) wraca do kolejki przy kolejnym
  ``claim``; liczba prób jest ograniczona (``max_attempts``), po wyczerpaniu zlecenie jest ``failed``.
* Funkcje działają na przekazanej sesji; zatwierdzenie transakcji należy do wołającego.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Final

from sqlalchemy import and_, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.pog_registry import ImportJob

JOB_KIND_POG_COMMUNE: Final[str] = "pog_commune"
ACTIVE_STATUSES: Final[tuple[str, ...]] = ("queued", "running")


def scope_key_for_terc(terc: str) -> str:
    code = terc.strip()[:6]
    if not code.isdigit() or len(code) != 6:
        raise ValueError(f"TERC gminy musi mieć co najmniej 6 cyfr: {terc!r}.")
    return f"terc:{code}"


def worker_identity() -> str:
    return f"{socket.gethostname()}:{id(object()) % 100000}"[:80]


@dataclass(frozen=True)
class EnqueueResult:
    job_id: int
    created: bool
    status: str
    requested_at: datetime | None


@dataclass(frozen=True)
class ClaimedJob:
    job_id: int
    kind: str
    scope_key: str
    attempts: int
    max_attempts: int
    requested_by: str


def enqueue_commune_import(
    session: Session, terc: str, *, requested_by: str, max_attempts: int = 3
) -> EnqueueResult:
    """Zleca import gminy; istniejące aktywne zlecenie tej gminy jest zwracane (``created=False``)."""
    scope = scope_key_for_terc(terc)
    statement = (
        pg_insert(ImportJob)
        .values(
            kind=JOB_KIND_POG_COMMUNE,
            scope_key=scope,
            status="queued",
            requested_by=requested_by,
            max_attempts=max_attempts,
        )
        .on_conflict_do_nothing(
            index_elements=["kind", "scope_key"], index_where=text("status IN ('queued', 'running')")
        )
        .returning(ImportJob.id, ImportJob.status, ImportJob.requested_at)
    )
    row = session.execute(statement).first()
    if row is not None:
        return EnqueueResult(int(row.id), True, str(row.status), row.requested_at)
    existing = session.execute(
        select(ImportJob.id, ImportJob.status, ImportJob.requested_at)
        .where(
            ImportJob.kind == JOB_KIND_POG_COMMUNE,
            ImportJob.scope_key == scope,
            ImportJob.status.in_(ACTIVE_STATUSES),
        )
        .limit(1)
    ).first()
    if existing is None:  # zlecenie zakończyło się między INSERT a SELECT — zwróć je jako nowe
        return enqueue_commune_import(session, terc, requested_by=requested_by, max_attempts=max_attempts)
    return EnqueueResult(int(existing.id), False, str(existing.status), existing.requested_at)


def claim_next_job(
    session: Session, *, worker_id: str, lease_seconds: int, now: datetime | None = None
) -> ClaimedJob | None:
    stamp = now or datetime.now(timezone.utc)
    job = session.execute(
        select(ImportJob)
        .where(
            or_(
                and_(
                    ImportJob.status == "queued",
                    or_(ImportJob.not_before.is_(None), ImportJob.not_before <= stamp),
                ),
                and_(ImportJob.status == "running", ImportJob.lease_until < stamp),
            )
        )
        .order_by(ImportJob.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    ).scalar_one_or_none()
    if job is None:
        return None
    if job.status == "running" and job.attempts >= job.max_attempts:
        job.status = "failed"
        job.finished_at = stamp
        job.last_error = "lease_expired_max_attempts"
        session.flush()
        return claim_next_job(session, worker_id=worker_id, lease_seconds=lease_seconds, now=stamp)
    job.status = "running"
    job.attempts += 1
    job.worker_id = worker_id
    job.started_at = stamp
    job.lease_until = stamp + timedelta(seconds=lease_seconds)
    session.flush()
    return ClaimedJob(job.id, job.kind, job.scope_key, job.attempts, job.max_attempts, job.requested_by)


def extend_lease(session: Session, job_id: int, *, lease_seconds: int) -> None:
    session.execute(
        text("UPDATE import_jobs SET lease_until = :until WHERE id = :id AND status = 'running'"),
        {"until": datetime.now(timezone.utc) + timedelta(seconds=lease_seconds), "id": job_id},
    )


def complete_job(session: Session, job_id: int, result: dict[str, Any]) -> None:
    job = session.get(ImportJob, job_id)
    if job is None:
        return
    job.status = "succeeded"
    job.result = result
    job.finished_at = datetime.now(timezone.utc)
    job.lease_until = None
    job.last_error = None


def fail_job(
    session: Session, job_id: int, error: str, *, retry_delay_seconds: int = 300, retryable: bool = True
) -> str:
    """Zapisuje błąd zlecenia; zwraca status końcowy (``queued`` — będzie ponowione, ``failed`` — wyczerpane)."""
    job = session.get(ImportJob, job_id)
    if job is None:
        return "failed"
    job.last_error = error
    job.lease_until = None
    if retryable and job.attempts < job.max_attempts:
        job.status = "queued"
        job.not_before = datetime.now(timezone.utc) + timedelta(seconds=retry_delay_seconds * job.attempts)
        return "queued"
    job.status = "failed"
    job.finished_at = datetime.now(timezone.utc)
    return "failed"


def job_status_for_terc(session: Session, terc: str) -> dict[str, Any] | None:
    """Najnowsze zlecenie importu gminy (do informacji w odpowiedzi analizy i na ekranie pokrycia)."""
    row = session.execute(
        select(ImportJob)
        .where(ImportJob.kind == JOB_KIND_POG_COMMUNE, ImportJob.scope_key == scope_key_for_terc(terc))
        .order_by(ImportJob.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        return None
    return {
        "job_id": row.id,
        "status": row.status,
        "attempts": row.attempts,
        "requested_by": row.requested_by,
        "requested_at": row.requested_at,
        "finished_at": row.finished_at,
        "last_error": row.last_error,
    }


def queue_stats(session: Session) -> dict[str, int]:
    rows = session.execute(text("SELECT status, count(*) FROM import_jobs GROUP BY status")).all()
    return {str(status): int(count) for status, count in rows}
