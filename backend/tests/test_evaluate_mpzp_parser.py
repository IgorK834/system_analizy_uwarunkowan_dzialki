"""Checks for the MPZP parser evaluation harness (BK-603, PV3-03).

The control sets have hand-calculated metrics (see ``control.json``); the real
corpus checks assert structural guarantees (frozen annotations, evidence, offline
determinism, source checks) rather than pinning parser accuracy.
"""

from __future__ import annotations

import copy
import csv
import json
from pathlib import Path
from typing import Any

import pytest

from app.schemas.mpzp import MpzpParameter, MpzpParseResult, MpzpZoneResult
from scripts import evaluate_mpzp_parser as ev
from tests.parcel_fixtures_config import find_repo_root

FIXTURES = find_repo_root() / "backend/tests/fixtures/mpzp_evaluation"
MANIFEST_PATH = FIXTURES / "manifest.json"


@pytest.fixture(scope="module")
def manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def control() -> dict[str, Any]:
    return json.loads((FIXTURES / "control.json").read_text(encoding="utf-8"))


def _parse_result(control: dict[str, Any]) -> MpzpParseResult:
    zones = []
    for symbol, outputs in control["parser_output"].items():
        zones.append(
            MpzpZoneResult(
                zone_symbol=symbol,
                parameters=[
                    MpzpParameter(
                        name=item["name"],
                        normalized_value=item["value"],
                        confidence=item["confidence"],
                        manual_review_required=item["manual_review_required"],
                        page_number=1,
                        source_text="fragment",
                    )
                    for item in outputs
                ],
            )
        )
    return MpzpParseResult(status="complete", zones=zones)


def _control_rows(control: dict[str, Any]) -> list[dict[str, Any]]:
    return ev.score_sample(
        control["sample"],
        {"document_sha256": "0" * 64, "url": "https://example.invalid/control"},
        _parse_result(control),
        [1],
        "mpzp-parser/test",
    )


# --- control set with planted FP, FN and zone mix-up ---------------------------


def test_control_set_verdicts_match_hand_calculation(control: dict[str, Any]) -> None:
    rows = _control_rows(control)
    assert len(rows) == 18
    by_key = {(row["zone_symbol"], row["parameter"]): row["verdict"] for row in rows}
    assert by_key[("A", "min_biologically_active_percent")] == "fn"
    assert by_key[("A", "setback_m")] == "fp"
    assert by_key[("A", "max_storeys")] == "tp_wrong"
    assert by_key[("B", "max_building_height_m")] == "tp_wrong"
    assert by_key[("B", "max_intensity")] == "tp_partial"
    assert by_key[("A", "max_building_height_m")] == "tp_exact"
    counts = ev.detection_metrics(rows)["counts"]
    assert {key: counts[key] for key in control["expected"]["counts"]} == control["expected"]["counts"]


def test_control_set_detection_and_assignment_metrics(control: dict[str, Any]) -> None:
    expected = control["expected"]
    metrics = ev.detection_metrics(_control_rows(control))
    for name, key in (
        ("precision", "precision"),
        ("recall", "recall"),
        ("exact_value_accuracy_among_found", "exact_among_found"),
        ("exact_value_accuracy_end_to_end", "exact_end_to_end"),
        ("zone_assignment_accuracy", "zone_assignment"),
    ):
        numerator, denominator = expected[key]
        assert metrics[name]["numerator"] == numerator
        assert metrics[name]["denominator"] == denominator
        assert metrics[name]["value"] == pytest.approx(numerator / denominator)
    assert metrics["cross_zone_errors"] == expected["cross_zone_errors"]
    assert metrics["f1"] == pytest.approx(8 / 9)


def test_control_set_calibration_matches_hand_calculation(control: dict[str, Any]) -> None:
    expected = control["expected"]["calibration"]
    calibration = ev.calibration_metrics(ev.value_rows(_control_rows(control)))
    assert calibration["n_values"] == expected["n_values"]
    for band, (n, errors) in expected["bands"].items():
        assert calibration["by_band"][band]["n"] == n
        assert calibration["by_band"][band]["errors"] == errors
    assert calibration["brier_score"] == pytest.approx(expected["brier"])
    assert calibration["expected_calibration_error"] == pytest.approx(expected["ece"])
    comparison = calibration["low_vs_high"]
    assert comparison["low_more_error_prone"] is True
    assert comparison["fisher_one_sided_p"] == pytest.approx(expected["fisher_p"])
    flag = calibration["manual_review_flag"]
    assert flag["flagged"] == expected["flagged"]
    assert flag["errors_flagged"]["numerator"] == expected["flagged_errors"]
    assert flag["error_recall_of_flag"]["numerator"] == expected["wrong_flagged"]
    assert flag["error_recall_of_flag"]["denominator"] == expected["wrong_total"]


def test_control_errors_are_traced_to_document_and_parser_version(control: dict[str, Any]) -> None:
    errors = ev.build_error_trace(_control_rows(control))
    kinds = {(item["zone_symbol"], item["parameter"]): item["error_type"] for item in errors}
    assert kinds[("B", "max_building_height_m")] == "zone_assignment"
    assert kinds[("A", "min_biologically_active_percent")] == "detection_fn"
    assert kinds[("A", "setback_m")] == "detection_fp"
    assert kinds[("A", "max_storeys")] == "value_error"
    categories = {(item["zone_symbol"], item["parameter"]): item["category"] for item in errors}
    assert categories[("A", "min_biologically_active_percent")] == "recognition"
    assert categories[("A", "max_storeys")] == "normalization_or_value"
    assert categories[("B", "max_building_height_m")] == "assignment"
    hints = {(item["zone_symbol"], item["parameter"]): item["cause_hint"] for item in errors}
    assert hints[("A", "setback_m")] == "value_without_annotation"
    assert hints[("A", "max_storeys")] == "value_from_other_context"
    assert hints[("B", "max_intensity")] == "value_from_other_context"
    assert hints[("A", "min_biologically_active_percent")] == "wording_not_matched"
    assert [item["error_id"] for item in errors] == [f"P-{n:03d}" for n in range(1, len(errors) + 1)]
    for item in errors:
        assert item["parser_version"] == "mpzp-parser/test"
        assert item["document_url"] == "https://example.invalid/control"
        assert item["document_sha256"] == "0" * 64
        assert item["sample_id"] == "CONTROL-1"


