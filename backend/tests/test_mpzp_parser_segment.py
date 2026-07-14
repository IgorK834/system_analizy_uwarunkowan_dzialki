import json
from pathlib import Path

import pytest

from app.services.mpzp_parser_extract import ExtractedTable, TextExtractionResult
from app.services.mpzp_parser_segment import (
    DocumentSegment,
    find_zone_sections,
    segment_document,
)

REAL_FIXTURE_DIR = (
    Path(__file__).parent
    / "fixtures"
    / "mpzp_documents"
    / "bielsko_biala_uchwala_viii_187_2024"
)


def _real_bielsko_biala_extraction() -> TextExtractionResult:
    data = json.loads((REAL_FIXTURE_DIR / "pages.json").read_text(encoding="utf-8"))
    return TextExtractionResult(pages=data["pages"])


def _real_bielsko_biala_segments() -> list[DocumentSegment]:
    return segment_document(_real_bielsko_biala_extraction())


def test_find_zone_sections_locates_real_bielsko_biala_zone_230_u() -> None:
    result = find_zone_sections(_real_bielsko_biala_segments(), ["230_U"])[0]

    assert len(result.candidates) == 2
    assert result.candidates[0].confidence == pytest.approx(0.9 * 0.7)
    assert result.candidates[0].page_number == 5
    assert "230_U" in result.candidates[0].source_text
    # Kontynuacja § 10 na stronie 6 zawiera drugą realną referencję do symbolu.
    assert result.manual_review_required is True
    assert any("ZONE_SECTION_AMBIGUOUS" in warning for warning in result.warnings)


def test_find_zone_sections_locates_real_bielsko_biala_zone_230_umw_and_230_zp() -> (
    None
):
    results = find_zone_sections(_real_bielsko_biala_segments(), ["230_UMW", "230_ZP"])

    assert [result.zone_symbol for result in results] == ["230_UMW", "230_ZP"]
    assert [result.candidates[0].page_number for result in results] == [6, 7]
    assert all(result.candidates[0].confidence == 0.9 for result in results)
    assert all(
        result.zone_symbol in result.candidates[0].source_text for result in results
    )
    assert all(result.manual_review_required is False for result in results)


def test_find_zone_sections_unknown_symbol_returns_partial_with_manual_review() -> None:
    result = find_zone_sections(_real_bielsko_biala_segments(), ["999_NIEISTNIEJACY"])[
        0
    ]

    assert result.candidates == []
    assert result.manual_review_required is True
    assert result.warnings
    assert "ZONE_SECTION_NOT_FOUND" in result.warnings[0]


def test_find_zone_sections_multiple_candidates_lowers_confidence_and_warns() -> None:
    # Jawnie syntetyczne segmenty izolują logikę kary za wieloznaczność.
    segments = [
        DocumentSegment(
            segment_id="seg-0001",
            text="teren MN pierwsza wzmianka",
            page_number=3,
            heading=None,
            source="paragraph",
        ),
        DocumentSegment(
            segment_id="seg-0002",
            text="symbol MN druga wzmianka w innym miejscu",
            page_number=7,
            heading=None,
            source="paragraph",
        ),
    ]

    result = find_zone_sections(segments, ["MN"])[0]

    assert len(result.candidates) == 2
    assert result.candidates[0].confidence < 0.75
    assert result.manual_review_required is True
    assert any(
        "ZONE_SECTION_AMBIGUOUS" in warning or "wiele" in warning.lower()
        for warning in result.warnings
    )


def test_find_zone_sections_links_table_row_to_zone_symbol() -> None:
    # Jawnie syntetyczna tabela weryfikuje wariant dokumentu tabelarycznego.
    text_result = TextExtractionResult(
        pages=[""],
        tables=[
            ExtractedTable(
                page_number=4,
                rows=[
                    ["Symbol", "Przeznaczenie", "Wysokość"],
                    ["230_U", "usługowa", "15 m"],
                    ["230_UMW", "usługowo-mieszkaniowa", "15 m"],
                ],
            )
        ],
    )

    segments = segment_document(text_result)
    result = find_zone_sections(segments, ["230_U"])[0]
    table_segment_ids = {
        segment.segment_id for segment in segments if segment.source == "table"
    }

    assert len(table_segment_ids) == 3
    assert result.candidates[0].segment_id in table_segment_ids
    assert result.candidates[0].page_number == 4
    assert "230_U" in result.candidates[0].source_text


def test_segment_document_tracks_chapter_heading_across_pages() -> None:
    # Jawnie syntetyczny tekst sprawdza persystencję nagłówka między stronami.
    text_result = TextExtractionResult(
        pages=[
            "Rozdział 2.\nUstalenia szczegółowe\n§ 10. Treść pierwsza.",
            "§ 11. Treść na kolejnej stronie.",
        ]
    )

    segments = segment_document(text_result)
    paragraph_10 = next(segment for segment in segments if "§ 10." in segment.text)
    paragraph_11 = next(segment for segment in segments if "§ 11." in segment.text)

    assert paragraph_10.heading == "Rozdział 2."
    assert paragraph_11.heading == "Rozdział 2."


def test_segment_document_generates_stable_unique_segment_ids() -> None:
    # Jawnie syntetyczny tekst i tabela pozwalają sprawdzić wspólną sekwencję ID.
    text_result = TextExtractionResult(
        pages=["Wstęp\n§ 1. Pierwszy.\n§ 2. Drugi."],
        tables=[ExtractedTable(page_number=1, rows=[["MN", "mieszkaniowa"]])],
    )

    first = segment_document(text_result)
    second = segment_document(text_result)
    first_ids = [segment.segment_id for segment in first]

    assert first_ids == [segment.segment_id for segment in second]
    assert len(first_ids) == len(set(first_ids))
    assert first_ids == [f"seg-{index:04d}" for index in range(len(first_ids))]


def test_zone_reference_pattern_word_boundary_does_not_confuse_u_with_umw() -> None:
    # Jawnie syntetyczny przypadek regresyjny dla granicy słowa przy podkreśleniu.
    segments = [
        DocumentSegment(
            segment_id="seg-0000",
            text="teren oznaczony symbolem 230_UMW",
            page_number=1,
            heading=None,
            source="paragraph",
        )
    ]

    result = find_zone_sections(segments, ["U"])[0]

    assert result.candidates == []
    assert result.manual_review_required is True


def test_zone_section_result_candidates_are_never_in_random_order() -> None:
    # Jawnie syntetyczne segmenty podane poza kolejnością sprawdzają ranking.
    segments = [
        DocumentSegment(
            segment_id="seg-0002",
            text="symbol MN",
            page_number=5,
            heading=None,
            source="paragraph",
        ),
        DocumentSegment(
            segment_id="seg-0001",
            text="teren MN",
            page_number=2,
            heading=None,
            source="paragraph",
        ),
    ]

    first = find_zone_sections(segments, ["MN"])[0].candidates
    second = find_zone_sections(segments, ["MN"])[0].candidates

    assert first == second
    assert [candidate.confidence for candidate in first] == sorted(
        (candidate.confidence for candidate in first), reverse=True
    )
    assert [candidate.page_number for candidate in first] == [2, 5]
