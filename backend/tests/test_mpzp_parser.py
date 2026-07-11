from unittest.mock import AsyncMock, patch

import pytest

from app.schemas.mpzp import MpzpParameter, MpzpParseResult, MpzpZoneResult
from app.schemas.source import SourceMetadata
from app.services.mpzp_fetch import DocumentBlob
from app.services.mpzp_parser import (
    _DocumentSegment,
    extract_parameters,
    parse_mpzp_document,
    segment_document,
    validate_result,
)
from app.services.mpzp_parser_extract import TextExtractionResult


PDF_DOCUMENT = DocumentBlob(
    content=b"%PDF-mock",
    media_type="application/pdf",
    filename="plan.pdf",
    source_metadata=SourceMetadata(
        source_name="MPZP_BIP",
        confidence=0.9,
        manual_review_required=False,
    ),
)


def _extraction(warnings: list[str] | None = None) -> TextExtractionResult:
    return TextExtractionResult(
        pages=["Treść strony pierwszej", "Treść strony drugiej"],
        quality_score=1.0,
        warnings=warnings or [],
    )


@pytest.mark.asyncio
async def test_parse_mpzp_document_returns_mpzp_parse_result() -> None:
    with patch(
        "app.services.mpzp_parser.extract_document_text",
        new=AsyncMock(return_value=_extraction()),
    ):
        result = await parse_mpzp_document(PDF_DOCUMENT, ["MN"])

    assert isinstance(result, MpzpParseResult)


@pytest.mark.asyncio
async def test_parse_with_extracted_text_returns_honest_partial_status() -> None:
    with patch(
        "app.services.mpzp_parser.extract_document_text",
        new=AsyncMock(return_value=_extraction()),
    ):
        result = await parse_mpzp_document(PDF_DOCUMENT, ["MN", "U"])

    assert result.status == "partial"
    assert result.zones[0].parameters == []


@pytest.mark.asyncio
async def test_parse_never_raises_on_unexpected_internal_error() -> None:
    with patch(
        "app.services.mpzp_parser.extract_document_text",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        result = await parse_mpzp_document(PDF_DOCUMENT)

    assert result.status == "failed"
    assert result.zones == []
    assert any(
        warning.code == "PARSER_UNEXPECTED_ERROR" for warning in result.warnings
    )


@pytest.mark.asyncio
async def test_parse_uses_first_discovery_zone_symbol() -> None:
    with patch(
        "app.services.mpzp_parser.extract_document_text",
        new=AsyncMock(return_value=_extraction()),
    ):
        result = await parse_mpzp_document(PDF_DOCUMENT, ["MN", "U"])

    assert result.zones[0].zone_symbol == "MN"


@pytest.mark.asyncio
async def test_parse_defaults_zone_symbol_to_unknown() -> None:
    with patch(
        "app.services.mpzp_parser.extract_document_text",
        new=AsyncMock(return_value=_extraction()),
    ):
        result = await parse_mpzp_document(PDF_DOCUMENT, None)

    assert result.zones[0].zone_symbol == "UNKNOWN"


@pytest.mark.asyncio
async def test_parse_calls_extract_document_text_with_document() -> None:
    extract_mock = AsyncMock(return_value=_extraction(["niepełny tekst"]))
    with patch(
        "app.services.mpzp_parser.extract_document_text",
        new=extract_mock,
    ):
        result = await parse_mpzp_document(PDF_DOCUMENT, ["MN"])

    extract_mock.assert_awaited_once_with(PDF_DOCUMENT)
    assert result.warnings[0].stage == "extract_text"
    assert result.warnings[0].code == "TEXT_EXTRACTION_WARNING"


def test_segment_document_combines_all_pages_into_one_segment() -> None:
    segments = segment_document(_extraction(), ["MN"])

    assert len(segments) == 1
    assert segments[0].zone_symbol == "MN"
    assert segments[0].text == "Treść strony pierwszej\nTreść strony drugiej"
    assert segments[0].page_number == 1


def test_segment_document_without_pages_has_no_page_number() -> None:
    extraction = TextExtractionResult(pages=[])

    segments = segment_document(extraction, [])

    assert segments[0].zone_symbol == "UNKNOWN"
    assert segments[0].page_number is None


def test_extract_parameters_returns_empty_parameters_per_segment() -> None:
    segments = [
        _DocumentSegment("MN", "tekst MN", 1),
        _DocumentSegment("U", "tekst U", 2),
    ]

    zones = extract_parameters(segments)

    assert [zone.zone_symbol for zone in zones] == ["MN", "U"]
    assert all(zone.parameters == [] for zone in zones)


def test_validate_result_empty_zones_is_failed() -> None:
    assert validate_result([]) == "failed"


def test_validate_result_zones_without_parameters_is_partial() -> None:
    assert validate_result([MpzpZoneResult(zone_symbol="MN")]) == "partial"


def test_validate_result_with_parameter_is_complete() -> None:
    parameter = MpzpParameter(
        name="height",
        normalized_value=9.0,
        confidence=0.8,
        manual_review_required=False,
    )
    zones = [MpzpZoneResult(zone_symbol="MN", parameters=[parameter])]

    assert validate_result(zones) == "complete"
