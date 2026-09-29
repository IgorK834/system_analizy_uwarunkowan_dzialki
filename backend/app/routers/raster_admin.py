"""Administracyjny kontrakt ręcznej akceptacji rastrów planistycznych.

Akceptacja rastra (``verified``) jest jedynym sposobem dopuszczenia go do warstwy
mapy. Endpointy zapisu wymagają klucza ``X-Admin-Key`` (``ADMIN_API_KEYS``);
operator w audycie ManualReview (``subject_type='raster_asset'``) pochodzi z
klucza, a nie z treści żądania. ``operator_id`` w treści jest opcjonalny i jeśli
podany, musi zgadzać się z uwierzytelnionym operatorem.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.access_control import AdminOperator
from app.db.session import get_db
from app.models.versioned import RasterAsset
from app.modules.imports.application.raster_import import (
    RasterReviewError,
    accept_raster_asset,
    reject_raster_asset,
)
from app.modules.imports.infrastructure.raster.repository import (
    SqlAlchemyRasterRepository,
)
from app.services.raster_assets import list_servable_rasters

router = APIRouter(prefix="/api/v1/raster-assets", tags=["raster-assets"])


class RasterReviewRequest(BaseModel):
    operator_id: str | None = Field(default=None, min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=2000)


class RasterAssetStatus(BaseModel):
    id: int
    review_status: str
    rmse_m: float | None
    planning_act_version_id: int | None
    qa_report: dict | None


class RasterReviewResponse(BaseModel):
    raster_asset_id: int
    review_status: str
    manual_review_id: int


def _ensure_operator_matches(payload: RasterReviewRequest, operator: str) -> None:
    if payload.operator_id is not None and payload.operator_id != operator:
        raise HTTPException(
            status_code=403,
            detail="operator_id nie zgadza się z operatorem klucza administracyjnego.",
        )


@router.get("/servable", description="Lista zaakceptowanych rastrów gotowych do mapy.")
def get_servable(db: Annotated[Session, Depends(get_db)]) -> list[dict]:
    return list_servable_rasters(db)


@router.get(
    "/{raster_asset_id}",
    response_model=RasterAssetStatus,
    responses={404: {"description": "Raster nie istnieje."}},
)
def get_raster_asset(
    raster_asset_id: int, db: Annotated[Session, Depends(get_db)]
) -> RasterAssetStatus:
    asset = db.get(RasterAsset, raster_asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Raster nie istnieje.")
    return RasterAssetStatus(
        id=asset.id,
        review_status=asset.review_status,
        rmse_m=asset.rmse_m,
        planning_act_version_id=asset.planning_act_version_id,
        qa_report=asset.qa_report,
    )


@router.post(
    "/{raster_asset_id}/accept",
    response_model=RasterReviewResponse,
    description="Ręcznie akceptuje raster (verified) z audytem ManualReview.",
    responses={
        401: {"description": "Brak lub nieprawidłowy X-Admin-Key."},
        403: {"description": "Endpointy administracyjne wyłączone lub operator niezgodny."},
        404: {"description": "Raster nie istnieje."},
    },
)
def accept(
    raster_asset_id: int,
    payload: RasterReviewRequest,
    db: Annotated[Session, Depends(get_db)],
    operator: AdminOperator,
) -> RasterReviewResponse:
    _ensure_operator_matches(payload, operator)
    repository = SqlAlchemyRasterRepository(db)
    try:
        outcome = accept_raster_asset(
            repository,
            raster_asset_id=raster_asset_id,
            operator_id=operator,
            reason=payload.reason,
        )
    except RasterReviewError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db.commit()
    return RasterReviewResponse(
        raster_asset_id=outcome.raster_asset_id,
        review_status=outcome.review_status,
        manual_review_id=outcome.manual_review_id,
    )


@router.post(
    "/{raster_asset_id}/reject",
    response_model=RasterReviewResponse,
    description="Ręcznie odrzuca raster (rejected) z audytem ManualReview.",
    responses={
        401: {"description": "Brak lub nieprawidłowy X-Admin-Key."},
        403: {"description": "Endpointy administracyjne wyłączone lub operator niezgodny."},
        404: {"description": "Raster nie istnieje."},
    },
)
def reject(
    raster_asset_id: int,
    payload: RasterReviewRequest,
    db: Annotated[Session, Depends(get_db)],
    operator: AdminOperator,
) -> RasterReviewResponse:
    _ensure_operator_matches(payload, operator)
    repository = SqlAlchemyRasterRepository(db)
    try:
        outcome = reject_raster_asset(
            repository,
            raster_asset_id=raster_asset_id,
            operator_id=operator,
            reason=payload.reason,
        )
    except RasterReviewError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db.commit()
    return RasterReviewResponse(
        raster_asset_id=outcome.raster_asset_id,
        review_status=outcome.review_status,
        manual_review_id=outcome.manual_review_id,
    )
