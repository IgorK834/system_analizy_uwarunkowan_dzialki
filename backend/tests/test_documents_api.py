from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.modules.documents.api.router import (
    _unit_response,
    get_document_legal_units,
    get_document_service,
    get_legal_unit,
    get_planning_act_documents,
)
from app.modules.documents.application.service import DocumentNotFoundError
from app.modules.documents.domain.models import DocumentSnapshot, LegalUnitNode


def _unit() -> LegalUnitNode:
    return LegalUnitNode(
        id=10,
        document_version_id=7,
        unit_type="paragraph",
        number="8",
        order_index=0,
        page_from=4,
        page_to=4,
        source_text="§ 8. Tekst źródłowy.",
        normalized_text="§ 8. Tekst źródłowy.",
        children=(
            LegalUnitNode(
                id=11,
                document_version_id=7,
                unit_type="point",
                number="1",
                parent_id=10,
                order_index=1,
                page_from=4,
                page_to=4,
                source_text="1) punkt",
            ),
        ),
    )


class _Service:
    def __init__(self, *, missing: bool = False) -> None:
        self.missing = missing

    def get_planning_act_documents(self, planning_act_id: int):
        if planning_act_id != 5:
            return []
        now = datetime(2026, 7, 24, tzinfo=timezone.utc)
        return [
            DocumentSnapshot(
                document_version_id=7,
                source_document_id=6,
                source_artifact_id=4,
                content_hash="sha256",
                media_type="application/pdf",
                extraction_method="ocr",
                ocr_engine_version="Tesseract 5.5.0",
                quality_score=0.94,
                original_uri="https://bip.example/uchwala.pdf",
                valid_from=now,
                published_at=now,
            )
        ]

    def get_document_legal_units(self, document_version_id: int):
        if self.missing:
            raise DocumentNotFoundError
        return (_unit(),)

    def get_legal_unit(self, legal_unit_id: int):
        if self.missing:
            raise DocumentNotFoundError
        return _unit()


def test_documents_api_serializes_versions_and_citable_tree() -> None:
    service = _Service()

    documents = get_planning_act_documents(5, service)
    units = get_document_legal_units(7, service)
    fragment = get_legal_unit(10, service)

    assert documents[0].original_uri == "https://bip.example/uchwala.pdf"
    assert documents[0].legal_units_url.endswith("/7/legal-units")
    assert documents[0].ocr_engine_version == "Tesseract 5.5.0"
    assert units[0].children[0].source_text == "1) punkt"
    assert fragment.source_text == "§ 8. Tekst źródłowy."
    assert _unit_response(_unit()).page_from == 4


def test_documents_api_maps_missing_resources_to_404() -> None:
    service = _Service(missing=True)

    with pytest.raises(HTTPException) as version_error:
        get_document_legal_units(999, service)
    with pytest.raises(HTTPException) as unit_error:
        get_legal_unit(999, service)

    assert version_error.value.status_code == 404
    assert unit_error.value.status_code == 404


def test_document_service_dependency_uses_composition(monkeypatch) -> None:
    sentinel = object()
    monkeypatch.setattr(
        "app.modules.documents.api.router.build_document_service",
        lambda db: sentinel,
    )
    assert get_document_service(object()) is sentinel
