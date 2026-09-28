"""Ewaluator BK-303: confusion matrix i błąd pola wyłącznie z pól strukturalnych."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.schemas.analyze import AnalyzeResponse, RiskResult
from app.schemas.source import SourceMetadata
from app.services.report import _build_limitations, _build_report_context, _render_report_html
from scripts.evaluate_reference_corpus import (
    _observation_as_structured,
    _relation_as_bool,
    _risk_errors,
    _risk_section,
    calculate_metrics,
    risk_section_from_structured,
    sections_from_analyze_response,
)

AVAILABLE = {"section": "flood", "status": "available", "union_intersection_area_sqm": 12.0}


def _risk(**values):
    return {"section": "flood", "risk_type": "flood", **values}


def test_structured_section_relations() -> None:
    none = risk_section_from_structured(AVAILABLE, [])
    edge = risk_section_from_structured(AVAILABLE, [_risk(intersection_area_sqm=0.0, touches_boundary=True)])
    inferred_edge = risk_section_from_structured(AVAILABLE, [_risk(intersection_area_sqm=0.0)])
    hit = risk_section_from_structured(
        AVAILABLE,
        [_risk(intersection_area_sqm=12.0, intersection_pct=1.2, probability_class="Q1", feature_id="A")],
    )

    assert none["values"]["relation"] == "none"
    assert edge["values"]["relation"] == "boundary"
    assert inferred_edge["values"]["relation"] == "boundary"
    assert hit["values"]["relation"] == "intersection"
    assert hit["values"]["features"][0] == {
        "area": 12.0,
        "class": "Q1",
        "severity": None,
        "share": 1.2,
        "type": "flood",
        "feature_id": "A",
        "touches_boundary": False,  # wyprowadzone z pola > epsilon
    }
    assert hit["values"]["union_area"] == 12.0
    assert _relation_as_bool("boundary") is False


@pytest.mark.parametrize("status", ["unavailable", "error", "unknown", None])
def test_failed_or_legacy_section_is_unknown_not_no_risk(status) -> None:
    section = None if status is None else {"section": "nature", "status": status}
    result = risk_section_from_structured(section, [])
    assert result["status"] == "unknown"
    assert result["values"]["relation"] is None


def test_frozen_observation_uses_same_structured_path() -> None:
    section, risks = _observation_as_structured(
        {
            "status": "available",
            "features": [
                {"type": "natura2000", "name": "Dolina", "intersection_area_sqm": 5.0, "area_ratio": 0.5},
                "garbage",
            ],
        },
        "nature",
    )
    assert section == {"section": "nature", "status": "available"}
    assert risks[0]["protection_type"] == "natura2000" and risks[0]["intersection_pct"] == 50.0
    assert _observation_as_structured(None, "flood") == (None, [])
    assert _observation_as_structured({"status": "unavailable"}, "flood")[0]["status"] == "unavailable"
    assert _risk_section({"status": "timeout"}, "flood")["status"] == "unknown"


def test_area_errors_match_by_class_and_skip_unmatched() -> None:
    expected = {"values": {"features": [
        {"class": "Q1", "area": 100.0, "share": 10.0},
        {"class": "Q10", "area": 50.0, "share": 5.0},
        {"class": "Q1", "area": "x"},
        "bad",
    ]}}
    actual = {"values": {"features": [
        {"class": "Q1", "area": 90.0, "share": 9.0},
        {"class": "Q1", "area": 400.0, "share": None},
        "bad",
    ]}}
    assert _risk_errors(expected, actual) == ([10.0], [1.0])
    assert _risk_errors({"values": None}, actual) == ([], [])
    assert _risk_errors({"values": {"features": None}}, actual) == ([], [])


def test_api_payload_sections_and_metrics_without_description() -> None:
    payload = {
        "risk_sections": [
            {"section": "flood", "status": "available", "union_intersection_area_sqm": 40.0},
            {"section": "nature", "status": "unavailable"},
            "bad",
        ],
        "risks": [
            _risk(intersection_area_sqm=40.0, intersection_pct=4.0, probability_class="Q1",
                  description="brak ryzyka"),
            "bad",
        ],
    }
    sections = sections_from_analyze_response(payload)
    assert sections["flood"]["values"]["relation"] == "intersection"
    assert sections["nature"]["status"] == "unknown"
    assert sections_from_analyze_response({})["flood"]["status"] == "unknown"

    metrics = calculate_metrics([
        {
            "expected_sections": {
                "flood": {"status": "available", "values": {"relation": "intersection", "features": [
                    {"class": "Q1", "area": 42.0, "share": 4.2}]}},
                "nature": {"status": "available", "values": {"relation": "none"}},
            },
            "actual_sections": sections,
        }
    ])
    assert metrics["binary_conditions"]["flood_intersection"]["tp"] == 1
    assert metrics["risk_area_mae"]["value"] == pytest.approx(2.0)
    assert metrics["risk_share_mae"]["value"] == pytest.approx(0.2)
    empty = calculate_metrics([])
    assert empty["risk_area_mae"]["reason"] == "no_matched_risk_features"


def test_report_without_sections_marks_legacy_and_shows_boundary_rows() -> None:
    now = datetime(2026, 9, 28, tzinfo=timezone.utc)
    source = SourceMetadata(source_name="GDOS", fetched_at=now, confidence=0.85, manual_review_required=False)
    response = AnalyzeResponse(
        status="partial",
        analyzed_at=now,
        mpzp_zones=[],
        infrastructure=[],
        risks=[
            RiskResult(risk_type="flood_zone", description="stary zapis", source=source),
            RiskResult(risk_type="rezerwat_przyrody", protection_type="rezerwat_przyrody", name="Brzeg",
                       touches_boundary=True, intersection_area_sqm=0.0, intersection_pct=0.0,
                       severity="low", description="styk", source=source),
        ],
        warnings=[],
        sources=[],
    )
    html = _render_report_html(_build_report_context(response, None, None))

    assert html.count("brak informacji w zapisanym wyniku") >= 2
    assert "styk granicy" in html and "Brzeg" in html
    limitations = _build_limitations(response, None)
    assert any("nie zawiera statusu sprawdzenia" in item for item in limitations)
