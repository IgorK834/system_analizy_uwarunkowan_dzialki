"""CLI importu POG (AU-106): postęp na stderr, jedna linia JSON na stdout i kody wyjścia."""

from __future__ import annotations

import json
import signal
import time
from typing import Any

import pytest

from app.modules.imports import __main__ as cli
from app.modules.imports.application.common import ImportOutcome
from app.modules.imports.domain.pog import PogValidationError
from app.modules.imports.infrastructure.ogc_client import OgcTransportError
from app.modules.imports.infrastructure.wfs import WfsFetchError, WfsFetchInterrupted
from app.shared.provenance import Provenance

ARGS = ["pog", "--source", "pog_app", "--act-id", "246101-POG", "--teryt", "246101", "--progress-interval", "0.05"]


class FakeSessionLocal:
    def __call__(self) -> "FakeSessionLocal":
        return self

    def __enter__(self) -> "FakeSessionLocal":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {}
    monkeypatch.setattr(cli, "SessionLocal", FakeSessionLocal())
    return state


def _run(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], behaviour, argv=ARGS):  # noqa: ANN001, ANN202
    def fake_run_pog_command(session, **kwargs):  # noqa: ANN001, ANN202
        return behaviour(kwargs["options"], kwargs)

    monkeypatch.setattr(cli, "run_pog_command", fake_run_pog_command)
    code = cli.main(argv)
    out, err = capsys.readouterr()
    return code, out, err


def _json(out: str) -> dict[str, Any]:
    lines = [line for line in out.splitlines() if line.strip()]
    assert len(lines) == 1, out  # stdout niesie dokładnie jeden obiekt JSON
    return json.loads(lines[0])


def _provenance() -> Provenance:
    return Provenance(source_id="pog_app", fetched_at=None, request_url="https://x", operation="WFS:GetFeature", complete=False)


def test_successful_import_prints_json_on_stdout_and_progress_on_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], captured: dict[str, Any]
) -> None:
    def behaviour(options, kwargs):  # noqa: ANN001, ANN202
        options.progress.set_phase("download")
        time.sleep(0.2)  # dłuższy import — komunikaty postępu muszą się pojawiać regularnie
        return ImportOutcome(status="succeeded", stats={"input": 1397}, warnings=("w1",), import_run_id=7, data_release_id=9)

    code, out, err = _run(monkeypatch, capsys, behaviour)

    payload = _json(out)
    assert code == 0 and payload["status"] == "succeeded" and payload["exit_code"] == 0
    assert payload["stats"] == {"input": 1397} and payload["import_run_id"] == 7 and payload["data_release_id"] == 9
    assert payload["timing"]["total_seconds"] >= 0.2 and "download" in payload["timing"]["phases_seconds"]
    assert payload["progress"]["phase"] == "done"
    progress_lines = [line for line in err.splitlines() if line.startswith("[import]")]
    assert len(progress_lines) >= 4  # start + kolejne ticki co 50 ms w 200 ms + zakończenie
    assert "[import]" not in out


def test_dry_run_is_a_complete_import_with_exit_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], captured: dict[str, Any]
) -> None:
    code, out, _err = _run(
        monkeypatch, capsys, lambda options, kwargs: ImportOutcome(status="dry_run_only", stats={"publishable": 1}), [*ARGS, "--dry-run"]
    )

    assert code == 0 and _json(out)["status"] == "dry_run_only"


def test_incomplete_download_exits_with_code_three_and_tells_how_to_resume(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], captured: dict[str, Any]
) -> None:
    def behaviour(options, kwargs):  # noqa: ANN001, ANN202
        options.resume_key = "abc123"
        raise WfsFetchError("WFS zwrócił niekompletny zasób 'app-pog:StrefaPlanistyczna'.", source=_provenance())

    code, out, _err = _run(monkeypatch, capsys, behaviour)

    payload = _json(out)
    assert code == 3 and payload["status"] == "incomplete" and payload["exit_code"] == 3
    assert "niekompletny" in payload["error"]
    assert payload["resume"]["resume_key"] == "abc123" and "ponownie" in payload["resume"]["hint"]


def test_service_error_outside_the_fetcher_is_also_incomplete(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], captured: dict[str, Any]
) -> None:
    def behaviour(options, kwargs):  # noqa: ANN001, ANN202
        raise OgcTransportError("Usługa OGC zwróciła HTTP 503.", source=_provenance())

    code, out, _err = _run(monkeypatch, capsys, behaviour)

    assert code == 3 and _json(out)["status"] == "incomplete"


