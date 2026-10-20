"""Zlecenia powiatów BDOT w istniejącej kolejce import_jobs (AU-201)."""
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.pog_registry import ImportJob
from app.modules.imports.domain.bdot10k import scope_key
from app.modules.imports.infrastructure.job_queue import EnqueueResult

JOB_KIND_BDOT_COUNTY = "bdot_county"


def enqueue_county_import(session: Session, powiat: str) -> EnqueueResult:
    scope = scope_key(powiat)
    row = session.execute(
        insert(ImportJob).values(kind=JOB_KIND_BDOT_COUNTY, scope_key=scope, status="queued",
                                requested_by="coverage", max_attempts=3)
        .on_conflict_do_nothing(index_elements=["kind", "scope_key"], index_where=text("status IN ('queued', 'running')"))
        .returning(ImportJob.id, ImportJob.status, ImportJob.requested_at)
    ).first()
    if row is not None:
        return EnqueueResult(row.id, True, row.status, row.requested_at)
    row = session.execute(select(ImportJob.id, ImportJob.status, ImportJob.requested_at).where(
        ImportJob.kind == JOB_KIND_BDOT_COUNTY, ImportJob.scope_key == scope,
        ImportJob.status.in_(("queued", "running")),
    )).first()
    if row is None:
        return enqueue_county_import(session, powiat)
    return EnqueueResult(row.id, False, row.status, row.requested_at)
