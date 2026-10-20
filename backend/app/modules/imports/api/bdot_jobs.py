"""Wykonanie zlecenia BDOT tą samą kompozycją co CLI; błędy zostają w kolejce."""
from time import monotonic
from typing import Any

from app.db.session import SessionLocal
from app.modules.imports.composition import run_bdot10k_command, Bdot10kReadError, ClaimedJob, complete_job, fail_job
from app.modules.imports.domain.bdot10k import Bdot10kValidationError


def process_county_job(job: ClaimedJob) -> dict[str, Any]:
    started = monotonic()
    powiat = job.scope_key.removeprefix("powiat:")
    try:
        with SessionLocal() as db:
            result = run_bdot10k_command(db, source_id="bdot10k", dry_run=False, powiat=powiat)
        outcome = {"job_id": job.job_id, "powiat": powiat, "status": result.status,
                   "data_release_id": result.data_release_id, "stats": result.stats,
                   "seconds": round(monotonic() - started, 2)}
        with SessionLocal() as db:
            if result.status == "succeeded":
                complete_job(db, job.job_id, outcome)
                outcome["job_status"] = "succeeded"
            else:
                outcome["job_status"] = fail_job(db, job.job_id, str(result.status))
            db.commit()
        return outcome
    except Exception as exc:  # noqa: BLE001 - awaria zlecenia nie zatrzymuje workera
        with SessionLocal() as db:
            status = fail_job(db, job.job_id, f"{type(exc).__name__}: {exc}"[:480],
                              retryable=not isinstance(exc, (Bdot10kValidationError, Bdot10kReadError)))
            db.commit()
        return {"job_id": job.job_id, "powiat": powiat, "status": "failed", "job_status": status}