def test_interrupted_import_exits_three_with_a_distinct_status(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], captured: dict[str, Any]
) -> None:
    def behaviour(options, kwargs):  # noqa: ANN001, ANN202
        raise WfsFetchInterrupted("przerwano", source=_provenance())

    code, out, _err = _run(monkeypatch, capsys, behaviour)

    assert code == 3 and _json(out)["status"] == "interrupted"


def test_qa_rejection_exits_one_and_is_not_confused_with_an_incomplete_import(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], captured: dict[str, Any]
) -> None:
    def behaviour(options, kwargs):  # noqa: ANN001, ANN202
        raise PogValidationError("Krytyczne QA POG odrzuciło cały import")

    code, out, _err = _run(monkeypatch, capsys, behaviour)

    payload = _json(out)
    assert code == 1 and payload["status"] == "rejected" and "resume" not in payload


def test_unexpected_error_exits_one_with_json_and_a_traceback_on_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], captured: dict[str, Any]
) -> None:
    def behaviour(options, kwargs):  # noqa: ANN001, ANN202
        raise RuntimeError("niespodziewany błąd")

    code, out, err = _run(monkeypatch, capsys, behaviour)

    payload = _json(out)
    assert code == 1 and payload["status"] == "failed" and "niespodziewany błąd" in payload["error"]
    assert "Traceback" in err and "RuntimeError" in err


def test_signal_cancels_the_download_gracefully_and_restores_the_previous_handler(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], captured: dict[str, Any]
) -> None:
    previous = signal.getsignal(signal.SIGTERM)

    def behaviour(options, kwargs):  # noqa: ANN001, ANN202
        signal.raise_signal(signal.SIGTERM)  # operator wysyła SIGTERM w trakcie pobierania
        assert options.cancel.is_set()
        raise WfsFetchInterrupted("przerwano na żądanie operatora", source=_provenance())

    code, out, err = _run(monkeypatch, capsys, behaviour)

    assert code == 3 and _json(out)["status"] == "interrupted"
    assert "sygnał przerwania" in err
    assert signal.getsignal(signal.SIGTERM) is previous


def test_second_signal_stops_immediately_and_still_prints_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], captured: dict[str, Any]
) -> None:
    def behaviour(options, kwargs):  # noqa: ANN001, ANN202
        signal.raise_signal(signal.SIGINT)
        signal.raise_signal(signal.SIGINT)
        raise AssertionError("drugi sygnał powinien przerwać")

    code, out, _err = _run(monkeypatch, capsys, behaviour)

    assert code == 3 and _json(out)["status"] == "interrupted"


def test_options_reach_the_command_with_defaults_from_settings(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], captured: dict[str, Any]
) -> None:
    seen: dict[str, Any] = {}

    def behaviour(options, kwargs):  # noqa: ANN001, ANN202
        seen["options"] = options
        return ImportOutcome(status="succeeded", stats={})

    _run(monkeypatch, capsys, behaviour)
    options = seen["options"]
    assert options.concurrency == cli.settings.import_wfs_concurrency and options.resume is True and options.fresh is False
    assert options.progress is not None and options.cancel is not None and options.checkpoints is not None

    _run(monkeypatch, capsys, behaviour, [*ARGS, "--concurrency", "6", "--fresh", "--no-resume"])
    options = seen["options"]
    assert (options.concurrency, options.resume, options.fresh) == (6, False, True)


def test_local_files_import_has_no_database_checkpoint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], captured: dict[str, Any]
) -> None:
    seen: dict[str, Any] = {}

    def behaviour(options, kwargs):  # noqa: ANN001, ANN202
        seen["options"] = options
        return ImportOutcome(status="dry_run_only", stats={})

    _run(
        monkeypatch,
        capsys,
        behaviour,
        ["pog", "--source", "pog_pilot_krakow", "--dry-run", "--resource", "planning_zone=strefy.gml", "--act-id", "x", "--teryt", "1261011"],
    )

    assert seen["options"].checkpoints is None


def test_unknown_source_is_a_json_failure_with_exit_one_not_a_bare_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], captured: dict[str, Any]
) -> None:
    code, out, err = _run(
        monkeypatch,
        capsys,
        lambda options, kwargs: ImportOutcome(status="succeeded", stats={}),
        ["pog", "--source", "zrodlo_ktorego_nie_ma", "--act-id", "x", "--teryt", "246101"],
    )

    payload = _json(out)
    assert code == 1 and payload["status"] == "failed" and payload["exit_code"] == 1
    assert "zrodlo_ktorego_nie_ma" in payload["error"] or "Error" in payload["error"]
    assert "Traceback" in err
