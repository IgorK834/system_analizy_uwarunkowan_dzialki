from unittest.mock import AsyncMock, patch

import pytest

from app.schemas.mpzp import MpzpParameter, MpzpParseResult, MpzpZoneResult
from app.schemas.source import SourceMetadata
from app.services.mpzp_fetch import DocumentBlob
from app.services.mpzp_parser import (
    extract_parameters,
    parse_mpzp_document,
    validate_result,
)
from app.services.mpzp_parser_extract import TextExtractionResult
from app.services.mpzp_parser_segment import (
    DocumentSegment,
    ZoneSectionCandidate,
    ZoneSectionResult,
)

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
async def test_parse_with_many_zone_symbols_returns_one_result_per_symbol() -> None:
    with patch(
        "app.services.mpzp_parser.extract_document_text",
        new=AsyncMock(return_value=_extraction()),
    ):
        result = await parse_mpzp_document(PDF_DOCUMENT, ["MN", "U"])

    assert [zone.zone_symbol for zone in result.zones] == ["MN", "U"]
    assert [warning.code for warning in result.warnings] == [
        "ZONE_SECTION_NOT_FOUND",
        "ZONE_SECTION_NOT_FOUND",
    ]


@pytest.mark.asyncio
async def test_parse_defaults_zone_symbol_to_unknown() -> None:
    with patch(
        "app.services.mpzp_parser.extract_document_text",
        new=AsyncMock(return_value=_extraction()),
    ):
        result = await parse_mpzp_document(PDF_DOCUMENT, None)

    assert result.zones[0].zone_symbol == "UNKNOWN"


@pytest.mark.asyncio
async def test_parse_empty_zone_symbols_preserves_unknown_without_segmentation() -> None:
    with (
        patch(
            "app.services.mpzp_parser.extract_document_text",
            new=AsyncMock(return_value=_extraction()),
        ),
        patch("app.services.mpzp_parser.segment_document") as segment_mock,
    ):
        result = await parse_mpzp_document(PDF_DOCUMENT, [])

    segment_mock.assert_not_called()
    assert result.zones[0].zone_symbol == "UNKNOWN"


@pytest.mark.asyncio
async def test_parse_mpzp_document_with_zone_symbols_uses_real_segmentation() -> None:
    extraction = TextExtractionResult(
        pages=[
            "§ 10. Dla terenu wyznaczonego na rysunku planu liniami "
            "rozgraniczającymi i oznaczonego symbolem 230_U ustala się:"
        ],
        quality_score=1.0,
    )
    with patch(
        "app.services.mpzp_parser.extract_document_text",
        new=AsyncMock(return_value=extraction),
    ):
        result = await parse_mpzp_document(PDF_DOCUMENT, ["230_U"])

    assert result.zones[0].zone_evidence is not None
    assert "230_U" in (result.zones[0].zone_evidence.source_text or "")
    # Mock jest ucięty na jednej linii wprowadzającej symbol strefy, bez
    # dalszych parametrów planistycznych — parameters=[] jest tu poprawnym,
    # uczciwym wynikiem, a nie regresją ekstraktorów.
    assert result.zones[0].parameters == []


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


def test_extract_parameters_returns_empty_parameters_and_evidence_per_zone() -> None:
    # Segment "teren MN" nie zawiera żadnego wzorca liczbowego/opisowego, więc
    # rzeczywiste zachowanie ekstraktorów po zmianie sygnatury to wciąż [] —
    # ta konkretna wartość tekstowa jest po prostu za krótka na dopasowanie.
    segments = [
        DocumentSegment(
            segment_id="seg-0001",
            text="teren MN",
            page_number=1,
            heading=None,
            source="paragraph",
        )
    ]
    section_results = [
        ZoneSectionResult(
            zone_symbol="MN",
            candidates=[
                ZoneSectionCandidate(
                    zone_symbol="MN",
                    segment_id="seg-0001",
                    source_text="teren MN",
                    page_number=1,
                    confidence=0.75,
                    match_pattern=r"\bteren\w*\s+{symbol}\b",
                )
            ],
        ),
        ZoneSectionResult(zone_symbol="U"),
    ]

    zones = extract_parameters(section_results, segments)

    assert [zone.zone_symbol for zone in zones] == ["MN", "U"]
    assert all(zone.parameters == [] for zone in zones)
    assert zones[0].zone_evidence is not None
    assert zones[0].zone_evidence.source_text == "teren MN"
    assert zones[0].zone_evidence.page_number == 1
    assert zones[1].zone_evidence is None


def test_extract_parameters_without_segments_argument_returns_empty_parameters() -> (
    None
):
    # Wywołanie bez segments (stary sposób) nadal działa: brak tekstu do
    # przeszukania nie jest błędem tego etapu, tylko konsekwencją braku
    # segmentacji dostarczonej przez wywołującego.
    section_results = [
        ZoneSectionResult(
            zone_symbol="MN",
            candidates=[
                ZoneSectionCandidate(
                    zone_symbol="MN",
                    segment_id="seg-0001",
                    source_text="teren MN",
                    page_number=1,
                    confidence=0.75,
                    match_pattern=r"\bteren\w*\s+{symbol}\b",
                )
            ],
        )
    ]

    zones = extract_parameters(section_results)

    assert zones[0].parameters == []


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
