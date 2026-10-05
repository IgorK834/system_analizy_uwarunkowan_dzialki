"""Resolver zakresu strefy: strategie 0–6, bramkowanie list, klauzule i korpus (PV3-06)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.modules.documents.domain.document_tree import build_document_tree
from app.modules.planning.domain.zone_blocks import (
    ZONE_SCOPE_AMBIGUOUS,
    DocumentStructureView,
    ZoneBlock,
    blocks_digest,
    blocks_from_json,
    blocks_to_json,
)
from app.modules.planning.domain.zone_scope import (
    ZONE_SCOPE_MULTIPLE_SECTIONS,
    ZONE_SECTION_NOT_FOUND,
    ZONE_SYMBOL_OCR_MATCH,
    ScopeResolution,
    resolve_zone_scope,
)
from app.services.mpzp_parser_structure import structure_view
from scripts import evaluate_mpzp_parser as ev
from tests.parcel_fixtures_config import find_repo_root

FIXTURES = find_repo_root() / "backend/tests/fixtures/mpzp_evaluation"


def _view(pages: list[str], **kwargs: Any) -> DocumentStructureView:
    return structure_view(build_document_tree(pages, **kwargs))


def _only(resolution: ScopeResolution, symbol: str) -> ZoneBlock:
    blocks = resolution.blocks_for(symbol)
    assert len(blocks) == 1, [(b.scope_kind, b.strategy, b.path) for b in blocks]
    return blocks[0]


# --- 1. osobny § na strefę ----------------------------------------------------------------------------


def test_strategy_1_gives_each_zone_its_own_paragraph_and_never_mixes_them() -> None:
    view = _view([
        "§ 5. Dla terenu oznaczonego symbolem A ustala się:\n1) wysokość zabudowy do 10 m;\n2) powierzchnia zabudowy do 40%.\n"
        "§ 6. Dla terenu oznaczonego symbolem B ustala się:\n1) wysokość zabudowy do 12 m.\n"
    ])
    resolution = resolve_zone_scope(view, ["A", "B"])
    a, b = _only(resolution, "A"), _only(resolution, "B")
    assert (a.strategy, a.scope_kind, a.path, a.scope_confidence) == (1, "zone_section", "§5", 0.9)
    assert "10 m" in a.text and "12 m" not in a.text and "10 m" not in b.text and "12 m" in b.text
    assert a.char_span[1] == b.char_span[0] and a.strategy_reason == "osobny § (ustęp) na strefę"
    assert resolution.warnings_for("A") == () and a.pages == (1,)


def test_the_first_section_of_a_paragraph_without_own_text_makes_the_whole_paragraph_the_zone() -> None:
    view = _view([
        "§ 36. 1. Teren oznaczony na rysunku planu symbolem 1.4U,MN przeznacza się pod zabudowę.\n"
        "2. Ustala się wysokość zabudowy do 10 m.\n3. Dopuszcza się garaże.\n"
        "§ 37. Ustalenia końcowe: wysokość 99 m."
    ])
    block = _only(resolve_zone_scope(view, ["1.4U,MN"]), "1.4U,MN")
    assert block.path == "§36" and "10 m" in block.text and "99 m" not in block.text and block.strategy == 1


def test_sections_directly_under_a_chapter_run_to_the_next_zone_introduction() -> None:
    view = _view([
        "ROZDZIAŁ II\nUstalenia szczegółowe\n"
        "1. Dla terenów oznaczonych symbolami MN ustala się:\n1) funkcja mieszkaniowa;\n2) wysokość 9 m.\n"
        "2. Zasady formy: dachy dwuspadowe, wysokość kalenicy 7 m.\n"
        "3. Zasady podziału: działki 800 m2.\n"
        "1. Dla terenu oznaczonego symbolem KDW ustala się:\n1) szerokość 5 m.\n"
    ])
    mn, kdw = _only(resolve_zone_scope(view, ["MN"]), "MN"), _only(resolve_zone_scope(view, ["KDW"]), "KDW")
    assert "kalenicy 7 m" in mn.text and "działki 800 m2" in mn.text and "szerokość 5 m" not in mn.text
    assert "ustępy do następnego wprowadzenia" in mn.strategy_reason
    assert "szerokość 5 m" in kdw.text and "kalenicy" not in kdw.text


# --- 2. wspólny § dla listy lub zakresu symboli ------------------------------------------------------------------


def test_strategy_2_shared_paragraph_is_one_block_for_all_listed_symbols() -> None:
    view = _view(["§ 7. Dla terenów oznaczonych symbolami C, D i E ustala się:\n1) wysokość zabudowy do 9 m.\n§ 8. Inne."])
    resolution = resolve_zone_scope(view, ["C", "D", "X"])
    block = _only(resolution, "C")
    assert block is _only(resolution, "D") and block.symbols == ("C", "D")
    assert (block.strategy, block.scope_kind, block.scope_confidence) == (2, "zone_section", 0.8)
    assert resolution.blocks_for("X") == () and resolution.warnings_for("X") == (ZONE_SECTION_NOT_FOUND,)
    assert len(resolution.blocks) == 1


def test_a_symbol_inside_a_range_gets_the_shared_block_without_being_named() -> None:
    view = _view([
        "§ 14. W zakresie zasad zabudowy terenów oznaczonych na rysunku planu symbolami terenu: 1MN-U – 5MN-U:\n"
        "1) ustala się: wysokość 8 m.\n§ 15. Dla terenu oznaczonego symbolem 9MN-U ustala się: wysokość 3 m."
    ])
    resolution = resolve_zone_scope(view, ["3MN-U", "1MN-U", "6MN-U", "9MN-U"])
    assert _only(resolution, "3MN-U") is _only(resolution, "1MN-U") and _only(resolution, "3MN-U").strategy == 2
    assert resolution.blocks_for("6MN-U") == ()  # poza zakresem
    assert _only(resolution, "9MN-U").strategy == 1 and "3 m" in _only(resolution, "9MN-U").text


def test_a_shared_clause_becomes_general_when_the_zone_has_its_own_section() -> None:
    view = _view([
        "§ 17. 1. Dla terenów MN.1 – MN.11 ustala się zakaz zabudowy bliźniaczej, wysokość 5 m ogrodzeń.\n"
        "4. Ustala się wskaźniki:\n1) dla terenu MN.1:\na) wysokość 10 m;\n2) dla terenu MN.2:\na) wysokość 11 m;\n"
    ])
    resolution = resolve_zone_scope(view, ["MN.2"])
    kinds = {(b.scope_kind, b.strategy) for b in resolution.blocks_for("MN.2")}
    assert kinds == {("general_clause", 5), ("zone_section", 3)}
    section = next(b for b in resolution.blocks_for("MN.2") if b.scope_kind == "zone_section")
    general = next(b for b in resolution.blocks_for("MN.2") if b.scope_kind == "general_clause")
    assert "11 m" in section.text and "10 m" not in section.text and "5 m" not in section.text
    assert "5 m ogrodzeń" in general.text and "11 m" not in general.text  # klauzula wspólna nie miesza sekcji strefy


# --- 3. numerowane podpunkty „N) dla terenu X:” ---------------------------------------------------------------------


def test_strategy_3_numbered_points_are_separate_blocks_with_their_pages() -> None:
    view = _view(
        ["§ 17. 4. Ustala się wskaźniki:\n1) dla terenu M1:\na) wysokość 10 m,\nb) zabudowa 30%;\n2) dla terenu M2:\na) wysokość",
         "12 m,\nb) zabudowa 35%;\n3) dla terenu M3:\na) wysokość 14 m."],
        page_numbers=[18, 19],
    )
    resolution = resolve_zone_scope(view, ["M1", "M2", "M3"])
    m1, m2, m3 = (_only(resolution, s) for s in ("M1", "M2", "M3"))
    assert [b.strategy for b in (m1, m2, m3)] == [3, 3, 3] and m2.path == "§17 ust.4 pkt 2"
    assert m2.pages == (18, 19) and m3.pages == (19,)
    assert "12 m" in m2.text and "10 m" not in m2.text and "14 m" not in m2.text


# --- 4. wartości per symbol w liście: bramkowanie, klauzula resztowa, zakres nierozstrzygnięty ----------------------------


_LIST = (
    "§ 1. 1. Dla terenów oznaczonych na rysunku planu symbolami: A1, B2 i C3 obowiązują ustalenia poniżej.\n"
    "2. Ustala się:\n"
    "1) wskaźnik zabudowy:\n"
    "- w terenie A1 - maksimum 55%,\n"
    "- w terenie B2 - maksimum 45%,\n"
    "- w pozostałych terenach - maksimum 50%,\n"
    "2) wysokość:\n"
    "- w strefie SWZ 22 oznaczonej na rysunku planu - maksimum 22,0 m,\n"
    "- w terenach A1 i C3 - maksimum 12,0 m,\n"
)


def test_strategy_4_keeps_only_the_list_items_that_name_the_symbol() -> None:
    resolution = resolve_zone_scope(_view([_LIST]), ["A1", "B2"])
    a1 = next(b for b in resolution.blocks_for("A1") if b.scope_kind == "zone_section")
    assert "maksimum 55%" in a1.text and "maksimum 45%" not in a1.text and "maksimum 50%" not in a1.text
    assert "maksimum 12,0 m" in a1.text and "22,0 m" not in a1.text  # pozycja „w terenach A1 i C3” dotyczy A1
    assert not a1.is_contiguous and a1.scope_kind == "zone_section" and a1.strategy == 2
    b2 = next(b for b in resolution.blocks_for("B2") if b.scope_kind == "zone_section")
    assert "maksimum 45%" in b2.text and "maksimum 55%" not in b2.text and "12,0 m" not in b2.text
    assert [b.scope_kind for b in resolution.blocks_for("B2")].count("residual_clause") == 0  # B2 występuje wprost


def test_residual_clause_applies_only_when_the_symbol_is_not_named_and_is_never_a_zone_section() -> None:
    resolution = resolve_zone_scope(_view([_LIST]), ["C3"])
    kinds = [b.scope_kind for b in resolution.blocks_for("C3")]
    assert kinds.count("residual_clause") == 1 and kinds.count("zone_section") == 1
    residual = next(b for b in resolution.blocks_for("C3") if b.scope_kind == "residual_clause")
    section = next(b for b in resolution.blocks_for("C3") if b.scope_kind == "zone_section")
    assert "w pozostałych terenach - maksimum 50%" in residual.text and "wskaźnik zabudowy" in residual.text
    assert "pozostałych terenach" not in section.text  # klauzula resztowa nie trafia do sekcji strefy
    assert residual.strategy == 4 and residual.scope_confidence == 0.6


def test_items_that_depend_on_the_drawing_are_unresolved_with_a_warning() -> None:
    resolution = resolve_zone_scope(_view([_LIST]), ["A1"])
    unresolved = next(b for b in resolution.blocks_for("A1") if b.scope_kind == "fallback")
    assert "SWZ 22" in unresolved.text and ZONE_SCOPE_AMBIGUOUS in unresolved.warnings and unresolved.scope_confidence < 0.4
    section = next(b for b in resolution.blocks_for("A1") if b.scope_kind == "zone_section")
    assert "SWZ" not in section.text  # nierozstrzygnięty zakres nie jest cicho dołączony do sekcji strefy


# --- 5. klauzula ogólna per symbol ---------------------------------------------------------------------------------------


def test_strategy_5_general_clause_is_separate_from_the_zone_section() -> None:
    view = _view([
        "§ 2. 1. Dla terenu oznaczonego symbolem MN ustala się:\n1) wysokość zabudowy do 9 m.\n"
        "§ 11. Ustala się maksymalny wskaźnik powierzchni zabudowy, dla terenów oznaczonych symbolami:\n"
        "a) MN – 60 % powierzchni działki,\nb) US – 50 % powierzchni działki,\n"
    ])
    resolution = resolve_zone_scope(view, ["MN", "US"])
    mn = {b.scope_kind: b for b in resolution.blocks_for("MN")}
    assert set(mn) == {"zone_section", "general_clause"} and mn["general_clause"].strategy == 5
    assert "60 %" in mn["general_clause"].text and "50 %" not in mn["general_clause"].text
    assert "60 %" not in mn["zone_section"].text and mn["general_clause"].text.startswith("§ 11. Ustala się maksymalny")
    us = resolution.blocks_for("US")
    assert [b.scope_kind for b in us] == ["general_clause"]  # US nie ma własnej sekcji w tym fragmencie
    assert not us[0].is_contiguous and "50 %" in us[0].text and "60 %" not in us[0].text


# --- 6. tabela ---------------------------------------------------------------------------------------------------------------


def test_strategy_6_table_rows_belong_to_the_section_that_introduces_the_symbols() -> None:
    page = ("§ 9. 1. Ustalenia dla terenów oznaczonych symbolami 1UZ i 2UZ:\nWskaźniki\nnadziemna intensywność zabudowy 0,15 – 2,50\n"
            "maksymalna wysokość budynku 16 m\n§ 10. 1. Dla terenu oznaczonego symbolem 3UZ ustala się:\nwysokość 8 m\n")
    tables = [(1, [["nadziemna intensywność zabudowy", "0,15 – 2,50"], ["maksymalna wysokość budynku", "16 m"]])]
    view = _view([page], tables=tables)
    resolution = resolve_zone_scope(view, ["1UZ", "2UZ", "3UZ"])
    block = _only(resolution, "1UZ")
    assert block is _only(resolution, "2UZ") and block.strategy == 6 and "16 m" in block.text and "8 m" not in block.text
    assert block.scope_confidence == 0.75 and "wiersze tabeli" in block.strategy_reason
    assert _only(resolution, "3UZ").strategy == 1


# --- 0. zapas, OCR, wielokrotne sekcje -----------------------------------------------------------------------------------------


def test_strategy_0_unstructured_mentions_return_the_whole_paragraph_with_a_warning() -> None:
    view = _view(["§ 3. Zgodnie z rysunkiem teren MN jest przeznaczony pod zabudowę. Wysokość 9 m.\n§ 4. Inny."])
    resolution = resolve_zone_scope(view, ["MN"])
    block = _only(resolution, "MN")
    assert (block.scope_kind, block.strategy, block.scope_confidence) == ("fallback", 0, 0.3)
    assert ZONE_SCOPE_AMBIGUOUS in block.warnings and ZONE_SCOPE_AMBIGUOUS in resolution.warnings_for("MN")
    assert block.path == "§3" and "Inny" not in block.text


def test_an_enumeration_line_is_not_an_introduction() -> None:
    view = _view(["§ 4. Typy terenów:\n8) teren elektroenergetyki, dla terenu oznaczonego symbolem 1IE;\n9) teren gazownictwa, GZ."])
    block = _only(resolve_zone_scope(view, ["1IE"]), "1IE")
    assert block.strategy == 0  # wyliczenie bez zdania wprowadzającego nie jest sekcją strefy


def test_ocr_confusions_only_lower_confidence_and_warn() -> None:
    view = _view(["§ 16. Dla terenu oznaczonego symbolem AIMN, ustala się:\n1) wysokość 10 m.\n"])
    resolution = resolve_zone_scope(view, ["A1MN"])
    block = _only(resolution, "A1MN")
    assert block.strategy == 1 and block.scope_confidence == pytest.approx(0.9 * 0.75)
    assert ZONE_SYMBOL_OCR_MATCH in resolution.warnings_for("A1MN") and ZONE_SYMBOL_OCR_MATCH in block.warnings
    exact = resolve_zone_scope(_view(["§ 16. Dla terenu oznaczonego symbolem A1MN, ustala się:\n1) wysokość 10 m.\n"]), ["A1MN"])
    assert _only(exact, "A1MN").scope_confidence == 0.9 and exact.warnings_for("A1MN") == ()


def test_two_separate_sections_for_one_symbol_are_reported_not_chosen() -> None:
    view = _view(["§ 5. Dla terenu oznaczonego symbolem A ustala się: wysokość 10 m.\n§ 6. Dla terenu oznaczonego symbolem A ustala się: wysokość 12 m."])
    resolution = resolve_zone_scope(view, ["A"])
    assert len(resolution.blocks_for("A")) == 2 and ZONE_SCOPE_MULTIPLE_SECTIONS in resolution.warnings_for("A")


def test_similar_symbols_are_not_merged_and_a_spaced_symbol_is_not_a_zone_of_its_tail() -> None:
    view = _view(["§ 1. Dla terenu oznaczonego symbolem MN.1 ustala się: wysokość 10 m.\n§ 2. Dla terenu oznaczonego symbolem 146 MN ustala się: wysokość 7 m."])
    resolution = resolve_zone_scope(view, ["MN", "MN.1", "146MN"])
    assert resolution.blocks_for("MN") == () and resolution.warnings_for("MN") == (ZONE_SECTION_NOT_FOUND,)
    assert "10 m" in _only(resolution, "MN.1").text and "7 m" in _only(resolution, "146MN").text


# --- determinizm i serializacja ---------------------------------------------------------------------------------------------------


def test_blocks_are_deterministic_ordered_and_serializable() -> None:
    first = resolve_zone_scope(_view([_LIST]), ["A1", "B2", "C3"])
    second = resolve_zone_scope(_view([_LIST]), ["A1", "B2", "C3"])
    assert blocks_digest(first.blocks) == blocks_digest(second.blocks) and first == second
    assert [b.char_span[0] for b in first.blocks] == sorted(b.char_span[0] for b in first.blocks)
    assert blocks_from_json(blocks_to_json(first.blocks)) == first.blocks
    reversed_order = resolve_zone_scope(_view([_LIST]), ["C3", "B2", "A1"])
    assert blocks_digest(reversed_order.blocks) == blocks_digest(first.blocks)  # kolejność żądania nie zmienia bloków


def test_block_locate_maps_text_positions_to_the_document_across_segments() -> None:
    view = _view([_LIST])
    block = next(b for b in resolve_zone_scope(view, ["A1"]).blocks_for("A1") if b.scope_kind == "zone_section")
    needle = "maksimum 55%"
    position = block.text.index(needle)
    (start, end), = block.locate(position, position + len(needle))
    assert view.text[start:end] == needle
    assert block.locate(0, 0) == () and all(view.text[a:b] for a, b in block.segments)
    with pytest.raises(ValueError):
        ZoneBlock.from_dict({**block.to_dict(), "segments": [[block.char_span[0], block.char_span[0] + 3]]})


# --- korpus (zbiór rozwojowy): zasięg, zanieczyszczenie i źródło -----------------------------------------------------------------------


@pytest.fixture(scope="module")
def corpus_evaluation() -> dict[str, Any]:
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    return ev.evaluate(manifest, FIXTURES, engine=ev.V3Engine())


def test_corpus_scope_coverage_is_at_least_95_percent_per_layout(corpus_evaluation: dict[str, Any]) -> None:
    """Zbiór rozwojowy: resolver powstał na tych samych próbkach, więc to wynik rozwojowy, nie uogólnienia."""
    scope = corpus_evaluation["metrics"]["scope"]
    assert scope["reported"] and scope["coverage"]["value"] >= 0.95
    labels = scope["by_label"]
    assert {"1", "2", "3", "4", "5", "6"} <= set(labels)
    for label in ("1", "2", "3", "4", "5", "6"):
        assert labels[label]["value"] >= 0.95, (label, labels[label])


def test_corpus_has_zero_cross_zone_contamination_in_zone_sections(corpus_evaluation: dict[str, Any]) -> None:
    contamination = corpus_evaluation["metrics"]["scope"]["contamination"]
    assert contamination["numerator"] == 0 and contamination["denominator"] > 300


def test_corpus_values_come_from_their_own_block_not_from_another_zones_section(corpus_evaluation: dict[str, Any]) -> None:
    detection = corpus_evaluation["metrics"]["overall"]["detection"]
    counts = detection["source_status_counts"]
    assert counts.get("wrong_page", 0) == 0 and counts.get("unlocatable", 0) == 0
    # Od PV3-07 silnik zwraca także POWTÓRZENIA tej samej wartości w dalszych punktach sekcji strefy
    # (np. „wiaty, altany: wysokość 6,0 m” po „budynki gospodarcze: 6,0 m”). Adnotacja wskazuje jedno
    # miejsce, a blok źródła kończy się 300 znaków za nim, więc takie powtórzenie leży „poza blokiem
    # adnotacji” choć jest w tej samej sekcji tej samej strefy. To artefakt miary, nie przeciek między
    # strefami: przeciek mierzy ``cross_zone_errors`` i kontaminacja (testy wyżej), oba równe 0.
    assert detection["source_consistent"]["value"] >= 0.95
    assert counts.get("wrong_block", 0) <= 9 and counts.get("consistent", 0) >= 350
    # Blok zapasowy (strategia 0: skan bez struktury) czyta wartości z całego dokumentu: takie wartości
    # mają ``manual_review_required`` i ``scope_kind="fallback"``. Wartość z innej strony BEZ tej flagi
    # byłaby błędem zakresu.
    unflagged_wrong = [
        output
        for row in corpus_evaluation["rows"]
        for output in row["parser_outputs"]
        if output["source_check"]["status"] in {"wrong_page", "wrong_block"} and not output["manual_review_required"]
    ]
    assert len(unflagged_wrong) <= 9
    assert detection["cross_zone_errors"] == 0


def test_corpus_blocks_are_identical_on_every_build() -> None:
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    for doc_id in ("krakow_morelowa", "lodz_lxxviii_2337_23", "raszkow_xxxvi_242_2021"):
        loaded = ev.load_document(FIXTURES, manifest["documents"][doc_id])
        symbols = [z["symbol"] for s in manifest["samples"] if s["document_id"] == doc_id for z in s["zones"]]
        digests = {
            blocks_digest(resolve_zone_scope(structure_view(build_document_tree(loaded["pages"], page_numbers=loaded["page_numbers"])), symbols).blocks)
            for _ in range(2)
        }
        assert len(digests) == 1
