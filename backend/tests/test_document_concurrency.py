"""Regresja wyścigu KIMPZP z live smoke: dwie niezależne transakcje PostgreSQL.

Wejście: pełne 99 stron rzeczywistej uchwały Stare Miasto XII/131/11 i jej metadane
z zamrożonego korpusu, tego samego dokumentu co w błędzie 126105_9.0001.580/4.
Nie odtwarzamy PDF z tekstu: testuje się zapis ekstrakcji oraz oryginalnego SHA-256.
"""
from concurrent.futures import ThreadPoolExecutor
import asyncio
from dataclasses import replace
from datetime import datetime
from functools import lru_cache
import json
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import event, func, select, text

from app.db.session import engine, SessionLocal
from app.models.versioned import (
    DataRelease, DataSource, DocumentPage, DocumentVersion, LegalUnit, PlanningAct, SourceArtifact, SourceDocument,
)
from app.modules.documents.domain.legal_structure import build_legal_units
from app.modules.documents.domain.models import DocumentPageSnapshot, DocumentRegistration
from app.modules.documents.domain.parser_models import DocumentTextSegment
from app.modules.documents.infrastructure.repository import SqlAlchemyDocumentRepository
from tests.test_mpzp_live_corpus import _parse

pytestmark = pytest.mark.integration
FIXTURE = Path(__file__).parent / "fixtures/mpzp_live/krakow_stare_miasto_xii_131_11"


@pytest.fixture
def empty_document_database():
    # conftest._isolated_migration_database tworzy dla tego modułu jednorazową bazę.
    assert engine.url.database.startswith("test_migrations_")
    with SessionLocal.begin() as db:
        db.execute(text("TRUNCATE data_sources, planning_acts RESTART IDENTITY CASCADE"))


@lru_cache
def real_document(act: str):
    source = json.loads((FIXTURE / "source.json").read_text())
    audit = asyncio.run(_parse("krakow_stare_miasto_xii_131_11", ["MW/U.4"], "legacy")).document_audit
    assert audit is not None
    registration = DocumentRegistration(
        planning_act_identifier=act, source_id="kimpzp", source_owner="GUGiK / właściwa gmina",
        original_uri=source["url"], media_type=source["media_type"], content_hash=source["document_sha256"],
        published_at=datetime.fromisoformat(source["fetched_at"]), title=source["filename"],
    )
    pages = tuple(DocumentPageSnapshot(page_number=page.page_number, text=page.text, ocr_used=page.ocr_used,
                                      quality=page.quality, blocks=tuple(page.blocks)) for page in audit.pages)
    units = build_legal_units(tuple(DocumentTextSegment(segment_id=segment.segment_id, text=segment.text,
        page_number=segment.page_number, heading=segment.heading, source=segment.source) for segment in audit.segments))
    assert len(pages) == 99 and units
    return registration, pages, units


