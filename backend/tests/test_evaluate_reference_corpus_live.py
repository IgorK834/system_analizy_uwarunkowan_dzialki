"""Tryb ``--mode live`` ewaluatora korpusu referencyjnego (AU-011)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.schemas.analyze import AnalyzeResponse
from scripts import evaluate_reference_corpus as evaluation
from tests.parcel_fixtures_config import find_repo_root


@pytest.fixture(scope="module")
def corpus_path() -> Path:
    return find_repo_root() / "backend/tests/fixtures/reference_corpus/manifest.json"


def _payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "analysis_id": 5,
        "status": "partial",
        "parcel": {"parcel_identifier": "146510_8.0502.1/3", "metrics": {"area_sqm": 15893.459, "perimeter_m": 1386.555}},
        "mpzp_zones": [],
        "pog": None,
        "risks": [],
        "risk_sections": [
            {"section": "flood", "status": "available", "union_intersection_area_sqm": 0.0},
            {"section": "nature", "status": "unavailable"},
        ],
        "terrain": {"status": "available", "min_height_m": 109.1, "max_height_m": 113.7, "height_difference_m": 4.6},
        "manual_zone_required": False,
        "mpzp_discovery": None,
        "manual_zone_context": None,
    }
    payload.update(overrides)
    return payload


def _pog(coverage: str = "available", legal: str = "binding", **extra: Any) -> dict[str, Any]:
    return {
        "legal_status": legal,
        "coverage_status": coverage,
        "zones": [{"id": "z1", "symbol": "SW", "area_sqm": 600.0, "area_pct": 60.0}, {"id": "z2", "symbol": "SU", "area_sqm": 400.0, "area_pct": 40.0}],
        "ouz_intersection_area_sqm": 0.0,
        "ouz_intersection_pct": 0.0,
        **extra,
    }


# --- Mapowanie odpowiedzi API na sekcje ewaluatora ---------------------------------------


def test_geometry_and_terrain_come_from_the_response() -> None:
    sections = evaluation.sections_from_live_response(_payload())

    assert sections["geometry"] == {"status": "available", "values": {"area": 15893.459, "perimeter": 1386.555}}
    assert sections["terrain"]["values"] == {"minimum": 109.1, "maximum": 113.7, "relief": 4.6, "class": "moderate"}
    assert sections["flood"]["values"]["relation"] == "none"
    assert sections["nature"]["status"] == "unknown"  # status źródła ≠ available nie jest „brakiem ryzyka”


def test_missing_parcel_or_terrain_is_unknown_not_zero() -> None:
    sections = evaluation.sections_from_live_response(
        _payload(parcel=None, terrain={"status": "unavailable", "min_height_m": None})
    )

    assert sections["geometry"]["status"] == "unknown" and sections["geometry"]["values"] == {"area": None, "perimeter": None}
    assert sections["terrain"]["status"] == "unknown" and sections["terrain"]["values"]["minimum"] is None


def test_pog_available_uses_the_ground_truth_vocabulary_for_legal_status() -> None:
    sections = evaluation.sections_from_live_response(_payload(pog=_pog()))

    assert sections["pog"]["status"] == "available"
    assert sections["pog"]["values"]["legal_status"] == "legal_force"
    assert sections["pog"]["values"]["zone_count"] == 2
    assert sections["pog"]["values"]["zones"][0] == {"area": 600.0, "local_id": "z1", "share": 60.0, "symbol": "SW"}
    assert sections["ouz"] == {"status": "available", "values": {"intersection_area": 0.0, "relation": "outside", "share": 0.0}}


def test_pog_project_status_is_passed_through_not_promoted_to_legal_force() -> None:
    sections = evaluation.sections_from_live_response(_payload(pog=_pog(legal="project")))

    assert sections["pog"]["values"]["legal_status"] == "project"


@pytest.mark.parametrize("coverage", ["unknown", "no_act_confirmed", "act_without_spatial_data"])
def test_pog_without_coverage_is_unknown_and_never_an_empty_available_section(coverage: str) -> None:
    sections = evaluation.sections_from_live_response(_payload(pog=_pog(coverage=coverage)))

    assert sections["pog"] == {"status": "unknown", "values": {"legal_status": None, "zone_count": None, "zones": None}}
    assert sections["ouz"]["status"] == "unknown"


def test_pog_partial_coverage_needs_manual_review() -> None:
    sections = evaluation.sections_from_live_response(_payload(pog=_pog(coverage="partial")))

    assert sections["pog"]["status"] == "manual_review"


def test_ouz_without_a_measured_area_is_unknown() -> None:
    sections = evaluation.sections_from_live_response(_payload(pog=_pog(ouz_intersection_area_sqm=None)))

    assert sections["ouz"]["status"] == "unknown"


def test_ouz_relations_follow_the_offline_thresholds() -> None:
    inside = evaluation.sections_from_live_response(_payload(pog=_pog(ouz_intersection_area_sqm=900.0, ouz_intersection_pct=100.0)))
    partial = evaluation.sections_from_live_response(_payload(pog=_pog(ouz_intersection_area_sqm=450.0, ouz_intersection_pct=45.0)))
    boundary = evaluation.sections_from_live_response(_payload(pog=_pog(ouz_intersection_area_sqm=0.4, ouz_intersection_pct=0.1)))

    assert inside["ouz"]["values"]["relation"] == "inside"
    assert partial["ouz"]["values"]["relation"] == "partial"
    assert (boundary["ouz"]["status"], boundary["ouz"]["values"]["relation"]) == ("manual_review", "boundary")


def test_mpzp_vector_zones_skip_boundary_touches() -> None:
    zones = [
        {"zone_symbol": "MN", "intersection_area_sqm": 900.0, "intersection_pct": 90.0, "act_identifier": "plan-1", "document_url": "https://x/u.pdf"},
        {"zone_symbol": "KD", "intersection_area_sqm": 0.2, "intersection_pct": 0.0, "touches_boundary": True},
    ]

    section = evaluation.sections_from_live_response(_payload(mpzp_zones=zones))["mpzp"]

    assert section["status"] == "available"
    assert section["values"] == {
        "document_url": "https://x/u.pdf", "mode": "vector", "plan_id": "plan-1", "zone_count": 1,
        "zones": [{"area": 900.0, "share": 90.0, "symbol": "MN"}],
    }


def test_mpzp_manual_mode_is_manual_review_with_candidates() -> None:
    context = {"plan_id": "MPZP/2020/1", "candidate_zone_symbols": ["230_U", "MN"], "document": {"requested_url": "https://x/uchwala.pdf"}}

    section = evaluation.sections_from_live_response(_payload(manual_zone_required=True, manual_zone_context=context))["mpzp"]

    assert section["status"] == "manual_review"
    assert section["values"]["mode"] == "raster_manual" and section["values"]["plan_id"] == "MPZP/2020/1"
    assert [zone["symbol"] for zone in section["values"]["zones"]] == ["230_U", "MN"]
    assert section["values"]["zone_count"] == 2


def test_mpzp_discovery_without_vectors_is_vector_discovery() -> None:
    discovery = {"status": "available", "acts": [{"id": "a"}], "selected_act": "plan-9", "candidate_zone_symbols": ["U"]}

    section = evaluation.sections_from_live_response(_payload(mpzp_discovery=discovery))["mpzp"]

    assert (section["status"], section["values"]["mode"], section["values"]["plan_id"]) == ("manual_review", "vector_discovery", "plan-9")


@pytest.mark.parametrize("discovery", [None, {"status": "no_match", "acts": []}, {"status": "unavailable"}, {"status": "unknown"}])
def test_mpzp_without_evidence_is_unknown(discovery: Any) -> None:
    section = evaluation.sections_from_live_response(_payload(mpzp_discovery=discovery))["mpzp"]

    assert section == {"status": "unknown", "values": {"document_url": None, "mode": None, "plan_id": None, "zone_count": None, "zones": None}}


def test_mapping_accepts_a_real_serialized_analyze_response() -> None:
    response = AnalyzeResponse(
        analysis_id=7, status="partial", analyzed_at=datetime(2026, 10, 8, tzinfo=UTC), mpzp_zones=[], pog=None,
        infrastructure=[], risks=[], warnings=[], sources=[],
    )

    sections = evaluation.sections_from_live_response(json.loads(response.model_dump_json()))

    assert set(sections) == {"geometry", "pog", "ouz", "mpzp", "flood", "nature", "terrain"}
    assert all(section["status"] == "unknown" for section in sections.values())  # puste wejście nie udaje wiedzy


# --- Runner ------------------------------------------------------------------------------


def _runner(handler, **kwargs: Any) -> evaluation.LiveApiAnalysisRunner:
    kwargs.setdefault("sleep", lambda seconds: None)
    return evaluation.LiveApiAnalysisRunner("http://api.test", transport=httpx.MockTransport(handler), **kwargs)


CASE = {"case_id": "real-001", "parcel_identifier": "146510_8.0502.1/3", "geometry": {"secret": "g"}, "observations": {}}


def test_runner_posts_only_the_parcel_identifier_and_never_expected_values() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_payload(pog=_pog()))

    result = _runner(handler).analyze(CASE)

    assert json.loads(seen[0].content) == {"method": "parcel_id", "parcel_identifier": "146510_8.0502.1/3"}
    assert result["parcel_identifier"] == "146510_8.0502.1/3" and result["status"] == "partial"
    assert result["cache_status"] == "unknown" and result["duration_ms"] >= 0.0
    assert result["sections"]["pog"]["status"] == "available"
    assert result["manual_review_required"] is False


def test_runner_flags_manual_review_when_any_section_needs_it() -> None:
    result = _runner(lambda request: httpx.Response(200, json=_payload(pog=_pog(coverage="partial")))).analyze(CASE)

    assert result["manual_review_required"] is True


def test_runner_retries_429_with_retry_after_then_succeeds() -> None:
    waits: list[float] = []
    replies = iter([httpx.Response(429, json={"error": "RATE_LIMITED"}, headers={"Retry-After": "12"}), httpx.Response(200, json=_payload())])

    result = _runner(lambda request: next(replies), sleep=waits.append).analyze(CASE)

    assert waits == [12.0] and result["status"] == "partial"


def test_runner_caps_the_wait_and_gives_up_after_max_retries() -> None:
    waits: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "RATE_LIMITED"}, headers={"Retry-After": "600"})

    with pytest.raises(evaluation.EvaluationError, match="HTTP 429 RATE_LIMITED"):
        _runner(handler, sleep=waits.append, max_retries=2, max_wait=30).analyze(CASE)

    assert waits == [30.0, 30.0]


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (httpx.Response(500, json={"error": "INTERNAL_ERROR"}, headers={"X-Request-ID": "srv-0001"}), "HTTP 500 INTERNAL_ERROR (request_id=srv-0001)"),
        (httpx.Response(404, json={"error": "PARCEL_NOT_FOUND"}), "HTTP 404 PARCEL_NOT_FOUND"),
        (httpx.Response(502, text="<html>"), "HTTP 502 bez ciała JSON"),
        (httpx.Response(200, json=[1, 2]), "HTTP 200 bez ciała JSON"),
    ],
)
def test_runner_turns_non_success_into_a_visible_failed_case(response: httpx.Response, expected: str) -> None:
    with pytest.raises(evaluation.EvaluationError) as caught:
        _runner(lambda request: response).analyze(CASE)

    assert expected in str(caught.value)


def test_runner_reports_a_missing_api() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("odmowa", request=request)

    with pytest.raises(evaluation.EvaluationError, match="brak odpowiedzi API"):
        _runner(handler).analyze(CASE)


# --- Cały przebieg na korpusie ----------------------------------------------------------------


def _offline_like_api(corpus_path: Path):
    """Atrapa API zwracająca odpowiedź zgodną z ground truth — sprawdza całą ścieżkę metryk."""
    manifest, _ = evaluation.load_corpus(corpus_path)
    by_identifier = {case["parcel_identifier"]: case for case in manifest["cases"]}

    def handler(request: httpx.Request) -> httpx.Response:
        case = by_identifier[json.loads(request.content)["parcel_identifier"]]
        expected = case["expected"]
        geometry = evaluation._unwrap(expected["geometry"]["values"])
        return httpx.Response(
            200,
            json=_payload(
                parcel={"parcel_identifier": case["parcel_identifier"], "metrics": {"area_sqm": geometry["area"], "perimeter_m": geometry["perimeter"]}},
            ),
        )

    return handler


def test_evaluate_runs_the_whole_corpus_through_the_live_runner(corpus_path: Path) -> None:
    manifest, _ = evaluation.load_corpus(corpus_path)
    runner = _runner(_offline_like_api(corpus_path))

    records, metrics = evaluation.evaluate(manifest, corpus_path.parent, runner)

    assert len(records) == 30 and metrics["failed_cases"]["value"] == 0
    assert metrics["parcel_identification_accuracy"]["value"] == 1.0  # identyfikator z odpowiedzi API == manifest
    assert metrics["cache_hits"]["value"] == 0 and metrics["cache_misses"]["value"] == 0
    # Odpowiedź live bez POG/MPZP daje dużo „unknown” — mniej kompletności niż zamrożony offline.
    _, offline_metrics = evaluation.evaluate(manifest, corpus_path.parent)
    assert metrics["field_completeness"]["value"] < offline_metrics["field_completeness"]["value"]


def test_a_5xx_case_stays_visible_as_failed_and_counts_in_the_metric(corpus_path: Path) -> None:
    manifest, _ = evaluation.load_corpus(corpus_path)
    good = _offline_like_api(corpus_path)

    def handler(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content)["parcel_identifier"] == "146510_8.0502.1/3":
            return httpx.Response(500, json={"error": "INTERNAL_ERROR"})
        return good(request)

    records, metrics = evaluation.evaluate(manifest, corpus_path.parent, _runner(handler))

    failed = [record for record in records if record["status"] == "failed"]
    assert [record["case_id"] for record in failed] == ["real-001-146510-8-0502-1-3"]
    assert "HTTP 500 INTERNAL_ERROR" in failed[0]["error"] and failed[0]["has_unknown"] is True
    assert metrics["failed_cases"]["value"] == 1


def test_compare_offline_live_reports_metric_deltas_and_section_differences(corpus_path: Path) -> None:
    manifest, _ = evaluation.load_corpus(corpus_path)
    offline_records, offline_metrics = evaluation.evaluate(manifest, corpus_path.parent)
    live_records, live_metrics = evaluation.evaluate(manifest, corpus_path.parent, _runner(_offline_like_api(corpus_path)))

    comparison = evaluation.compare_offline_live(offline_metrics, live_metrics, offline_records, live_records)

    rows = {row["metric"]: row for row in comparison["metrics"]}
    completeness = rows["field_completeness"]
    assert completeness["delta"] == pytest.approx(completeness["live"] - completeness["offline"])
    assert completeness["delta"] < 0
    assert "binary_conditions.aggregate.recall" in rows and "binary_conditions" not in rows
    assert comparison["cases_with_differences"] > 0
    first = next(case for case in comparison["cases"] if case["section_differences"])
    assert {"section", "offline", "live"} <= set(first["section_differences"][0])
    markdown = evaluation.render_offline_vs_live({"base_url": "http://api.test", "commit_sha": "abc", "corpus_sha256": "0" * 64}, comparison)
    assert "| `field_completeness` |" in markdown and "Statusy sekcji per działka" in markdown


# --- CLI ---------------------------------------------------------------------------------------


def test_live_cli_writes_the_report_and_the_offline_vs_live_comparison(
    corpus_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    handler = _offline_like_api(corpus_path)
    original = evaluation.LiveApiAnalysisRunner

    class MockedRunner(original):  # type: ignore[valid-type, misc]
        def __init__(self, base_url: str) -> None:
            super().__init__(base_url, transport=httpx.MockTransport(handler), sleep=lambda seconds: None)

    monkeypatch.setattr(evaluation, "LiveApiAnalysisRunner", MockedRunner)
    out = tmp_path / "live"

    code = evaluation.main(["--mode", "live", "--base-url", "http://api.test", "--corpus", str(corpus_path), "--output-dir", str(out)])

    assert code == 0
    report = json.loads((out / "evaluation.json").read_text(encoding="utf-8"))
    assert report["metadata"]["mode"] == "live" and report["metadata"]["base_url"] == "http://api.test"
    assert len(report["cases"]) == 30
    comparison = json.loads((out / "offline-vs-live.json").read_text(encoding="utf-8"))
    assert comparison["metadata"]["mode"] == "live" and comparison["cases_with_differences"] > 0
    assert (out / "offline-vs-live.md").read_text(encoding="utf-8").startswith("# Offline vs live")
    assert "| `mode` | `live` |" in (out / "report.md").read_text(encoding="utf-8")
    assert "live evaluation: 30 cases, 0 failed" in capsys.readouterr().out


def test_live_cli_fails_when_a_case_fails(corpus_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = evaluation.LiveApiAnalysisRunner

    class Broken(original):  # type: ignore[valid-type, misc]
        def __init__(self, base_url: str) -> None:
            super().__init__(
                base_url, transport=httpx.MockTransport(lambda request: httpx.Response(500, json={"error": "INTERNAL_ERROR"}))
            )

    monkeypatch.setattr(evaluation, "LiveApiAnalysisRunner", Broken)

    code = evaluation.main(["--mode", "live", "--corpus", str(corpus_path), "--output-dir", str(tmp_path / "o")])

    assert code == 1
    assert (tmp_path / "o" / "offline-vs-live.md").exists()  # raport powstaje także przy awarii


def test_offline_mode_keeps_its_default_output_dir_and_live_has_its_own() -> None:
    assert evaluation.DEFAULT_OUTPUT_DIR.name == "reference-corpus"
    assert evaluation.DEFAULT_LIVE_OUTPUT_DIR.name == "reference-corpus-live"
    assert evaluation.build_parser().parse_args([]).mode == "offline"
    assert evaluation.build_parser().parse_args(["--mode", "live"]).base_url == "http://localhost:8000"
