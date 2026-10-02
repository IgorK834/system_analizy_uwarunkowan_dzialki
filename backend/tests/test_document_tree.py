"""Drzewo struktury dokumentu, normalizacja tekstu i model ``ZoneBlock`` (PV3-05)."""

from __future__ import annotations

import json
import re

import pytest
from bs4 import BeautifulSoup

from app.modules.documents.domain.document_tree import DocumentTree, build_document_tree
from app.modules.documents.domain.parser_models import StructureHint
from app.modules.documents.domain.text_normalization import normalize_document
from app.modules.planning.domain.zone_blocks import (
    ZoneBlock,
    block_from_node,
    block_from_span,
    blocks_digest,
    blocks_from_json,
    blocks_to_json,
)
from app.services.mpzp_parser_extract import (
    DocumentBlob,
    ExtractedTable,
    TextExtractionResult,
    extract_document_text,
)
from app.services.mpzp_parser_segment import segment_document
from app.services.mpzp_parser_structure import build_tree_from_extraction, structure_view
from tests.parcel_fixtures_config import find_repo_root

CORPUS = find_repo_root() / "backend/tests/fixtures/mpzp_evaluation"
MANIFEST = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))


def _load(doc_id: str) -> tuple[list[str], list[int] | None, list[tuple[int, list[list[str | None]]]]]:
    directory = (CORPUS / MANIFEST["documents"][doc_id]["path"]).resolve()
    pages = json.loads((directory / "pages.json").read_text(encoding="utf-8"))["pages"]
    numbers = json.loads((directory / "source.json").read_text(encoding="utf-8")).get("source_page_numbers")
    tables_path = directory / "tables.json"
    tables = (
        [(item["page_number"], item["rows"]) for item in json.loads(tables_path.read_text(encoding="utf-8"))]
        if tables_path.is_file()
        else []
    )
    return pages, numbers, tables


def _tree(doc_id: str) -> DocumentTree:
    pages, numbers, tables = _load(doc_id)
    return build_document_tree(pages, page_numbers=numbers, tables=tables)


# --- normalizacja: surowy tekst niezmienny, odstępy ujednolicone, artefakty tylko flagowane ---------


def test_normalization_keeps_raw_text_and_maps_every_character_back() -> None:
    raw = "§\u00a01.\u00a0Teren  X\t:\r\n   a) 300\u200b m  \nkoniec"
    document = normalize_document([raw])
    page = document.pages[0]
    assert page.raw == raw  # niezmienny
    assert page.text == "§ 1. Teren X :\na) 300 m\nkoniec\n"
    assert len(page.origin) == len(page.text) and list(page.origin) == sorted(page.origin)
    for index, char in enumerate(page.text[:-1]):
        raw_char = raw[page.origin[index]]
        assert raw_char == char or (char == " " and raw_char.isspace()) or (char == "\n" and raw_char in "\r\n")
    assert page.origin[-1] == len(raw)  # końcowy znak nowej linii jest syntetyczny
    assert document.stat("space_variants_replaced") >= 3 and document.stat("format_characters_removed") == 1


def test_raw_spans_return_text_positions_per_page_and_skip_synthetic_newlines() -> None:
    document = normalize_document(["ab  cd", "ef"], page_numbers=[7, 8])
    assert document.text == "ab cd\nef\n"
    assert document.raw_spans(3, 5) == ((7, 4, 6),)  # „cd” w surowym „ab  cd”
    assert document.raw_spans(3, 8) == ((7, 4, 6), (8, 0, 2))  # zakres przez granicę stron
    assert document.raw_spans(5, 6) == ()  # tylko znak syntetyczny
    assert document.raw_spans(4, 4) == ()
    assert [document.page_number_at(offset) for offset in (0, 5, 6, 8)] == [7, 7, 8, 8]
    assert document.page_span(8) == (6, 9)


