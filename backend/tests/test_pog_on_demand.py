"""Import POG na żądanie z analizy (issue 300): zlecenie w tle, deduplikacja i jawna informacja w odpowiedzi."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from shapely.geometry import box
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.services.analysis_orchestrator import _analyze_pog_best_effort
from app.services.pog_on_demand import request_commune_import
from tests.national_support import FixtureGateway, build_stack, frozen_listing

pytestmark = pytest.mark.integration

PARCEL = box(400000, 300000, 400020, 300020)


@pytest.fixture
def session() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()


def _jobs() -> list[tuple[str, str, str]]:
    with SessionLocal() as db:
        return [tuple(row) for row in db.execute(text("SELECT scope_key, status, requested_by FROM import_jobs ORDER BY id"))]  # type: ignore[misc]


def test_commune_with_a_ru_plan_and_no_local_state_gets_one_deduplicated_job(session: Session, tmp_path: Path) -> None:
    stack = build_stack(tmp_path)
    stack.run(FixtureGateway(frozen_listing()), limit=0)  # sam wykaz RU, bez importu
    session.expire_all()

    first = request_commune_import(session, "2461011", discovery_found_act=False)
    again = request_commune_import(session, "2461011", discovery_found_act=False)

    assert first is not None and first.created and first.job_status == "queued"
    assert again is not None and not again.created and again.job_id == first.job_id
    assert _jobs() == [("terc:246101", "queued", "analysis")]
    assert first.warning().code == "POG_IMPORT_QUEUED" and "w tle" in first.warning().message
    assert again.warning().code == "POG_IMPORT_PENDING"
    assert "nie oznacza braku planu" in first.warning().message


def test_no_job_without_evidence_of_a_ru_plan_or_when_the_commune_is_already_imported(
    session: Session, tmp_path: Path
) -> None:
    assert request_commune_import(session, "146501", discovery_found_act=False) is None  # brak wykazu i discovery
    assert request_commune_import(session, "bad", discovery_found_act=True) is None
    assert request_commune_import(None, "246101", discovery_found_act=True) is None
    assert _jobs() == []

    created = request_commune_import(session, "146501", discovery_found_act=True)  # discovery RU znalazło akt
    assert created is not None and created.created

    stack = build_stack(tmp_path)
    stack.run(FixtureGateway(frozen_listing()), terc=("241203",))
    session.expire_all()
    assert request_commune_import(session, "2412031", discovery_found_act=True) is None  # już zaimportowana


def test_recent_failure_suppresses_a_new_job_for_six_hours(session: Session) -> None:
    with SessionLocal() as db:
        db.execute(
            text(
                "INSERT INTO import_jobs (kind, scope_key, status, requested_by, attempts, max_attempts, finished_at, last_error) "
                "VALUES ('pog_commune', 'terc:246101', 'failed', 'analysis', 3, 3, now(), 'transport')"
            )
        )
        db.commit()
    request = request_commune_import(session, "246101", discovery_found_act=True)
    assert request is not None and request.job_status == "failed" and not request.created
    assert request.warning().code == "POG_IMPORT_FAILED_RECENTLY"
    assert len(_jobs()) == 1


@pytest.mark.asyncio
async def test_analysis_reports_the_queued_import_in_its_warnings(session: Session, tmp_path: Path) -> None:
    build_stack(tmp_path).run(FixtureGateway(frozen_listing()), limit=0)
    session.expire_all()
    with patch("app.services.analysis_orchestrator.discover_pog", new=AsyncMock(side_effect=TimeoutError("RU"))):
        pog, _ouz, warnings, _sources = await _analyze_pog_best_effort(PARCEL, "2461011", session)

    assert pog.legal_status == "unknown" and pog.data_availability == "unavailable"
    assert [item.code for item in warnings if item.code.startswith("POG_IMPORT")] == ["POG_IMPORT_QUEUED"]
    assert _jobs() == [("terc:246101", "queued", "analysis")]
