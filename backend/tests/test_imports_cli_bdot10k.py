"""CLI importu BDOT10k (Task 23.1): jedna linia JSON na stdout, kody wyjścia 0/1 i obsługa błędów paczki."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.modules.imports import __main__ as cli
from app.modules.imports.application.common import ImportOutcome
from app.modules.imports.domain.bdot10k import Bdot10kValidationError
from app.modules.imports.infrastructure.bdot10k.download import Bdot10kDownloadError
from app.modules.imports.infrastructure.bdot10k.reader import Bdot10kReadError

ARGS = ["bdot10k", "--powiat", "1261", "--package", "/pkg/1261_GML.zip"]


class FakeSessionLocal:
    def __call__(self) -> "FakeSessionLocal":
        return self

    def __enter__(self) -> "FakeSessionLocal":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def _run(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], behaviour, argv=ARGS):  # noqa: ANN001, ANN202
    seen: dict[str, Any] = {}

    def fake_run_bdot10k_command(session, **kwargs):  # noqa: ANN001, ANN202
        seen.update(kwargs)
        return behaviour(kwargs)

    monkeypatch.setattr(cli, "SessionLocal", FakeSessionLocal())
    monkeypatch.setattr(cli, "run_bdot10k_command", fake_run_bdot10k_command)
    code = cli.main(argv)
    out, err = capsys.readouterr()
    return code, out, err, seen


def _json(out: str) -> dict[str, Any]:
    lines = [line for line in out.splitlines() if line.strip()]
    assert len(lines) == 1, out  # stdout niesie dokładnie jeden obiekt JSON
    return json.loads(lines[0])


def test_a_successful_import_prints_one_json_line_with_the_release_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    outcome = ImportOutcome(
        status="succeeded", stats={"roads_imported": 33585, "buildings_imported": 90563}, warnings=("regression:roads:100->70",),
        import_run_id=7, data_release_id=21,
    )

    code, out, _err, seen = _run(monkeypatch, capsys, lambda _kwargs: outcome)

    payload = _json(out)
    assert code == 0 and payload["exit_code"] == 0
    assert payload["status"] == "succeeded" and payload["data_release_id"] == 21 and payload["import_run_id"] == 7
    assert payload["stats"]["roads_imported"] == 33585 and payload["warnings"] == ["regression:roads:100->70"]
    assert payload["timing"]["total_seconds"] >= 0
    assert seen == {"source_id": "bdot10k", "dry_run": False, "powiat": "1261", "package": "/pkg/1261_GML.zip"}


def test_dry_run_and_the_default_package_source_are_passed_through(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _err, seen = _run(
        monkeypatch, capsys, lambda _kwargs: ImportOutcome(status="dry_run_only", stats={}, warnings=()),
        argv=["bdot10k", "--powiat", "0221", "--dry-run"],
    )

    assert code == 0 and _json(out)["status"] == "dry_run_only"
    assert seen["dry_run"] is True and seen["package"] is None and seen["powiat"] == "0221"


@pytest.mark.parametrize(
    "error",
    [Bdot10kValidationError("brak OT_ADJA_A"), Bdot10kReadError("uszkodzony ZIP"), Bdot10kDownloadError("host spoza listy")],
)
def test_a_rejected_package_exits_one_with_a_named_error_and_no_traceback_on_stdout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], error: Exception
) -> None:
    def failing(_kwargs: dict[str, Any]) -> ImportOutcome:
        raise error

    code, out, _err, _seen = _run(monkeypatch, capsys, failing)

    payload = _json(out)
    assert code == 1 and payload["exit_code"] == 1 and payload["status"] == "rejected"
    assert payload["error"].startswith(type(error).__name__) and payload["stats"] == {}


def test_an_unexpected_exception_still_ends_with_json_and_a_non_zero_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def failing(_kwargs: dict[str, Any]) -> ImportOutcome:
        raise RuntimeError("baza niedostępna")

    code, out, err, _seen = _run(monkeypatch, capsys, failing)

    payload = _json(out)
    assert code == 1 and payload["status"] == "failed" and "RuntimeError: baza niedostępna" in payload["error"]
    assert "Traceback" in err and "Traceback" not in out  # ślad idzie na stderr, stdout zostaje parsowalny