def test_artifacts_are_flagged_and_never_fixed_silently() -> None:
    text = "Kąt nachylenia połaci dachowych od 300 do 450. Wysokość 300 m. Dach 30o oraz budyn-\nków."
    document = normalize_document([text])
    assert "300" in document.text and "30o" in document.text  # tekst nietknięty
    kinds = {(flag.kind, flag.suggestion) for flag in document.flags}
    assert ("degree_sign_as_zero", "30°") in kinds and ("degree_sign_as_zero", "45°") in kinds
    assert ("degree_sign_as_letter", "30°") in kinds and any(kind == "line_hyphenation" for kind, _ in kinds)
    flagged = [document.text[flag.start : flag.end] for flag in document.flags if flag.kind == "degree_sign_as_zero"]
    assert "300 m" not in " ".join(flagged) and len(flagged) == 2  # „300 m” to wysokość, nie kąt
    spaced = normalize_document(["w y s o k o ś ć zabudowy"])
    assert [f.kind for f in spaced.flags] == ["spaced_letters"] and spaced.flags[0].suggestion == "wysokość"
    broken = normalize_document(["tekst \ufffd\ufffd uszkodzony"])
    assert [f.kind for f in broken.flags] == ["replacement_character"] * 2
    assert normalize_document(["Powierzchnia 300 m² oraz 450 zł"]).flags == ()  # bez kontekstu dachu nie flagujemy


def test_normalization_rejects_mismatched_page_numbers_and_handles_no_pages() -> None:
    with pytest.raises(ValueError):
        normalize_document(["a", "b"], page_numbers=[1])
    empty = build_document_tree([])
    assert empty.document.text == "" and empty.verify_tiling() == [] and len(empty.nodes) == 1


# --- struktura: poziomy, ścieżki, strony -----------------------------------------------------------------


_SAMPLE = (
    "Rozdział 3\nUstalenia\n"
    "§ 16. 1. Ustalenia ogólne.\n"
    "2. Dla terenu A ustala się:\n"
    "1) funkcja:\n"
    "a) mieszkaniowa,\n"
    "b) usługowa;\n"
    "– dopuszcza się garaże,\n"
    "– zakazuje się hałasu;\n"
    "2) wysokość: 10 m.\n"
    "§ 17. Dla terenu B ustala się: 1) wysokość 8 m; 2) zabudowa 30%."
)


def test_tree_has_all_levels_with_paths_and_pages() -> None:
    tree = build_document_tree([_SAMPLE])
    by_path = {node.path: node for node in tree.nodes}
    assert tree.verify_tiling() == []
    for path in ("rozdz. 3", "rozdz. 3 §16", "rozdz. 3 §16 ust.1", "rozdz. 3 §16 ust.2", "rozdz. 3 §16 ust.2 pkt 1",
                 "rozdz. 3 §16 ust.2 pkt 1 lit.a", "rozdz. 3 §16 ust.2 pkt 1 lit.b", "rozdz. 3 §16 ust.2 pkt 1 lit.b tiret 1",
                 "rozdz. 3 §16 ust.2 pkt 1 lit.b tiret 2", "rozdz. 3 §16 ust.2 pkt 2", "rozdz. 3 §17"):
        assert path in by_path, path
    assert tree.text_of(by_path["rozdz. 3 §16 ust.2 pkt 1 lit.b tiret 2"]).startswith("– zakazuje się hałasu")
    # punkty „w jednym wierszu” po „:” i „;” są osobnymi węzłami (numeracja ciągła)
    assert "rozdz. 3 §17 pkt 1" in by_path and "rozdz. 3 §17 pkt 2" in by_path
    assert {node.node_type for node in tree.nodes} >= {"chapter", "paragraph", "section", "point", "letter", "dash", "fragment"}
    assert all(node.page_from == node.page_to == 1 for node in tree.nodes)
    assert tree.warnings == ()


def test_references_and_broken_numbering_do_not_create_nodes() -> None:
    text = "§ 5. Zgodnie z § 7. ust. 2 oraz pkt 3) lit. a) dopuszcza się. Teren A:\n1) pierwszy, wartość 2) nie jest punktem;\n3) trzeci"
    tree = build_document_tree([text])
    kinds = [(n.node_type, n.label) for n in tree.nodes if n.node_type in {"paragraph", "point"}]
    assert ("paragraph", "5") in kinds and ("paragraph", "7") not in kinds  # odesłanie „w § 7.”
    assert ("point", "1") in kinds and ("point", "3") in kinds  # „2)” w środku zdania bez ciągłości
    assert any(w.startswith("numbering_gap:point:3") for w in tree.warnings)  # luka numeracji jest zgłoszona
    assert tree.verify_tiling() == []


def test_a_document_without_markers_is_one_preamble_leaf() -> None:
    tree = build_document_tree(["Zwykły tekst bez znaczników.", "Druga strona."])
    assert [n.node_type for n in tree.nodes] == ["document", "fragment"]
    assert tree.nodes[1].role == "preamble" and (tree.nodes[1].page_from, tree.nodes[1].page_to) == (1, 2)
    assert tree.verify_tiling() == []


