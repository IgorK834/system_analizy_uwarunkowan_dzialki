"""Integracja wersjonowanego magazynu dokumentów z PostgreSQL/PostGIS."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.versioned import (
    DocumentPage,
    DocumentVersion,
    LegalUnit,
    PlanningRule,
)
from app.modules.documents.composition import persist_parser_audit
from app.modules.documents.domain.models import (
    DocumentPageSnapshot,
    DocumentRegistration,
    DocumentSnapshot,
    LegalUnitNode,
)
from app.modules.documents.infrastructure.repository import (
    SqlAlchemyDocumentRepository,
)
from app.schemas.source import SourceMetadata
from app.services.mpzp_fetch import DocumentBlob
from app.services.mpzp_parser import parse_mpzp_document

pytestmark = pytest.mark.integration


@pytest.fixture
def session() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()


def _registration(
    suffix: str,
    *,
    content_hash: str,
    published_at: datetime,
    original_uri: str,
) -> DocumentRegistration:
    return DocumentRegistration(
        planning_act_identifier=f"act-documents-{suffix}",
        source_id=f"documents-test-{suffix}",
        source_owner="Test",
        original_uri=original_uri,
        media_type="application/pdf",
        content_hash=content_hash,
        published_at=published_at,
        title="Uchwała testowa",
        document_type="uchwala",
    )


def _with_content(
    base: DocumentSnapshot,
    *,
    text: str,
    quality: float,
) -> DocumentSnapshot:
    return replace(
        base,
        extraction_method="ocr",
        ocr_engine_version="Tesseract test",
        quality_score=quality,
        pages=(
            DocumentPageSnapshot(
                page_number=1,
                text=text,
                ocr_used=True,
                quality=quality,
                blocks=({"text": "§ 1.", "left": 10},),
            ),
        ),
        legal_units=(
            LegalUnitNode(
                unit_type="chapter",
                number="1",
                order_index=0,
                page_from=1,
                page_to=1,
                source_text="Rozdział 1.",
                node_key="chapter",
            ),
            LegalUnitNode(
                unit_type="paragraph",
                number="1",
                order_index=1,
                page_from=1,
                page_to=1,
                source_text=text,
                normalized_text=" ".join(text.split()),
                node_key="paragraph",
                parent_key="chapter",
            ),
            LegalUnitNode(
                unit_type="point",
                number="1",
                order_index=2,
                page_from=1,
                page_to=1,
                source_text="1) punkt",
                parent_key="paragraph",
            ),
        ),
    )


def test_repository_preserves_old_version_hierarchy_and_safe_links(
    session: Session,
) -> None:
    suffix = uuid4().hex[:10]
    now = datetime(2026, 7, 24, tzinfo=timezone.utc)
    repository = SqlAlchemyDocumentRepository(session)

    old_base = repository.register_document_version(
        _registration(
            suffix,
            content_hash=f"old-{suffix}",
            published_at=now,
            original_uri="/srv/private/old.pdf",
        )
    )
    old_saved = repository.replace_document_content(
        _with_content(old_base, text="§ 1. Stary tekst.", quality=0.81)
    )
    old_unit_ids = [unit.id for unit in old_saved.legal_units]

    new_base = repository.register_document_version(
        _registration(
            suffix,
            content_hash=f"new-{suffix}",
            published_at=now + timedelta(days=1),
            original_uri="https://bip.example/new.pdf",
        )
    )
    new_saved = repository.replace_document_content(
        _with_content(new_base, text="§ 1. Nowy tekst.", quality=0.93)
    )
    session.flush()

    assert old_saved.document_version_id != new_saved.document_version_id
    assert session.scalar(select(func.count()).select_from(DocumentVersion)) >= 2
    assert session.scalar(
        select(func.count())
        .select_from(LegalUnit)
        .where(LegalUnit.document_version_id == old_saved.document_version_id)
    ) == 3
    assert all(session.get(LegalUnit, unit_id) is not None for unit_id in old_unit_ids)
    assert session.scalar(
        select(DocumentPage.text).where(
            DocumentPage.document_version_id == old_saved.document_version_id
        )
    ) == "§ 1. Stary tekst."

    old_read = repository.get_document_version(old_saved.document_version_id)
    new_read = repository.get_document_version(new_saved.document_version_id)
    assert old_read is not None and old_read.original_uri is None
    assert new_read is not None
    assert new_read.original_uri == "https://bip.example/new.pdf"
    assert new_read.ocr_engine_version == "Tesseract test"

    units = repository.get_legal_units_for_version(new_saved.document_version_id)
    assert [unit.order_index for unit in units] == [0, 1, 2]
    assert units[1].parent_id == units[0].id
    assert units[2].parent_id == units[1].id
    assert all(
        unit.document_version_id == new_saved.document_version_id
        for unit in units
    )
    assert repository.get_legal_unit(units[2].id).source_text == "1) punkt"

    same_again = repository.register_document_version(
        _registration(
            suffix,
            content_hash=f"new-{suffix}",
            published_at=now + timedelta(days=1),
            original_uri="https://bip.example/new.pdf",
        )
    )
    assert same_again.document_version_id == new_saved.document_version_id


def test_repository_lists_all_versions_for_planning_act_and_missing_cases(
    session: Session,
) -> None:
    suffix = uuid4().hex[:10]
    now = datetime(2026, 7, 24, tzinfo=timezone.utc)
    repository = SqlAlchemyDocumentRepository(session)
    base = repository.register_document_version(
        _registration(
            suffix,
            content_hash=f"one-{suffix}",
            published_at=now,
            original_uri="https://bip.example/one.pdf",
        )
    )
    from app.models.versioned import SourceDocument

    source_document = session.get(SourceDocument, base.source_document_id)
    assert source_document is not None
    documents = repository.get_documents_for_act(
        source_document.planning_act_id
    )

    assert [item.document_version_id for item in documents] == [
        base.document_version_id
    ]
    assert repository.get_document_version(2_000_000_000) is None
    assert repository.get_legal_unit(2_000_000_000) is None
    with pytest.raises(ValueError):
        repository.replace_document_content(
            replace(base, document_version_id=2_000_000_000)
        )


def test_database_rejects_high_confidence_rule_without_source_evidence(
    session: Session,
) -> None:
    suffix = uuid4().hex[:10]
    now = datetime(2026, 7, 24, tzinfo=timezone.utc)
    repository = SqlAlchemyDocumentRepository(session)
    base = repository.register_document_version(
        _registration(
            suffix,
            content_hash=f"rule-{suffix}",
            published_at=now,
            original_uri="https://bip.example/rule.pdf",
        )
    )
    saved = repository.replace_document_content(
        _with_content(base, text="§ 1. Wysokość 9 m.", quality=1.0)
    )
    legal_unit_id = saved.legal_units[-1].id
    assert legal_unit_id is not None
    session.add(
        PlanningRule(
            legal_unit_id=legal_unit_id,
            code="max_building_height",
            operator="lte",
            value=9.0,
            unit="m",
            source_text=None,
            raw_value="9 m",
            parser_version="constraint-test",
            confidence=0.9,
            review_status="unreviewed",
        )
    )

    with pytest.raises(DBAPIError):
        session.flush()


@pytest.mark.asyncio
async def test_html_document_runs_end_to_end_into_citable_rules(
    session: Session,
) -> None:
    suffix = uuid4().hex[:10]
    html = f"""
    <html><body>
      <h1>Rozdział 1. Ustalenia szczegółowe</h1>
      <p>§ 8. 1. Dla terenu oznaczonego symbolem 1MN ustala się
      maksymalną wysokość zabudowy 9,5 m i maksymalnie 2 kondygnacje.
      Przeznaczenie podstawowe: zabudowa mieszkaniowa.</p>
      <footer>menu serwisu {suffix}</footer>
    </body></html>
    """.encode()
    document = DocumentBlob(
        content=html,
        media_type="text/html",
        filename=f"plan-{suffix}.html",
        source_metadata=SourceMetadata(
            source_name="BIP_TEST",
            source_url=f"https://bip.example/{suffix}.html",
            fetched_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
            confidence=0.9,
            manual_review_required=False,
        ),
    )

    parsed = await parse_mpzp_document(document, ["1MN"])
    snapshot = persist_parser_audit(
        session,
        planning_act_identifier=f"e2e-{suffix}",
        document=document,
        parse_result=parsed,
    )
    session.flush()

    assert parsed.status == "complete"
    assert parsed.document_audit is not None
    assert snapshot is not None
    assert snapshot.extraction_method == "html"
    assert snapshot.pages[0].text
    assert snapshot.legal_units
    assert all(unit.page_from == 1 for unit in snapshot.legal_units)
    rules = session.scalars(
        select(PlanningRule).where(
            PlanningRule.legal_unit_id.in_(
                [
                    unit.id
                    for unit in snapshot.legal_units
                    if unit.id is not None
                ]
            )
        )
    ).all()
    assert {"max_building_height", "max_storeys", "primary_use"} <= {
        rule.code for rule in rules
    }
    assert all(rule.source_text for rule in rules)
    assert all(rule.parser_version == "mpzp-rules/2.0" for rule in rules)
