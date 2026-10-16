"""Usługa harmonogramu ``pog-sync`` (ADR-026): kolejka zleceń gmin, blokada przed dublami, cykl i status operatora.

Import gminy jest realnym przebiegiem krajowym na zamrożonych plikach GML z RU (``national_support``); podmieniona jest
tylko fabryka przebiegu (``run_pog_national_command``), która normalnie łączy się z siecią RU.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from app.db.session import SessionLocal, engine
from app.modules.imports.api import sync_loop
from app.modules.imports.application.national_import import NationalImportOptions
from app.modules.imports.infrastructure.job_queue import enqueue_commune_import, job_status_for_terc
from app.modules.imports.infrastructure.sync_status import pog_sync_status
from tests.national_support import FIXTURES, JEJKOWICE, SEJNY, FixtureGateway, build_stack, fixture_listing

pytestmark = pytest.mark.integration

JEJKOWICE_TERC, SEJNY_TERC = "241203", "200901"


@pytest.fixture
def fixture_runner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[dict[str, Any]]:
    """Podstawia przebieg krajowy z zamrożonej bramy RU; ``overrides`` podmienia treść pliku aktu."""
    state: dict[str, Any] = {"overrides": {}, "calls": [], "raise": None}
    stack = build_stack(tmp_path)

    def run(options: NationalImportOptions, **kwargs: Any):  # noqa: ANN202
        state["calls"].append((options, kwargs))
        if state["raise"] is not None:
            raise state["raise"]
        gateway = FixtureGateway(fixture_listing(JEJKOWICE, SEJNY), overrides=state["overrides"])
        return stack.run(gateway, terc=options.terc, concurrency=1, max_withdraw_fraction=1.0)

    monkeypatch.setattr(sync_loop, "run_pog_national_command", run)
    yield state


def _enqueue(terc: str, *, attempts: int = 3) -> int:
    with SessionLocal() as db:
        job = enqueue_commune_import(db, terc, requested_by="analysis", max_attempts=attempts)
        db.commit()
        return job.job_id


def test_empty_queue_returns_nothing(fixture_runner: dict[str, Any]) -> None:
    assert sync_loop.process_one_job() is None
    assert fixture_runner["calls"] == []


def test_job_of_a_commune_imports_it_and_completes(fixture_runner: dict[str, Any]) -> None:
    job_id = _enqueue(JEJKOWICE_TERC)

    outcome = sync_loop.process_one_job()

    assert outcome is not None and outcome["job_id"] == job_id and outcome["terc"] == JEJKOWICE_TERC
    assert outcome["status"] == "succeeded" and outcome["job_status"] == "succeeded" and outcome["counts"] == {"succeeded": 1}
    options, kwargs = fixture_runner["calls"][0]
    assert options.terc == (JEJKOWICE_TERC,) and options.concurrency == 1 and kwargs["with_csw"] is True
    with SessionLocal() as db:
        assert job_status_for_terc(db, JEJKOWICE_TERC)["status"] == "succeeded"  # type: ignore[index]
        assert db.execute(
            text("SELECT count(*) FROM pog_registry_entries e JOIN planning_acts pa ON pa.id = e.planning_act_id "
                 "WHERE e.state_to_id IS NULL AND pa.teryt LIKE '241203%'")
        ).scalar_one() == 1


def test_a_data_defect_in_the_source_ends_the_job_at_once_instead_of_retrying(fixture_runner: dict[str, Any]) -> None:
    original = (FIXTURES / f"act_{SEJNY}.gml").read_text(encoding="utf-8")
    fixture_runner["overrides"][SEJNY] = original.replace(
        "<app:minUdzialPowierzchniBiologicznieCzynnej>30.0<", "<app:minUdzialPowierzchniBiologicznieCzynnej>150.0<", 1
    ).encode()
    _enqueue(SEJNY_TERC, attempts=3)

    outcome = sync_loop.process_one_job()

    assert outcome is not None and outcome["status"] == "partial"
    # Ponowienie tego samego pliku z wadą da ten sam wynik: zlecenie kończy się od razu jako failed (1 próba z 3).
    assert outcome["job_status"] == "failed"
    with SessionLocal() as db:
        job = job_status_for_terc(db, SEJNY_TERC)
        assert job is not None and job["status"] == "failed" and job["attempts"] == 1
        assert "qa_rejected" in (job["last_error"] or "")
    assert sync_loop.process_one_job() is None  # nic w kolejce


def test_an_unexpected_error_is_isolated_and_the_job_is_retried_later(fixture_runner: dict[str, Any]) -> None:
    fixture_runner["raise"] = RuntimeError("baza chwilowo niedostępna")
    _enqueue(JEJKOWICE_TERC)

    outcome = sync_loop.process_one_job()

    assert outcome is not None and outcome["status"] == "failed" and outcome["job_status"] == "queued"
    assert "baza chwilowo niedostępna" in outcome["error"]
    with SessionLocal() as db:
        job = job_status_for_terc(db, JEJKOWICE_TERC)
        assert job is not None and job["status"] == "queued" and "RuntimeError" in (job["last_error"] or "")
    # Ponowienie jest odroczone (``not_before``), więc natychmiastowe pobranie nic nie znajduje.
    assert sync_loop.process_one_job() is None


def test_cycle_skips_when_another_instance_holds_the_lock(fixture_runner: dict[str, Any]) -> None:
    _enqueue(JEJKOWICE_TERC)
    messages: list[str] = []
    with engine.connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(hashtext(:name))"), {"name": sync_loop.SYNC_LOCK_NAME})
        try:
            summary = sync_loop.run_sync_cycle(schedule=False, emit=messages.append)
        finally:
            holder.execute(text("SELECT pg_advisory_unlock(hashtext(:name))"), {"name": sync_loop.SYNC_LOCK_NAME})
            holder.commit()
    assert summary == {"status": "locked"} and any("blokadę" in item for item in messages)
    assert fixture_runner["calls"] == [], "druga instancja nie może dublować pobierania"

    # Po zwolnieniu blokady cykl działa i przetwarza zlecenie, a blokada jest zwalniana po cyklu.
    done = sync_loop.run_sync_cycle(schedule=False)
    assert done["status"] == "ok" and [item["terc"] for item in done["jobs"]] == [JEJKOWICE_TERC]
    assert done["map_rebuilt"] == {"zones": 0, "overlays": 0, "acts": 0} or done["map_rebuilt"]["acts"] >= 0
    again = sync_loop.run_sync_cycle(schedule=False)
    assert again["status"] == "ok" and again["jobs"] == []


def test_cycle_runs_the_scheduled_incremental_import_only_after_the_interval(
    fixture_runner: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sync_loop.settings, "pog_sync_interval_seconds", 3600)
    first = sync_loop.run_sync_cycle()
    # Brak udanego przebiegu krajowego: import przyrostowy startuje (w teście: dwa realne akty z bramy).
    assert first["status"] == "ok" and first["scheduled"]["status"] == "succeeded"
    assert len(fixture_runner["calls"]) == 1
    options = fixture_runner["calls"][0][0]
    assert options.concurrency == sync_loop.settings.pog_national_concurrency and options.terc == ()

    # Świeży udany przebieg: kolejny cykl nie pobiera niczego (interwał nie minął).
    second = sync_loop.run_sync_cycle()
    assert second["status"] == "ok" and second["scheduled"] is None
    assert len(fixture_runner["calls"]) == 1


def test_forever_loop_survives_a_failing_cycle_and_stops_on_the_event(monkeypatch: pytest.MonkeyPatch) -> None:
    stop = threading.Event()
    calls: list[int] = []

    def flaky_cycle(**_kwargs: Any) -> dict[str, Any]:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("chwilowa awaria bazy")
        stop.set()
        return {"status": "ok"}

    monkeypatch.setattr(sync_loop, "run_sync_cycle", flaky_cycle)

    sync_loop.run_sync_forever(stop, poll_seconds=0)

    assert len(calls) == 2  # pierwszy cykl padł, usługa przeżyła i wykonała kolejny


def test_status_reports_registry_catalog_queue_and_staleness(fixture_runner: dict[str, Any]) -> None:
    with SessionLocal() as db:
        empty = pog_sync_status(db, interval_seconds=3600)
    assert empty["registry"]["active_acts"] == 0 and empty["registry"]["state_id"] is None
    assert empty["last_national_run"] is None and empty["stale"] is True and empty["lag_seconds"] is None

    fixture_runner["overrides"] = {}
    sync_loop.run_sync_cycle()  # import przyrostowy z bramy: dwie gminy
    _enqueue("226301")
    with SessionLocal() as db:
        status = pog_sync_status(db, interval_seconds=3600)
    assert status["registry"]["active_acts"] == 2 and status["registry"]["imported_communes"] == 2
    assert status["ru_catalog"]["current_acts"] == 2 and status["ru_catalog"]["communes_not_imported"] == 0
    assert status["last_national_run"]["status"] == "succeeded" and status["last_national_run"]["counts"] == {"succeeded": 2}
    assert status["stale"] is False and status["lag_seconds"] is not None and status["lag_seconds"] < 3600
    assert status["queue"].get("queued") == 1 and status["versions_without_map_rows"] == 0
    with SessionLocal() as db:
        # Przy interwale krótszym niż wiek ostatniego sukcesu status mówi „nieaktualne”.
        assert pog_sync_status(db, interval_seconds=0)["stale"] is True