# --- classification edge cases -------------------------------------------------


@pytest.mark.parametrize(
    ("required", "acceptable", "general", "returned", "verdict"),
    [
        ([], [], [], [], "tn"),
        ([5.0], [], [], [], "fn"),
        ([5.0], [], [], [5.0], "tp_exact"),
        ([5.0], [6.0], [], [5.0, 6.0], "tp_exact"),
        ([5.0, 6.0], [], [], [5.0], "tp_partial"),
        ([5.0], [], [], [5.0, 9.0], "tp_partial"),
        ([5.0], [], [], [9.0], "tp_wrong"),
        ([], [], [], [9.0], "fp"),
        ([], [9.0], [], [9.0], "acceptable_only"),
        ([], [], [9.0], [], "general_missed"),
        ([], [], [9.0], [9.0], "general_found"),
        ([], [], [9.0], [1.0], "fp"),
    ],
)
def test_classify_verdicts(
    required: list[float], acceptable: list[float], general: list[float], returned: list[float], verdict: str
) -> None:
    assert ev.classify(required, acceptable, general, returned) == verdict


def test_cause_hint_uses_annotation_ambiguity() -> None:
    base = {"verdict": "fn", "format": "pdf_text", "returned_values": [], "required_values": [35.0],
            "acceptable_values": [], "general_values": []}
    implicit = {**base, "annotation_evidence": [{"ambiguity": {"kind": "implicit_percent", "note": "x"}}]}
    assert ev.cause_hint(implicit) == "implicit_percent"
    layered = {**base, "annotation_evidence": [{"ambiguity": {"kind": "conditional_value"}},
                                              {"ambiguity": {"kind": "number_word"}}]}
    assert ev.cause_hint(layered) == "number_word"  # priority order, not list order
    assert ev.cause_hint({**base, "annotation_evidence": [], "format": "ocr_simulated"}) == "ocr_noise_or_wording"
    partial = {**base, "verdict": "tp_partial", "annotation_evidence": [], "required_values": [5.0, 8.0],
               "returned_values": [5.0]}
    assert ev.cause_hint(partial) == "missing_conditional_value"


def test_statistics_helpers_hand_values() -> None:
    interval = ev.wilson(0, 10)
    assert interval["low"] == 0.0 and 0.25 < interval["high"] < 0.32
    assert ev.wilson(0, 0) == {"low": None, "high": None}
    empty = ev.ratio(0, 0)
    assert empty["value"] is None and empty["reason"] == "no_eligible_cases"
    # 2 errors among 2 low vs 0 among 5 high: one-sided p = 1/21.
    assert ev._fisher_upper(2, 0, 0, 5) == pytest.approx(1 / 21)
    assert ev._fisher_upper(0, 0, 1, 1) is None


# --- normalization and evidence --------------------------------------------------


@pytest.mark.parametrize(
    ("rule", "raw", "expected"),
    [
        ("identity", "10,5 m", 10.5),
        ("identity", "7.0 m", 7.0),
        ("ratio_to_percent", "0,35", 35.0),
        ("range_lower", "25°– 40°", 25.0),
        ("range_upper", "25°– 40°", 40.0),
        ("range_lower", "od 30o do 45o", 30.0),
        ("range_upper", "0,0- 0,5", 0.5),
        ("word_number", "do dwóch kondygnacji", 2.0),
        ("word_number", "jednokondygnacyjne", 1.0),
        ("manual", "od 300 do 450", None),
    ],
)
def test_normalization_rules(rule: str, raw: str, expected: float | None) -> None:
    assert ev.normalize_value(rule, raw) == expected


def test_normalization_rejects_unusable_input() -> None:
    with pytest.raises(ev.EvaluationError):
        ev.normalize_value("identity", "brak liczby")
    with pytest.raises(ev.EvaluationError):
        ev.normalize_value("range_upper", "tylko 5")
    with pytest.raises(ev.EvaluationError):
        ev.normalize_value("word_number", "xyz")
    with pytest.raises(ev.EvaluationError):
        ev.normalize_value("nieznana", "5")


def test_truth_text_locates_evidence_with_anchor_and_page() -> None:
    truth = ev.TruthText(["Strefa A: wysokość 10 m.\nStrefa B:", "wysokość 12 m."], [4, 5])
    assert truth.locate("wysokość 10 m", None) == 4
    assert truth.locate("wysokość 12 m", "Strefa B:") == 5
    with pytest.raises(ev.EvaluationError):
        truth.locate("wysokość 99 m", None)
    with pytest.raises(ev.EvaluationError):
        truth.locate("wysokość 10 m", "brak kotwicy")
    # evidence before the anchor must not be attributed to the anchored zone
    with pytest.raises(ev.EvaluationError):
        truth.locate("wysokość 10 m", "Strefa B:")


# --- frozen, validated corpus ------------------------------------------------------


def test_real_corpus_is_valid_frozen_and_meets_acceptance_shape(manifest: dict[str, Any]) -> None:
    assert ev.validate_corpus(manifest, FIXTURES) == []
    samples = manifest["samples"]
    assert len(samples) >= 20
    assert manifest["freeze"]["annotations_sha256"] == ev.annotations_sha256(manifest)
    assert manifest["freeze"]["parser_run_on_final_split_before_freeze"] is False
    formats = {sample["format"] for sample in samples}
    assert {"pdf_text", "pdf_table", "html"} <= formats
    assert formats & {"ocr_real", "ocr_simulated"}
    assert sum(sample["multi_zone"] for sample in samples) >= 3
    gminas = {manifest["documents"][s["document_id"]]["gmina"] for s in samples}
    assert len(gminas) >= 5
    assert {sample["split"] for sample in samples} == {"development", "final"}
    for sample in samples:
        document = manifest["documents"][sample["document_id"]]
        assert document["split"] == sample["split"]
        assert document["url"] and document["pages_sha256"]
        for zone in sample["zones"]:
            for annotation in zone["annotations"]:
                assert annotation["evidence"] and annotation["raw_value"]
                assert annotation["page"] >= 1


