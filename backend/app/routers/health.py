from fastapi import APIRouter
from pydantic import BaseModel


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