def test_tree_is_deterministic() -> None:
    first, second = build_document_tree([_SAMPLE]), build_document_tree([_SAMPLE])
    assert first.digest() == second.digest() and first.nodes == second.nodes
    assert build_document_tree([_SAMPLE + " dopisek"]).digest() != first.digest()


# --- korpus: konkatenacja liści odtwarza tekst, strony monotoniczne --------------------------------------


@pytest.mark.parametrize("doc_id", sorted(MANIFEST["documents"]))
def test_leaves_of_every_corpus_document_reproduce_the_normalized_text(doc_id: str) -> None:
    tree = _tree(doc_id)
    assert tree.verify_tiling() == []
    leaves = tree.leaves()
    assert "".join(tree.text_of(leaf) for leaf in leaves) == tree.document.text
    pages = [leaf.page_from for leaf in leaves]
    assert pages == sorted(pages)  # numery stron są monotoniczne
    assert all(leaf.page_to >= leaf.page_from for leaf in leaves)
    expected_numbers = _load(doc_id)[1] or list(range(1, len(_load(doc_id)[0]) + 1))
    assert set(pages) <= set(expected_numbers) and tree.nodes[0].page_from == expected_numbers[0]
    for page in tree.document.pages:  # tekst surowy jest nietknięty
        assert page.raw == _load(doc_id)[0][expected_numbers.index(page.page_number)]


def test_corpus_documents_contain_every_structural_level() -> None:
    kinds = set()
    for doc_id in MANIFEST["documents"]:
        kinds |= {node.node_type for node in _tree(doc_id).nodes}
    assert kinds >= {"chapter", "paragraph", "section", "point", "letter", "dash", "table_row", "fragment"}


def test_corpus_tree_digests_are_stable_across_builds() -> None:
    for doc_id in ("krakow_morelowa", "warszawa_falenica_a2", "lodz_vi_214_19"):
        assert _tree(doc_id).digest() == _tree(doc_id).digest()


# --- układ „N) dla terenu X:” (Kraków): osobny blok na strefę ------------------------------------------------


def test_krakow_numbered_points_give_one_node_and_block_per_zone() -> None:
    tree = _tree("krakow_morelowa")
    view = structure_view(tree)
    points = [n for n in view.nodes if n.node_type == "point" and re.match(r"\d+\) dla terenu MN\.\d+:", view.text_of(n))]
    symbols = [re.search(r"dla terenu (MN\.\d+):", view.text_of(n)).group(1) for n in points]  # type: ignore[union-attr]
    assert symbols[:11] == [f"MN.{i}" for i in range(1, 12)]  # 11 stref w jednym ustępie
    blocks = [
        block_from_node(view, n.node_id, symbols=[symbol], scope_kind="zone_section", scope_confidence=0.9,
                        strategy=3, strategy_reason="punkt „N) dla terenu X:”")
        for n, symbol in zip(points, symbols, strict=True)
    ]
    mn11 = next(b for b in blocks if b.symbols == ("MN.11",))
    assert mn11.path == "rozdz. III §17 ust.4 pkt 11" and mn11.pages == (19,)
    assert "11 m" in mn11.text and "MN.10" not in mn11.text and "MN.9" not in mn11.text
    mn8 = next(b for b in blocks if b.symbols == ("MN.8",))
    assert mn8.pages == (18, 19)  # blok zachowuje strony, także przez granicę
    spans = sorted(b.char_span for b in blocks)
    assert all(left[1] <= right[0] for left, right in zip(spans, spans[1:], strict=False))  # bloki się nie nakładają


# --- tabele i listy HTML jako węzły drzewa ---------------------------------------------------------------------


def test_pdf_table_rows_become_nodes_inside_the_page_text() -> None:
    tree = _tree("legnica_xiii_161_25")
    rows = [n for n in tree.nodes if n.node_type == "table_row"]
    assert len(rows) >= 15 and tree.verify_tiling() == []
    assert all(row.path.endswith(f"wiersz {row.label.split('.')[1]}") and not row.children for row in rows)
    first = tree.text_of(rows[0])
    assert first.strip() and "\n" not in first.strip()[:1]
    assert tree.unmapped == ("table 1 row 1: tekstu wiersza nie znaleziono na stronie 1",) or len(tree.unmapped) <= 2


