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


# --- BK-601: badanie poprawności ------------------------------------------


def _case(tolerance_share: float = 0.1) -> dict:
    return {
        "case_id": "control-1",
        "tolerances": {
            "geometry_area_abs": {"unit": "square_metre", "value": 0.05},
            "intersection_area_abs": {"unit": "square_metre", "value": 1.0},
            "share_abs": {"unit": "percentage_point", "value": tolerance_share},
            "elevation_abs": {"unit": "metre", "value": 0.1},
        },
    }


def _rows_by_field(rows: list[dict]) -> dict[str, dict]:
    return {f"{row['section']}.{row['field']}": row for row in rows}


def test_field_comparison_hand_calculated_verdicts() -> None:
    expected = {
        "geometry": {"values": {"area": 100.0, "perimeter": 40.0}},
        "pog": {
            "values": {
                "legal_status": "legal_force",
                "zone_count": 2,
                "zones": [
                    {"symbol": "A", "share": 60.0, "area": 60.0},
                    {"symbol": "B", "share": 40.0, "area": 40.0},
                ],
            }
        },
        "ouz": {"values": {"intersection_area": 0.158, "relation": "boundary", "share": 0.0}},
        "mpzp": {"values": {"mode": "raster_manual", "zone_count": None, "zones": []}},
        "flood": {
            "values": {
                "feature_count": 1,
                "relation": "intersection",
                "boundary_feature_area": 0.0,
                "features": [{"class": "Q1", "area": 50.0, "share": 50.0, "severity": "high"}],
            }
        },
        "nature": {
            "values": {
                "feature_count": 1,
                "relation": "intersection",
                "features": [{"name": "Puszcza", "area": 10.0, "share": 10.0}],
            }
        },
        "terrain": {"values": {"minimum": 10.0, "maximum": 12.0, "relief": 2.0, "class": "flat"}},
    }
    actual = {
        "geometry": {"values": {"area": 100.04, "perimeter": 40.5}},
        "pog": {
            "values": {
                "legal_status": "legal_force",
                "zone_count": 2,
                "zones": [
                    {"symbol": "A", "share": 60.6, "area": 60.0},
                    {"symbol": "C", "share": 39.4, "area": 40.0},
                ],
            }
        },
        "ouz": {"values": {"intersection_area": 0.158, "relation": "inside", "share": 0.0}},
        "mpzp": {"values": {"mode": "vector_discovery", "zone_count": 1, "zones": [{"symbol": "NULL"}]}},
        "flood": {
            "values": {
                "feature_count": 1,
                "relation": "intersection",
                "features": [{"class": "Q1", "area": 50.0, "share": 50.0, "severity": "high"}],
            }
        },
        "nature": {
            "values": {
                "feature_count": 1,
                "relation": "intersection",
                "features": [{"class": "Puszcza", "area": 10.0, "share": 10.0}],
            }
        },
        "terrain": {"values": {"minimum": 10.0, "maximum": 12.0, "relief": 2.0, "class": "moderate"}},
    }
    rows = evaluation.compare_case_fields(_case(), expected, actual, ["ouz.relation"])
    by = _rows_by_field(rows)

    assert by["geometry.area"]["verdict"] == "within_tolerance"
    assert by["geometry.area"]["difference"] == pytest.approx(0.04)
    assert by["geometry.perimeter"]["verdict"] == "mismatch"  # 0.5 m > 0.02 m
    assert by["pog.zone_count"]["verdict"] == "match"
    assert by["pog.zones.symbols"]["verdict"] == "mismatch"
    assert by["pog.zones[A].share"]["verdict"] == "mismatch"  # 0.6 pp > 0.1 pp
    assert by["pog.zones[A].share"]["difference"] == pytest.approx(0.6)
    assert by["pog.zones[B].share"]["verdict"] == "missing_actual"
    assert by["pog.zones[C].presence"]["verdict"] == "unexpected_actual"
    # `ambiguous` wyłącza tylko wskazaną ścieżkę, a wartość pozostaje zapisana.
    assert by["ouz.relation"]["verdict"] == "ambiguous_excluded"
    assert by["ouz.relation"]["raw_verdict"] == "mismatch"
    assert by["ouz.intersection_area"]["verdict"] == "match"
    # null oczekiwane + wartość po stronie wyniku = fałszywa pewność, nie zero.
    assert by["mpzp.mode"]["verdict"] == "mismatch"
    assert by["mpzp.zone_count"]["verdict"] == "unexpected_actual"
    assert by["mpzp.zones.symbols"]["verdict"] == "unexpected_actual"
    # Zero oczekiwane, wynik nieokreślony: brak wartości nie jest zerem.
    assert by["flood.boundary_feature_area"]["verdict"] == "missing_actual"
    assert by["flood.features[Q1].area"]["verdict"] == "match"
    # Cecha przyrody dopasowana po `name` oczekiwanej i `class` wyniku.
    assert by["nature.features[Puszcza].area"]["verdict"] == "match"
    assert by["terrain.class"]["verdict"] == "mismatch"
    assert by["terrain.relief"]["verdict"] == "match"


