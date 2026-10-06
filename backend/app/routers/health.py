import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.core.access_control import AdminOperator
from app.db.session import SessionLocal
from app.modules.planning import composition as planning_composition
from app.modules.planning.application import llm_metrics
from app.modules.planning.application.llm_monitoring import HealthStatus, LlmHealth

logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    status: str
    service: str


class LlmComponentHealth(BaseModel):
    """Komponent ścieżki modelu językowego (PV3-19): ``ok``, ``degraded`` albo ``disabled`` — nigdy „failed”."""

    status: HealthStatus
    reasons: list[str] = Field(default_factory=list, description="Stałe kody powodów degradacji; puste przy ok/disabled.")
    mode: str | None = Field(default=None, description="Tryb parsera MPZP (legacy, v3, hybrid_shadow, hybrid).")


class HealthComponents(BaseModel):
    llm: LlmComponentHealth


class ServiceHealthResponse(HealthResponse):
    components: HealthComponents


class LlmHealthDetails(BaseModel):
    status: Literal["ok", "degraded", "disabled"]
    reasons: list[str]
    warnings: list[str]
    details: dict[str, Any]
    counters: dict[str, int]
    gauges: dict[str, float]
    window_counters: dict[str, int]


router = APIRouter(prefix="", tags=["health"])


def _llm_health() -> LlmHealth:
    # Zdrowie ścieżki modelu nie wpływa na ``status`` usługi ani na gotowość: ścieżka jest opcjonalna,
    # a rdzeń deterministyczny odpowiada bez niej. Ocena nie wywołuje dostawcy i nie zgłasza wyjątków.
    return planning_composition.llm_health()


@router.get(
    "/health",
    response_model=ServiceHealthResponse,
    description=(
        "Zwraca podstawowy status działania backendu i stan komponentów opcjonalnych. Komponent `llm` "
        "raportuje `ok`, `degraded` albo `disabled` (nigdy `failed`) i nie wpływa na gotowość usługi."
    ),
)
def health() -> ServiceHealthResponse:
    component = _llm_health().component()
    return ServiceHealthResponse(
        status="ok",
        service="backend",
        components=HealthComponents(llm=LlmComponentHealth(**component)),
    )


@router.get(
    "/health/llm",
    response_model=LlmHealthDetails,
    responses={401: {"description": "Brak klucza administracyjnego."}, 403: {"description": "Klucz niepoprawny."}},
    description=(
        "Szczegóły ścieżki modelu językowego dla operatora (klucz `X-Admin-Key`): powody degradacji, odsetki "
        "odrzuceń i degradacji, zużycie, progi alarmów, przypięcie wersji, liczniki. Nie zawiera treści żądań "
        "ani kluczy."
    ),
)
def health_llm(operator: AdminOperator) -> LlmHealthDetails:
    del operator  # klucz uwierzytelnia; operator nie jest tu potrzebny
    health_state = _llm_health()
    thresholds = planning_composition.alarm_thresholds()
    return LlmHealthDetails(
        status=health_state.status,
        reasons=list(health_state.reasons),
        warnings=list(health_state.warnings),
        details=dict(health_state.details),
        counters=llm_metrics.metrics.snapshot(),
        gauges=llm_metrics.metrics.gauges(),
        window_counters=llm_metrics.metrics.window_totals(thresholds.window_seconds),
    )


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
