"""Porty magazynu i etapów parsera dokumentów."""

from __future__ import annotations

from typing import Protocol

from app.modules.documents.domain.models import (
    DocumentSnapshot,
    DocumentRegistration,
    LegalUnitNode,
)


class DocumentRepository(Protocol):
    """Port trwałego magazynu cytowalnych wersji dokumentów."""

    def get_documents_for_act(self, planning_act_id: int) -> list[DocumentSnapshot]:
        """Zwraca wszystkie wersje dokumentów aktu, bez nadpisywania historii."""

    def get_document_version(
        self, document_version_id: int
    ) -> DocumentSnapshot | None:
        """Zwraca metadane jednej wersji."""

    def get_legal_units_for_version(
        self, document_version_id: int
    ) -> list[LegalUnitNode]:
        """Zwraca jednostki w deterministycznej kolejności źródłowej."""

    def get_legal_unit(self, legal_unit_id: int) -> LegalUnitNode | None:
        """Zwraca pojedynczy cytowalny fragment."""

    def replace_document_content(
        self, snapshot: DocumentSnapshot
    ) -> DocumentSnapshot:
        """Atomowo zastępuje ekstrakcję wyłącznie wskazanej wersji dokumentu."""

    def register_document_version(
        self, registration: DocumentRegistration
    ) -> DocumentSnapshot:
        """Idempotentnie rejestruje lineage i zwraca wersję do parsowania."""
