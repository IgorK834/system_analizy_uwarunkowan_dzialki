"""Raport PDF: wartości warunkowe z warunkiem i cytatem, sprzeczność osobno (PV3-08).

Sprawdza kontekst i HTML raportu (bez renderu PDF), więc działa bez bibliotek WeasyPrint.
"""

from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup

from app.schemas.analyze import AnalyzeResponse, MpzpParameterEvidence, MpzpValueCondition
from app.services.report import (
    _build_limitations,
    _build_report_context,
    _mpzp_section_context,
    _render_report_html,
)
from tests.report_map_reference import load_fixture

SHA = "a" * 64
NB = "\u00a0"  # jednostka w raporcie jest oddzielona od liczby twardą spacją


def parameter(value: float, *conditions: tuple[str, str, str], kind: str | None = None, group: str | None = None,
              name: str = "max_building_height_m") -> MpzpParameterEvidence:
    return MpzpParameterEvidence(
        name=name, normalized_value=value, raw_value=f"{value} m", unit="m",
        evidence_text=f"fragment uchwały dla {value} m", page_number=12, segment_id="§8", legal_unit_id=120,
        document_sha256=SHA, document_version_id=3, parser_version="mpzp-parser/3.0-det", extraction_method="pdf_text",
        confidence=0.8, conflict_group_id=group, manual_review_required=kind == "conflict",
        conditions=[MpzpValueCondition(kind=k, label=label, quote=quote) for k, label, quote in conditions],  # type: ignore[arg-type]
        value_kind=kind,  # type: ignore[arg-type]
    )


FLAT = ("roof_type", "dach płaski", "dachem płaskim")
STEEP = ("roof_type", "dach stromy", "dachem stromym")


def response_with(*parameters: MpzpParameterEvidence, flat_height: float | None = None) -> AnalyzeResponse:
    response, _ = load_fixture("multizone")
    zone = response.mpzp_zones[0].model_copy(update={"parameters": list(parameters), "max_building_height_m": flat_height})
    return response.model_copy(update={"mpzp_zones": [zone]})


def parameter_rows(response: AnalyzeResponse) -> dict[str, dict[str, Any]]:
    context = _mpzp_section_context(response)
    return {row["parameter"]: row for row in context["parameters"]}


def test_two_conditional_heights_are_one_row_with_both_values_and_conditions() -> None:
    response = response_with(parameter(8.0, FLAT), parameter(10.0, STEEP))
    row = parameter_rows(response)["Maksymalna wysokość zabudowy"]
    assert row["status"] == "conditional"
    assert row["value"] == f"brak jednej wartości dla całej strefy; warunkowo: 8{NB}m — dach płaski [E1]; 10{NB}m — dach stromy [E2]"
    assert row["refs"] == ["E1", "E2"]


def test_unconditional_value_is_shown_next_to_the_conditional_one() -> None:
    response = response_with(parameter(11.0), parameter(9.5, FLAT), flat_height=11.0)
    row = parameter_rows(response)["Maksymalna wysokość zabudowy"]
    assert row["status"] == "conditional"
    assert row["value"] == f"11{NB}m; warunkowo: 9,5{NB}m — dach płaski [E2]"


def test_conflict_is_reported_separately_from_conditional_values() -> None:
    group = "sha:MN.1:max_building_height_m"
    response = response_with(
        parameter(9.0, kind="conflict", group=group),
        parameter(12.0, kind="conflict", group=group),
        parameter(6.0, FLAT),
    )
    row = parameter_rows(response)["Maksymalna wysokość zabudowy"]
    assert row["status"] == "conflict"
    assert row["value"].startswith(f"wymaga weryfikacji — kandydaci: 9{NB}m [E1]; 12{NB}m [E2]")
    assert row["value"].endswith(f"; warunkowo: 6{NB}m — dach płaski [E3]")


def test_evidence_rows_carry_kind_condition_and_quote() -> None:
    context = _mpzp_section_context(response_with(parameter(8.0, FLAT, ("building_type", "budynki usługowe", "budynków usługowych"))))
    (evidence,) = context["evidence"]
    assert evidence["value_kind"] == "conditional" and evidence["conflict"] is False
    assert evidence["conditions"] == [
        {"kind": "roof_type", "kind_label": "rodzaj dachu", "label": "dach płaski", "quote": "dachem płaskim"},
        {"kind": "building_type", "kind_label": "rodzaj zabudowy", "label": "budynki usługowe", "quote": "budynków usługowych"},
    ]


def _html(response: AnalyzeResponse) -> BeautifulSoup:
    return BeautifulSoup(_render_report_html(_build_report_context(response)), "html.parser")


def test_html_shows_the_condition_tag_the_value_and_the_quote_in_the_evidence_table() -> None:
    soup = _html(response_with(parameter(8.0, FLAT), parameter(10.0, STEEP)))
    rows = soup.select('tr[data-row="mpzp-parameter"]')
    height = next(r for r in rows if "Maksymalna wysokość zabudowy" in r.get_text())
    assert "warunkowe" in height.get_text() and "dach płaski [E1]" in height.get_text()
    assert "sprzeczność" not in height.get_text()
    evidence = soup.select('tr[data-row="mpzp-evidence"]')
    assert len(evidence) == 2
    text = evidence[0].get_text(" ", strip=True)
    assert "warunkowa" in text and "rodzaj dachu: dach płaski" in text and "„dachem płaskim”" in text
    assert "sprzeczna kandydatura" not in text


def test_conflict_keeps_the_conflict_tag_in_html() -> None:
    group = "sha:MN.1:max_building_height_m"
    soup = _html(response_with(parameter(9.0, kind="conflict", group=group), parameter(12.0, kind="conflict", group=group)))
    assert "sprzeczność" in next(r for r in soup.select('tr[data-row="mpzp-parameter"]') if "Maksymalna wysokość zabudowy" in r.get_text()).get_text()
    assert "sprzeczna kandydatura" in soup.select('tr[data-row="mpzp-evidence"]')[0].get_text()


def test_limitations_mention_conditions_but_conflict_only_for_real_conflicts() -> None:
    conditional_only = _build_limitations(response_with(parameter(8.0, FLAT), parameter(10.0, STEEP)))
    assert any("wartości zależne od warunków" in item for item in conditional_only)
    assert not any("sprzeczne wartości parametrów MPZP" in item for item in conditional_only)
    group = "sha:MN.1:max_building_height_m"
    conflict = _build_limitations(response_with(parameter(9.0, kind="conflict", group=group), parameter(12.0, kind="conflict", group=group)))
    assert any("sprzeczne wartości parametrów MPZP" in item for item in conflict)


def test_snapshot_from_before_the_change_renders_as_plain_values() -> None:
    old = MpzpParameterEvidence.model_validate({
        "name": "max_building_height_m", "normalized_value": 9.0, "raw_value": "9 m", "unit": "m",
        "evidence_text": "wysokość 9 m", "page_number": 3, "confidence": 0.7,
    })
    row = parameter_rows(response_with(old, flat_height=9.0))["Maksymalna wysokość zabudowy"]
    assert row["status"] == "ok" and row["value"] == "9" and row["unit"] == "m"
