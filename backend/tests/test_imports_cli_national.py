"""CLI importu krajowego POG (ADR-026): ``pog-national``, ``pog-sync``, ``pog-status``, ``pog-enqueue``,
``pog-map-rebuild``, ``pog-withdraw`` i ``pog-rollback`` — jedna linia JSON na stdout, postęp na stderr, kody wyjścia 0/1/3.

Przebieg krajowy działa na zamrożonych plikach GML z RU (``national_support``); podmieniona jest wyłącznie fabryka
przebiegu, która normalnie łączy się z siecią RU.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from app.db.session import SessionLocal
from app.modules.imports import __main__ as cli
from app.modules.imports.api import sync_loop
from tests.national_support import FIXTURES, JEJKOWICE, SEJNY, FixtureGateway, build_stack, fixture_listing

pytestmark = pytest.mark.integration


def _json(out: str) -> dict[str, Any]:
    lines = [line for line in out.splitlines() if line.strip()]
    assert len(lines) == 1, out  # stdout niesie dokładnie jeden obiekt JSON
    return json.loads(lines[0])


@pytest.fixture
def national(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    state: dict[str, Any] = {"calls": [], "overrides": {}, "raise": None}
    stack = build_stack(tmp_path)

    def run(options: Any, **kwargs: Any):  # noqa: ANN202
        state["calls"].append((options, kwargs))
        if state["raise"] is not None:
            raise state["raise"]
        gateway = FixtureGateway(fixture_listing(JEJKOWICE, SEJNY), overrides=state["overrides"])
        return stack.run(gateway, terc=options.terc, concurrency=1, dry_run=options.dry_run, plan_only=options.plan_only)

    monkeypatch.setattr(cli, "run_pog_national_command", run)
    state["stack"] = stack
    return state


def test_national_import_prints_progress_on_stderr_and_one_json_line_with_exit_zero(
    national: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["pog-national", "--terc", "241203", "--concurrency", "2", "--no-withdraw", "--progress-interval", "0.05"])

    out, err = capsys.readouterr()
    payload = _json(out)
    assert code == 0 and payload["exit_code"] == 0 and payload["status"] == "succeeded"
    assert payload["counts"] == {"succeeded": 1} and payload["plan"]["to_import"] == 1
    assert payload["timing"]["wall_seconds"] >= 0
    options, kwargs = national["calls"][0]
    assert options.terc == ("241203",) and options.concurrency == 2 and options.withdraw is False
    assert kwargs["with_csw"] is True and kwargs["allow_regression"] is False
    assert "[import-krajowy]" in err  # żywotność procesu: linia postępu na stderr


def test_national_import_with_a_failed_act_exits_with_three_and_says_to_rerun(
    national: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    original = (FIXTURES / f"act_{SEJNY}.gml").read_text(encoding="utf-8")
    national["overrides"][SEJNY] = original.replace(
        "<app:minUdzialPowierzchniBiologicznieCzynnej>30.0<", "<app:minUdzialPowierzchniBiologicznieCzynnej>150.0<", 1
    ).encode()

    code = cli.main(["pog-national", "--no-csw", "--allow-regression", "--full", "--limit", "5"])

    payload = _json(capsys.readouterr().out)
    assert code == 3 and payload["exit_code"] == 3 and payload["status"] == "partial"
    assert payload["failure_codes"] == {"qa_rejected": 1} and "Uruchom to samo polecenie ponownie" in payload["resume"]["hint"]
    options, kwargs = national["calls"][0]
    assert options.full is True and options.limit == 5
    assert kwargs["with_csw"] is False and kwargs["allow_regression"] is True


def test_national_import_error_ends_with_json_and_exit_one(national: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    national["raise"] = RuntimeError("RU niedostępne")

    code = cli.main(["pog-national", "--plan-only"])

    payload = _json(capsys.readouterr().out)
    assert code == 1 and payload["status"] == "failed" and "RU niedostępne" in payload["error"] and payload["exit_code"] == 1


def test_status_enqueue_and_map_rebuild_operations(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["pog-enqueue", "--terc", "241203", "--terc", "200901"]) == 0
    queued = _json(capsys.readouterr().out)
    assert queued["status"] == "queued" and [item["created"] for item in queued["jobs"]] == [True, True]
    assert cli.main(["pog-enqueue", "--terc", "241203"]) == 0
    again = _json(capsys.readouterr().out)
    assert again["jobs"][0]["created"] is False  # deduplikacja aktywnego zlecenia tej gminy
    with pytest.raises(SystemExit):  # TERC musi mieć 6 cyfr — odrzucone już przy czytaniu argumentów
        cli.main(["pog-enqueue", "--terc", "12"])
    capsys.readouterr()

    assert cli.main(["pog-map-rebuild"]) == 0
    rebuilt = _json(capsys.readouterr().out)
    assert rebuilt == {"status": "succeeded", "acts": 0, "zones": 0, "overlays": 0, "exit_code": 0}

    assert cli.main(["pog-status"]) == 0
    status = _json(capsys.readouterr().out)
    assert status["status"] == "ok" and status["queue"]["queued"] == 2 and status["registry"]["active_acts"] == 0


def test_withdraw_and_rollback_keep_history_and_reject_unknown_acts(
    national: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    stack = national["stack"]
    stack.run(FixtureGateway(fixture_listing(JEJKOWICE)))
    act_id = "PL.ZIPPZP.10169/241203-POG/1POG"

    assert cli.main(["pog-withdraw", "--act-id", "nie-ma-takiego"]) == 1
    assert _json(capsys.readouterr().out)["status"] == "rejected"

    assert cli.main(["pog-withdraw", "--act-id", act_id, "--reason", "not_current"]) == 0
    withdrawn = _json(capsys.readouterr().out)
    assert withdrawn["status"] == "succeeded" and withdrawn["state_id"]
    with SessionLocal() as db:
        active = db.execute(text("SELECT count(*) FROM pog_registry_entries WHERE state_to_id IS NULL")).scalar_one()
        history = db.execute(text("SELECT count(*) FROM pog_registry_entries WHERE end_reason = 'not_current'")).scalar_one()
    assert (active, history) == (0, 1)  # akt dezaktywowany, historia zachowana

    # Cofnięcie przywraca wersję wycofanego aktu (jedyna wersja) jako nowy stan „rollback”.
    assert cli.main(["pog-rollback", "--act-id", act_id]) == 0
    rolled = _json(capsys.readouterr().out)
    assert rolled["status"] == "succeeded" and rolled["version_id"]
    with SessionLocal() as db:
        assert db.execute(text("SELECT count(*) FROM pog_registry_entries WHERE state_to_id IS NULL")).scalar_one() == 1
    assert cli.main(["pog-rollback", "--act-id", "nie-ma-takiego"]) == 1
    assert _json(capsys.readouterr().out)["status"] == "rejected"


def test_sync_service_one_cycle_prints_the_summary_and_maps_status_to_exit_codes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    results = iter([{"status": "ok", "jobs": []}, {"status": "locked"}, {"status": "failed"}])
    seen: dict[str, Any] = {}

    def cycle(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return dict(next(results))

    monkeypatch.setattr(sync_loop, "run_sync_cycle", cycle)
    monkeypatch.setattr(cli.settings, "pog_sync_interval_seconds", 86400)

    assert cli.main(["pog-sync", "--once", "--no-csw", "--interval", "7200"]) == 0
    assert _json(capsys.readouterr().out)["status"] == "ok" and seen["with_csw"] is False
    assert cli.settings.pog_sync_interval_seconds == 7200
    assert cli.main(["pog-sync", "--once"]) == 0  # inna instancja trzyma blokadę — to nie błąd
    assert _json(capsys.readouterr().out)["status"] == "locked"
    assert cli.main(["pog-sync", "--once"]) == 3
    assert _json(capsys.readouterr().out)["exit_code"] == 3


def test_sync_service_loop_announces_itself_and_stops_cleanly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    called: dict[str, Any] = {}

    def forever(stop: Any, **kwargs: Any) -> None:
        called.update(kwargs)

    monkeypatch.setattr(sync_loop, "run_sync_forever", forever)

    assert cli.main(["pog-sync"]) == 0

    out, err = capsys.readouterr()
    assert _json(out)["status"] == "stopped" and "usługa uruchomiona" in err and called["with_csw"] is True