@pytest.mark.parametrize(
    ("mutation", "fragment"),
    [
        (lambda m: m["samples"][0]["zones"][0]["annotations"][0].update(evidence="zmyślony cytat"), "Evidence not found"),
        (lambda m: m["samples"][0]["zones"][0]["annotations"][0].update(normalized_value=99.0), "normalization gives"),
        (lambda m: m["samples"][0]["zones"][0]["annotations"][0].update(page=99), "evidence is on page"),
        (lambda m: m["samples"][0]["zones"][0]["annotations"][0].update(operator="exact"), "operator"),
        (lambda m: m["samples"][0]["zones"][0]["annotations"][0].update(parameter="nieznany"), "not in the catalog"),
        (lambda m: m["samples"][0].update(split="final"), "split differs"),
        (lambda m: m["samples"][0].update(multi_zone=False), "multi_zone"),
        (lambda m: m["samples"][0]["zones"][0]["annotations"][0].update(unit="km"), "unit must be"),
        (lambda m: m["documents"]["krakow_morelowa"].update(pages_sha256="0" * 64), "hash differs"),
        (lambda m: m["samples"].pop(), "annotations changed after freeze"),
        (lambda m: m["samples"][0].update(sample_id=m["samples"][1]["sample_id"]), "duplicate sample_id"),
    ],
)
def test_validation_detects_tampering(manifest: dict[str, Any], mutation: Any, fragment: str) -> None:
    tampered = copy.deepcopy(manifest)
    mutation(tampered)
    errors = ev.validate_corpus(tampered, FIXTURES)
    assert any(fragment in error for error in errors), errors


def test_validation_requires_freeze_and_minimum_samples(manifest: dict[str, Any]) -> None:
    unfrozen = copy.deepcopy(manifest)
    del unfrozen["freeze"]
    assert "missing freeze record" in ev.validate_corpus(unfrozen, FIXTURES)
    small = copy.deepcopy(manifest)
    small["samples"] = small["samples"][:5]
    errors = ev.validate_corpus(small, FIXTURES)
    assert any("samples < 20" in error for error in errors)
    assert ev.validate_corpus({"documents": {}}, FIXTURES)
    with pytest.raises(ev.EvaluationError):
        ev.load_corpus(FIXTURES / "does-not-exist.json")


# --- running the production parser on the frozen corpus ------------------------------


@pytest.fixture(scope="module")
def evaluation(manifest: dict[str, Any]) -> dict[str, Any]:
    return ev.evaluate(manifest, FIXTURES)


def test_evaluation_covers_every_zone_and_catalog_parameter(
    manifest: dict[str, Any], evaluation: dict[str, Any]
) -> None:
    zones = sum(len(sample["zones"]) for sample in manifest["samples"])
    rows = evaluation["rows"]
    assert len(rows) == zones * len(ev.CATALOG)
    counts = evaluation["metrics"]["overall"]["detection"]["counts"]
    assert sum(counts.values()) == len(rows)
    assert {row["sample_id"] for row in rows} == {s["sample_id"] for s in manifest["samples"]}
    assert evaluation["parser_version"] == "mpzp-parser/2.0"


def test_split_and_format_slices_partition_the_rows(evaluation: dict[str, Any]) -> None:
    metrics = evaluation["metrics"]
    total = sum(metrics["overall"]["detection"]["counts"].values())
    for dimension in ("by_split", "by_format"):
        assert sum(sum(item["detection"]["counts"].values()) for item in metrics[dimension].values()) == total
    assert set(metrics["by_split"]) == {"development", "final"}
    assert {"pdf_text", "pdf_table", "html"} <= set(metrics["by_format"])
    assert set(metrics["by_parameter"]) == set(ev.CATALOG)
    # every metric reports its denominator
    precision = metrics["overall"]["detection"]["precision"]
    assert precision["denominator"] == precision["numerator"] + metrics["overall"]["detection"]["counts"].get("fp", 0)


def test_every_wrong_outcome_has_a_trace(evaluation: dict[str, Any]) -> None:
    errors = ev.build_error_trace(evaluation["rows"])
    wrong = [
        r for r in evaluation["rows"]
        if r["verdict"] in {"fn", "fp", "tp_wrong", "tp_partial"} or r["source_assignment"] == "wrong_source"
    ]
    assert len(errors) == len(wrong)
    assert len({item["error_id"] for item in errors}) == len(errors)
    for item in errors:
        assert item["document_url"].startswith("http")
        assert item["parser_version"] == "mpzp-parser/2.0"
        assert item["sample_id"] and item["zone_symbol"] and item["parameter"]
        assert item["category"] in {"recognition", "normalization_or_value", "assignment"}
        assert item["cause_hint"]
        assert item["engine"] == "legacy"


def test_returned_values_are_evidence_backed_and_calibration_is_reported(evaluation: dict[str, Any]) -> None:
    values = evaluation["values"]
    assert values and all(0.0 <= row["confidence"] <= 1.0 for row in values)
    calibration = evaluation["metrics"]["overall"]["calibration"]
    assert calibration["n_values"] == len(values)
    assert sum(band["n"] for band in calibration["by_band"].values()) == len(values)
    assert 0.0 <= calibration["brier_score"] <= 1.0