def test_both_unknown_is_not_counted_as_accuracy() -> None:
    expected = {"pog": {"values": {"legal_status": "unknown", "zone_count": None, "zones": []}}}
    actual = {"pog": {"values": {"legal_status": None, "zone_count": None, "zones": []}}}
    rows = [
        row
        for row in evaluation.compare_case_fields(_case(), expected, actual, [])
        if row["section"] == "pog"
    ]
    assert {row["verdict"] for row in rows} == {"both_unknown"}
    metrics = evaluation.calculate_study_metrics(
        [{"status": "partial", "case_id": "control-1"}], rows, []
    )
    assert metrics["field_agreement"]["value"] is None
    assert metrics["field_agreement"]["reason"] == "no_eligible_cases"
    assert metrics["false_certainty_fields"]["numerator"] == 0
    assert metrics["false_certainty_fields"]["denominator"] == len(rows)


def test_study_metrics_share_threshold_and_denominators() -> None:
    rows = [
        {"case_id": "c", "section": "pog", "field": "zones[A].share", "kind": "numeric",
         "unit": "percentage_point", "expected": 50.0, "actual": 50.6, "difference": 0.6,
         "tolerance": 0.1, "verdict": "mismatch"},
        {"case_id": "c", "section": "pog", "field": "zones[B].share", "kind": "numeric",
         "unit": "percentage_point", "expected": 50.0, "actual": 50.3, "difference": 0.3,
         "tolerance": 0.1, "verdict": "mismatch"},
        {"case_id": "c", "section": "pog", "field": "zones[C].share", "kind": "numeric",
         "unit": "percentage_point", "expected": 10.0, "actual": 10.0, "difference": 0.0,
         "tolerance": 0.1, "verdict": "match"},
    ]
    status = [
        {"case_id": "c", "section": "pog", "expected_status": "available",
         "actual_status": "available", "agrees": True},
        {"case_id": "c", "section": "mpzp", "expected_status": "manual_review",
         "actual_status": "available", "agrees": False},
    ]
    metrics = evaluation.calculate_study_metrics(
        [{"status": "partial", "case_id": "c", "actual_sections": {"pog": {"status": "available"}}}],
        rows,
        status,
    )
    assert metrics["share_comparisons"]["value"] == 3
    assert metrics["share_differences_above_threshold"]["numerator"] == 1
    assert metrics["share_differences_above_threshold"]["denominator"] == 3
    assert metrics["share_max_absolute_error"]["value"] == pytest.approx(0.6)
    assert metrics["share_mean_absolute_error"]["value"] == pytest.approx(0.3)
    assert metrics["field_agreement"]["numerator"] == 1
    assert metrics["field_agreement"]["denominator"] == 3
    assert metrics["status_agreement"]["numerator"] == 1
    assert metrics["status_agreement"]["denominator"] == 2
    assert metrics["numeric_errors"]["pog.zones.share"]["tolerance_exceeded"] == 2
    assert metrics["case_identity_holds"]["value"] == 1


