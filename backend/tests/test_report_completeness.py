"""Strona 1 raportu PDF: kompletność analizy (AU-104) — ten sam komunikat i te same stany co w API i UI."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from app.schemas.analyze import AnalyzeResponse
from app.services.completeness import with_completeness
from app.services.report import _build_report_context, _render_report_html
from tests.report_map_reference import load_fixture
from tests.test_completeness import _base

NBSP = chr(0xA0)


def _cover(response: AnalyzeResponse) -> BeautifulSoup:
    html = _render_report_html(_build_report_context(response, None))
    return BeautifulSoup(html, "html.parser").select_one("section.cover")


def _text(node) -> str:  # noqa: ANN001
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True).replace(NBSP, " "))


def _complete() -> AnalyzeResponse:
    return with_completeness(_base())


def test_cover_shows_the_same_headline_as_the_api() -> None:
    response = _complete()

    cover = _cover(response)

    row = cover.select_one("td[data-completeness]")
    assert row is not None and row["data-completeness"] == "complete"
    assert _text(row) == response.headline
    assert "Sprawdzono wszystkie 11 sekcji" in _text(row)


def test_cover_lists_every_section_with_its_state_basis_and_source_status() -> None:
    response = _complete()

    table = _cover(response).select_one('table[data-table="completeness"]')

    rows = [[_text(cell) for cell in tr.find_all("td")] for tr in table.select("tbody tr")]
    assert len(rows) == 12  # także sekcje poza zakresem są wymienione
    by_label = {row[0]: row for row in rows}
    assert by_label["MPZP"][1].startswith("sprawdzono") and "lokalnym wektorem" in by_label["MPZP"][2]
    assert by_label["Uzbrojenie terenu (KIUT)"][1] == "poza zakresem"
    assert by_label["Transport i dostęp do drogi"][1] == "sprawdzono"  # od Task 23.2 w zakresie (BDOT10k)
    assert by_label["Zabudowa sąsiednia"][1] == "sprawdzono"
    assert all("status źródła:" in row[2] for row in rows)


def test_incomplete_analysis_says_what_is_missing_on_the_first_page() -> None:
    response = with_completeness(_complete().model_copy(update={"terrain": None, "completeness": None, "section_quality": None}))

    cover = _cover(response)

    row = cover.select_one("td[data-completeness]")
    assert row["data-completeness"] == "partial"
    assert "10 z 11 sekcji sprawdzono; brak: teren (NMT)." in _text(row)
    terrain = next(tr for tr in cover.select("table[data-table=completeness] tbody tr") if "Teren" in _text(tr.find("td")))
    assert _text(terrain.find_all("td")[1]).startswith("brak")


def test_notes_about_the_missing_vector_and_the_utilities_preview_are_on_the_cover() -> None:
    response = _complete()
    candidates = [zone.model_copy(update={"assignment_method": "document_candidate"}) for zone in response.mpzp_zones]
    response = with_completeness(response.model_copy(update={"mpzp_zones": candidates, "completeness": None, "section_quality": None}))

    notes = [_text(node) for node in _cover(response).select("p.note")]

    assert any("document_candidate" in note and "bez lokalnego wektora" in note for note in notes)
    assert any("podgląd pokrycia KIUT" in note for note in notes)


def test_status_label_no_longer_claims_that_complete_needs_a_vector() -> None:
    cover = _cover(_complete())

    text = _text(cover)
    assert "wszystkie sekcje w zakresie sprawdzono" in text
    assert "przecięcia wektorowego" not in text


def test_record_without_completeness_keeps_the_old_cover_without_the_block() -> None:
    response, _ = load_fixture("multizone")
    legacy = response.model_copy(update={"completeness": None, "headline": None})

    cover = _cover(legacy)

    assert cover.select_one("td[data-completeness]") is None
    assert cover.select_one("table[data-table=completeness]") is None
