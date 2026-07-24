"""Jawny, testowalny pipeline classify→extract→ocr→normalize→segment→persist."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Protocol

from app.modules.documents.application.ports import DocumentRepository
from app.modules.documents.domain.legal_structure import build_legal_units
from app.modules.documents.domain.models import (
    DocumentPageSnapshot,
    DocumentSnapshot,
)
from app.modules.documents.domain.parser_models import (
    DocumentInput,
    DocumentTextSegment,
    ExtractedDocument,
)


class DocumentParserBackend(Protocol):
    """Port do istniejących ekstraktorów i adaptera OCR."""

    def classify(self, document: DocumentInput) -> str: ...

    async def extract(self, document: DocumentInput) -> ExtractedDocument: ...

    async def ocr(
        self,
        document: DocumentInput,
        extraction: ExtractedDocument,
    ) -> ExtractedDocument: ...

    def segment(
        self, extraction: ExtractedDocument
    ) -> tuple[DocumentTextSegment, ...]: ...


@dataclass(frozen=True)
class DocumentPipelineResult:
    status: str
    snapshot: DocumentSnapshot | None
    completed_stages: tuple[str, ...]
    warnings: tuple[str, ...]
    manual_review_required: bool


def classify_stage(
    backend: DocumentParserBackend, document: DocumentInput
) -> str:
    return backend.classify(document)


async def extract_stage(
    backend: DocumentParserBackend, document: DocumentInput
) -> ExtractedDocument:
    return await backend.extract(document)


async def ocr_stage(
    backend: DocumentParserBackend,
    document: DocumentInput,
    extraction: ExtractedDocument,
) -> ExtractedDocument:
    if not extraction.needs_ocr:
        return extraction
    return await backend.ocr(document, extraction)


def normalize_stage(extraction: ExtractedDocument) -> ExtractedDocument:
    pages = tuple(
        unicodedata.normalize("NFKC", page).replace("\r\n", "\n")
        for page in extraction.pages
    )
    return ExtractedDocument(
        pages=pages,
        tables=extraction.tables,
        page_qualities=extraction.page_qualities,
        blocks=extraction.blocks,
        quality_score=extraction.quality_score,
        needs_ocr=extraction.needs_ocr,
        ocr_used=extraction.ocr_used,
        extraction_method=extraction.extraction_method,
        ocr_engine_version=extraction.ocr_engine_version,
        manual_review_required=extraction.manual_review_required,
        warnings=extraction.warnings,
    )


def segment_stage(
    backend: DocumentParserBackend,
    extraction: ExtractedDocument,
) -> tuple[DocumentTextSegment, ...]:
    return backend.segment(extraction)


def persist_stage(
    repository: DocumentRepository,
    base_snapshot: DocumentSnapshot,
    extraction: ExtractedDocument,
    segments: tuple[DocumentTextSegment, ...],
) -> DocumentSnapshot:
    pages = tuple(
        DocumentPageSnapshot(
            page_number=index,
            text=text,
            ocr_used=extraction.ocr_used,
            quality=(
                extraction.page_qualities[index - 1]
                if index <= len(extraction.page_qualities)
                else None
            ),
            blocks=(
                extraction.blocks[index - 1]
                if index <= len(extraction.blocks)
                else ()
            ),
        )
        for index, text in enumerate(extraction.pages, start=1)
    )
    snapshot = DocumentSnapshot(
        document_version_id=base_snapshot.document_version_id,
        source_document_id=base_snapshot.source_document_id,
        source_artifact_id=base_snapshot.source_artifact_id,
        content_hash=base_snapshot.content_hash,
        media_type=base_snapshot.media_type,
        extraction_method=extraction.extraction_method,
        ocr_engine_version=extraction.ocr_engine_version,
        quality_score=extraction.quality_score,
        original_uri=base_snapshot.original_uri,
        valid_from=base_snapshot.valid_from,
        valid_to=base_snapshot.valid_to,
        published_at=base_snapshot.published_at,
        review_status=base_snapshot.review_status,
        pages=pages,
        legal_units=build_legal_units(segments),
    )
    return repository.replace_document_content(snapshot)


class DocumentParserPipeline:
    """Fasada jednego dokumentu: wyjątek daje jawny failed, nigdy pusty sukces."""

    def __init__(
        self,
        backend: DocumentParserBackend,
        repository: DocumentRepository,
    ) -> None:
        self._backend = backend
        self._repository = repository

    async def run(
        self,
        document: DocumentInput,
        base_snapshot: DocumentSnapshot,
    ) -> DocumentPipelineResult:
        stages: list[str] = []
        try:
            classify_stage(self._backend, document)
            stages.append("classify")
            extraction = await extract_stage(self._backend, document)
            stages.append("extract")
            extraction = await ocr_stage(self._backend, document, extraction)
            stages.append("ocr")
            extraction = normalize_stage(extraction)
            stages.append("normalize")
            segments = segment_stage(self._backend, extraction)
            stages.append("segment")
            snapshot = persist_stage(
                self._repository,
                base_snapshot,
                extraction,
                segments,
            )
            stages.append("persist")
            status = (
                "partial"
                if extraction.manual_review_required or not segments
                else "complete"
            )
            return DocumentPipelineResult(
                status=status,
                snapshot=snapshot,
                completed_stages=tuple(stages),
                warnings=extraction.warnings,
                manual_review_required=(
                    extraction.manual_review_required or not segments
                ),
            )
        except Exception as exc:
            return DocumentPipelineResult(
                status="failed",
                snapshot=None,
                completed_stages=tuple(stages),
                warnings=(
                    "Przetwarzanie dokumentu zakończyło się błędem na etapie "
                    f"{stages[-1] if stages else 'classify'} "
                    f"({type(exc).__name__}).",
                ),
                manual_review_required=True,
            )
