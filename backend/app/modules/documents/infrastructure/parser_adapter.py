"""Adapter istniejących ``mpzp_parser_*`` do portu nowego pipeline'u."""

from __future__ import annotations

from app.modules.documents.application.parser import DocumentParserBackend
from app.modules.documents.domain.parser_models import (
    DocumentInput,
    DocumentTextSegment,
    ExtractedDocument,
)
from app.services.mpzp_fetch import DocumentBlob
from app.services.mpzp_parser_extract import (
    OcrProvider,
    TextExtractionResult,
    apply_ocr_to_extraction,
    classify_document,
    extract_document_text,
)
from app.services.mpzp_parser_segment import segment_document


class LegacyMpzpParserAdapter(DocumentParserBackend):
    """Integruje działające ekstraktory bez kopiowania ich implementacji."""

    def __init__(self, ocr_provider: OcrProvider) -> None:
        self._ocr_provider = ocr_provider

    def classify(self, document: DocumentInput) -> str:
        return classify_document(_blob(document))

    async def extract(self, document: DocumentInput) -> ExtractedDocument:
        result = await extract_document_text(_blob(document))
        return _to_domain(result)

    async def ocr(
        self,
        document: DocumentInput,
        extraction: ExtractedDocument,
    ) -> ExtractedDocument:
        result = await apply_ocr_to_extraction(
            _blob(document),
            _to_legacy(extraction),
            self._ocr_provider,
        )
        return _to_domain(result)

    def segment(
        self, extraction: ExtractedDocument
    ) -> tuple[DocumentTextSegment, ...]:
        segments = segment_document(_to_legacy(extraction))
        return tuple(
            DocumentTextSegment(
                segment_id=segment.segment_id,
                text=segment.text,
                page_number=segment.page_number,
                heading=segment.heading,
                source=segment.source,
            )
            for segment in segments
        )


def _blob(document: DocumentInput) -> DocumentBlob:
    # Adapter nie wykonuje pobierania; provenance URL jest już w SourceArtifact.
    from app.schemas.source import SourceMetadata

    return DocumentBlob(
        content=document.content,
        media_type=document.media_type,
        filename=document.filename,
        source_metadata=SourceMetadata(
            source_name="document_pipeline",
            confidence=1.0,
            manual_review_required=False,
        ),
    )


def _to_domain(result: TextExtractionResult) -> ExtractedDocument:
    return ExtractedDocument(
        pages=tuple(result.pages),
        tables=tuple(
            (
                table.page_number,
                tuple(tuple(cell for cell in row) for row in table.rows),
            )
            for table in result.tables
        ),
        page_qualities=tuple(result.page_qualities),
        blocks=tuple(tuple(blocks) for blocks in result.blocks),
        quality_score=result.quality_score,
        needs_ocr=result.needs_ocr,
        ocr_used=result.ocr_used,
        extraction_method=result.extraction_method,
        ocr_engine_version=result.ocr_engine_version,
        manual_review_required=result.manual_review_required,
        warnings=tuple(result.warnings),
    )


def _to_legacy(result: ExtractedDocument) -> TextExtractionResult:
    from app.services.mpzp_parser_extract import ExtractedTable

    return TextExtractionResult(
        pages=list(result.pages),
        tables=[
            ExtractedTable(
                page_number=page_number,
                rows=[list(row) for row in rows],
            )
            for page_number, rows in result.tables
        ],
        page_qualities=list(result.page_qualities),
        blocks=[list(blocks) for blocks in result.blocks],
        quality_score=result.quality_score,
        needs_ocr=result.needs_ocr,
        ocr_used=result.ocr_used,
        extraction_method=result.extraction_method,
        ocr_engine_version=result.ocr_engine_version,
        manual_review_required=result.manual_review_required,
        warnings=list(result.warnings),
    )
