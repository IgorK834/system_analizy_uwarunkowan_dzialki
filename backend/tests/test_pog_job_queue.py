"""Kolejka zleceń importu gmin w PostgreSQL (``import_jobs``): deduplikacja, dzierżawa, ponowienia (ADR-026, issue 300)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest
from sqlalchemy import text

from app.db.session import SessionLocal
from app.modules.imports.infrastructure.job_queue import (
    claim_next_job,
    complete_job,
    enqueue_commune_import,
    fail_job,
    job_status_for_terc,
    queue_stats,
    scope_key_for_terc,
)

pytestmark = pytest.mark.integration


def test_scope_key_accepts_six_or_seven_digits_and_rejects_garbage() -> None:
    assert scope_key_for_terc("2461011") == "terc:246101"
    with pytest.raises(ValueError):
        scope_key_for_terc("24")
    with pytest.raises(ValueError):
        scope_key_for_terc("abcdef")


def test_enqueue_deduplicates_active_jobs_per_commune() -> None:
    with SessionLocal() as db:
        first = enqueue_commune_import(db, "246101", requested_by="analysis")
        again = enqueue_commune_import(db, "2461011", requested_by="analysis")
        other = enqueue_commune_import(db, "226401", requested_by="operator")
        db.commit()
        assert first.created and not again.created and again.job_id == first.job_id
        assert other.created and other.job_id != first.job_id
        assert queue_stats(db) == {"queued": 2}


def test_finished_job_allows_a_new_request_for_the_same_commune() -> None:
    with SessionLocal() as db:
        first = enqueue_commune_import(db, "246101", requested_by="analysis")
        claimed = claim_next_job(db, worker_id="w1", lease_seconds=60)
        assert claimed is not None and claimed.job_id == first.job_id and claimed.attempts == 1
        complete_job(db, claimed.job_id, {"status": "succeeded"})
        db.commit()
        second = enqueue_commune_import(db, "246101", requested_by="analysis")
        db.commit()
        assert second.created and second.job_id != first.job_id
        status = job_status_for_terc(db, "246101")
        assert status is not None and status["status"] == "queued"


def test_claim_is_exclusive_and_ordered_and_skips_locked_rows() -> None:
    with SessionLocal() as db:
        ids = [enqueue_commune_import(db, terc, requested_by="operator").job_id for terc in ("010101", "020202", "030303")]
        db.commit()
    barrier = Barrier(3)

    def worker(index: int) -> int | None:
        with SessionLocal() as session:
            barrier.wait(timeout=10)
            job = claim_next_job(session, worker_id=f"w{index}", lease_seconds=60)
            session.commit()
            return job.job_id if job else None

    with ThreadPoolExecutor(max_workers=3) as pool:
        claimed = list(pool.map(worker, range(3)))
    assert sorted(item for item in claimed if item is not None) == sorted(ids)
    assert len(set(claimed)) == 3, "dwa procesy roboczych wzięły to samo zlecenie"
    with SessionLocal() as db:
        assert claim_next_job(db, worker_id="late", lease_seconds=60) is None


def test_expired_lease_returns_the_job_to_the_queue_until_attempts_run_out() -> None:
    with SessionLocal() as db:
        job = enqueue_commune_import(db, "246101", requested_by="analysis", max_attempts=2)
        past = datetime.now(timezone.utc) - timedelta(hours=2)
        first = claim_next_job(db, worker_id="w1", lease_seconds=60, now=past)
        assert first is not None and first.attempts == 1
        db.commit()
        # Dzierżawa wygasła (proces zginął): zlecenie wraca do puli.
        second = claim_next_job(db, worker_id="w2", lease_seconds=60)
        assert second is not None and second.job_id == job.job_id and second.attempts == 2
        db.commit()
        # Kolejne wygaśnięcie przy wyczerpanych próbach kończy zlecenie jako nieudane.
        db.execute(text("UPDATE import_jobs SET lease_until = now() - interval '1 hour' WHERE id = :id"), {"id": job.job_id})
        assert claim_next_job(db, worker_id="w3", lease_seconds=60) is None
        db.commit()
        assert job_status_for_terc(db, "246101")["status"] == "failed"  # type: ignore[index]


def test_failed_job_is_retried_with_backoff_then_marked_failed() -> None:
    with SessionLocal() as db:
        job = enqueue_commune_import(db, "246101", requested_by="analysis", max_attempts=2)
        first = claim_next_job(db, worker_id="w", lease_seconds=60)
        assert first is not None
        assert fail_job(db, job.job_id, "transport", retry_delay_seconds=3600) == "queued"
        db.commit()
        # Odroczone (not_before w przyszłości): nie jest od razu dostępne.
        assert claim_next_job(db, worker_id="w", lease_seconds=60) is None
        later = claim_next_job(db, worker_id="w", lease_seconds=60, now=datetime.now(timezone.utc) + timedelta(hours=3))
        assert later is not None and later.attempts == 2
        assert fail_job(db, job.job_id, "transport") == "failed"
        db.commit()
        status = job_status_for_terc(db, "246101")
        assert status["status"] == "failed" and status["last_error"] == "transport" and status["finished_at"]  # type: ignore[index]


def test_non_retryable_failure_ends_immediately() -> None:
    with SessionLocal() as db:
        job = enqueue_commune_import(db, "246101", requested_by="analysis")
        claim_next_job(db, worker_id="w", lease_seconds=60)
        assert fail_job(db, job.job_id, "gml_terc_mismatch", retryable=False) == "failed"
