"""Bramka jakości PV3-17: zamrożone kryteria, przegląd ręczny, zmienność i decyzja (bez sieci).

Wyniki ``legacy``/``v3`` pochodzą z prawdziwego biegu ewaluatora na korpusie BK-603 (offline). Wyniki
``hybrid`` są w testach SYNTETYCZNE (kopie ``v3`` z oznaczonymi wartościami ``ai_candidate``) — służą do
sprawdzenia logiki bramki, nie są pomiarem modelu.
"""

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from scripts import evaluate_mpzp_parser as ev
from scripts import mpzp_quality_gate as gate

CORPUS = ev.DEFAULT_MANIFEST


@pytest.fixture(scope="module")
def results(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("parser-v3")
    assert ev.main(["--engine", "legacy", "v3", "--output-dir", str(root), "--repeat", "1",
                    "--bootstrap-resamples", "50"]) == 0
    return root


def synthetic_hybrid(results: Path, target: Path, *, forge_quote: bool = False, cost: float = 0.03) -> Path:
    """Kopia ``v3`` jako ``hybrid``: co trzecia wartość oznaczona jako ``ai_candidate``, koszt i opóźnienie."""
    shutil.copytree(results, target, dirs_exist_ok=True)
    hybrid = target / "hybrid"
    shutil.rmtree(hybrid, ignore_errors=True)
    shutil.copytree(target / "v3", hybrid)
    data = json.loads((hybrid / "parameter_results.json").read_text("utf-8"))
    marked = 0
    for index, row in enumerate(data["rows"]):
        for output in row.get("parser_outputs") or []:
            if index % 3 == 0:
                output["review_status"] = "ai_candidate"
                marked += 1
                if forge_quote and marked == 1:
                    output["source_text"] = "tego cytatu nie ma w dokumencie: wysokość 99 m"
    (hybrid / "parameter_results.json").write_text(json.dumps(data), encoding="utf-8")
    metrics = json.loads((hybrid / "metrics.json").read_text("utf-8"))
    # Rdzeń v3 ma na BK-603 source_consistent 0,975 (< 0,98); syntetyczna hybryda ma próg spełniony.
    metrics["overall"]["detection"]["source_consistent"].update(value=0.99, numerator=99, denominator=100)
    (hybrid / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    observations = json.loads((hybrid / "observations.json").read_text("utf-8"))
    observations["summary"]["cost_usd_per_sample"] = cost
    observations["summary"]["calls"] = 40
    for item in observations["per_sample"]:
        item["wall_ms"] = (item["wall_ms"] or 0) + 6_000.0  # +6 s na próbkę
    (hybrid / "observations.json").write_text(json.dumps(observations), encoding="utf-8")
    return target


def reviewed_sheet(results: Path, path: Path, *, n: int = 100, incorrect: int = 2) -> Path:
    gate.review_sheet(results / "hybrid", path, n=n)
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for index, row in enumerate(rows):
        row.update(verdict="incorrect" if index < incorrect else "correct", reviewer="Recenzent A", reviewed_at="2026-10-06")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=gate.REVIEW_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return path


VARIANCE = {"runs": 3, "pairs_with_model_values": 10, "identical_across_runs": 9, "agreement": 0.9,
            "agreement_ci95": [0.6, 0.98], "same_manifest": True, "per_run_metrics": []}


def test_the_criteria_are_the_ones_frozen_in_adr_012() -> None:
    assert gate.FROZEN_ON == "2026-10-05"
    assert {key: (operator, threshold) for key, _label, operator, threshold in gate.CRITERIA} == {
        "precision": (">=", 0.95), "recall": (">=", 0.80), "source_consistent": (">=", 0.98),
        "unverified_quotes": ("==", 0), "zone_assignment_error_rate": ("<=", 0.02), "ece": ("<=", 0.10),
        "cost_per_analysis_usd": ("<=", 0.05), "additional_latency_p95_s": ("<=", 30.0),
        "manual_review_count": (">=", 100),
    }


def test_the_development_corpus_is_not_decidable_and_lists_every_missing_input(results: Path, tmp_path: Path) -> None:
    report = gate.evaluate_gate(results, CORPUS, None, None)
    assert report["decision"] == "NOT_DECIDABLE"
    joined = " ".join(report["blockers"])
    assert "final-v2" in joined and "hybrid" in joined and "przeglądu ręcznego" in joined and "zmienności" in joined
    assert report["engines"]["v3"]["precision"]["value"] == 1.0 and report["engines"]["hybrid"] is None
    gate.write_gate(report, tmp_path / "out")
    markdown = (tmp_path / "out" / "gate_report.md").read_text("utf-8")
    assert "NOT_DECIDABLE" in markdown and "nie gwarancją" in markdown and "nie zmierzono" in markdown
    assert gate.main(["gate", "--results", str(results), "--output-dir", str(tmp_path / "cli")]) == 3


def test_go_only_when_every_criterion_is_measured_and_met_on_a_final_corpus(
    results: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = synthetic_hybrid(results, tmp_path / "run")
    review = gate.review_score(reviewed_sheet(root, tmp_path / "sheet.csv"))
    assert review["reviewed"] >= 100 and review["precision"] == 0.98 and review["problems"] == []
    monkeypatch.setattr(gate, "corpus_problems", lambda corpus: [])  # zbiór końcowy z Task 20.2 — symulacja
    report = gate.evaluate_gate(root, CORPUS, review, VARIANCE)
    by_key = {row["criterion"]: row for row in report["criteria"]}
    assert report["decision"] == "GO", report["failed"] or report["unmeasured"]
    assert by_key["unverified_quotes"]["value"] == 0 and by_key["additional_latency_p95_s"]["value"] == pytest.approx(6.0, abs=0.5)
    assert by_key["precision"]["counts"].count("/") == 1
    gate.write_gate(report, tmp_path / "go")
    assert "**GO**" in (tmp_path / "go" / "gate_report.md").read_text("utf-8")


@pytest.mark.parametrize(
    ("kwargs", "failed"),
    [({"forge_quote": True}, "unverified_quotes"), ({"cost": 0.08}, "cost_per_analysis_usd")],
    ids=["unverified_quote", "cost"],
)
def test_a_single_failed_criterion_is_no_go(
    results: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kwargs: dict[str, Any], failed: str
) -> None:
    root = synthetic_hybrid(results, tmp_path / "run", **kwargs)
    review = gate.review_score(reviewed_sheet(root, tmp_path / "sheet.csv"))
    monkeypatch.setattr(gate, "corpus_problems", lambda corpus: [])
    report = gate.evaluate_gate(root, CORPUS, review, VARIANCE)
    assert report["decision"] == "NO_GO" and report["failed"] == [failed]


def test_a_too_small_manual_review_fails_the_gate(results: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = synthetic_hybrid(results, tmp_path / "run")
    review = gate.review_score(reviewed_sheet(root, tmp_path / "sheet.csv", n=40))
    monkeypatch.setattr(gate, "corpus_problems", lambda corpus: [])
    assert gate.evaluate_gate(root, CORPUS, review, VARIANCE)["failed"] == ["manual_review_count"]


def test_the_review_sheet_is_reproducible_and_scoring_requires_a_reviewer(results: Path, tmp_path: Path) -> None:
    root = synthetic_hybrid(results, tmp_path / "run")
    first, second = tmp_path / "a.csv", tmp_path / "b.csv"
    assert gate.review_sheet(root / "hybrid", first, n=30) == 30
    gate.review_sheet(root / "hybrid", second, n=30)
    assert first.read_text("utf-8") == second.read_text("utf-8")
    with first.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert set(rows[0]) == set(gate.REVIEW_COLUMNS) and all(row["quote"] for row in rows)
    rows[0].update(verdict="correct", reviewer="")
    rows[1].update(verdict="maybe", reviewer="X")
    rows[2].update(verdict="unclear", reviewer="X")
    rows[3].update(verdict="incorrect", reviewer="X")
    with first.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=gate.REVIEW_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    scored = gate.review_score(first)
    assert scored["counts"] == {"correct": 0, "incorrect": 1, "unclear": 1} and len(scored["problems"]) == 2
    assert scored["precision"] == 0.0 and gate.main(["review-score", "--sheet", str(first)]) == 1
    assert gate.main(["review-sheet", "--results", str(root / "hybrid"), "--output", str(tmp_path / "c.csv")]) == 0


def test_variance_measures_agreement_between_live_runs(results: Path, tmp_path: Path) -> None:
    runs = [synthetic_hybrid(results, tmp_path / f"run{index}") / "hybrid" for index in range(3)]
    data = json.loads((runs[2] / "parameter_results.json").read_text("utf-8"))
    changed = next(output for row in data["rows"] for output in row["parser_outputs"] if output.get("review_status"))
    changed["value"] = 12345.0
    (runs[2] / "parameter_results.json").write_text(json.dumps(data), encoding="utf-8")
    report = gate.variance(runs)
    assert report["runs"] == 3 and report["same_manifest"] is True
    assert report["identical_across_runs"] == report["pairs_with_model_values"] - 1 and 0 < report["agreement"] < 1
    with pytest.raises(ValueError):
        gate.variance(runs[:1])
    assert gate.main(["variance", "--runs", *map(str, runs), "--output", str(tmp_path / "v.json")]) == 0


def test_helpers() -> None:
    assert gate.p95([]) is None and gate.p95([1.0] * 19 + [10.0]) == 1.0 and gate.p95(list(range(1, 101))) == 95
    assert gate.wilson(0, 0) is None and gate.wilson(98, 100)[0] < 0.98 < gate.wilson(98, 100)[1]
