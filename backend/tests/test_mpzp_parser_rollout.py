"""Przełączenie domyślnego trybu parsera MPZP tylko po udokumentowanej decyzji (PV3-21).

Offline, bez klucza i bez sieci. Zapis przesłanek (``app/core/mpzp_parser_rollout.json``) jest śledzony w
repozytorium, więc CI pilnuje, że domyślny tryb w ustawieniach jest równy zapisowi, a odejście od ``legacy``
wymaga bramki ``GO`` (Task 20.17), potwierdzonych progów okresu cienia, raportu cienia bez regresji, kontroli
dryfu w oknie, przypięcia z ewaluacją ``--live`` i decyzji właściciela.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from app.core.settings import Settings
from scripts import check_parser_default_switch as switch
from scripts import mpzp_shadow_report as shadow_report

PIN_MODEL, PIN_PROMPT = "gemini-3.8-flash", "mpzp-extraction/1"


def _line(ts: str, event: str, **fields: object) -> str:
    rendered = " ".join(f"{k}={v}" for k, v in sorted(fields.items()))
    return f"backend-1  | {ts},123 INFO app.mpzp_llm mpzp_llm event={event} {rendered}"


def _ready_record() -> dict[str, Any]:
    record = switch.load_record()
    record = copy.deepcopy(record)
    record["gate"] = {"decision": "GO", "evaluated_at": "2026-11-01T10:00:00Z",
                      "report": "docs/evaluation/results/parser-v3/gate_report.json", "report_sha256": "a" * 64}
    record["shadow_requirements"].update({"status": "confirmed", "confirmed_by_owner_on": "2026-11-02"})
    record["shadow_observation"] = {
        "report": "docs/evaluation/results/parser-v3/shadow_observation.json", "report_sha256": "b" * 64,
        "window": {"start": "2026-11-03T00:00:00Z", "end": "2026-11-20T00:00:00Z", "days": 17.0},
        "shadow": {"runs": 240, "errors": 1, "agree": 900, "disagree": 20, "llm_only": 60, "det_only": 300,
                   "disagree_rate": 0.021739, "error_rate": 0.004149},
        "pipeline": {"runs": 241, "degraded": 4, "degradation_rate": 0.016598, "accepted": 980, "rejected": 70,
                     "rejection_rate": 0.066667, "unavailable": {"timeout": 4}},
        "drift": {"status": "ok", "checked_at": "2026-11-15T08:00:00Z", "drift_rate": 0.0, "threshold": 0.2,
                  "model_id": PIN_MODEL, "prompt_version": PIN_PROMPT},
    }
    record["owner_decision"] = {"decision": "GO", "date": "2026-11-21", "reference": "ADR-012, aneks PV3-21 pkt 3"}
    return record


def _assess(record: dict[str, Any], **overrides: Any) -> switch.Assessment:
    options: dict[str, Any] = {"settings_default": "legacy", "pin_problems": (), "pin_model": PIN_MODEL,
                               "pin_prompt": PIN_PROMPT}
    options.update(overrides)
    return switch.assess(record, **options)


# --- stan repozytorium --------------------------------------------------------------------------


def test_the_repository_record_matches_the_settings_default_and_forbids_a_switch_today() -> None:
    record = switch.load_record()
    assert record["default_mode"] == switch.settings_default_mode() == Settings.model_fields["mpzp_parser_mode"].default
    assert record["rollback_mode"] in switch.DETERMINISTIC_MODES
    result = switch.evaluate(switch.check_llm_pin.default_repo_root(), verify_files=False)
    # Stan 2026-10-05: bramka NOT_DECIDABLE, brak okresu cienia i decyzji — przełączenie niedozwolone.
    assert result.violations == ()
    if record["default_mode"] != "legacy":
        assert result.status == "READY", f"tryb przełączony bez przesłanek: {result.missing}"
    else:
        assert result.status == "NOT_READY"
        assert {switch.GATE_NOT_GO, switch.SHADOW_MISSING, switch.OWNER_DECISION_MISSING,
                switch.SHADOW_REQUIREMENTS_UNCONFIRMED} <= set(result.missing)


def test_the_record_keeps_the_gate_decision_and_report_hash_from_task_20_17() -> None:
    gate = switch.load_record()["gate"]
    assert gate["report"] == "docs/evaluation/results/parser-v3/gate_report.json"
    assert len(gate["report_sha256"]) == 64 and gate["decision"] in ("GO", "NO_GO", "NOT_DECIDABLE")


def test_cli_check_exits_3_while_not_ready(capsys: pytest.CaptureFixture[str]) -> None:
    assert switch.main(["check"]) == 3
    assert "NOT_READY" in capsys.readouterr().out


# --- ocena przesłanek ---------------------------------------------------------------------------


def test_all_preconditions_met_is_ready_and_allows_the_switch() -> None:
    record = _ready_record()
    assert _assess(record) == switch.Assessment("READY", (), (), "legacy")
    switched = dict(record, default_mode="hybrid")
    assert _assess(switched, settings_default="hybrid").exit_code == 0


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda r: r["gate"].update(decision="NOT_DECIDABLE"), switch.GATE_NOT_GO),
        (lambda r: r["gate"].update(decision="NO_GO"), switch.GATE_NOT_GO),
        (lambda r: r["shadow_requirements"].update(status="proposed"), switch.SHADOW_REQUIREMENTS_UNCONFIRMED),
        (lambda r: r.update(shadow_observation=None), switch.SHADOW_MISSING),
        (lambda r: r["shadow_observation"]["window"].update(days=13.9), switch.SHADOW_TOO_SHORT),
        (lambda r: r["shadow_observation"]["shadow"].update(runs=199), switch.SHADOW_TOO_FEW_RUNS),
        (lambda r: r["shadow_observation"]["shadow"].update(disagree_rate=0.051), switch.SHADOW_DISAGREEMENT),
        (lambda r: r["shadow_observation"]["shadow"].update(disagree_rate=None), switch.SHADOW_DISAGREEMENT),
        (lambda r: r["shadow_observation"]["shadow"].update(error_rate=0.06), switch.SHADOW_ERRORS),
        (lambda r: r["shadow_observation"]["pipeline"].update(degradation_rate=0.21), switch.SHADOW_DEGRADATION),
        (lambda r: r["shadow_observation"]["pipeline"].update(rejection_rate=0.31), switch.SHADOW_REJECTIONS),
        (lambda r: r["shadow_observation"].update(drift=None), switch.DRIFT_NOT_CHECKED),
        (lambda r: r["shadow_observation"]["drift"].update(checked_at="2026-10-01T00:00:00Z"), switch.DRIFT_NOT_CHECKED),
        (lambda r: r["shadow_observation"]["drift"].update(status="alarm"), switch.DRIFT_NOT_OK),
        (lambda r: r["shadow_observation"]["drift"].update(model_id="inny-model"), switch.DRIFT_NOT_OK),
        (lambda r: r.update(owner_decision=None), switch.OWNER_DECISION_MISSING),
        (lambda r: r["owner_decision"].update(reference=""), switch.OWNER_DECISION_MISSING),
        (lambda r: r["owner_decision"].update(date="2026-11-10"), switch.OWNER_DECISION_TOO_EARLY),
    ],
)
def test_each_missing_precondition_blocks_the_switch(mutate: Any, code: str) -> None:
    record = _ready_record()
    mutate(record)
    result = _assess(record)
    assert result.status == "NOT_READY" and code in result.missing and result.exit_code == 3
    # Ten sam brak przy przełączonym trybie to naruszenie (CI nie przepuści takiej zmiany).
    switched = _assess(dict(record, default_mode="hybrid"), settings_default="hybrid")
    assert switch.SWITCHED_WITHOUT_DECISION in switched.violations and switched.exit_code == 1


def test_pin_without_live_evaluation_blocks_the_switch() -> None:
    result = _assess(_ready_record(), pin_problems=("evaluation_not_recorded",))
    assert result.missing == (switch.PIN_NOT_READY,)


def test_changed_report_files_are_detected_when_verified() -> None:
    record = _ready_record()
    gate_ref, shadow_ref = record["gate"]["report"], record["shadow_observation"]["report"]
    result = _assess(record, file_hashes={gate_ref: "c" * 64, shadow_ref: "b" * 64})
    assert result.missing == (switch.GATE_REPORT_CHANGED,)
    assert _assess(record, file_hashes={gate_ref: "a" * 64, shadow_ref: None}).missing == (switch.SHADOW_REPORT_CHANGED,)


@pytest.mark.parametrize(
    ("changes", "settings_default", "code"),
    [
        ({}, "v3", switch.DEFAULT_MODE_DIFFERS),
        ({"rollback_mode": "hybrid_shadow"}, "legacy", switch.ROLLBACK_NOT_DETERMINISTIC),
        ({"target_mode": "llm_only"}, "legacy", switch.UNKNOWN_MODE),
    ],
)
def test_inconsistent_records_are_violations(changes: dict[str, str], settings_default: str, code: str) -> None:
    result = _assess(dict(_ready_record(), **changes), settings_default=settings_default)
    assert code in result.violations and result.exit_code == 1


def test_switching_to_v3_is_also_a_switch() -> None:
    record = dict(switch.load_record(), default_mode="v3")
    result = _assess(record, settings_default="v3")
    assert switch.SWITCHED_WITHOUT_DECISION in result.violations


# --- zapis raportów do rekordu ------------------------------------------------------------------


def test_record_gate_and_shadow_store_decision_window_and_hashes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    reports = repo / "docs" / "evaluation" / "results" / "parser-v3"
    reports.mkdir(parents=True)
    (reports / "gate_report.json").write_text(json.dumps({"decision": "GO", "evaluated_at": "2026-11-01T10:00:00Z"}))
    observation = _ready_record()["shadow_observation"]
    (reports / "shadow_observation.json").write_text(json.dumps({"schema": switch.SHADOW_SCHEMA, **observation}))
    path = tmp_path / "rollout.json"
    path.write_text(switch.ROLLOUT_PATH.read_text(encoding="utf-8"), encoding="utf-8")

    assert switch.main(["record-gate", "--report", str(reports / "gate_report.json")], repo_root=repo, path=path) == 0
    assert switch.main(["record-shadow", "--report", str(reports / "shadow_observation.json")], repo_root=repo, path=path) == 0
    record = switch.load_record(path)
    assert record["gate"]["decision"] == "GO"
    assert record["gate"]["report_sha256"] == switch.sha256_file(reports / "gate_report.json")
    assert record["shadow_observation"]["window"] == observation["window"]
    assert record["shadow_observation"]["report"] == "docs/evaluation/results/parser-v3/shadow_observation.json"
    # Raport spoza repozytorium albo bez schematu nie jest przyjmowany.
    outside = tmp_path / "gate.json"
    outside.write_text(json.dumps({"decision": "GO"}))
    assert switch.main(["record-gate", "--report", str(outside)], repo_root=repo, path=path) == 2
    (reports / "bad.json").write_text("{}")
    assert switch.main(["record-shadow", "--report", str(reports / "bad.json")], repo_root=repo, path=path) == 2


def test_an_unreadable_record_is_a_usage_error(tmp_path: Path) -> None:
    path = tmp_path / "rollout.json"
    path.write_text('{"schema": "inny"}')
    assert switch.main(["check"], path=path) == 2
    path.write_text("nie json")
    with pytest.raises(switch.RolloutError):
        switch.load_record(path)


# --- raport okresu cienia z logów ---------------------------------------------------------------


def test_shadow_report_aggregates_log_events_into_rates_and_a_window(tmp_path: Path) -> None:
    lines = [
        _line("2026-11-03 08:00:00", "shadow_compare", agree=3, disagree=1, llm_only=1, det_only=2, calls=1,
              document="abc", unavailable="none"),
        _line("2026-11-03 08:00:01", "pipeline", accepted=4, rejected=1, unavailable="none", blocks=2, calls=1),
        "2026-11-04 09:00:00,001 INFO app.api request served",  # inne wiersze logu są pomijane
        _line("2026-11-10 12:00:00", "pipeline", accepted=0, rejected=0, unavailable="timeout,budget", calls=1),
        "2026-11-10 12:00:02,000 WARNING app.services.mpzp_parser_hybrid mpzp_llm shadow failed: TimeoutError",
        _line("2026-11-17 08:00:00", "shadow_compare", agree=5, disagree=0, llm_only=0, det_only=1, calls=2),
        _line("2026-11-17 08:00:00", "hybrid_merge", added=1),
    ]
    log = tmp_path / "shadow.log"
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    drift = tmp_path / "drift.json"
    drift.write_text(json.dumps({"schema": "mpzp-llm-drift-state/1", "status": "ok", "checked_at": "2026-11-15T08:00:00Z",
                                 "drift_rate": 0.0, "threshold": 0.2, "model_id": PIN_MODEL, "prompt_version": PIN_PROMPT}))
    output = tmp_path / "out" / "shadow_observation.json"
    assert shadow_report.main(["--log", str(log), "--drift-state", str(drift), "--output", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["schema"] == switch.SHADOW_SCHEMA
    assert report["window"] == {"start": "2026-11-03T08:00:00Z", "end": "2026-11-17T08:00:00Z", "days": 14.0}
    assert report["shadow"] == {"runs": 2, "errors": 1, "agree": 8, "disagree": 1, "llm_only": 1, "det_only": 3,
                                "disagree_rate": round(1 / 9, 6), "error_rate": round(1 / 3, 6)}
    assert report["pipeline"]["degraded"] == 1 and report["pipeline"]["unavailable"] == {"budget": 1, "timeout": 1}
    assert report["pipeline"]["rejection_rate"] == 0.2 and report["pipeline"]["degradation_rate"] == 0.5
    assert report["sources"][0]["file"] == "shadow.log" and len(report["sources"][0]["sha256"]) == 64
    assert report["drift"] == {"status": "ok", "checked_at": "2026-11-15T08:00:00Z", "drift_rate": 0.0, "threshold": 0.2,
                               "model_id": PIN_MODEL, "prompt_version": PIN_PROMPT}
    # Raport przyjęty do rekordu z ustawionymi progami daje ocenę opartą na tych liczbach (tu: za mało porównań).
    record = _ready_record()
    record["shadow_observation"] = {"report": "x", "report_sha256": "d" * 64,
                                    **{key: report[key] for key in ("window", "shadow", "pipeline", "drift")}}
    assert switch.SHADOW_TOO_FEW_RUNS in _assess(record).missing
    # Bez stanu dryfu raport nie udaje kontroli: ``drift`` = null (ocena zgłosi brak kontroli w oknie).
    assert shadow_report.build_report(shadow_report.aggregate(lines), [], None)["drift"] is None


def test_shadow_report_without_shadow_events_is_an_error(tmp_path: Path) -> None:
    log = tmp_path / "empty.log"
    log.write_text("2026-11-03 08:00:00,000 INFO app.api nothing\n", encoding="utf-8")
    assert shadow_report.main(["--log", str(log), "--output", str(tmp_path / "x.json")]) == 2
    assert shadow_report.main(["--log", str(tmp_path / "missing.log"), "--output", str(tmp_path / "x.json")]) == 2
    assert shadow_report.build_report(shadow_report.ShadowTotals(), [], None)["shadow"]["disagree_rate"] is None
