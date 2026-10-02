"""Paired engine comparison: statistics with hand-calculated values (PV3-03)."""

from __future__ import annotations

from typing import Any

import pytest

from scripts import mpzp_eval_compare as compare


def _row(sample: str, zone: str, parameter: str, verdict: str, *, required: bool = True, fmt: str = "pdf_text",
         gmina: str = "Kraków", source: str | None = "correct") -> dict[str, Any]:
    return {
        "sample_id": sample, "zone_symbol": zone, "parameter": parameter, "format": fmt, "split": "final",
        "gmina": gmina, "required_values": [1.0] if required else [], "verdict": verdict,
        "assignment": "correct" if verdict == "tp_exact" else None, "source_assignment": source,
    }


# --- statistics ------------------------------------------------------------------------------


def test_percentile_is_nearest_rank() -> None:
    values = [10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
    assert compare.percentile(values, 50) == 5
    assert compare.percentile(values, 95) == 10
    assert compare.percentile(values, 0) == 1 and compare.percentile(values, 100) == 10
    assert compare.percentile([7.5], 95) == 7.5
    assert compare.percentile([], 50) is None


@pytest.mark.parametrize(
    ("only_baseline", "only_candidate", "expected"),
    [
        (0, 0, None),
        (1, 1, 1.0),            # 2·(1+2)/4 = 1.5, capped at 1
        (0, 5, 2 / 32),         # 2·1/2^5
        (5, 0, 2 / 32),         # symmetric
        (2, 8, 112 / 1024),     # 2·(1+10+45)/2^10
        (0, 10, 2 / 1024),
        (4, 4, 1.0),
    ],
)
def test_mcnemar_exact_hand_values(only_baseline: int, only_candidate: int, expected: float | None) -> None:
    result = compare.mcnemar_exact(only_baseline, only_candidate)
    assert result == (pytest.approx(expected) if expected is not None else None)


def test_bootstrap_is_deterministic_and_brackets_the_estimate() -> None:
    clusters = [(10, 2, 6), (8, 1, 5), (12, 3, 7), (9, 2, 5), (11, 4, 6), (10, 3, 8)]
    first = compare.cluster_bootstrap_difference(clusters, 400, seed=603)
    assert first == compare.cluster_bootstrap_difference(clusters, 400, seed=603)
    estimate = (sum(c[2] for c in clusters) - sum(c[1] for c in clusters)) / sum(c[0] for c in clusters)
    assert first["low"] <= estimate <= first["high"] and first["low"] > 0  # candidate clearly better
    assert first != compare.cluster_bootstrap_difference(clusters, 400, seed=7)
    identical = compare.cluster_bootstrap_difference([(10, 5, 5)] * 6, 100, seed=1)
    assert identical == {"low": 0.0, "high": 0.0}
    assert compare.cluster_bootstrap_difference([], 10, 1) == {"low": None, "high": None}
    assert compare.cluster_bootstrap_difference([(0, 0, 0)] * 3, 10, 1) == {"low": None, "high": None}


# --- paired outcomes -------------------------------------------------------------------------


def _baseline_and_candidate() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Hand-made pairs: 6 with a required value (3 samples), 2 without."""
    baseline = [
        _row("s1", "A", "max_building_height_m", "tp_exact"),
        _row("s1", "A", "max_storeys", "fn"),
        _row("s2", "B", "max_building_height_m", "tp_exact", fmt="html", gmina="Łódź"),
        _row("s2", "B", "max_storeys", "tp_exact", fmt="html", gmina="Łódź", source="wrong_source"),
        _row("s3", "C", "max_building_height_m", "fn", gmina="Szczytno"),
        _row("s3", "C", "max_storeys", "tp_wrong", gmina="Szczytno"),
        _row("s1", "A", "setback_m", "fp", required=False),
        _row("s3", "C", "setback_m", "tn", required=False, gmina="Szczytno"),
    ]
    candidate = [
        _row("s1", "A", "max_building_height_m", "tp_exact"),
        _row("s1", "A", "max_storeys", "tp_exact"),                        # gained
        _row("s2", "B", "max_building_height_m", "fn", fmt="html", gmina="Łódź"),   # lost
        _row("s2", "B", "max_storeys", "tp_exact", fmt="html", gmina="Łódź"),       # same value, now right source
        _row("s3", "C", "max_building_height_m", "tp_exact", gmina="Szczytno"),     # gained
        _row("s3", "C", "max_storeys", "tp_wrong", gmina="Szczytno"),
        _row("s1", "A", "setback_m", "tn", required=False),                # false alarm removed
        _row("s3", "C", "setback_m", "fp", required=False, gmina="Szczytno"),       # false alarm added
    ]
    return baseline, candidate


def test_pair_outcomes_follow_the_definitions() -> None:
    baseline, candidate = _baseline_and_candidate()
    pairs = compare.paired_table(baseline, candidate)
    assert len(pairs) == 8
    by_key = {(p["sample_id"], p["parameter"]): p for p in pairs}
    # a right value from the wrong source is exact but not strict
    assert by_key[("s2", "max_storeys")]["baseline"] == {"exact": True, "strict": False, "false_alarm": None}
    assert by_key[("s2", "max_storeys")]["candidate"] == {"exact": True, "strict": True, "false_alarm": None}
    assert by_key[("s1", "setback_m")]["baseline"] == {"exact": None, "strict": None, "false_alarm": True}


def test_paired_statistics_match_hand_calculation() -> None:
    baseline, candidate = _baseline_and_candidate()
    result = compare.compare_pair(baseline, candidate, resamples=200, seed=1)
    exact = result["overall"]["exact"]
    assert exact["n_pairs"] == 6
    assert exact["baseline_successes"] == 3 and exact["candidate_successes"] == 4
    assert (exact["only_baseline"], exact["only_candidate"]) == (1, 2)       # lost s2 height; gained s1 storeys, s3 height
    assert exact["mcnemar_exact_p"] == pytest.approx(1.0)                    # n = 3, k = 1: 2·(1+3)/8 = 1
    assert exact["difference"] == pytest.approx(1 / 6)
    strict = result["overall"]["strict"]
    assert strict["baseline_successes"] == 2 and strict["candidate_successes"] == 4
    assert (strict["only_baseline"], strict["only_candidate"]) == (1, 3)     # lost s2 height; gained s1/s2 storeys, s3 height
    assert strict["mcnemar_exact_p"] == pytest.approx(2 * 5 / 16)            # n = 4, k = 1: 2·(1+4)/16
    alarm = result["overall"]["false_alarm"]
    assert alarm["n_pairs"] == 2 and (alarm["only_baseline"], alarm["only_candidate"]) == (1, 1)
    # three samples are too few for a bootstrap interval, and the report says why
    assert exact["bootstrap_ci95"]["low"] is None and "fewer than" in exact["bootstrap_ci95"]["reason"]
    assert set(result["by_format"]) == {"html", "pdf_text"} and set(result["by_gmina"]) == {"Kraków", "Łódź", "Szczytno"}
    html = result["by_format"]["html"]["exact"]
    assert (html["n_pairs"], html["baseline_successes"], html["candidate_successes"]) == (2, 2, 1)
    assert result["by_split"]["final"]["exact"]["n_pairs"] == 6


def test_slices_with_enough_samples_get_a_bootstrap_interval() -> None:
    baseline, candidate = [], []
    for index in range(6):
        baseline.append(_row(f"s{index}", "A", "max_storeys", "fn", gmina=f"G{index}"))
        candidate.append(_row(f"s{index}", "A", "max_storeys", "tp_exact", gmina=f"G{index}"))
    interval = compare.compare_pair(baseline, candidate, resamples=100, seed=3)["overall"]["exact"]["bootstrap_ci95"]
    assert interval["reason"] is None and interval["low"] == pytest.approx(1.0) and interval["high"] == pytest.approx(1.0)


def test_engines_scored_on_different_pairs_cannot_be_compared() -> None:
    baseline, candidate = _baseline_and_candidate()
    with pytest.raises(compare.ComparisonError, match="same pairs"):
        compare.paired_table(baseline, candidate[:-1])


# --- observations and gates --------------------------------------------------------------------


def test_observation_summary_separates_not_reported_from_zero() -> None:
    silent = {"per_sample": [
        {"sample_id": "a", "document_id": "d1", "zones": 2, "wall_ms": 10.0, "calls": 0},
        {"sample_id": "b", "document_id": "d1", "zones": 3, "wall_ms": 30.0, "calls": 0},
    ]}
    summary = compare.summarize_observations(silent)
    assert summary["documents"] == 1 and summary["zones"] == 5
    assert summary["input_tokens"] is None and summary["cost_usd_total"] is None and summary["cost_usd_per_zone"] is None
    assert summary["wall_ms_p50"] == 10.0 and summary["wall_ms_p95"] == 30.0 and summary["calls"] == 0
    billed = {"per_sample": [
        {"sample_id": "a", "document_id": "d1", "zones": 2, "wall_ms": 5.0, "calls": 1, "input_tokens": 1000,
         "output_tokens": 100, "latency_ms": 800.0, "cost_usd": 0.002},
        {"sample_id": "b", "document_id": "d2", "zones": 2, "wall_ms": 5.0, "calls": 1, "input_tokens": 3000,
         "output_tokens": 300, "latency_ms": 1600.0, "cost_usd": 0.006},
    ]}
    summary = compare.summarize_observations(billed)
    assert summary["input_tokens"] == 4000 and summary["output_tokens"] == 400 and summary["calls"] == 2
    assert summary["cost_usd_total"] == pytest.approx(0.008)
    assert summary["cost_usd_per_document"] == pytest.approx(0.004) and summary["cost_usd_per_zone"] == pytest.approx(0.002)
    assert summary["model_latency_ms_p50"] == 800.0 and summary["model_latency_ms_p95"] == 1600.0


def test_rejection_gate_table_splits_correct_and_wrong_refusals() -> None:
    table = compare.rejection_gate_table([
        {"gate": "quote_not_found", "value_was_correct": False},
        {"gate": "quote_not_found", "value_was_correct": True},
        {"gate": "unit_mismatch", "value_was_correct": None},
    ])
    assert table == {
        "quote_not_found": {"rejected": 2, "value_correct": 1, "value_wrong": 1},
        "unit_mismatch": {"rejected": 1, "value_unknown": 1},
    }
    assert compare.rejection_gate_table([]) == {}


def test_comparison_needs_a_known_baseline_and_a_single_engine_has_no_pairs() -> None:
    detection = {"precision": {"value": None, "reason": "no_eligible_cases"}}
    metrics = {"overall": {"detection": detection}, "by_format": {}, "by_gmina": {}}
    results = {"only": {"rows": [], "metrics": metrics, "observations": {"per_sample": []}}}
    with pytest.raises(compare.ComparisonError, match="baseline"):
        compare.build_comparison(results, "missing")
    comparison = compare.build_comparison(results, "only")
    assert comparison["paired"] == {} and comparison["rejection_gates"] == {"only": {}}
    report = compare.render_comparison_report(
        comparison, {"only": {"per_sample": []}}, {"annotations_sha256": "a" * 64, "corpus_sha256": "b" * 64}
    )
    assert "wymaga co najmniej dwóch" in report
    assert "<svg" in compare.comparison_svg(comparison)