def _write_mini_corpus(root: Path, observation: dict, expected_share: float) -> dict:
    artifact = {"path": "e.json", "sha256": "0" * 64}
    (root / "e.json").write_text(json.dumps({"observations": observation}), encoding="utf-8")
    case = {
        "case_id": "control-1",
        "expected": {
            "pog": {
                "status": "available",
                "method": "kontrola ręczna",
                "values": {
                    "zones": [
                        {"symbol": {"unit": "code", "value": "A"},
                         "share": {"unit": "percent", "value": expected_share}}
                    ],
                },
            },
        },
        **_case(),
    }
    return {"artifacts": {"evidence:control-1": artifact}, "cases": [case]}


def test_ledger_records_share_above_half_pp_with_evidence(tmp_path: Path) -> None:
    manifest = _write_mini_corpus(tmp_path, {"pog_ouz": {"zones": []}}, 50.0)
    expected = {"pog": {"status": "available", "values": {"zones": [{"symbol": "A", "share": 50.0}]}}}
    actual = {"pog": {"status": "available", "values": {"zones": [{"symbol": "A", "share": 50.7}]}}}
    rows = [
        row
        for row in evaluation.compare_case_fields(manifest["cases"][0], expected, actual, [])
        if row["section"] == "pog"
    ]
    records = [{"case_id": "control-1", "status": "partial",
                "expected_sections": expected, "actual_sections": actual}]
    status_rows = evaluation.compare_section_status(records)
    ledger = evaluation.build_error_ledger(manifest, tmp_path, records, rows, status_rows)
    shares = [item for item in ledger if item["field"] == "zones[A].share"]
    assert len(shares) == 1
    entry = shares[0]
    assert entry["exceeds_0_5_pp"] is True
    assert entry["difference"] == pytest.approx(0.7)
    assert entry["category"] in evaluation.ERROR_CATEGORIES
    assert entry["root_cause"] == "RC-99"  # brak reguły: jawnie do ręcznej analizy
    assert entry["evidence"] and entry["evidence"][0]["type"] == "ground_truth"
    assert entry["entry_id"] == "E-001"


def test_ledger_keeps_failed_case_and_limitations(tmp_path: Path) -> None:
    manifest = _write_mini_corpus(tmp_path, {}, 50.0)
    records = [
        {"case_id": "control-1", "status": "failed", "error": "RuntimeError: boom",
         "expected_sections": {}, "actual_sections": {}},
    ]
    ledger = evaluation.build_error_ledger(manifest, tmp_path, records, [], [])
    assert [item["kind"] for item in ledger] == ["case_failure"]
    assert "boom" in ledger[0]["explanation"]
    assert ledger[0]["category"] in evaluation.ERROR_CATEGORIES


def test_run_manifest_is_frozen_and_independent_of_time(corpus_path: Path) -> None:
    manifest, digest = evaluation.load_corpus(corpus_path)
    first = evaluation.build_run_manifest(manifest, digest)
    second = evaluation.build_run_manifest(manifest, digest)
    assert first["manifest_sha256"] == second["manifest_sha256"]
    for key in (
        "commit_sha", "code_fingerprint", "corpus_sha256", "source_release_ids",
        "versions", "environment", "parameters", "corpus_artifact_hashes",
    ):
        assert first[key], key
    assert first["corpus_sha256"] == digest
    assert "missing" not in first["code_fingerprint"].values()
    assert first["parameters"]["discrepancy_threshold_pp"] == 0.5
    assert first["parameters"]["measurement_kinds"]["pog.*"]["kind"] == "observation_passthrough"
    assert first["versions"]["mpzp_parser"] == "mpzp-parser/2.0"
    assert not any(str(v).startswith("unavailable") for v in first["versions"].values())
    # Zmiana wejścia zmienia skrót manifestu.
    changed = evaluation.build_run_manifest(manifest, "f" * 64)
    assert changed["manifest_sha256"] != first["manifest_sha256"]


