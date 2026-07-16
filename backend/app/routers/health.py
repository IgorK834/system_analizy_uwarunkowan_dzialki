import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from app.db.session import SessionLocal

logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    status: str
    service: str


router = APIRouter(prefix="", tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    description="Zwraca podstawowy status działania backendu.",
)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="backend")


@router.get(
    "/health/live",
    response_model=HealthResponse,
    description="Potwierdza działanie procesu backendu bez sprawdzania bazy.",
)
def health_live() -> HealthResponse:
    return HealthResponse(status="ok", service="backend")


@router.get(
    "/health/ready",
    response_model=HealthResponse,
    responses={503: {"description": "Baza danych jest niedostępna."}},
    description="Potwierdza gotowość backendu przez lekkie zapytanie do bazy.",
)
def health_ready() -> HealthResponse:
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
    except Exception as exc:
        # Logujemy tylko typ błędu, ponieważ treść wyjątku sterownika może
        # zawierać pełny connection string wraz z poświadczeniami.
        logger.warning("readiness_check_failed error_type=%s", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="Baza danych jest niedostępna.",
        ) from exc
    return HealthResponse(status="ok", service="backend")
