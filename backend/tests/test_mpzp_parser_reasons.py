"""Jawne powody braku sekcji strefy (AU-102): ``ZONE_NOT_IN_DOCUMENT`` i ``SCANNED_NO_OCR``."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.schemas.mpzp import MpzpParserWarning
from app.schemas.source import SourceMetadata
from app.services.mpzp_fetch import DocumentBlob
from app.services.mpzp_parser import parse_mpzp_document
from app.services.mpzp_parser_extract import TextExtractionResult
from app.services.mpzp_parser_reasons import (
    SCANNED_NO_OCR,
    ZONE_NOT_IN_DOCUMENT,
    ZONE_SECTION_NOT_FOUND,
    refine_missing_section_warnings,
    symbol_in_text,
)

DOCUMENT = DocumentBlob(
    content=b"%PDF-fixture",
    media_type="application/pdf",
    filename="uchwala.pdf",
    source_metadata=SourceMetadata(source_name="MPZP_BIP", source_url="https://bip.example.test/u.pdf", confidence=0.9, manual_review_required=False),
)
READABLE = [
    "§ 5. Dla terenów oznaczonych symbolami 1KDZ-11KDZ ustala się:\n1) przeznaczenie: droga publiczna - zbiorcza;",
    "§ 6. Dla terenu oznaczonego symbolem 3MN ustala się:\n1) przeznaczenie: zabudowa jednorodzinna;",
]


def _extraction(pages: list[str], *, needs_ocr: bool = False, ocr_used: bool = False) -> TextExtractionResult:
    return TextExtractionResult(
        pages=pages,
        page_qualities=[1.0 if page.strip() else 0.0 for page in pages],
        blocks=[[] for _ in pages],
        quality_score=0.0 if needs_ocr else 1.0,
        needs_ocr=needs_ocr,
        ocr_used=ocr_used,
        manual_review_required=needs_ocr,
    )


async def _parse(extraction: TextExtractionResult, symbols: list[str], mode: str):  # noqa: ANN202
    with patch("app.services.mpzp_parser.extract_document_text", new=AsyncMock(return_value=extraction)):
        return await parse_mpzp_document(DOCUMENT, symbols, scope_mode=mode)  # type: ignore[arg-type]


def _codes(result, symbol: str) -> list[str]:  # noqa: ANN001
    return [w.code for w in result.warnings if w.zone_symbol == symbol]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "blocks"])
async def test_a_symbol_absent_from_readable_text_is_reported_as_not_in_the_document(mode: str) -> None:
    result = await _parse(_extraction(READABLE), ["9ZZ"], mode)
    assert _codes(result, "9ZZ") == [ZONE_NOT_IN_DOCUMENT]
    assert result.zones[0].parameters == []
    warning = next(w for w in result.warnings if w.zone_symbol == "9ZZ")
    assert "9ZZ" in warning.message and "nie oznacza braku ustaleń" in warning.message


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "blocks"])
async def test_a_scan_without_text_is_reported_as_scanned_no_ocr_never_as_absence(mode: str) -> None:
    result = await _parse(_extraction(["", "", ""], needs_ocr=True), ["3MN"], mode)
    assert _codes(result, "3MN") == [SCANNED_NO_OCR]
    assert ZONE_NOT_IN_DOCUMENT not in _codes(result, "3MN")
    assert "nie znaczy, że strefy nie ma" in next(w for w in result.warnings if w.code == SCANNED_NO_OCR).message


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "blocks"])
async def test_a_partially_scanned_document_never_claims_the_symbol_is_absent(mode: str) -> None:
    result = await _parse(_extraction([READABLE[0], "", ""], needs_ocr=True), ["9ZZ"], mode)
    assert _codes(result, "9ZZ") == [SCANNED_NO_OCR]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "blocks"])
async def test_a_symbol_inside_a_range_is_found_and_gets_no_missing_section_reason(mode: str) -> None:
    result = await _parse(_extraction(READABLE), ["2KDZ"], mode)
    assert not {ZONE_NOT_IN_DOCUMENT, SCANNED_NO_OCR, ZONE_SECTION_NOT_FOUND} & set(_codes(result, "2KDZ"))
    assert [p.normalized_value for p in result.zones[0].parameters if p.name == "primary_use"] == ["droga publiczna - zbiorcza"]


def test_the_generic_code_survives_only_for_a_symbol_that_is_present_in_the_text() -> None:
    warning = MpzpParserWarning(
        stage="segment_document", code=ZONE_SECTION_NOT_FOUND, message="x", zone_symbol="3MN", severity="warning"
    )
    kept = refine_missing_section_warnings([warning], _extraction(READABLE))
    assert [w.code for w in kept] == [ZONE_SECTION_NOT_FOUND]
    changed = refine_missing_section_warnings([warning.model_copy(update={"zone_symbol": "7ZZ"})], _extraction(READABLE))
    assert [w.code for w in changed] == [ZONE_NOT_IN_DOCUMENT]


def test_other_warnings_and_missing_symbol_names_are_left_alone() -> None:
    other = MpzpParserWarning(stage="extract_text", code="NEEDS_OCR", message="m", severity="warning")
    nameless = MpzpParserWarning(stage="segment_document", code=ZONE_SECTION_NOT_FOUND, message="m", severity="warning")
    assert refine_missing_section_warnings([other, nameless], _extraction(READABLE)) == [other, nameless]


def test_symbol_in_text_sees_ranges_and_ocr_confusions() -> None:
    assert symbol_in_text("symbolami 1KDZ-11KDZ ustala się", "5KDZ")
    assert symbol_in_text("teren AIMN ustala się", "A1MN")  # pomyłka OCR 1 -> I
    assert not symbol_in_text("symbolami 1KDZ-11KDZ ustala się", "12KDZ")
