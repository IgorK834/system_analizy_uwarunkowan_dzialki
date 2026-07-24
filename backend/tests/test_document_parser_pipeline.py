from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.modules.documents.application.parser import (
    DocumentParserPipeline,
    classify_stage,
    extract_stage,
    normalize_stage,
    ocr_stage,
    segment_stage,
)
from app.modules.documents.application.service import (
    DocumentNotFoundError,
    DocumentService,
)
from app.modules.documents.domain.models import (
    DocumentSnapshot,
    LegalUnitNode,
)
from app.modules.documents.domain.parser_models import (
    DocumentInput,
    DocumentTextSegment,
    ExtractedDocument,
)


class _Repository:
    def __init__(self) -> None:
        self.saved: DocumentSnapshot | None = None
        self.versions: dict[int, DocumentSnapshot] = {}
        self.units: dict[int, LegalUnitNode] = {}

    def replace_document_content(
        self, snapshot: DocumentSnapshot
    ) -> DocumentSnapshot:
        self.saved = snapshot
        return snapshot

    def get_documents_for_act(self, planning_act_id: int):
        return list(self.versions.values()) if planning_act_id == 11 else []

    def get_document_version(self, document_version_id: int):
        return self.versions.get(document_version_id)

    def get_legal_units_for_version(self, document_version_id: int):
        return [
            unit
            for unit in self.units.values()
            if unit.document_version_id == document_version_id
        ]

    def get_legal_unit(self, legal_unit_id: int):
        return self.units.get(legal_unit_id)


class _Backend:
    def __init__(self, *, fail_classify: bool = False) -> None:
        self.fail_classify = fail_classify
        self.ocr_calls = 0

    def classify(self, document: DocumentInput) -> str:
        if self.fail_classify:
            raise RuntimeError("uszkodzony dokument")
        return "pdf"

    async def extract(self, document: DocumentInput) -> ExtractedDocument:
        return ExtractedDocument(
            pages=("",),
            needs_ocr=True,
            manual_review_required=True,
            warnings=("skan",),
        )

    async def ocr(
        self,
        document: DocumentInput,
        extraction: ExtractedDocument,
    ) -> ExtractedDocument:
        self.ocr_calls += 1
        return ExtractedDocument(
            pages=("§ 8.\r\n1. Teren oznaczony 1MN.",),
            page_qualities=(0.92,),
            blocks=(({"text": "§ 8.", "left": 10},),),
            quality_score=0.92,
            ocr_used=True,
            extraction_method="ocr",
            ocr_engine_version="Tesseract test",
            manual_review_required=True,
            warnings=("OCR wymaga kontroli",),
        )

    def segment(
        self, extraction: ExtractedDocument
    ) -> tuple[DocumentTextSegment, ...]:
        return (
            DocumentTextSegment(
                segment_id="seg-1",
                text=extraction.pages[0],
                page_number=1,
                heading="Rozdział 1. Ustalenia",
                source="paragraph",
            ),
        )


def _base_snapshot() -> DocumentSnapshot:
    now = datetime(2026, 7, 24, tzinfo=timezone.utc)
    return DocumentSnapshot(
        document_version_id=7,
        source_document_id=5,
        source_artifact_id=3,
        content_hash="abc",
        media_type="application/pdf",
        valid_from=now,
        published_at=now,
    )


@pytest.mark.asyncio
async def test_pipeline_runs_ocr_and_persists_citable_structure() -> None:
    repository = _Repository()
    backend = _Backend()
    document = DocumentInput(
        content=b"%PDF-scan",
        media_type="application/pdf",
        filename="uchwala.pdf",
    )

    result = await DocumentParserPipeline(backend, repository).run(
        document, _base_snapshot()
    )

    assert result.status == "partial"
    assert result.completed_stages == (
        "classify",
        "extract",
        "ocr",
        "normalize",
        "segment",
        "persist",
    )
    assert result.manual_review_required is True
    assert backend.ocr_calls == 1
    assert repository.saved is not None
    assert repository.saved.ocr_engine_version == "Tesseract test"
    assert repository.saved.pages[0].text == "§ 8.\n1. Teren oznaczony 1MN."
    assert repository.saved.pages[0].blocks[0]["left"] == 10
    assert repository.saved.legal_units[0].page_from == 1
    assert repository.saved.legal_units[1].document_version_id is None


@pytest.mark.asyncio
async def test_individual_pipeline_stages_are_testable() -> None:
    backend = _Backend()
    document = DocumentInput(b"x", "application/pdf")
    assert classify_stage(backend, document) == "pdf"
    extracted = await extract_stage(backend, document)
    ocr_result = await ocr_stage(backend, document, extracted)
    normalized = normalize_stage(ocr_result)
    assert segment_stage(backend, normalized)[0].page_number == 1

    no_ocr = ExtractedDocument(pages=("tekst",), needs_ocr=False)
    assert await ocr_stage(backend, document, no_ocr) is no_ocr


@pytest.mark.asyncio
async def test_pipeline_failure_is_explicit_and_never_empty_success() -> None:
    result = await DocumentParserPipeline(
        _Backend(fail_classify=True), _Repository()
    ).run(DocumentInput(b"broken", "application/pdf"), _base_snapshot())

    assert result.status == "failed"
    assert result.snapshot is None
    assert result.manual_review_required is True
    assert result.warnings
    assert "RuntimeError" in result.warnings[0]


def test_document_service_builds_tree_and_reports_missing_resources() -> None:
    repository = _Repository()
    snapshot = _base_snapshot()
    repository.versions[7] = snapshot
    repository.units[1] = LegalUnitNode(
        id=1,
        document_version_id=7,
        unit_type="paragraph",
        order_index=0,
        source_text="§ 1.",
    )
    repository.units[2] = LegalUnitNode(
        id=2,
        document_version_id=7,
        unit_type="point",
        order_index=1,
        source_text="1) punkt",
        parent_id=1,
    )
    service = DocumentService(repository)

    assert service.get_planning_act_documents(11) == [snapshot]
    assert service.get_document_legal_units(7)[0].children[0].id == 2
    assert service.get_legal_unit(2).source_text == "1) punkt"
    with pytest.raises(DocumentNotFoundError):
        service.get_document_legal_units(999)
    with pytest.raises(DocumentNotFoundError):
        service.get_legal_unit(999)
