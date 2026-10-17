"""Adapter SQLAlchemy magazynu dokumentów prawnych."""

from __future__ import annotations

import json
from typing import cast
from urllib.parse import urlparse

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.versioned import (
    DataRelease,
    DataSource,
    DocumentPage,
    DocumentVersion,
    LegalUnit,
    SourceArtifact,
    SourceDocument,
    PlanningAct,
)
from app.modules.documents.application.ports import DocumentRepository
from app.modules.documents.domain.models import (
    DocumentPageSnapshot,
    DocumentRegistration,
    DocumentSnapshot,
    LegalUnitNode,
    LegalUnitType,
)


def _public_original_uri(uri: str | None) -> str | None:
    """Przepuszcza wyłącznie publiczny URL, nigdy ścieżkę kontenera."""
    if not uri:
        return None
    parsed = urlparse(uri)
    return uri if parsed.scheme in {"http", "https"} and parsed.netloc else None


class SqlAlchemyDocumentRepository(DocumentRepository):
    """Implementuje port na istniejącym wersjonowanym modelu SQLAlchemy."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_documents_for_act(self, planning_act_id: int) -> list[DocumentSnapshot]:
        rows = self._session.execute(
            select(DocumentVersion, SourceArtifact.uri)
            .join(
                SourceDocument,
                SourceDocument.id == DocumentVersion.source_document_id,
            )
            .join(
                SourceArtifact,
                SourceArtifact.id == DocumentVersion.source_artifact_id,
            )
            .where(SourceDocument.planning_act_id == planning_act_id)
            .order_by(DocumentVersion.published_at.desc(), DocumentVersion.id.desc())
        ).all()
        return [
            self._snapshot(version, artifact_uri)
            for version, artifact_uri in rows
        ]

    def get_document_version(
        self, document_version_id: int
    ) -> DocumentSnapshot | None:
        row = self._session.execute(
            select(DocumentVersion, SourceArtifact.uri)
            .join(
                SourceArtifact,
                SourceArtifact.id == DocumentVersion.source_artifact_id,
            )
            .where(DocumentVersion.id == document_version_id)
        ).one_or_none()
        if row is None:
            return None
        return self._snapshot(row[0], row[1])

    def get_legal_units_for_version(
        self, document_version_id: int
    ) -> list[LegalUnitNode]:
        records = self._session.scalars(
            select(LegalUnit)
            .where(LegalUnit.document_version_id == document_version_id)
            .order_by(LegalUnit.order_index, LegalUnit.id)
        ).all()
        return [self._unit(record) for record in records]

    def get_legal_unit(self, legal_unit_id: int) -> LegalUnitNode | None:
        record = self._session.get(LegalUnit, legal_unit_id)
        return self._unit(record) if record is not None else None

    def replace_document_content(
        self, snapshot: DocumentSnapshot
    ) -> DocumentSnapshot:
        version = self._session.scalars(
            select(DocumentVersion).where(DocumentVersion.id == snapshot.document_version_id).with_for_update()
        ).one_or_none()
        if version is None:
            raise ValueError(
                f"Nie istnieje DocumentVersion {snapshot.document_version_id}."
            )

        existing_units = self.get_legal_units_for_version(snapshot.document_version_id)
        if self._same_content(version, snapshot, existing_units):
            # Kolejne działki mogą cytować tę samą uchwałę. Identyczny wynik
            # ekstrakcji nie może usuwać jednostek wskazanych przez ich snapshoty.
            return self._snapshot(
                version,
                self._session.scalar(select(SourceArtifact.uri).where(SourceArtifact.id == version.source_artifact_id)),
                pages=snapshot.pages,
                legal_units=tuple(existing_units),
            )

        version.media_type = snapshot.media_type
        version.extraction_method = snapshot.extraction_method
        version.ocr_engine_version = snapshot.ocr_engine_version
        version.quality_score = snapshot.quality_score

        # Zastępujemy tylko wynik tej samej wersji. Inne DocumentVersion i ich
        # relacje pozostają nienaruszone, co zachowuje historię źródła.
        self._session.execute(
            delete(LegalUnit).where(
                LegalUnit.document_version_id == snapshot.document_version_id
            )
        )
        self._session.execute(
            delete(DocumentPage).where(
                DocumentPage.document_version_id == snapshot.document_version_id
            )
        )
        for page in snapshot.pages:
            self._session.add(
                DocumentPage(
                    document_version_id=snapshot.document_version_id,
                    page_number=page.page_number,
                    text=page.text,
                    ocr_used=page.ocr_used,
                    quality=page.quality,
                    blocks=list(page.blocks) or None,
                )
            )

        self._session.flush()
        key_to_id: dict[str, int] = {}
        persisted: list[LegalUnitNode] = []
        for unit in sorted(snapshot.legal_units, key=lambda item: item.order_index):
            parent_id = (
                key_to_id.get(unit.parent_key)
                if unit.parent_key is not None
                else unit.parent_id
            )
            record = LegalUnit(
                document_version_id=snapshot.document_version_id,
                unit_type=unit.unit_type,
                number=unit.number,
                parent_id=parent_id,
                order_index=unit.order_index,
                page_from=unit.page_from,
                page_to=unit.page_to,
                source_text=unit.source_text,
                normalized_text=unit.normalized_text,
            )
            self._session.add(record)
            self._session.flush()
            if unit.node_key is not None:
                key_to_id[unit.node_key] = record.id
            persisted.append(self._unit(record))

        return self._snapshot(
            version,
            self._session.scalar(
                select(SourceArtifact.uri).where(
                    SourceArtifact.id == version.source_artifact_id
                )
            ),
            pages=tuple(snapshot.pages),
            legal_units=tuple(persisted),
        )

    def _same_content(
        self, version: DocumentVersion, snapshot: DocumentSnapshot, existing_units: list[LegalUnitNode],
    ) -> bool:
        if (version.media_type, version.extraction_method, version.ocr_engine_version, version.quality_score) != (
            snapshot.media_type, snapshot.extraction_method, snapshot.ocr_engine_version, snapshot.quality_score,
        ):
            return False
        pages = self._session.scalars(
            select(DocumentPage).where(DocumentPage.document_version_id == version.id).order_by(DocumentPage.page_number)
        ).all()
        expected_pages = sorted(snapshot.pages, key=lambda page: page.page_number)
        if len(pages) != len(expected_pages) or len(existing_units) != len(snapshot.legal_units):
            return False
        for stored, expected in zip(pages, expected_pages):
            if (stored.page_number, stored.text, stored.ocr_used, stored.quality) != (
                expected.page_number, expected.text, expected.ocr_used, expected.quality,
            ) or json.dumps(stored.blocks or [], sort_keys=True) != json.dumps(expected.blocks, sort_keys=True):
                return False
        # Klucze drzewa parsera są tymczasowe; porównujemy ich relacje z zapisanymi
        # ID, zamiast traktować brak nowych ID jako zmianę treści.
        key_to_id: dict[str, int | None] = {}
        fields = ("unit_type", "number", "order_index", "page_from", "page_to", "source_text", "normalized_text")
        expected_units = sorted(snapshot.legal_units, key=lambda unit: unit.order_index)
        for stored_unit, expected_unit in zip(existing_units, expected_units):
            if expected_unit.parent_key is not None and expected_unit.parent_key not in key_to_id:
                return False
            parent_id = (
                key_to_id[expected_unit.parent_key] if expected_unit.parent_key is not None else expected_unit.parent_id
            )
            if parent_id != stored_unit.parent_id or any(
                getattr(stored_unit, field) != getattr(expected_unit, field) for field in fields
            ):
                return False
            if expected_unit.node_key is not None:
                key_to_id[expected_unit.node_key] = stored_unit.id
        return True

    def register_document_version(
        self, registration: DocumentRegistration
    ) -> DocumentSnapshot:
        # Wspólne źródło/artefakt/wydanie mogą być tworzone przez różne procesy
        # analizujące inne działki. Konflikt naturalnego klucza jest idempotentnym
        # zapisem, bez przerwania transakcji i bez nadpisywania metadanych źródła.
        self._session.execute(
            pg_insert(DataSource).values(
                source_id=registration.source_id,
                owner=registration.source_owner,
                status="production",
                access_type="document",
            ).on_conflict_do_nothing(index_elements=[DataSource.source_id])
        )
        source = self._session.scalars(
            select(DataSource).where(
                DataSource.source_id == registration.source_id
            )
        ).one()

        self._session.execute(
            pg_insert(SourceArtifact).values(
                data_source_id=source.id,
                uri=registration.original_uri,
                media_type=registration.media_type,
                content_hash=registration.content_hash,
                fetched_at=registration.published_at,
            ).on_conflict_do_nothing(index_elements=[SourceArtifact.data_source_id, SourceArtifact.content_hash])
        )
        artifact = self._session.scalars(
            select(SourceArtifact).where(
                SourceArtifact.data_source_id == source.id,
                SourceArtifact.content_hash == registration.content_hash,
            )
        ).one()

        release_label = f"document-{registration.content_hash[:32]}"
        self._session.execute(
            pg_insert(DataRelease).values(
                data_source_id=source.id,
                version_label=release_label,
                published_at=registration.published_at,
                importer_version="documents-parser/1.0",
            ).on_conflict_do_nothing(index_elements=[DataRelease.data_source_id, DataRelease.version_label])
        )
        release = self._session.scalars(
            select(DataRelease).where(
                DataRelease.data_source_id == source.id,
                DataRelease.version_label == release_label,
            )
        ).one()

        self._session.execute(
            pg_insert(PlanningAct).values(
                act_identifier=registration.planning_act_identifier,
                kind="mpzp",
            ).on_conflict_do_nothing(index_elements=[PlanningAct.act_identifier])
        )
        # Blokada dotyczy tylko danego aktu i trwa do commit/rollback wywołującego.
        # Chroni także SourceDocument, DocumentVersion i późniejszą wymianę stron/
        # jednostek prawnych. Pobieranie i parsowanie odbywa się przed rejestracją.
        act = self._session.scalars(
            select(PlanningAct).where(
                PlanningAct.act_identifier
                == registration.planning_act_identifier
            ).with_for_update()
        ).one()

        source_document = self._session.scalar(
            select(SourceDocument).where(
                SourceDocument.planning_act_id == act.id,
                SourceDocument.document_type == registration.document_type,
            )
        )
        if source_document is None:
            source_document = SourceDocument(
                planning_act_id=act.id,
                data_source_id=source.id,
                title=registration.title,
                document_type=registration.document_type,
            )
            self._session.add(source_document)
            self._session.flush()

        version = self._session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.source_document_id == source_document.id,
                DocumentVersion.content_hash == registration.content_hash,
            )
        )
        if version is None:
            version = DocumentVersion(
                source_document_id=source_document.id,
                source_artifact_id=artifact.id,
                data_release_id=release.id,
                valid_from=registration.published_at,
                valid_to=None,
                published_at=registration.published_at,
                content_hash=registration.content_hash,
                review_status="unreviewed",
                media_type=registration.media_type,
            )
            self._session.add(version)
            self._session.flush()
        return self._snapshot(version, artifact.uri)

    @staticmethod
    def _snapshot(
        version: DocumentVersion,
        artifact_uri: str | None,
        *,
        pages: tuple[DocumentPageSnapshot, ...] = (),
        legal_units: tuple[LegalUnitNode, ...] = (),
    ) -> DocumentSnapshot:
        return DocumentSnapshot(
            document_version_id=version.id,
            source_document_id=version.source_document_id,
            source_artifact_id=version.source_artifact_id,
            content_hash=version.content_hash,
            media_type=version.media_type,
            extraction_method=version.extraction_method,
            ocr_engine_version=version.ocr_engine_version,
            quality_score=version.quality_score,
            original_uri=_public_original_uri(artifact_uri),
            valid_from=version.valid_from,
            valid_to=version.valid_to,
            published_at=version.published_at,
            review_status=version.review_status,
            pages=pages,
            legal_units=legal_units,
        )

    @staticmethod
    def _unit(record: LegalUnit) -> LegalUnitNode:
        return LegalUnitNode(
            id=record.id,
            document_version_id=record.document_version_id,
            unit_type=cast(LegalUnitType, record.unit_type),
            number=record.number,
            parent_id=record.parent_id,
            order_index=record.order_index,
            page_from=record.page_from,
            page_to=record.page_to,
            source_text=record.source_text,
            normalized_text=record.normalized_text,
        )