def test_timing_is_split_from_substantive_result(corpus_path: Path) -> None:
    manifest, _ = evaluation.load_corpus(corpus_path)
    records, metrics = evaluation.evaluate(manifest, corpus_path.parent)
    slow = [{**record, "duration_ms": record["duration_ms"] + 1000.0} for record in records]
    slow_metrics = {
        **metrics,
        "duration_p50": {**metrics["duration_p50"], "value": 1000.0},
        "duration_p95": {**metrics["duration_p95"], "value": 2000.0},
    }
    base = evaluation.split_timing(records, metrics)
    other = evaluation.split_timing(slow, slow_metrics)
    assert evaluation.canonical_json(base[:2]) == evaluation.canonical_json(other[:2])
    assert base[2]["per_case"] != other[2]["per_case"]
    assert "duration_p50" not in base[1]


def test_repeated_study_is_identical_and_covers_every_case(corpus_path: Path) -> None:
    manifest, digest = evaluation.load_corpus(corpus_path)
    study = evaluation.run_study(manifest, corpus_path.parent, digest, repeat=3)
    assert study["determinism"]["identical"] is True
    assert len(set(study["determinism"]["substantive_sha256"])) == 1
    payload = study["payload"]
    metrics = payload["study_metrics"]
    assert evaluation.study_invariants(metrics) == []
    assert metrics["input_cases"]["value"] == 30
    assert (
        metrics["complete_cases"]["value"]
        + metrics["partial_cases"]["value"]
        + metrics["failed_cases"]["value"]
        == 30
    )
    assert {row["case_id"] for row in payload["section_status"]} == {
        case["case_id"] for case in manifest["cases"]
    }
    assert len(payload["section_status"]) == 30 * len(evaluation.SECTION_ORDER)
    ids = [item["entry_id"] for item in payload["error_ledger"]]
    assert ids == sorted(ids) and len(ids) == len(set(ids))
    for item in payload["error_ledger"]:
        assert item["category"] in evaluation.ERROR_CATEGORIES
        assert item["evidence"], item["entry_id"]
        assert item["root_cause"] != "RC-99", item
    # Wynik jest pełny: nie ma przypadku usuniętego z mianownika.
    assert metrics["field_agreement"]["denominator"] > 0


def test_study_reports_known_defects_not_hidden_by_headline_metrics(corpus_path: Path) -> None:
    manifest, digest = evaluation.load_corpus(corpus_path)
    payload = evaluation.run_study(manifest, corpus_path.parent, digest, repeat=1)["payload"]
    ledger = payload["error_ledger"]
    null_cases = {
        item["case_id"] for item in ledger if item["root_cause"] == "RC-01"
    }
    assert len(null_cases) == 8 and all("-0" in case for case in null_cases)
    probe = next(
        evidence
        for item in ledger
        if item["root_cause"] == "RC-01"
        for evidence in item["evidence"]
        if evidence["type"] == "code_probe"
    )
    assert probe["result"]["zone_symbol_from_payload_NULL"] == {"html": "NULL", "json": "NULL"}
    # Nagłówkowa metryka klas stref = 1,0, a porównanie pól widzi rozbieżności.
    assert payload["metrics"]["zone_class_accuracy"]["value"] == 1.0
    assert payload["study_metrics"]["field_agreement"]["value"] < 1.0
    assert not any(item.get("exceeds_0_5_pp") for item in ledger)
    assert payload["study_metrics"]["share_differences_above_threshold"]["numerator"] == 0


def test_study_invariants_reject_small_or_inconsistent_runs() -> None:
    metrics = {
        "input_cases": {"value": 23},
        "case_identity_holds": {"value": 0},
    }
    failures = evaluation.study_invariants(metrics)
    assert failures == [
        "input_cases=23 < 24",
        "input_cases != complete + partial + failed",
    ]
    with pytest.raises(evaluation.EvaluationError):
        evaluation.run_study({}, Path("."), "0" * 64, repeat=0)


