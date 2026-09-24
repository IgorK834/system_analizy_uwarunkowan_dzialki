"""End-to-end and hand-calculated checks for the BK-004 harness."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Mapping

import pytest

from scripts import evaluate_reference_corpus as evaluation
from tests.parcel_fixtures_config import find_repo_root


@pytest.fixture(scope="module")
def corpus_path() -> Path:
    return (
        find_repo_root()
        / "backend/tests/fixtures/reference_corpus/manifest.json"
    )


def _section(
    *,
    status: str = "available",
    relation: str = "none",
    zone_symbol: str = "A",
    zone_share: float = 10.0,
) -> dict:
    return {
        "status": status,
        "values": {
            "area": 100.0,
            "perimeter": 40.0,
            "legal_status": "legal_force",
            "zone_count": 1,
            "zones": [{"symbol": zone_symbol, "share": zone_share}],
            "intersection_area": 0.0,
            "relation": relation,
            "share": 0.0,
            "mode": "vector",
            "plan_id": "PLAN-1",
            "feature_count": 0 if relation == "none" else 1,
            "minimum": 100.0,
            "maximum": 101.0,
            "relief": 1.0,
            "class": "flat",
        },
    }


def _control_record(
    index: int,
    expected_positive: bool,
    actual_positive: bool,
    expected_share: float,
    actual_share: float,
) -> dict:
    expected_relation = "intersection" if expected_positive else "none"
    actual_relation = "intersection" if actual_positive else "none"
    expected = {
        name: _section(
            relation=(
                "partial"
                if name == "ouz" and expected_positive
                else "outside"
                if name == "ouz"
                else expected_relation
            ),
            zone_share=expected_share,
        )
        for name in ("geometry", "pog", "ouz", "mpzp", "flood", "nature", "terrain")
    }
    actual = {
        name: _section(
            relation=(
                "partial"
                if name == "ouz" and actual_positive
                else "outside"
                if name == "ouz"
                else actual_relation
            ),
            zone_share=actual_share,
        )
        for name in ("geometry", "pog", "ouz", "mpzp", "flood", "nature", "terrain")
    }
    return {
        "case_id": f"control-{index}",
        "status": "complete",
        "error": None,
        "duration_ms": index * 10.0,
        "cache_status": "hit" if index % 2 == 0 else "miss",
        "has_unknown": False,
        "manual_review_required": False,
        "parcel_identification_correct": True,
        "ambiguous_metrics": [],
        "expected_sections": expected,
        "actual_sections": actual,
    }


def test_hand_calculated_confusion_mae_and_percentiles() -> None:
    records = [
        _control_record(1, True, True, 10.0, 12.0),
        _control_record(2, True, False, 20.0, 16.0),
        _control_record(3, False, True, 30.0, 30.0),
        _control_record(4, False, False, 40.0, 40.0),
    ]
    metrics = evaluation.calculate_metrics(records)

    for condition in ("flood_intersection", "nature_intersection", "ouz_presence"):
        assert {
            key: metrics["binary_conditions"][condition][key]
            for key in ("tp", "fp", "fn", "tn")
        } == {"tp": 1, "fp": 1, "fn": 1, "tn": 1}
    assert metrics["binary_conditions"]["aggregate"]["tp"] == 3
    assert metrics["binary_conditions"]["aggregate"]["fp"] == 3
    assert metrics["binary_conditions"]["aggregate"]["fn"] == 3
    assert metrics["binary_conditions"]["aggregate"]["tn"] == 3
    # Shares are assessed for both POG and MPZP: [2, 2, 4, 4, 0, 0, 0, 0].
    assert metrics["zone_share_mae"]["value"] == pytest.approx(1.5)
    assert metrics["zone_share_max_absolute_error"]["value"] == 4.0
    assert metrics["duration_p50"]["value"] == 25.0
    assert metrics["duration_p95"]["value"] == pytest.approx(38.5)


def test_zero_division_is_null_with_reason() -> None:
    record = {
        "status": "failed",
        "duration_ms": 1.0,
        "cache_status": "miss",
        "has_unknown": True,
        "manual_review_required": False,
        "parcel_identification_correct": False,
        "ambiguous_metrics": [],
        "expected_sections": {},
        "actual_sections": {},
    }
    metrics = evaluation.calculate_metrics([record])
    assert metrics["zone_class_accuracy"]["value"] is None
    assert metrics["zone_class_accuracy"]["reason"] == "no_eligible_cases"
    assert metrics["binary_conditions"]["aggregate"]["precision"]["value"] is None
    assert metrics["binary_conditions"]["aggregate"]["precision"]["reason"] == "no_eligible_cases"


def test_percentile_empty_and_single_value() -> None:
    assert evaluation.percentile([], 0.95) is None
    assert evaluation.percentile([7.0], 0.95) == 7.0


def test_runner_input_never_contains_expected(corpus_path: Path) -> None:
    manifest, _ = evaluation.load_corpus(corpus_path)

    class SpyRunner:
        def __init__(self) -> None:
            self.inputs: list[Mapping[str, object]] = []

        def analyze(self, case_input: Mapping[str, object]) -> Mapping[str, object]:
            self.inputs.append(case_input)
            assert "expected" not in case_input
            assert "expected_sections" not in case_input
            return {
                "parcel_identifier": case_input["parcel_identifier"],
                "status": "partial",
                "duration_ms": 1.0,
                "cache_status": "miss",
                "manual_review_required": False,
                "sections": {},
            }

    runner = SpyRunner()
    records, metrics = evaluation.evaluate(manifest, corpus_path.parent, runner)
    assert len(runner.inputs) == len(records) == 30
    assert metrics["case_count"]["value"] == 30


def test_single_case_failure_is_retained_and_counted(corpus_path: Path) -> None:
    manifest, _ = evaluation.load_corpus(corpus_path)

    class FailingRunner:
        calls = 0

        def analyze(self, case_input: Mapping[str, object]) -> Mapping[str, object]:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("controlled failure")
            return {
                "parcel_identifier": case_input["parcel_identifier"],
                "status": "partial",
                "duration_ms": 1.0,
                "cache_status": "hit",
                "manual_review_required": False,
                "sections": {},
            }

    records, metrics = evaluation.evaluate(manifest, corpus_path.parent, FailingRunner())
    assert len(records) == 30
    assert records[0]["status"] == "failed"
    assert "controlled failure" in records[0]["error"]
    assert metrics["failed_cases"]["value"] == 1


def test_ambiguous_metric_keeps_case_in_completeness_denominator() -> None:
    record = _control_record(1, True, True, 10.0, 10.0)
    record["ambiguous_metrics"] = ["ouz.relation"]
    metrics = evaluation.calculate_metrics([record])
    assert metrics["field_completeness"]["denominator"] == len(
        evaluation.COMPLETENESS_PATHS
    )
    assert metrics["field_completeness"]["numerator"] == len(
        evaluation.COMPLETENESS_PATHS
    )
    assert metrics["binary_conditions"]["ouz_presence"]["tp"] == 0


def test_one_command_writes_consistent_json_csv_markdown_and_svg(
    tmp_path: Path, corpus_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    timestamps = iter(("2026-09-23T10:00:00Z", "2026-09-23T10:00:01Z"))
    monkeypatch.setattr(evaluation, "_timestamp", lambda: next(timestamps))
    monkeypatch.setattr(evaluation, "_git_commit_sha", lambda _root: "abc123")
    output = tmp_path / "results"

    exit_code = evaluation.main(
        [
            "--corpus",
            str(corpus_path),
            "--output-dir",
            str(output),
            "--mode",
            "offline",
            "--fail-on-regression",
        ]
    )
    assert exit_code == 0
    assert {path.name for path in output.iterdir()} == {
        "evaluation.json",
        "cases.csv",
        "metrics.csv",
        "report.md",
        "metrics.svg",
    }

    report = json.loads((output / "evaluation.json").read_text())
    with (output / "cases.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    markdown = (output / "report.md").read_text()
    assert report["metadata"]["commit_sha"] == rows[0]["commit_sha"] == "abc123"
    assert report["metadata"]["corpus_sha256"] == rows[0]["corpus_sha256"]
    assert report["metadata"]["schema_version"] in markdown
    assert len(report["cases"]) == len(rows) == 30
    assert report["regressions"] == []


def test_invalid_corpus_and_regression_return_exit_code_one(
    tmp_path: Path, corpus_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{}", encoding="utf-8")
    assert evaluation.main(["--corpus", str(invalid), "--output-dir", str(tmp_path / "bad")]) == 1

    manifest, digest = evaluation.load_corpus(corpus_path)
    records, metrics = evaluation.evaluate(manifest, corpus_path.parent)
    metrics["parcel_identification_accuracy"]["value"] = 0.5
    failures = evaluation._threshold_failures(
        metrics, {"min_parcel_identification_accuracy": 1.0}
    )
    assert failures == ["parcel_identification_accuracy=0.5 < 1.0"]
    metadata = evaluation.build_metadata(
        manifest, digest, "2026-09-23T10:00:00Z", "2026-09-23T10:00:01Z"
    )
    monkeypatch.setattr(evaluation, "evaluate", lambda *_args, **_kwargs: (records, metrics))
    monkeypatch.setattr(evaluation, "build_metadata", lambda *_args, **_kwargs: metadata)
    assert evaluation.main(
        [
            "--corpus",
            str(corpus_path),
            "--output-dir",
            str(tmp_path / "regression"),
            "--fail-on-regression",
        ]
    ) == 1

    metrics["failed_cases"]["value"] = 1
    assert evaluation.main(
        [
            "--corpus",
            str(corpus_path),
            "--output-dir",
            str(tmp_path / "failed-case"),
        ]
    ) == 1