def test_evaluation_is_deterministic_offline(
    manifest: dict[str, Any], evaluation: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket

    def blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network access is not allowed in the offline evaluation")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    second = ev.evaluate(manifest, FIXTURES)
    assert ev.canonical_json({"rows": evaluation["rows"], "metrics": evaluation["metrics"]}) == ev.canonical_json(
        {"rows": second["rows"], "metrics": second["metrics"]}
    )


# --- CLI --------------------------------------------------------------------------------


def test_cli_writes_reports_and_fails_on_bad_input(tmp_path: Path, manifest: dict[str, Any]) -> None:
    root = tmp_path / "parser"
    assert ev.main(["--output-dir", str(root), "--repeat", "2"]) == 0
    assert {path.name for path in root.iterdir()} == {"legacy"}  # one directory per engine
    output = root / "legacy"
    assert {path.name for path in output.iterdir()} == {
        "calibration.svg", "determinism.json", "errors.csv", "errors.json", "metrics.json", "observations.json",
        "parameter_results.csv", "parameter_results.json", "rejections.csv", "report.md", "run_manifest.json",
        "scope_results.json", "values.csv",
    }
    run_manifest = json.loads((output / "run_manifest.json").read_text())
    metrics = json.loads((output / "metrics.json").read_text())
    results = json.loads((output / "parameter_results.json").read_text())
    errors = json.loads((output / "errors.json").read_text())
    assert run_manifest["manifest_sha256"] == metrics["manifest_sha256"] == results["manifest_sha256"] == errors["manifest_sha256"]
    assert run_manifest["annotations_sha256"] == manifest["freeze"]["annotations_sha256"]
    assert run_manifest["parser_version"] == "mpzp-parser/2.0"
    assert run_manifest["engine"] == "legacy" and run_manifest["parameters"]["mode"] == "offline"
    determinism = json.loads((output / "determinism.json").read_text())
    assert determinism["identical"] is True and determinism["excluded"] == ["observations"]
    observations = json.loads((output / "observations.json").read_text())
    assert observations["manifest_sha256"] == run_manifest["manifest_sha256"]
    assert observations["summary"]["calls"] == 0 and observations["summary"]["cost_usd_total"] is None
    with (output / "parameter_results.csv").open(newline="", encoding="utf-8") as handle:
        assert len(list(csv.DictReader(handle))) == len(results["rows"])
    with (output / "errors.csv").open(newline="", encoding="utf-8") as handle:
        assert len(list(csv.DictReader(handle))) == len(errors["errors"])
    report = (output / "report.md").read_text()
    for heading in ("Zamrożony manifest", "Według formatu", "Według gminy", "Źródło wartości", "Kalibracja confidence",
                    "Błędy", "Zakres strefy", "Bramki odrzuceń", "Koszt i opóźnienie"):
        assert heading in report
    assert "<svg" in (output / "calibration.svg").read_text()

    assert ev.main(["--output-dir", str(tmp_path / "bad"), "--repeat", "0"]) == 1
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{}", encoding="utf-8")
    assert ev.main(["--corpus", str(invalid), "--output-dir", str(tmp_path / "bad2")]) == 1


def test_cli_fails_when_annotations_change_after_freeze(
    tmp_path: Path, manifest: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    copy_ = copy.deepcopy(manifest)
    for document in copy_["documents"].values():
        document["path"] = str((FIXTURES / document["path"]).resolve())  # the copy lives in tmp_path
    copy_["freeze"]["annotations_sha256"] = ev.annotations_sha256(copy_)
    assert ev.validate_corpus(copy_, tmp_path) == []  # a legitimate new freeze passes
    # an annotation edited after the freeze, without a new hash, is rejected
    copy_["samples"][0]["zones"][0]["annotations"][0]["raw_value"] += " "
    path = tmp_path / "manifest.tampered.json"
    path.write_text(json.dumps(copy_), encoding="utf-8")
    assert ev.main(["--corpus", str(path), "--output-dir", str(tmp_path / "out")]) == 1
    assert "annotations changed after freeze" in capsys.readouterr().err


# --- source consistency (PV3-03) ---------------------------------------------------------


def _engine_result(control: dict[str, Any]) -> Any:
    from scripts.mpzp_eval_engines import EngineResult, EngineValue

    zones = {
        symbol: [
            EngineValue(
                parameter=item["name"], value=item["value"], confidence=item["confidence"],
                manual_review_required=False, page=item["page"], source_text=item["source_text"],
                raw_value=item["raw_value"],
            )
            for item in outputs
        ]
        for symbol, outputs in control["engine_output"].items()
    }
    return EngineResult(zones=zones)


@pytest.fixture(scope="module")
def source_control(control: dict[str, Any]) -> dict[str, Any]:
    return control["source_control"]


@pytest.fixture(scope="module")
def source_rows(source_control: dict[str, Any]) -> list[dict[str, Any]]:
    truth = ev.TruthText(source_control["pages"], source_control["page_numbers"])
    return ev.score_sample(
        source_control["sample"], {"document_sha256": "1" * 64, "url": "https://example.invalid/control-2"},
        _engine_result(source_control), source_control["page_numbers"], "engine/test", truth=truth, engine="control",
    )


def test_source_control_matches_hand_calculation(source_control: dict[str, Any], source_rows: list[dict[str, Any]]) -> None:
    expected = source_control["expected"]
    assert len(source_rows) == 36
    detection = ev.detection_metrics(source_rows)
    assert {key: detection["counts"].get(key, 0) for key in expected["counts"]} == expected["counts"]
    for name, key in (
        ("precision", "precision"), ("recall", "recall"),
        ("exact_value_accuracy_among_found", "exact_among_found"),
        ("zone_assignment_accuracy", "zone_assignment"),
        ("source_consistent", "source_consistent"),
        ("zone_assignment_source_aware", "zone_assignment_source_aware"),
        ("strict_end_to_end", "strict_end_to_end"),
    ):
        numerator, denominator = expected[key]
        assert (detection[name]["numerator"], detection[name]["denominator"]) == (numerator, denominator), name
    assert detection["cross_zone_errors"] == expected["cross_zone_errors"]
    assert detection["wrong_source_errors"] == expected["wrong_source_errors"]
    assert detection["source_status_counts"] == expected["source_status_counts"]


def test_value_based_assignment_hides_what_the_source_check_finds(source_rows: list[dict[str, Any]]) -> None:
    by_key = {(row["zone_symbol"], row["parameter"]): row for row in source_rows}
    # the value is right in A and D, so the value-based assignment calls both correct
    for key in (("A", "max_building_height_m"), ("D", "max_building_height_m")):
        assert by_key[key]["assignment"] == "correct"
        assert by_key[key]["source_assignment"] == "wrong_source"
        assert by_key[key]["verdict"] == "tp_exact"  # the value-level verdict is unchanged
    checks = {
        (row["zone_symbol"], row["parameter"]): row["parser_outputs"][0]["source_check"]
        for row in source_rows if row["parser_outputs"]
    }
    assert checks[("A", "max_building_height_m")]["status"] == "wrong_page"
    assert checks[("D", "max_building_height_m")]["status"] == "wrong_block"
    assert checks[("B", "max_building_height_m")]["status"] == "consistent"
    assert checks[("B", "max_building_coverage_percent")]["status"] == "not_applicable"  # wrong value, not wrong source


def test_source_control_error_trace(source_control: dict[str, Any], source_rows: list[dict[str, Any]]) -> None:
    errors = ev.build_error_trace(source_rows)
    kinds = {f"{item['zone_symbol']}/{item['parameter']}": item["error_type"] for item in errors}
    assert kinds == source_control["expected"]["error_types"]
    wrong_source = [item for item in errors if item["error_type"] == "wrong_source"]
    assert {item["category"] for item in wrong_source} == {"assignment"}
    assert {item["cause_hint"] for item in wrong_source} == {"value_from_other_source"}
    assert all(item["source_checks"][0]["status"] in {"wrong_page", "wrong_block"} for item in wrong_source)


def test_without_source_text_the_check_is_reported_as_not_checked(control: dict[str, Any]) -> None:
    rows = _control_rows(control)
    detection = ev.detection_metrics(rows)
    assert detection["source_consistent"]["value"] is None
    assert detection["source_consistent"]["reason"] == "no_source_checks"
    # without a source text the strict figures fall back to the value-based ones
    assert detection["strict_end_to_end"]["numerator"] == detection["exact_value_accuracy_end_to_end"]["numerator"]
    assert detection["wrong_source_errors"] == 0


def _location(**changes: Any) -> ev.AnnotationLocation:
    base = {"value": 10.0, "page": 1, "evidence": (10, 30), "block": (0, 100), "applicability": "zone_section",
            "status": "required"}
    return ev.AnnotationLocation(**{**base, **changes})


def _value(**changes: Any) -> Any:
    from scripts.mpzp_eval_engines import EngineValue

    base = {"parameter": "max_building_height_m", "value": 10.0, "confidence": 0.8,
            "manual_review_required": False, "page": 1, "source_text": "wysokość 10 m", "raw_value": "10 m"}
    return EngineValue(**{**base, **changes})


def test_check_source_statuses() -> None:
    page = "Strefa A: wysokość 10 m, " + "x" * 120 + " Strefa B: wysokość 10 m."
    truth = ev.TruthText([page, "Strefa C: wysokość 10 m."], [1, 2])
    zone_a = _location(block=(0, 60), evidence=(9, 22))
    zone_b = _location(block=(60, 200), evidence=(155, 168))
    assert ev.check_source(_value(), [zone_a], None)["status"] == "not_checked"
    assert ev.check_source(_value(), [], truth)["status"] == "not_applicable"
    assert ev.check_source(_value(page=None), [zone_a], truth)["status"] == "unlocatable"
    assert ev.check_source(_value(page=2), [zone_a], truth)["status"] == "wrong_page"
    # the same sentence occurs in zone A and zone B: neither block can be proven
    assert ev.check_source(_value(), [zone_a], truth)["status"] == "indeterminate"
    # a unique sentence decides the block
    assert ev.check_source(_value(source_text="Strefa A: wysokość 10 m,"), [zone_a], truth)["status"] == "consistent"
    assert ev.check_source(_value(source_text="Strefa B: wysokość 10 m."), [zone_a], truth)["status"] == "wrong_block"
    assert ev.check_source(_value(source_text="nie ma takiego tekstu"), [zone_a], truth)["status"] == "unlocatable"
    # the most favourable evidence decides when a value is annotated twice
    assert ev.check_source(_value(source_text="Strefa B: wysokość 10 m."), [zone_a, zone_b], truth)["status"] == "consistent"
    # no source text: the raw value alone is located (and is ambiguous here)
    assert ev.check_source(_value(source_text=None), [zone_a], truth)["status"] == "indeterminate"
    assert ev.check_source(_value(source_text=None, raw_value=None), [zone_a], truth)["status"] == "unlocatable"
    # annotation without an anchor: the evidence span itself is the reference
    evidence_only = _location(block=None, evidence=(9, 22))
    assert ev.check_source(_value(source_text="Strefa A: wysokość 10 m,"), [evidence_only], truth)["basis"] == "evidence"
    # simulated scan: the annotated text is not what the engine read, so only the page can be compared
    page_only = ev.check_source(_value(source_text="coś innego"), [zone_a], truth, page_only=True)
    assert page_only["status"] == "consistent" and page_only["basis"] == "page"
    assert ev.check_source(_value(page=2), [zone_a], truth, page_only=True)["status"] == "wrong_page"


def test_check_source_prefers_the_exact_character_span() -> None:
    from scripts.mpzp_eval_engines import SourceSpan

    raw_page = "Strefa A:\n  wysokość   10 m.   Strefa B:\n wysokość 10 m."
    truth = ev.TruthText([raw_page], [7])
    zone_b = _location(page=7, evidence=(0, 0), block=(truth.text.index("Strefa B:"), len(truth.text)))
    in_a = raw_page.index("10 m")
    in_b = raw_page.rindex("10 m")
    # the span is authoritative even though the same text occurs in both zones
    assert ev.check_source(_value(page=7, span=SourceSpan(7, in_b, in_b + 4)), [zone_b], truth)["status"] == "consistent"
    assert ev.check_source(_value(page=7, span=SourceSpan(7, in_a, in_a + 4)), [zone_b], truth)["status"] == "wrong_block"
    assert ev.check_source(_value(page=1, span=SourceSpan(7, in_b, in_b + 4)), [zone_b], truth)["status"] == "consistent"
    assert ev.check_source(_value(page=7, span=SourceSpan(7, 5000, 5004)), [zone_b], truth)["status"] == "unlocatable"


def test_truth_text_offsets_survive_whitespace_normalization() -> None:
    for raw in ("  a  b\n c ", "x", "", "   ", "a\u00a0b\tc", "ab \n"):
        normalized, origin = ev.normalize_with_map(raw)
        assert normalized == ev.normalize_text(raw)
        assert len(origin) == len(normalized)
    truth = ev.TruthText(["ab  cd", "ef"], [3, 4])
    assert truth.find_in_page("cd", 3) == [3] and truth.find_in_page("ef", 4) == [6]
    assert truth.find_in_page("cd", 9) == [] and truth.find_in_page("", 3) == []
    assert truth.raw_span_to_doc(3, 4, 6) == (3, 5)  # raw "cd" after the double space
    assert truth.raw_span_to_doc(3, 6, 6) is None and truth.raw_span_to_doc(9, 0, 1) is None


def test_zone_blocks_stop_at_the_next_anchor_and_keep_their_evidence() -> None:
    pages = ["Teren A: wysokość 10 m." + " " * 5 + "Teren B: wysokość 12 m."]
    truth = ev.TruthText(pages, [1])
    sample = {"zones": [
        {"symbol": "A", "annotations": [{"evidence": "wysokość 10 m", "anchor": "Teren A:"}]},
        {"symbol": "B", "annotations": [{"evidence": "wysokość 12 m", "anchor": "Teren B:"}]},
    ]}
    blocks = ev.zone_block_spans(sample, truth)
    assert blocks["Teren A:"] == (0, truth.text.index("Teren B:"))
    assert blocks["Teren B:"][0] == truth.text.index("Teren B:") and blocks["Teren B:"][1] == len(truth.text)


def test_krakow_mn11_height_from_page_18_is_a_wrong_source(manifest: dict[str, Any]) -> None:
    """The case named in the issue: 11 m is right for MN.11, but this 11 m is on page 18, not 19."""
    from scripts.mpzp_eval_engines import EngineResult, EngineValue

    sample = next(item for item in manifest["samples"] if item["sample_id"] == "D01-krakow-mn-multi")
    loaded = ev.load_document(FIXTURES, manifest["documents"][sample["document_id"]])
    truth = ev.TruthText(loaded["pages"], loaded["page_numbers"])
    page_18 = truth.text[truth._starts[truth.page_numbers.index(18)]:][:5000]
    start = page_18.index("maksymalną wysokość zabudowy: 11 m")
    snippet = page_18[start : start + 60]
    on_19 = truth.text[truth._starts[truth.page_numbers.index(19)]:]
    # the zone anchor makes the quoted context unique: the bare sentence recurs in several zones
    anchor = on_19.index("dla terenu MN.11:")
    sentence = "wysokość zabudowy: 11 m"
    right = on_19[anchor : on_19.index(sentence, anchor) + len(sentence)]

    def run(page: int, text: str) -> list[dict[str, Any]]:
        value = EngineValue("max_building_height_m", 11.0, 0.9, False, page=page, source_text=text, raw_value="11 m")
        result = EngineResult(zones={"MN.11": [value]})
        return ev.score_sample(sample, {}, result, loaded["page_numbers"], "engine/test", truth=truth)

    wrong = next(r for r in run(18, snippet) if r["zone_symbol"] == "MN.11" and r["parameter"] == "max_building_height_m")
    assert wrong["parser_outputs"][0]["source_check"]["status"] == "wrong_page"
    assert wrong["source_assignment"] == "wrong_source" and wrong["assignment"] == "correct"
    good = next(r for r in run(19, right) if r["zone_symbol"] == "MN.11" and r["parameter"] == "max_building_height_m")
    assert good["parser_outputs"][0]["source_check"]["status"] == "consistent"
    assert good["source_assignment"] == "correct"


def test_real_corpus_source_metrics_are_accounted_for(evaluation: dict[str, Any]) -> None:
    detection = evaluation["metrics"]["overall"]["detection"]
    counts = detection["source_status_counts"]
    metric = detection["source_consistent"]
    assert metric["denominator"] == sum(counts.values()) and metric["numerator"] == counts["consistent"]
    required = detection["source_status_counts_required_values"]
    assert sum(required.values()) <= sum(counts.values())
    assert set(counts) <= set(ev.SOURCE_STATUSES)
    # every returned value of a scored zone carries a source check
    for row in evaluation["rows"]:
        for output in row["parser_outputs"]:
            assert output["source_check"]["status"] in ev.SOURCE_STATUSES
            assert output["value_correct"] or output["source_check"]["status"] == "not_applicable"
    assert {row["gmina"] for row in evaluation["rows"]} == set(evaluation["metrics"]["by_gmina"])
    assert evaluation["metrics"]["rejections"] == {"total": 0, "by_gate": {}}


# --- engines, replay and comparison through the CLI (PV3-03) -----------------------------


def _fake_engine(name: str = "fake", *, latency: float = 12.0, status: str | None = None) -> Any:
    """Engine that returns, for every annotated required value, the right value (exactly sourced)."""
    from scripts.mpzp_eval_engines import EngineResult, EngineUsage, EngineValue, Rejection

    class Fake:
        supports_discovery = False

        def __init__(self) -> None:
            self.name, self.version = name, "fake/1"
            self.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
            self.latency = latency

        def run(self, loaded: dict[str, Any], symbols: list[str]) -> EngineResult:
            doc = loaded["source"]["url"]
            zones: dict[str, list[EngineValue]] = {}
            rejections = []
            for sample in self.manifest["samples"]:
                if self.manifest["documents"][sample["document_id"]]["url"] != doc:
                    continue
                for zone in sample["zones"]:
                    if zone["symbol"] not in symbols or not zone["annotations"]:
                        continue
                    values = []
                    for item in zone["annotations"][:2]:
                        values.append(EngineValue(
                            item["parameter"], item["normalized_value"], 0.9, False, page=item["page"],
                            source_text=item["evidence"], raw_value=item["raw_value"], review_status=status,
                        ))
                    zones[zone["symbol"]] = values
                    rejections.append(Rejection(zone["symbol"], "quote_not_found", zone["annotations"][0]["parameter"],
                                                zone["annotations"][0]["normalized_value"], "demo"))
                    rejections.append(Rejection(zone["symbol"], "unit_mismatch", None, None, "demo"))
            usage = EngineUsage(calls=1, input_tokens=100, output_tokens=10, latency_ms=self.latency, cost_usd=0.001)
            return EngineResult(zones=zones, usage=usage, rejections=rejections)

    return Fake()


@pytest.fixture
def fake_engine_registered() -> Any:
    from scripts import mpzp_eval_engines as engines

    engines.register_engine(engines.EngineSpec("fake", "test", factory=lambda _ctx: _fake_engine()))
    engines.register_engine(engines.EngineSpec("fake2", "test", factory=lambda _ctx: _fake_engine("fake2", latency=99.0)))
    yield
    engines.unregister_engine("fake")
    engines.unregister_engine("fake2")


def test_cli_compares_engines_on_identical_pairs(tmp_path: Path, fake_engine_registered: None) -> None:
    root = tmp_path / "parser"
    code = ev.main(["--engine", "legacy", "fake", "--output-dir", str(root), "--repeat", "2",
                    "--bootstrap-resamples", "200"])
    assert code == 0
    assert {path.name for path in root.iterdir()} == {"legacy", "fake", "comparison"}
    comparison = json.loads((root / "comparison" / "comparison.json").read_text())
    assert comparison["baseline"] == "legacy" and comparison["engines"] == ["legacy", "fake"]
    assert set(comparison["engine_manifest_sha256"]) == {"legacy", "fake"}
    paired = comparison["paired"]["fake"]
    assert paired["n_pairs_total"] == 423
    assert set(paired["by_format"]) >= {"pdf_text", "html"} and "Kraków" in paired["by_gmina"]
    exact = paired["overall"]["exact"]
    assert exact["only_baseline"] + exact["only_candidate"] > 0 and exact["mcnemar_exact_p"] is not None
    report = (root / "comparison" / "comparison.md").read_text()
    for heading in ("Metryki każdego silnika", "Metryki według formatu", "Metryki według gminy",
                    "Porównanie sparowane: `legacy` → `fake`", "Bramki odrzuceń", "Koszt i opóźnienie"):
        assert heading in report
    assert "quote_not_found" in report and "unit_mismatch" in report
    assert "<svg" in (root / "comparison" / "comparison.svg").read_text()
    # the gates are counted separately for a correct value that was refused
    gates = json.loads((root / "fake" / "metrics.json").read_text())["rejections"]["by_gate"]
    assert gates["quote_not_found"]["value_correct"] > 0 and gates["unit_mismatch"]["value_unknown"] > 0
    fake_obs = json.loads((root / "fake" / "observations.json").read_text())
    assert fake_obs["summary"]["input_tokens"] > 0 and fake_obs["summary"]["cost_usd_total"] > 0
    with (root / "comparison" / "comparison.csv").open(newline="", encoding="utf-8") as handle:
        assert any(row["dimension"] == "gmina" for row in csv.DictReader(handle))


def test_substance_is_identical_across_runs_while_observations_differ(
    manifest: dict[str, Any], fake_engine_registered: None
) -> None:
    first = ev.evaluate(manifest, FIXTURES, engine=_fake_engine(latency=5.0))
    second = ev.evaluate(manifest, FIXTURES, engine=_fake_engine(latency=500.0))
    assert ev.canonical_json({"rows": first["rows"], "metrics": first["metrics"]}) == ev.canonical_json(
        {"rows": second["rows"], "metrics": second["metrics"]}
    )
    assert first["observations"] != second["observations"]


def test_engine_may_not_return_a_verified_value(manifest: dict[str, Any]) -> None:
    with pytest.raises(ev.EvaluationError, match="candidate for manual review"):
        ev.evaluate(manifest, FIXTURES, engine=_fake_engine(status="verified"))
    ok = ev.evaluate(manifest, FIXTURES, engine=_fake_engine(status="ai_candidate"))
    assert ok["engine"] == "fake"


def test_unavailable_engines_and_bad_flags_exit_with_usage_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = ["--output-dir", str(tmp_path / "out")]
    assert ev.main(["--engine", "hybrid", *out]) == 2
    assert "not available" in capsys.readouterr().err  # hybrydy nie ma (Taski 20.10–20.14)
    assert ev.main(["--engine", "hybrid", "--live", *out]) == 2  # unavailable before the model flags are considered
    assert ev.main(["--engine", "nieistnieje", *out]) == 2
    assert "unknown engine" in capsys.readouterr().err
    assert ev.main(["--engine", "legacy", "--live", *out]) == 2
    assert "only to engines that use a model" in capsys.readouterr().err
    assert ev.main(["--engine", "legacy", "--llm-replay", str(tmp_path), *out]) == 2
    assert ev.main(["--engine", "legacy", "--baseline", "legacy", *out]) == 2
    assert ev.main(["--engine", "legacy", "--baseline", "fake", *out]) == 2
    assert not (tmp_path / "out").exists()  # nothing is written before the checks pass


def test_model_engine_needs_replay_or_live_and_live_is_gated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts import mpzp_eval_engines as engines

    engines.register_engine(engines.EngineSpec("llmfake", "t", factory=lambda ctx: _fake_engine("llmfake"), uses_llm=True))
    try:
        out = ["--output-dir", str(tmp_path / "out")]
        assert ev.main(["--engine", "llmfake", *out]) == 2
        assert "--llm-replay" in capsys.readouterr().err
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
        monkeypatch.delenv(engines.LLM_API_KEY_ENV, raising=False)
        assert ev.main(["--engine", "llmfake", "--live", "--llm-replay", str(tmp_path / "r"), *out]) == 2
        assert engines.LLM_API_KEY_ENV in capsys.readouterr().err
        monkeypatch.setenv(engines.LLM_API_KEY_ENV, "x")
        assert ev.main(["--engine", "llmfake", "--live", "--llm-replay", str(tmp_path / "r"), *out]) == 2
        assert "no live provider" in capsys.readouterr().err
        monkeypatch.setenv("CI", "true")
        assert ev.main(["--engine", "llmfake", "--live", "--llm-replay", str(tmp_path / "r"), *out]) == 2
        assert "disabled in CI" in capsys.readouterr().err
        monkeypatch.delenv("CI")
        # replay needs no key and no provider, and the manifest records that it was a replay
        assert ev.main(["--engine", "llmfake", "--llm-replay", str(tmp_path / "r"), *out, "--repeat", "1"]) == 0
        manifest = json.loads((tmp_path / "out" / "llmfake" / "run_manifest.json").read_text())
        assert manifest["parameters"]["mode"] == "replay" and manifest["llm"]["mode"] == "replay"
        assert manifest["llm"]["replayed"] == 0 and manifest["llm"]["responses_in_store"] == 0
    finally:
        engines.unregister_engine("llmfake")


def test_cli_reports_missing_replay_and_incompatible_pairs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts import mpzp_eval_engines as engines

    class NeedsReplay:
        name, version, supports_discovery = "needs_replay", "r/1", False

        def run(self, loaded: dict[str, Any], symbols: list[str]) -> Any:
            raise engines.ReplayMissError("no recorded response for cache key abc")

    engines.register_engine(engines.EngineSpec("needs_replay", "t", factory=lambda ctx: NeedsReplay(), uses_llm=True))
    try:
        code = ev.main(["--engine", "needs_replay", "--llm-replay", str(tmp_path), "--output-dir", str(tmp_path / "o")])
        assert code == 1 and "no recorded response" in capsys.readouterr().err
    finally:
        engines.unregister_engine("needs_replay")
    # engines that scored different pairs cannot be compared
    from scripts import mpzp_eval_compare as compare

    with pytest.raises(compare.ComparisonError):
        compare.build_comparison(
            {"a": {"rows": [{"sample_id": "s", "zone_symbol": "Z", "parameter": "p"}], "metrics": {}, "observations": {}},
             "b": {"rows": [], "metrics": {}, "observations": {}}}, "zzz",
        )


def test_live_run_records_responses_and_replay_reproduces_it_offline(
    tmp_path: Path, manifest: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The model path end to end: a (fake) live provider records, a replay needs neither key nor network."""
    import socket

    from scripts import mpzp_eval_engines as engines

    api_key = "test-key-never-written"
    calls: list[engines.LlmRequest] = []

    class Provider:
        def complete(self, request: engines.LlmRequest) -> engines.LlmResponse:
            calls.append(request)
            values = []
            for sample in manifest["samples"]:
                if sample["document_id"] != request.payload["document_id"]:
                    continue
                for zone in sample["zones"]:
                    if zone["symbol"] in request.payload["symbols"]:
                        values += [{"zone": zone["symbol"], "parameter": a["parameter"], "value": a["normalized_value"],
                                    "page": a["page"], "quote": a["evidence"], "raw": a["raw_value"]}
                                   for a in zone["annotations"][:2]]
            return engines.LlmResponse({"values": values}, input_tokens=500, output_tokens=50, latency_ms=700.0,
                                       cost_usd=0.0007, model_returned="fake-model", finish_reason="STOP")

    class ModelEngine:
        name, version, supports_discovery = "llmfake", "llmfake/1", False

        def __init__(self, gateway: engines.LlmGateway) -> None:
            self.gateway = gateway

        def run(self, loaded: dict[str, Any], symbols: list[str]) -> engines.EngineResult:
            text = "\n".join(loaded["pages"]) + "\n" + ",".join(symbols)
            document_id = next(k for k, d in manifest["documents"].items() if d["url"] == loaded["source"]["url"])
            request = engines.LlmRequest(
                "fake", "fake-model", "prompt/1", "schema/1", engines.sha256_text(text),
                payload={"symbols": symbols, "document_id": document_id},
            )
            response = self.gateway.complete(request)
            zones: dict[str, list[engines.EngineValue]] = {}
            for item in response.content["values"]:
                zones.setdefault(item["zone"], []).append(engines.EngineValue(
                    item["parameter"], item["value"], 0.9, True, page=item["page"], source_text=item["quote"],
                    raw_value=item["raw"], review_status="ai_candidate",
                ))
            usage = engines.EngineUsage(1, response.input_tokens, response.output_tokens, response.latency_ms,
                                        response.cost_usd)
            return engines.EngineResult(zones=zones, usage=usage)

    def factory(context: engines.EngineContext) -> ModelEngine:
        assert context.gateway is not None
        return ModelEngine(context.gateway)

    engines.register_engine(engines.EngineSpec("llmfake", "t", factory=factory, uses_llm=True))
    engines.register_live_provider(lambda key: Provider())
    try:
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
        monkeypatch.setenv(engines.LLM_API_KEY_ENV, api_key)
        store = tmp_path / "llm_replay"
        live_out, replay_out = tmp_path / "live", tmp_path / "replay"
        assert ev.main(["--engine", "legacy", "llmfake", "--live", "--llm-replay", str(store),
                        "--output-dir", str(live_out), "--repeat", "2", "--bootstrap-resamples", "100"]) == 0
        n_samples = len(manifest["samples"])
        assert len(calls) == n_samples and len(list(store.glob("*.json"))) == n_samples
        live_manifest = json.loads((live_out / "llmfake" / "run_manifest.json").read_text())
        assert live_manifest["parameters"]["mode"] == "live"
        assert live_manifest["llm"]["recorded"] == n_samples and live_manifest["llm"]["replayed"] == n_samples  # repeat 2
        assert all(api_key not in path.read_text(encoding="utf-8") for path in store.glob("*.json"))
        assert not any(api_key in path.read_text(encoding="utf-8") for path in live_out.rglob("*") if path.is_file())

        monkeypatch.delenv(engines.LLM_API_KEY_ENV)
        engines.register_live_provider(None)

        def blocked(*_a: object, **_k: object) -> None:
            raise AssertionError("replay must not touch the network")

        monkeypatch.setattr(socket.socket, "connect", blocked)
        monkeypatch.setattr(socket, "create_connection", blocked)
        assert ev.main(["--engine", "legacy", "llmfake", "--llm-replay", str(store),
                        "--output-dir", str(replay_out), "--repeat", "1", "--bootstrap-resamples", "100"]) == 0
        replay_manifest = json.loads((replay_out / "llmfake" / "run_manifest.json").read_text())
        assert replay_manifest["parameters"]["mode"] == "replay" and replay_manifest["llm"]["recorded"] == 0
        assert replay_manifest["llm"]["replay_store_sha256"] == live_manifest["llm"]["replay_store_sha256"]
        assert len(calls) == n_samples  # the replay made no call
        live_rows = json.loads((live_out / "llmfake" / "parameter_results.json").read_text())["rows"]
        replay_rows = json.loads((replay_out / "llmfake" / "parameter_results.json").read_text())["rows"]
        assert live_rows == replay_rows
        # the recorded usage is reported as an observation, and the model values stay candidates
        summary = json.loads((replay_out / "llmfake" / "observations.json").read_text())["summary"]
        assert summary["calls"] == n_samples and summary["input_tokens"] == 500 * n_samples
        assert summary["cost_usd_total"] == pytest.approx(0.0007 * n_samples)
        assert all(o["review_status"] == "ai_candidate" for r in replay_rows for o in r["parser_outputs"])
        # a changed prompt version is a different key: replay refuses instead of guessing
        for path in store.glob("*.json"):
            path.unlink()
        assert ev.main(["--engine", "llmfake", "--llm-replay", str(store), "--output-dir", str(tmp_path / "x")]) == 1
    finally:
        engines.unregister_engine("llmfake")
        engines.register_live_provider(None)
