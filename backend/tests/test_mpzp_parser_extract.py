from unittest.mock import MagicMock, patch

import pytest

from app.schemas.source import SourceMetadata
from app.services.mpzp_fetch import DocumentBlob
from app.services.mpzp_parser_extract import (
    MIN_CHARS_PER_PAGE_FOR_TEXT_LAYER,
    OCR_QUALITY_THRESHOLD,
    OcrProvider,
    classify_document,
    extract_document_text,
)


def _document(content: bytes, media_type: str, filename: str) -> DocumentBlob:
    return DocumentBlob(
        content=content,
        media_type=media_type,
        filename=filename,
        source_metadata=SourceMetadata(
            source_name="MPZP_BIP",
            confidence=0.9,
            manual_review_required=False,
        ),
    )


PDF_DOCUMENT = _document(b"%PDF-mock", "application/pdf", "plan.pdf")
HTML_DOCUMENT_WITH_NAV = _document(
    (
        b'<html><body><nav class="main-menu">Strona glowna Kontakt</nav>'
        b'<script>alert(1)</script><style>.x{color:red}</style>'
        b'<p>Ustala sie teren zabudowy mieszkaniowej jednorodzinnej '
        b'oznaczony symbolem MN.</p><footer id="stopka">BIP 2024</footer>'
        b"</body></html>"
    ),
    "text/html",
    "plan.html",
)
UNSUPPORTED_DOCUMENT = _document(b"\x89PNG...", "image/png", "scan.png")


def _page(text: str, tables: list | None = None) -> MagicMock:
    page = MagicMock()
    page.extract_text.return_value = text
    page.extract_tables.return_value = tables or []
    return page


def _pdf_context(pages: list[MagicMock]) -> MagicMock:
    context = MagicMock()
    context.__enter__.return_value.pages = pages
    return context


def test_classify_document_recognizes_supported_and_unsupported_types() -> None:
    assert classify_document(PDF_DOCUMENT) == "pdf"
    assert classify_document(HTML_DOCUMENT_WITH_NAV) == "html"
    assert classify_document(UNSUPPORTED_DOCUMENT) == "unsupported"


@pytest.mark.asyncio
async def test_pdf_with_text_layer_returns_pages_and_high_quality() -> None:
    pages = [_page("A" * 500), _page("B" * 400)]
    with patch(
        "app.services.mpzp_parser_extract.pdfplumber.open",
        return_value=_pdf_context(pages),
    ):
        result = await extract_document_text(PDF_DOCUMENT)

    assert result.pages == ["A" * 500, "B" * 400]
    assert result.quality_score >= OCR_QUALITY_THRESHOLD
    assert result.needs_ocr is False
    assert result.manual_review_required is False


@pytest.mark.asyncio
async def test_pdf_scan_without_text_requires_ocr_and_manual_review() -> None:
    with patch(
        "app.services.mpzp_parser_extract.pdfplumber.open",
        return_value=_pdf_context([_page(""), _page("")]),
    ):
        result = await extract_document_text(PDF_DOCUMENT)

    assert result.needs_ocr is True
    assert result.manual_review_required is True
    assert result.quality_score == 0.0
    assert len(result.warnings) >= 1


@pytest.mark.asyncio
async def test_pdf_quality_is_fraction_of_pages_with_text_layer() -> None:
    pages = [
        _page("A" * MIN_CHARS_PER_PAGE_FOR_TEXT_LAYER),
        _page("B" * (MIN_CHARS_PER_PAGE_FOR_TEXT_LAYER + 1)),
        _page(""),
    ]
    with patch(
        "app.services.mpzp_parser_extract.pdfplumber.open",
        return_value=_pdf_context(pages),
    ):
        result = await extract_document_text(PDF_DOCUMENT)

    assert result.quality_score == pytest.approx(2 / 3)


@pytest.mark.asyncio
async def test_pdf_extracts_tables_with_page_number() -> None:
    raw_table = [["a", "b"], ["c", "d"]]
    with patch(
        "app.services.mpzp_parser_extract.pdfplumber.open",
        return_value=_pdf_context([_page("A" * 500, [raw_table])]),
    ):
        result = await extract_document_text(PDF_DOCUMENT)

    assert len(result.tables) == 1
    assert result.tables[0].page_number == 1
    assert result.tables[0].rows == raw_table


@pytest.mark.asyncio
async def test_empty_pdf_is_treated_as_requiring_ocr() -> None:
    with patch(
        "app.services.mpzp_parser_extract.pdfplumber.open",
        return_value=_pdf_context([]),
    ):
        result = await extract_document_text(PDF_DOCUMENT)

    assert result.pages == []
    assert result.quality_score == 0.0
    assert result.needs_ocr is True


@pytest.mark.asyncio
async def test_html_removes_scripts_styles_and_navigation() -> None:
    result = await extract_document_text(HTML_DOCUMENT_WITH_NAV)

    text = result.pages[0]
    assert "alert(1)" not in text
    assert "color:red" not in text
    assert "Strona glowna" not in text
    assert "BIP 2024" not in text
    assert "zabudowy mieszkaniowej jednorodzinnej" in text
    assert result.quality_score == 1.0


@pytest.mark.asyncio
async def test_html_without_content_after_cleaning_requires_review() -> None:
    document = _document(
        b'<html><nav class="menu">Menu</nav><script>x()</script></html>',
        "text/html",
        "empty.html",
    )

    result = await extract_document_text(document)

    assert result.pages == [""]
    assert result.manual_review_required is True
    assert result.quality_score == 0.0
    assert len(result.warnings) == 1


@pytest.mark.asyncio
async def test_unsupported_document_does_not_call_pdfplumber() -> None:
    with patch("app.services.mpzp_parser_extract.pdfplumber.open") as open_pdf:
        result = await extract_document_text(UNSUPPORTED_DOCUMENT)

    open_pdf.assert_not_called()
    assert result.pages == []
    assert result.needs_ocr is False
    assert result.manual_review_required is True


def test_ocr_provider_protocol_is_structurally_checkable() -> None:
    class DummyOcr:
        async def extract_text_from_scan(self, pdf_bytes: bytes) -> str:
            return "tekst OCR"

    assert isinstance(DummyOcr(), OcrProvider)


@pytest.mark.asyncio
async def test_ocr_provider_is_invoked_for_scan() -> None:
    class DummyOcr:
        def __init__(self) -> None:
            self.called = False

        async def extract_text_from_scan(self, pdf_bytes: bytes) -> str:
            self.called = True
            return "tekst OCR"

    provider = DummyOcr()
    with patch(
        "app.services.mpzp_parser_extract.pdfplumber.open",
        return_value=_pdf_context([_page("")]),
    ):
        result = await extract_document_text(PDF_DOCUMENT, provider)

    assert result.needs_ocr is False
    assert result.ocr_used is True
    assert result.pages == ["tekst OCR"]
    assert result.ocr_engine_version == "DummyOcr"
    assert result.manual_review_required is True
    assert provider.called is True
