"""Root kompozycji modułu dokumentów."""

from __future__ import annotations

from functools import lru_cache
from datetime import datetime, timezone
from hashlib import sha256

from sqlalchemy.orm import Session

from app.modules.documents.application.service import DocumentService
from app.modules.documents.application.parser import (
    DocumentParserPipeline,
    persist_stage,
)
from app.modules.documents.domain.models import DocumentRegistration
from app.modules.documents.domain.parser_models import (
    DocumentTextSegment,
    ExtractedDocument,
)
from app.modules.documents.infrastructure.parser_adapter import (
    LegacyMpzpParserAdapter,
)
from app.modules.documents.infrastructure.repository import (
    SqlAlchemyDocumentRepository,
)
from app.modules.documents.infrastructure.ocr_tesseract import TesseractOcrProvider
from app.modules.planning.composition import build_planning_rule_service
from app.modules.planning.domain.rules import LegalTextUnit
from app.schemas.mpzp import MpzpParseResult
from app.services.mpzp_fetch import DocumentBlob


def build_document_service(session: Session) -> DocumentService:
    """Wiąże przypadki użycia dokumentów z adapterem SQLAlchemy."""
    return DocumentService(SqlAlchemyDocumentRepository(session))


@lru_cache(maxsize=1)
def build_ocr_provider() -> TesseractOcrProvider:
    """Zwraca współdzieloną konfigurację adaptera OCR/worker."""
    return TesseractOcrProvider()


def build_document_parser(session: Session) -> DocumentParserPipeline:
    """Wiąże jawny pipeline z istniejącym parserem, OCR i magazynem."""
    repository = SqlAlchemyDocumentRepository(session)
    return DocumentParserPipeline(
        LegacyMpzpParserAdapter(build_ocr_provider()),
        repository,
    )


def _registration(
    planning_act_identifier: str, document: DocumentBlob
) -> DocumentRegistration:
    """Wspólny klucz rejestracji — wstrzymanie i resume trafiają w tę samą wersję."""
    published_at = document.source_metadata.fetched_at or datetime.now(timezone.utc)
    return DocumentRegistration(
        planning_act_identifier=planning_act_identifier,
        source_id="kimpzp",
        source_owner="GUGiK / właściwa gmina",
        original_uri=document.source_metadata.source_url or "",
        media_type=document.media_type,
        content_hash=sha256(document.content).hexdigest(),
        published_at=published_at,
        title=document.filename,
        document_type="uchwala",
    )


def register_document_artifact(
    session: Session,
    *,
    planning_act_identifier: str,
    document: DocumentBlob,
) -> int:
    """Rejestruje artefakt i wersję dokumentu bez parsowania (BK-204).

    Używane przy wstrzymaniu analizy: wersja dokumentu jest przypinana zanim
    użytkownik poda symbol strefy. Operacja jest idempotentna po SHA-256 i
    wykonuje tylko ``flush`` — granicę transakcji kontroluje wywołujący.
    """
    repository = SqlAlchemyDocumentRepository(session)
    snapshot = repository.register_document_version(
        _registration(planning_act_identifier, document)
    )
    return snapshot.document_version_id


def persist_parser_audit(
    session: Session,
    *,
    planning_act_identifier: str,
    document: DocumentBlob,
    parse_result: MpzpParseResult,
):
    """Zapisuje dokładnie ten tekst, który posłużył parserowi w analizie."""
    audit = parse_result.document_audit
    if audit is None:
        return None
    repository = SqlAlchemyDocumentRepository(session)
    base = repository.register_document_version(
        _registration(planning_act_identifier, document)
    )
    extraction = ExtractedDocument(
        pages=tuple(page.text for page in audit.pages),
        page_qualities=tuple(page.quality for page in audit.pages),
        blocks=tuple(tuple(page.blocks) for page in audit.pages),
        quality_score=audit.quality_score,
        needs_ocr=False,
        ocr_used=any(page.ocr_used for page in audit.pages),
        extraction_method=audit.extraction_method,
        ocr_engine_version=audit.ocr_engine_version,
        manual_review_required=audit.manual_review_required,
        warnings=tuple(warning.message for warning in parse_result.warnings),
    )
    segments = tuple(
        DocumentTextSegment(
            segment_id=segment.segment_id,
            text=segment.text,
            page_number=segment.page_number,
            heading=segment.heading,
            source=segment.source,
        )
        for segment in audit.segments
    )
    snapshot = persist_stage(repository, base, extraction, segments)
    parent_ids = {
        unit.parent_id
        for unit in snapshot.legal_units
        if unit.parent_id is not None
    }
    # Jednostki nadrzędne powtarzają tekst swoich dzieci. Reguły wyciągamy
    # wyłącznie z liści, aby jeden dowód nie tworzył sztucznych duplikatów i
    # fałszywych conflict_group.
    legal_units = [
        LegalTextUnit(
            legal_unit_id=unit.id,
            source_text=unit.source_text,
        )
        for unit in snapshot.legal_units
        if (
            unit.id is not None
            and unit.id not in parent_ids
            and unit.source_text.strip()
        )
    ]
    build_planning_rule_service(session).extract_and_replace(legal_units)
    return snapshot
