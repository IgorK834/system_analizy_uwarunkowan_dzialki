"""Raport PDF: oznaczenie odczytu automatycznego (model językowy), cytat, strona, warunki i provenance (PV3-18).

Sprawdza trzy poziomy: kontekst raportu, HTML i prawdziwy PDF (WeasyPrint + PyMuPDF, bez bazy). Wartość
deterministyczna nigdy nie jest oznaczana jako odczyt modelu, brak danych jest różny od braku ograniczenia,
a ``null`` jest różny od 0. Raport nie przedstawia odczytu jako interpretacji prawnej.
"""

from __future__ import annotations

from typing import Any

import pytest
from bs4 import BeautifulSoup

from app.schemas.analyze import AnalyzeResponse, MpzpParameterEvidence, MpzpValueCondition
from app.services.report import (
    _build_limitations,
    _build_report_context,
    _html_to_pdf,
    _manual_flags,
    _mpzp_section_context,
    _render_report_html,
)
from app.shared.model_reading import (
    MODEL_READING_DISCLAIMER,
    MODEL_READING_MARK,
    MODEL_READING_SHORT,
    NO_DATA_NOT_NO_RESTRICTION,
    NULL_NOT_ZERO,
    is_model_reading,
)
from tests.report_map_reference import load_fixture
from tests.test_report_v2 import _doc, _rows, _squash, _text, assert_clean_pages

NB = " "
SHA = "a" * 64
RESPONSE_SHA = "c0ffee" + "d" * 58
MODEL_ID = "gemini-3.8-flash"
PROMPT = "mpzp-extraction/1"
DOC_QUOTE = "zabudowa nie wyższa aniżeli 3 kondygnacje nadziemne"


def det(name: str, value: float, unit: str | None, raw: str, quote: str, page: int = 12) -> MpzpParameterEvidence:
    return MpzpParameterEvidence(
        name=name, normalized_value=value, raw_value=raw, unit=unit, evidence_text=quote, page_number=page,
        segment_id="§8", legal_unit_id=120, document_sha256=SHA, document_version_id=3,
        parser_version="mpzp-parser/3.0-det", extraction_method="pdf_text", confidence=0.9,
    )


def model(name: str, value: float, unit: str | None, raw: str, quote: str, page: int = 13,
          conditions: tuple[tuple[str, str, str], ...] = ()) -> MpzpParameterEvidence:
    return MpzpParameterEvidence(
        name=name, normalized_value=value, raw_value=raw, unit=unit, evidence_text=quote, page_number=page,
        segment_id="zb-1", document_sha256=SHA, document_version_id=3, parser_version="mpzp-parser/3.0-det",
        extraction_method="llm_verified", confidence=0.62, manual_review_required=True,
        review_status="ai_candidate", model_id=MODEL_ID, prompt_version=PROMPT, response_sha256=RESPONSE_SHA,
        conditions=[MpzpValueCondition(kind=k, label=label, quote=q) for k, label, q in conditions],  # type: ignore[arg-type]
    )


def mixed_response() -> AnalyzeResponse:
    """Strefa z wartością deterministyczną, samym odczytem modelu, rozbieżnością, zerem i brakiem danych."""
    response, _ = load_fixture("multizone")
    parameters = [
        det("max_building_height_m", 12.0, "m", "12 m", "maksymalna wysokość zabudowy: 12 m"),
        det("max_building_coverage_percent", 0.0, "percent", "0%", "powierzchnia zabudowy: 0%"),
        det("min_intensity", 0.4, None, "0,4", "wskaźnik intensywności nie mniejszy niż 0,4"),
        model("max_storeys", 3, None, "3 kondygnacje", DOC_QUOTE),
        model("min_intensity", 0.5, None, "0,5", "intensywność zabudowy co najmniej 0,5", page=14),
        model("roof_angle_min_deg", 30, "deg", "30°", "dachy strome o kącie nachylenia nie mniejszym niż 30°",
              conditions=(("roof_type", "dach stromy", "dachy strome"),)),
    ]
    zone = response.mpzp_zones[0].model_copy(update={
        "parameters": parameters, "max_building_height_m": 12.0, "max_building_coverage_pct": 0.0,
        "min_floor_area_ratio": 0.4, "max_floors": None, "min_biologically_active_pct": None,
        "max_floor_area_ratio": None,
    })
    return response.model_copy(update={"mpzp_zones": [zone]})