@pytest.mark.parametrize("existing_source", [False, True], ids=["cold-source", "existing-source"])
@pytest.mark.parametrize("same_act", [False, True], ids=["different-acts", "same-act"])
def test_parallel_registration_preserves_complete_lineage_and_pages(empty_document_database, existing_source, same_act):
    if existing_source:
        with SessionLocal.begin() as db:
            db.add(DataSource(source_id="kimpzp", owner="Właściciel z katalogu", status="production", access_type="wms"))
    registration, pages, units = real_document("concurrent-stare-miasto")
    start = Barrier(2)
    absent_read = Barrier(2)
    race_table = "source_artifacts" if existing_source else "data_sources"

    def both_observed_absence(conn, cursor, statement, parameters, context, executemany):
        # Stary SELECT→INSERT musi w obu sesjach zobaczyć brak rekordu PRZED wstawieniem.
        # Atomowe INSERT→SELECT widzi już istniejący wiersz i nie potrzebuje tej bariery.
        if statement.lstrip().startswith("SELECT") and f"FROM {race_table}" in statement and cursor.rowcount == 0:
            absent_read.wait(timeout=10)

    def write(index):
        with SessionLocal.begin() as db:
            db.execute(text("SET LOCAL lock_timeout = '10s'"))
            db.execute(text("SET LOCAL statement_timeout = '15s'"))
            pid = db.scalar(text("SELECT pg_backend_pid()"))
            start.wait(timeout=10)
            repository = SqlAlchemyDocumentRepository(db)
            entry = registration if same_act else replace(registration, planning_act_identifier=f"{registration.planning_act_identifier}-{index}")
            snapshot = repository.register_document_version(entry)
            saved = repository.replace_document_content(replace(snapshot, pages=pages, legal_units=units,
                                                                 extraction_method="pdf_text", quality_score=1.0))
            return pid, snapshot.document_version_id, tuple(unit.id for unit in saved.legal_units)

    event.listen(engine, "after_cursor_execute", both_observed_absence)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(write, index) for index in range(2)]
            results = [future.result(timeout=30) for future in futures]
    finally:
        event.remove(engine, "after_cursor_execute", both_observed_absence)

    # Powtórzenie na kompletnym istniejącym łańcuchu obejmuje równoległą wymianę stron.
    if same_act:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(write, index) for index in range(2)]
            repeated = [future.result(timeout=30) for future in futures]
            assert [result[1:] for result in [*results, *repeated]] == [results[0][1:]] * 4

    with SessionLocal() as db:
        expected_versions = 1 if same_act else 2
        assert len({result[0] for result in results}) == 2
        assert len({result[1] for result in results}) == expected_versions
        for model, count in ((DataSource, 1), (SourceArtifact, 1), (DataRelease, 1),
                             (PlanningAct, expected_versions), (SourceDocument, expected_versions),
                             (DocumentVersion, expected_versions), (DocumentPage, len(pages) * expected_versions),
                             (LegalUnit, len(units) * expected_versions)):
            assert db.scalar(select(func.count()).select_from(model)) == count
        source = db.scalar(select(DataSource))
        if existing_source:
            assert source.owner == "Właściciel z katalogu" and source.access_type == "wms"
        for version in db.scalars(select(DocumentVersion)):
            assert version.content_hash == registration.content_hash
            stored = db.scalars(select(DocumentPage).where(DocumentPage.document_version_id == version.id)
                                .order_by(DocumentPage.page_number)).all()
            assert [(page.page_number, page.text) for page in stored] == [(page.page_number, page.text) for page in pages]
        for _, _, identifiers in results:
            assert all(db.get(LegalUnit, identifier) is not None for identifier in identifiers)


def test_identical_reparse_preserves_citations_but_changed_extraction_is_saved(empty_document_database):
    registration, pages, units = real_document("reparsed-stare-miasto")
    with SessionLocal.begin() as db:
        repository = SqlAlchemyDocumentRepository(db)
        base = repository.register_document_version(registration)
        original = replace(base, pages=pages, legal_units=units, extraction_method="pdf_text", quality_score=1.0)
        first = repository.replace_document_content(original)
        repeated = repository.replace_document_content(original)
        assert [unit.id for unit in repeated.legal_units] == [unit.id for unit in first.legal_units]
        changed_pages = (replace(pages[0], quality=0.9), *pages[1:])
        changed_units = (replace(units[0], normalized_text="kontrolna korekta ekstrakcji"), *units[1:])
        changed = repository.replace_document_content(replace(original, pages=changed_pages, legal_units=changed_units))
        assert changed.pages[0].quality == 0.9
        assert changed.legal_units[0].normalized_text == "kontrolna korekta ekstrakcji"
        assert changed.legal_units[0].id != first.legal_units[0].id


def test_registration_rollback_leaves_no_partial_lineage(empty_document_database):
    registration, _, _ = real_document("rolled-back-stare-miasto")
    with SessionLocal() as db:
        SqlAlchemyDocumentRepository(db).register_document_version(registration)
        db.rollback()
    with SessionLocal() as db:
        for model in (DataSource, SourceArtifact, DataRelease, PlanningAct, SourceDocument, DocumentVersion):
            assert db.scalar(select(func.count()).select_from(model)) == 0