def test_table_rows_that_cannot_be_placed_are_reported_not_dropped() -> None:
    text = "§ 1. 1. Dla terenu A:\nwysokość 10 m\n2) kolejny punkt"
    tree = build_document_tree(
        [text],
        tables=[
            (1, [["wysokość", "10 m"], ["brak w tekście", "x"]]),
            (1, [["Dla terenu A:", "2) kolejny"]]),  # zaczyna się przed znacznikiem punktu, kończy po nim
            (9, [["x"]]),
        ],
    )
    types = [n.node_type for n in tree.nodes]
    assert types.count("table_row") == 1
    row = next(n for n in tree.nodes if n.node_type == "table_row")
    assert tree.text_of(row) == "wysokość 10 m" and row.label == "1.1"
    assert len(tree.unmapped) == 3  # brak tekstu, wiersz przecina strukturę i nieistniejąca strona
    assert any("nie znaleziono" in u for u in tree.unmapped) and any("przecina" in u for u in tree.unmapped)
    assert any("strona 9" in u for u in tree.unmapped) and tree.verify_tiling() == []


_HTML = (
    "<html><body><h1>Uchwała</h1><ol><li>Teren A: wysokość 10 m</li><li>Teren B: wysokość 12 m"
    "<ul><li>w tym garaże</li></ul></li></ol>"
    "<table><tr><td>MN</td><td>30%</td></tr><tr><td>US</td><td>20%</td></tr></table></body></html>"
)


def _html_extraction() -> TextExtractionResult:
    import asyncio

    blob = DocumentBlob(content=_HTML.encode("utf-8"), media_type="text/html", filename="a.html",
                        source_metadata=_source_metadata())
    return asyncio.run(extract_document_text(blob))


def _source_metadata():  # noqa: ANN202
    from app.schemas.source import SourceMetadata

    return SourceMetadata(source_name="MPZP_BIP", source_url="https://example.invalid/a.html", confidence=0.9,
                          manual_review_required=False)


def test_html_list_items_and_table_rows_are_nodes_from_extraction_hints() -> None:
    extraction = _html_extraction()
    assert [h.kind for h in extraction.structure_hints] == ["html_list_item"] * 3 + ["table_row"] * 2
    tree = build_tree_from_extraction(extraction)
    assert tree.verify_tiling() == [] and tree.unmapped == ()
    items = [n for n in tree.nodes if n.node_type == "html_list_item"]
    rows = [n for n in tree.nodes if n.node_type == "table_row"]
    assert [tree.text_of(n).strip() for n in items[:1]] == ["Teren A: wysokość 10 m"]
    assert "w tym garaże" in tree.text_of(items[1]) and tree.node(items[2].parent_id or "").node_type == "html_list_item"
    assert [n.path for n in rows] == ["tabela 2 wiersz 1", "tabela 2 wiersz 2"] or len(rows) == 2
    assert [tree.text_of(n).split() for n in rows] == [["MN", "30%"], ["US", "20%"]]
    assert items[0].path == "pozycja 1" and items[1].path == "pozycja 2"


def test_hints_on_missing_pages_or_straddling_structure_are_reported() -> None:
    tree = build_document_tree(
        ["§ 1. Dla terenu A:\n1) pierwszy\n2) drugi"],
        hints=[StructureHint("html_list_item", 4, 0, 5), StructureHint("html_list_item", 1, 3, 40)],
    )
    assert len(tree.unmapped) == 2 and tree.verify_tiling() == []


def test_html_hints_are_empty_for_text_without_lists() -> None:
    soup = BeautifulSoup("<p>Zwykły akapit</p>", "html.parser")
    from app.services.mpzp_parser_extract import _html_structure_hints

    assert _html_structure_hints(soup, soup.get_text(separator="\n", strip=True)) == []


# --- zgodność wsteczna segmentacji ---------------------------------------------------------------------------------


def test_legacy_segmentation_is_unchanged_next_to_the_tree() -> None:
    pages, _, tables = _load("bielsko_viii_187_2024")
    extraction = TextExtractionResult(pages=pages, tables=[ExtractedTable(p, r) for p, r in tables])
    segments = segment_document(extraction)
    assert segments and [s.segment_id for s in segments][:3] == ["seg-0000", "seg-0001", "seg-0002"]
    tree = build_tree_from_extraction(extraction)
    assert tree.verify_tiling() == [] and len({n.page_from for n in tree.nodes}) > 5


