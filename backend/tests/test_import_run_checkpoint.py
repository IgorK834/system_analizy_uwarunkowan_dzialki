"""Checkpoint fazy pobierania w ``import_runs.checkpoint`` (AU-106) — PostGIS, osobne transakcje."""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.data_sources import DataSourceEntry
from app.db.session import SessionLocal
from app.models.versioned import DataSource, ImportRun
from app.modules.imports.infrastructure.run_checkpoint import (
    DOWNLOAD_IMPORTER_VERSION,
    ImportRunCheckpoints,
)


def _source(source_id: str) -> DataSourceEntry:
    return DataSourceEntry.model_validate(
        {
            "source_id": source_id,
            "name": "Fixture POG",
            "owner": "Test",
            "status": "production",
            "production_ready": True,
            "contract_confirmed": True,
            "access_type": "app_gml",
            "capabilities_url": None,
            "file_url": "file:///fixture.gml",
            "type_names": ["strefaPlanistyczna"],
            "layers": None,
            "protocol_version": "fixture/1",
            "source_crs": "EPSG:2180",
            "target_crs": "EPSG:2180",
            "teryt_scope": ["1261011"],
            "license": "Fixture syntetyczny.",
            "attribution": "Test.",
            "expected_update_interval": "never",
            "sla": "test",
            "last_manual_verification": "2026-07-25",
        }
    )


@pytest.fixture
def source_id() -> Iterator[str]:
    identifier = f"pog_checkpoint_{uuid4().hex[:10]}"
    yield identifier
    with SessionLocal() as cleanup:
        row = cleanup.execute(select(DataSource).where(DataSource.source_id == identifier)).scalar_one_or_none()
        if row is not None:
            # Modele nie mają relacji, więc kolejność usuwania musi być jawna (najpierw wiersze zależne).
            cleanup.execute(delete(ImportRun).where(ImportRun.data_source_id == row.id))
            cleanup.execute(delete(DataSource).where(DataSource.id == row.id))
            cleanup.commit()


@pytest.fixture
def checkpoints(source_id: str, tmp_path) -> ImportRunCheckpoints:  # noqa: ANN001
    return ImportRunCheckpoints(SessionLocal, _source(source_id), artifact_dir=str(tmp_path))


def _runs(source_id: str) -> list[ImportRun]:
    with SessionLocal() as session:
        return list(
            session.execute(
                select(ImportRun)
                .join(DataSource, DataSource.id == ImportRun.data_source_id)
                .where(DataSource.source_id == source_id)
                .order_by(ImportRun.id)
            ).scalars()
        )


@pytest.mark.integration
def test_new_import_creates_a_running_row_with_the_resume_key(checkpoints: ImportRunCheckpoints, source_id: str) -> None:
    run = checkpoints.begin("klucz-1")

    assert run.resumed is False and run.previous is None
    (row,) = _runs(source_id)
    assert row.id == run.run_id and row.status == "running"
    assert row.importer_version == DOWNLOAD_IMPORTER_VERSION
    assert row.checkpoint == {"resume_key": "klucz-1", "resumes": 0}


@pytest.mark.integration
def test_progress_is_committed_in_its_own_transaction_and_survives_a_failed_main_session(
    checkpoints: ImportRunCheckpoints, source_id: str
) -> None:
    run = checkpoints.begin("klucz-2")
    main_session: Session = SessionLocal()
    try:
        checkpoints.save(run.run_id, {"progress": {"objects_fetched": 480}, "teryt": "246101"})
        main_session.rollback()  # główna transakcja importu wycofana (np. błąd) — postęp zostaje
    finally:
        main_session.close()

    (row,) = _runs(source_id)
    assert row.checkpoint["progress"] == {"objects_fetched": 480}
    assert row.checkpoint["teryt"] == "246101" and row.checkpoint["resume_key"] == "klucz-2"
    assert row.stats == {"objects_fetched": 480}


@pytest.mark.integration
def test_interrupted_run_is_resumed_instead_of_creating_a_new_one(checkpoints: ImportRunCheckpoints, source_id: str) -> None:
    first = checkpoints.begin("klucz-3")
    checkpoints.save(first.run_id, {"progress": {"objects_fetched": 100}}, status="interrupted")
    assert _runs(source_id)[0].finished_at is not None

    again = checkpoints.begin("klucz-3")

    assert again.resumed is True and again.run_id == first.run_id
    assert again.previous["progress"] == {"objects_fetched": 100} and again.previous["resumes"] == 1
    (row,) = _runs(source_id)
    assert row.status == "running" and row.finished_at is None


@pytest.mark.integration
@pytest.mark.parametrize("status", ["failed", "fetched"])
def test_failed_and_fetched_runs_are_resumable_but_finished_ones_are_not(
    checkpoints: ImportRunCheckpoints, source_id: str, status: str
) -> None:
    first = checkpoints.begin("klucz-4")
    checkpoints.save(first.run_id, {}, status=status)
    assert checkpoints.begin("klucz-4").resumed is True

    done = checkpoints.begin("klucz-5")
    checkpoints.save(done.run_id, {}, status="succeeded")
    fresh_start = checkpoints.begin("klucz-5")
    assert fresh_start.resumed is False and fresh_start.run_id != done.run_id


@pytest.mark.integration
def test_other_resume_key_never_resumes_a_foreign_run(checkpoints: ImportRunCheckpoints) -> None:
    first = checkpoints.begin("klucz-A")
    checkpoints.save(first.run_id, {}, status="interrupted")

    other = checkpoints.begin("klucz-B")

    assert other.resumed is False and other.run_id != first.run_id


@pytest.mark.integration
def test_fresh_abandons_the_previous_run(checkpoints: ImportRunCheckpoints, source_id: str) -> None:
    first = checkpoints.begin("klucz-6")
    checkpoints.save(first.run_id, {}, status="interrupted")

    restarted = checkpoints.begin("klucz-6", fresh=True)

    assert restarted.resumed is False and restarted.run_id != first.run_id
    statuses = [run.status for run in _runs(source_id)]
    assert statuses == ["abandoned", "running"]


@pytest.mark.integration
def test_save_never_raises_when_the_database_is_unavailable(checkpoints: ImportRunCheckpoints) -> None:
    run = checkpoints.begin("klucz-7")

    def broken() -> Session:
        raise RuntimeError("baza niedostępna")

    checkpoints._session_factory = broken  # type: ignore[assignment]
    checkpoints.save(run.run_id, {"progress": {}})  # postęp jest dodatkiem — nie zatrzymuje importu