def deterministic_only() -> AnalyzeResponse:
    response, _ = load_fixture("multizone")
    zone = response.mpzp_zones[0].model_copy(update={"parameters": [
        det("max_building_height_m", 12.0, "m", "12 m", "maksymalna wysokość zabudowy: 12 m")]})
    return response.model_copy(update={"mpzp_zones": [zone]})


def norm(text: str) -> str:
    return text.replace(NB, " ")


def rows(response: AnalyzeResponse) -> dict[str, dict[str, Any]]:
    return {row["parameter"]: row for row in _mpzp_section_context(response)["parameters"]}


def soup_of(response: AnalyzeResponse) -> BeautifulSoup:
    return BeautifulSoup(_render_report_html(_build_report_context(response)), "html.parser")


# --- rozpoznanie odczytu modelu ------------------------------------------------------------------------------


def test_a_value_is_a_model_reading_only_by_status_or_method_never_by_look() -> None:
    assert is_model_reading(model("max_storeys", 3, None, "3", "q"))
    assert not is_model_reading(det("max_storeys", 3, None, "3", "q"))
    only_method = det("max_storeys", 3, None, "3", "q").model_copy(update={"extraction_method": "llm_verified"})
    assert is_model_reading(only_method)  # metoda llm_verified nie istnieje bez statusu — rozpoznanie jest obronne
    low_confidence_det = det("max_storeys", 3, None, "3", "q").model_copy(update={"confidence": 0.1})
    assert not is_model_reading(low_confidence_det)  # niska pewność nie czyni wartości odczytem modelu


def test_the_marker_text_is_the_agreed_wording() -> None:
    assert MODEL_READING_MARK == "odczyt automatyczny (model językowy), zweryfikowany z cytatem — wymaga potwierdzenia"
    assert "nie jest interpretacją prawną" in MODEL_READING_DISCLAIMER
    assert "nie oznacza braku ograniczenia" in NO_DATA_NOT_NO_RESTRICTION and "nie jest zerem" in NULL_NOT_ZERO


# --- kontekst raportu ----------------------------------------------------------------------------------------


def test_evidence_rows_carry_the_model_provenance_only_for_model_values() -> None:
    evidence = _mpzp_section_context(mixed_response())["evidence"]
    flags = [(row["parameter"], row["model_reading"]) for row in evidence]
    assert flags == [
        # Numeracja [E#] idzie po polach tabeli 3.2 (wysokość, kondygnacje, zabudowa, ..., intensywność), nie po liście.
        ("Maksymalna wysokość zabudowy", False), ("Maksymalna liczba kondygnacji nadziemnych", True),
        ("Maksymalny udział powierzchni zabudowy", False), ("Minimalny wskaźnik intensywności zabudowy", False),
        ("Minimalny wskaźnik intensywności zabudowy", True), ("Inny zapis uchwały: roof_angle_min_deg", True),
    ]
    for row in evidence:
        if row["model_reading"]:
            assert row["model"] == {"model_id": MODEL_ID, "prompt_version": PROMPT, "response_sha256": RESPONSE_SHA}
            assert row["extraction"] == MODEL_READING_SHORT and row["manual_review"] is True
        else:
            assert row["model"] is None and row["extraction"] != MODEL_READING_SHORT
    storeys = evidence[1]
    assert storeys["page"] == 13 and storeys["text"] == DOC_QUOTE and storeys["raw_value"] == "3 kondygnacje"


def test_a_model_value_without_a_deterministic_one_is_a_marked_candidate_not_a_missing_value() -> None:
    row = rows(mixed_response())["Maksymalna liczba kondygnacji nadziemnych"]
    assert row["status"] == "ai_candidate" and row["kind"] == "model_reading" and row["value"] is None
    assert row["model_candidates"] == f"3{NB}kondygn. [E2]" and row["refs"] == ["E2"]


def test_a_model_value_next_to_a_deterministic_one_is_a_separate_line_and_never_replaces_it() -> None:
    row = rows(mixed_response())["Minimalny wskaźnik intensywności zabudowy"]
    assert row["status"] == "ok" and row["value"] == "0,4" and row["kind"] == "source_fact"
    assert row["model_candidates"] == "0,5 [E5]" and row["refs"] == ["E4", "E5"]


