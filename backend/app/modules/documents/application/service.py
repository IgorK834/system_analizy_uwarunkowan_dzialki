"""Przypadki użycia odczytu dokumentów i cytowalnych fragmentów."""

from __future__ import annotations

from app.modules.documents.application.ports import DocumentRepository
from app.modules.documents.domain.models import (
    DocumentSnapshot,
    LegalUnitNode,
    build_legal_unit_tree,
)


class DocumentNotFoundError(Exception):
    """Nie istnieje wskazana wersja dokumentu albo jednostka prawna."""


class DocumentService:
    """Cienka warstwa przypadków użycia nad portem magazynu."""

    def __init__(self, repository: DocumentRepository) -> None:
        self._repository = repository

    def get_planning_act_documents(
        self, planning_act_id: int
    ) -> list[DocumentSnapshot]:
        return self._repository.get_documents_for_act(planning_act_id)

    def get_document_legal_units(
        self, document_version_id: int
    ) -> tuple[LegalUnitNode, ...]:
        if self._repository.get_document_version(document_version_id) is None:
            raise DocumentNotFoundError
        units = tuple(
            self._repository.get_legal_units_for_version(document_version_id)
        )
        return build_legal_unit_tree(units)

    def get_legal_unit(self, legal_unit_id: int) -> LegalUnitNode:
        unit = self._repository.get_legal_unit(legal_unit_id)
        if unit is None:
            raise DocumentNotFoundError
        return unit
