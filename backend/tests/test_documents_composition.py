from __future__ import annotations

from datetime import datetime, timezone

from app.modules.documents.composition import (
    build_document_parser,
    build_document_service,
    build_ocr_provider,
    persist_parser_audit,
)
from app.modules.documents.domain.models import (
    DocumentRegistration,
    DocumentSnapshot,
    LegalUnitNode,
)
from app.schemas.mpzp import (
    MpzpParseResult,
    ParserDocumentAudit,
    ParserDocumentPage,
    ParserDocumentSegment,
)
from app.schemas.source import SourceMetadata
from app.services.mpzp_fetch import DocumentBlob


def _snapshot(legal_units=()) -> DocumentSnapshot:
    now = datetime(2026, 7, 24, tzinfo=timezone.utc)
    return DocumentSnapshot(
        document_version_id=7,
        source_document_id=6,
        source_artifact_id=5,
        content_hash="hash",
        media_type="application/pdf",
        valid_from=now,
        published_at=now,
        legal_units=legal_units,
    )


def test_composition_builders_bind_expected_adapters() -> None:
    service = build_document_service(object())
    parser = build_document_parser(object())
    assert service is not None
    assert parser is not None
    assert build_ocr_provider() is build_ocr_provider()


def test_persist_parser_audit_skips_absent_audit() -> None:
    document = DocumentBlob(
        b"x",
        "application/pdf",
        "plan.pdf",
        SourceMetadata(
            source_name="test",
            confidence=1.0,
            manual_review_required=False,
        ),
    )
    assert (
        persist_parser_audit(
            object(),
            planning_act_identifier="act",
            document=document,
            parse_result=MpzpParseResult(status="partial"),
        )
        is None
    )


def test_persist_parser_audit_saves_pages_units_and_rules_for_leaves_only(
    monkeypatch,
) -> None:
    captured = {}

    class Repository:
        def __init__(self, session) -> None:
            captured["session"] = session

        def register_document_version(
            self, registration: DocumentRegistration
        ) -> DocumentSnapshot:
            captured["registration"] = registration
            return _snapshot()

    parent = LegalUnitNode(
        id=10,
        document_version_id=7,
        unit_type="paragraph",
        order_index=0,
        source_text="§ 1. treść wspólna",
    )
    child = LegalUnitNode(
        id=11,
        document_version_id=7,
        unit_type="point",
        order_index=1,
        parent_id=10,
        source_text="1) Maksymalna wysokość zabudowy 9 m.",
    )

    def fake_persist(repository, base, extraction, segments):
        captured["extraction"] = extraction
        captured["segments"] = segments
        return _snapshot((parent, child))

    class Rules:
        def extract_and_replace(self, units):
            captured["rule_units"] = units
            return []

    monkeypatch.setattr(
        "app.modules.documents.composition.SqlAlchemyDocumentRepository",
        Repository,
    )
    monkeypatch.setattr(
        "app.modules.documents.composition.persist_stage", fake_persist
    )
    monkeypatch.setattr(
        "app.modules.documents.composition.build_planning_rule_service",
        lambda session: Rules(),
    )
    fetched_at = datetime(2026, 7, 24, tzinfo=timezone.utc)
    document = DocumentBlob(
        b"pdf",
        "application/pdf",
        "plan.pdf",
        SourceMetadata(
            source_name="BIP",
            source_url="https://bip.example/plan.pdf",
            fetched_at=fetched_at,
            confidence=0.9,
            manual_review_required=True,
        ),
    )
    audit = ParserDocumentAudit(
        media_type="application/pdf",
        extraction_method="ocr",
        ocr_engine_version="Tesseract 5.5",
        quality_score=0.9,
        manual_review_required=True,
        pages=[
            ParserDocumentPage(
                page_number=1,
                text="§ 1.",
                ocr_used=True,
                quality=0.9,
                blocks=[{"left": 1}],
            )
        ],
        segments=[
            ParserDocumentSegment(
                segment_id="seg-1",
                text="§ 1.",
                page_number=1,
                heading=None,
                source="paragraph",
            )
        ],
    )

    result = persist_parser_audit(
        object(),
        planning_act_identifier="act-1",
        document=document,
        parse_result=MpzpParseResult(status="complete", document_audit=audit),
    )

    assert result is not None
    assert captured["registration"].published_at == fetched_at
    assert captured["registration"].original_uri.startswith("https://")
    assert captured["extraction"].ocr_used is True
    assert captured["segments"][0].page_number == 1
    assert [unit.legal_unit_id for unit in captured["rule_units"]] == [11]