def test_a_model_value_with_conditions_shows_them_and_unmapped_ones_have_their_own_kind() -> None:
    row = rows(mixed_response())["Inny zapis uchwały: roof_angle_min_deg"]
    assert row["status"] == "ai_candidate" and row["kind"] == "model_reading"
    assert row["model_candidates"] == f"30{NB}deg — dach stromy [E6]"
    evidence = _mpzp_section_context(mixed_response())["evidence"][5]
    assert evidence["conditions"] == [{"kind": "roof_type", "kind_label": "rodzaj dachu", "label": "dach stromy", "quote": "dachy strome"}]


def test_deterministic_rows_have_no_model_line_and_zero_and_null_stay_different() -> None:
    parameters = rows(mixed_response())
    height = parameters["Maksymalna wysokość zabudowy"]
    assert height["model_candidates"] is None and height["status"] == "ok" and height["value"] == "12"
    zero = parameters["Maksymalny udział powierzchni zabudowy"]
    assert zero["status"] == "ok" and zero["value"] == "0" and zero["model_candidates"] is None
    missing = parameters["Minimalny udział powierzchni biologicznie czynnej"]
    assert missing["status"] == "null" and missing["value"] is None and missing["model_candidates"] is None


def test_a_deterministic_only_zone_has_no_model_marker_anywhere() -> None:
    response = deterministic_only()
    context = _mpzp_section_context(response)
    assert context["model_reading"]["present"] is False
    assert all(row["model"] is None and row["model_reading"] is False for row in context["evidence"])
    assert all(row["model_candidates"] is None for row in context["parameters"])
    html = _render_report_html(_build_report_context(response))
    assert MODEL_READING_MARK not in html and "data-tag=\"model-reading\"" not in html
    assert not any("odczyt automatyczny" in item for item in _build_limitations(response))
    assert not any("odczyt automatyczny" in item for item in _manual_flags(response))


def test_limitations_and_manual_flags_state_that_the_reading_is_not_a_legal_interpretation() -> None:
    response = mixed_response()
    limitation = next(item for item in _build_limitations(response) if MODEL_READING_MARK in item)
    assert MODEL_READING_DISCLAIMER in limitation and "skrót odpowiedzi modelu" in limitation
    assert any("wymaga potwierdzenia w uchwale" in item for item in _manual_flags(response))


# --- HTML --------------------------------------------------------------------------------------------------


def test_html_marks_model_values_in_both_tables_and_leaves_deterministic_values_unmarked() -> None:
    soup = soup_of(mixed_response())
    parameter_rows = {r.find_all("td")[1].get_text(" ", strip=True): r for r in soup.select('tr[data-row="mpzp-parameter"]')}
    storeys = parameter_rows["Maksymalna liczba kondygnacji nadziemnych"]
    assert storeys.select('[data-tag="model-reading"]') and "3 kondygn. [E2]" in norm(storeys.get_text(" ", strip=True))
    assert "nie ustalono deterministycznie" in storeys.get_text() and "to nie jest interpretacja prawna" in storeys.get_text()
    for deterministic_label in ("Maksymalna wysokość zabudowy", "Maksymalny udział powierzchni zabudowy"):
        assert not parameter_rows[deterministic_label].select('[data-tag="model-reading"]')
    evidence = soup.select('tr[data-row="mpzp-evidence"]')
    marked = [row for row in evidence if row.select('[data-tag="model-reading"]')]
    assert len(evidence) == 6 and len(marked) == 3
    for row in marked:
        assert MODEL_READING_MARK in row.get_text(" ", strip=True)
        assert row.select_one("[data-model-provenance]") is not None
        assert f"SHA-256 odpowiedzi modelu: {RESPONSE_SHA}" in row.get_text(" ", strip=True)
        assert f"model: {MODEL_ID}; wersja instrukcji: {PROMPT}" in row.get_text(" ", strip=True)
    for row in evidence:
        if row not in marked:
            assert "SHA-256 odpowiedzi modelu" not in row.get_text() and MODEL_READING_MARK not in row.get_text()