# --- ZoneBlock: serializacja i determinizm ---------------------------------------------------------------------------


def _blocks() -> list[ZoneBlock]:
    view = structure_view(build_document_tree([_SAMPLE]))
    node = next(n for n in view.nodes if n.path == "rozdz. 3 §17")
    return [
        block_from_node(view, node.node_id, symbols=["B"], scope_kind="zone_section", scope_confidence=0.9,
                        strategy=1, strategy_reason="osobny §"),
        block_from_span(view, 0, 20, symbols=["A", "B", "A"], scope_kind="general_clause", scope_confidence=0.4,
                        path="rozdz. 3", strategy=5, strategy_reason="klauzula", warnings=("ZONE_SCOPE_AMBIGUOUS",)),
    ]


def test_zone_block_round_trips_through_json_and_digest_is_deterministic() -> None:
    blocks = _blocks()
    assert blocks_digest(blocks) == blocks_digest(_blocks())
    restored = blocks_from_json(blocks_to_json(blocks))
    assert restored == tuple(blocks) and blocks_digest(restored) == blocks_digest(blocks)
    first = blocks[0]
    assert first.block_id.startswith("zb-") and first.sha256 == blocks[0].sha256
    assert blocks[1].symbols == ("A", "B")  # duplikaty symboli usunięte, kolejność zachowana
    data = first.to_dict()
    assert data["schema"] == "zone-block/1" and data["char_span"] == list(first.char_span)
    json.dumps(data)  # serializowalny


def test_zone_block_rejects_invalid_values_and_tampered_payloads() -> None:
    good = _blocks()[0].to_dict()
    for change in ({"scope_kind": "zone"}, {"scope_confidence": 1.5}, {"char_span": [5, 2]}, {"symbols": []}):
        with pytest.raises(ValueError):
            ZoneBlock.from_dict({**good, **change})
    with pytest.raises(ValueError):
        ZoneBlock.from_dict({**good, "text": good["text"] + " zmiana"})  # skrót nie zgadza się z treścią
    with pytest.raises(ValueError):
        ZoneBlock.from_dict({**good, "schema": "zone-block/9"})


def test_view_pages_and_navigation() -> None:
    view = structure_view(build_document_tree(["§ 1. A\n1) x\n", "2) y\n"], page_numbers=[3, 4]))
    point = next(n for n in view.nodes if n.path == "§1 pkt 2")
    assert view.page_at(point.start) == 4 and view.pages_between(0, len(view.text)) == (3, 4)
    assert view.pages_between(5, 5) == () and view.parent(view.nodes[0]) is None
    assert view.parent(point).path == "§1"  # type: ignore[union-attr]
    assert [c.node_id for c in view.children(view.parent(point))] == list(view.parent(point).children)  # type: ignore[union-attr]
    assert view.text_of(point).startswith("2)")


def test_verify_tiling_detects_gaps_overlaps_and_page_regressions() -> None:
    from dataclasses import replace

    tree = build_document_tree(["§ 1. A\n1) x\n", "2) y\n"])
    leaves = list(tree.leaves())
    assert len(leaves) >= 3
    first = leaves[0]
    tampered = [replace(n, start=n.start + 1) if n.node_id == first.node_id else n for n in tree.nodes]
    problems = DocumentTree(document=tree.document, nodes=tuple(tampered)).verify_tiling()
    assert any("luka lub nakładanie" in p for p in problems) or any("nie dzielą" in p for p in problems)
    shrunk = [replace(n, end=n.end - 1) if n.node_id == leaves[-1].node_id else n for n in tree.nodes]
    assert any("liście kończą się" in p for p in DocumentTree(document=tree.document, nodes=tuple(shrunk)).verify_tiling())
    empty_leaf = [replace(n, end=n.start) if n.node_id == leaves[1].node_id else n for n in tree.nodes]
    assert any("pusty liść" in p for p in DocumentTree(document=tree.document, nodes=tuple(empty_leaf)).verify_tiling())
    backwards = [replace(n, page_from=9, page_to=9) if n.node_id == leaves[0].node_id else n for n in tree.nodes]
    assert any("strona maleje" in p for p in DocumentTree(document=tree.document, nodes=tuple(backwards)).verify_tiling())
    assert tree.ancestors(leaves[-1])[0].node_type == "document"
