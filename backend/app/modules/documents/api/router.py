"""Publiczne API cytowalnych dokumentów planistycznych."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.rate_limit import rate_limit
from app.core.settings import settings
from app.db.session import get_db
from app.modules.documents.api.schemas import (
    DocumentVersionResponse,
    LegalUnitResponse,
)
from app.modules.documents.application.service import (
    DocumentNotFoundError,
    DocumentService,
)
from app.modules.documents.composition import build_document_service
from app.modules.documents.domain.models import LegalUnitNode

# Publiczne odczyty dokumentów (AU-006): limit per klient, ten sam klucz co w całej aplikacji.
_documents_limit = rate_limit(settings.rate_limit_data_per_minute)

router = APIRouter(
    prefix="/api/v1", tags=["planning-documents"], dependencies=[Depends(_documents_limit)]
)


def get_document_service(
    db: Annotated[Session, Depends(get_db)],
) -> DocumentService:
    return build_document_service(db)


def _unit_response(unit: LegalUnitNode) -> LegalUnitResponse:
    if unit.id is None or unit.document_version_id is None:
        raise ValueError("API może zwracać wyłącznie utrwalone jednostki prawne.")
    return LegalUnitResponse(
        id=unit.id,
        document_version_id=unit.document_version_id,
        unit_type=unit.unit_type,
        number=unit.number,
        parent_id=unit.parent_id,
        order_index=unit.order_index,
        page_from=unit.page_from,
        page_to=unit.page_to,
        source_text=unit.source_text,
        normalized_text=unit.normalized_text,
        children=[_unit_response(child) for child in unit.children],
    )


@router.get(
    "/planning-acts/{planning_act_id}/documents",
    response_model=list[DocumentVersionResponse],
    description=(
        "Zwraca historyczne wersje dokumentów aktu wraz z metadanymi "
        "ekstrakcji i bezpieczną referencją do oryginału."
    ),
)
def get_planning_act_documents(
    planning_act_id: int,
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> list[DocumentVersionResponse]:
    return [
        DocumentVersionResponse(
            id=item.document_version_id,
            source_document_id=item.source_document_id,
            source_artifact_id=item.source_artifact_id,
            content_hash=item.content_hash,
            media_type=item.media_type,
            extraction_method=item.extraction_method,
            ocr_engine_version=item.ocr_engine_version,
            quality_score=item.quality_score,
            original_uri=item.original_uri,
            valid_from=item.valid_from,
            valid_to=item.valid_to,
            published_at=item.published_at,
            review_status=item.review_status,
            legal_units_url=(
                f"/api/v1/document-versions/{item.document_version_id}/legal-units"
            ),
        )
        for item in service.get_planning_act_documents(planning_act_id)
    ]


@router.get(
    "/document-versions/{document_version_id}/legal-units",
    response_model=list[LegalUnitResponse],
    description="Zwraca hierarchię rozdziałów, paragrafów, ustępów i punktów.",
)
def get_document_legal_units(
    document_version_id: int,
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> list[LegalUnitResponse]:
    try:
        units = service.get_document_legal_units(document_version_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail="Wersja dokumentu nie istnieje."
        ) from exc
    return [_unit_response(unit) for unit in units]


@router.get(
    "/legal-units/{legal_unit_id}",
    response_model=LegalUnitResponse,
    description="Zwraca cytowalny fragment wraz z dosłownym tekstem źródłowym.",
)
def get_legal_unit(
    legal_unit_id: int,
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> LegalUnitResponse:
    try:
        return _unit_response(service.get_legal_unit(legal_unit_id))
    except DocumentNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail="Jednostka prawna nie istnieje."
        ) from exc