def test_html_shows_the_quote_the_page_and_the_conditions_of_a_model_value() -> None:
    soup = soup_of(mixed_response())
    row = next(r for r in soup.select('tr[data-row="mpzp-evidence"]') if "kondygnacji" in r.get_text())
    text = row.get_text(" ", strip=True)
    assert f"„{DOC_QUOTE}”" in norm(text) and "str. 13" in norm(text) and "dosłownie: „3 kondygnacje”" in norm(text)
    roof = next(r for r in soup.select('tr[data-row="mpzp-evidence"]') if "roof_angle_min_deg" in r.get_text())
    assert "rodzaj dachu: dach stromy — „dachy strome”" in norm(roof.get_text(" ", strip=True))


def test_html_has_the_disclaimer_the_no_data_note_and_the_kind_legend() -> None:
    soup = soup_of(mixed_response())
    disclaimer = soup.select_one('[data-note="model-reading"]')
    assert disclaimer is not None and MODEL_READING_DISCLAIMER in disclaimer.get_text(" ", strip=True)
    note = soup.select_one('[data-note="mpzp-no-data"]')
    assert note is not None and NO_DATA_NOT_NO_RESTRICTION in note.get_text(" ", strip=True)
    assert NULL_NOT_ZERO in note.get_text(" ", strip=True)
    assert "odczyt automatyczny" in soup.get_text()  # legenda rodzajów ustaleń zawiera nowy rodzaj
    assert soup_of(deterministic_only()).select_one('[data-note="model-reading"]') is None


# --- PDF ---------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pdf_pair() -> tuple[str, bytes]:
    html = _render_report_html(_build_report_context(mixed_response()))
    return html, _html_to_pdf(html)


def test_the_pdf_contains_the_marker_the_quote_the_provenance_and_the_disclaimer(pdf_pair: tuple[str, bytes]) -> None:
    _, pdf = pdf_pair
    squashed = _squash(_text(pdf))
    assert _squash(MODEL_READING_MARK) in squashed
    assert _squash(f"„{DOC_QUOTE}”") in squashed
    assert _squash(f"model: {MODEL_ID}; wersja instrukcji: {PROMPT}") in squashed
    assert _squash(f"SHA-256 odpowiedzi modelu: {RESPONSE_SHA}") in squashed
    assert _squash(MODEL_READING_DISCLAIMER) in squashed
    assert _squash("Maksymalna liczba kondygnacji nadziemnych") in squashed and _squash("3 kondygn. [E2]") in squashed
    assert _squash(NULL_NOT_ZERO) in squashed and _squash(NO_DATA_NOT_NO_RESTRICTION) in squashed


def test_the_pdf_table_rows_keep_zero_null_and_the_model_candidate_apart(pdf_pair: tuple[str, bytes]) -> None:
    html, pdf = pdf_pair
    table = {(row[0], row[1]): row[2] for row in _rows(html, "mpzp-parameter")}
    zone = next(iter({key[0] for key in table}))
    assert table[(zone, "Maksymalny udział powierzchni zabudowy")] == "0%"
    assert table[(zone, "Minimalny udział powierzchni biologicznie czynnej")] == "nie określono"
    assert table[(zone, "Maksymalna liczba kondygnacji nadziemnych")].startswith("nie ustalono deterministycznie")
    squashed = _squash(_text(pdf))
    assert _squash("Maksymalny udział powierzchni zabudowy 0% [E3]") in squashed


def test_the_pdf_layout_is_clean_with_the_new_elements(pdf_pair: tuple[str, bytes]) -> None:
    html, pdf = pdf_pair
    assert_clean_pages(html, pdf)
    with _doc(pdf) as doc:
        assert doc.page_count >= 10


def test_the_deterministic_pdf_has_no_model_marker() -> None:
    html = _render_report_html(_build_report_context(deterministic_only()))
    text = _squash(_text(_html_to_pdf(html)))
    assert _squash(MODEL_READING_MARK) not in text and _squash("SHA-256 odpowiedzi modelu") not in text
    assert _squash(MODEL_READING_DISCLAIMER) not in text


# --- spójność oznaczenia z interfejsem ----------------------------------------------------------------------


def test_the_frontend_uses_the_same_marker_wording_as_the_pdf() -> None:
    from tests.parcel_fixtures_config import find_repo_root

    source = (find_repo_root() / "frontend" / "lib" / "mpzpProvenance.ts").read_text(encoding="utf-8")
    assert MODEL_READING_MARK in source and MODEL_READING_SHORT in source
    assert MODEL_READING_DISCLAIMER in source and NO_DATA_NOT_NO_RESTRICTION in source and NULL_NOT_ZERO in source