def test_study_cli_writes_all_artifacts_and_generated_analysis(
    tmp_path: Path, corpus_path: Path
) -> None:
    output = tmp_path / "accuracy"
    analysis = tmp_path / "error_analysis.md"
    exit_code = evaluation.main(
        [
            "--study",
            "--corpus", str(corpus_path),
            "--study-dir", str(output),
            "--error-analysis", str(analysis),
            "--repeat", "2",
        ]
    )
    assert exit_code == 0
    assert {path.name for path in output.iterdir()} == {
        "accuracy.svg", "confusion.csv", "determinism.json", "error_ledger.csv",
        "error_ledger.json", "evaluation.json", "field_results.csv", "report.md",
        "run_manifest.json", "section_results.csv", "timing.json",
    }
    evaluation_json = json.loads((output / "evaluation.json").read_text())
    ledger = json.loads((output / "error_ledger.json").read_text())
    manifest = json.loads((output / "run_manifest.json").read_text())
    assert evaluation_json["manifest_sha256"] == manifest["manifest_sha256"] == ledger["manifest_sha256"]
    determinism = json.loads((output / "determinism.json").read_text())
    assert determinism["identical"] is True
    timing = json.loads((output / "timing.json").read_text())
    assert len(timing["per_case"]) == 30
    with (output / "error_ledger.csv").open(newline="", encoding="utf-8") as handle:
        assert len(list(csv.DictReader(handle))) == len(ledger["entries"])
    with (output / "field_results.csv").open(newline="", encoding="utf-8") as handle:
        assert len(list(csv.DictReader(handle))) == len(evaluation_json["field_results"])
    text = analysis.read_text()
    # Dokument jest generowany z rejestru: każdy wpis ma swój nagłówek.
    for entry in ledger["entries"]:
        assert f"### {entry['entry_id']} " in text
    assert evaluation_json["substantive_sha256"] in text
    assert "<svg" in (output / "accuracy.svg").read_text()
    report = (output / "report.md").read_text()
    assert "Zamrożony manifest biegu" in report and "Determinizm i czas" in report


def test_study_cli_fails_on_nondeterminism_and_bad_repeat(
    tmp_path: Path, corpus_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = ["--study", "--corpus", str(corpus_path), "--study-dir", str(tmp_path / "s"),
            "--error-analysis", str(tmp_path / "e.md")]
    assert evaluation.main([*base, "--repeat", "0"]) == 1

    original = evaluation.run_study_once
    calls = {"count": 0}

    def unstable(manifest: Mapping, corpus_dir: Path) -> dict:
        calls["count"] += 1
        result = original(manifest, corpus_dir)
        if calls["count"] == 2:
            result["substantive_sha256"] = "0" * 64
        return result

    monkeypatch.setattr(evaluation, "run_study_once", unstable)
    assert evaluation.main([*base, "--repeat", "2"]) == 1


def test_study_runs_offline_and_is_reproducible_across_processes(
    tmp_path: Path, corpus_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket
    import subprocess
    import sys

    def blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network access is not allowed in the offline study")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    manifest, digest = evaluation.load_corpus(corpus_path)
    in_process = evaluation.run_study(manifest, corpus_path.parent, digest, repeat=1)["substantive_sha256"]
    monkeypatch.undo()

    script = Path(evaluation.__file__)
    hashes = []
    for run in ("a", "b"):
        output = tmp_path / run
        completed = subprocess.run(
            [sys.executable, str(script), "--study", "--corpus", str(corpus_path),
             "--study-dir", str(output), "--error-analysis", str(output / "error_analysis.md"), "--repeat", "1"],
            capture_output=True, text=True, check=False,
        )
        assert completed.returncode == 0, completed.stderr
        hashes.append(json.loads((output / "evaluation.json").read_text())["substantive_sha256"])
    assert hashes[0] == hashes[1] == in_process
