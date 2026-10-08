"""Cienka warstwa HTTP dla uruchamiania i ręcznego wznawiania analizy."""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Path, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.access_control import (
    ANALYSIS_TOKEN_HEADER,
    ensure_analysis_access,
    require_analysis_token,
)
from app.core.rate_limit import rate_limit, rate_limit_refresh
from app.core.settings import settings
from app.db.session import get_db
from app.models.analysis import Analysis
from app.models.analysis_pending_document import AnalysisPendingDocument
from app.schemas.analyze import (
    AddressAnalyzeRequest,
    AnalyzeResponse,
    AnalyzeResumeRequest,
    ErrorResponse,
    MapAnalyzeRequest,
    ParcelIdAnalyzeRequest,
)
from app.services.analysis_resume import (
    AnalysisResumeDocumentError,
    AnalysisResumeNotFoundError,
    AnalysisResumeStateError,
    resume_analysis_with_zone,
)
from app.services.analysis_orchestrator import run_analysis
from app.services.mpzp_zones import (
    InvalidZoneSymbolError,
)

router = APIRouter(prefix="/analyze", tags=["analyze"])

# Każda analiza odpytuje zewnętrzne usługi GIS; ``force_refresh`` omija cache, więc
# ma osobny, ostrzejszy limit. Zapis pobrany z cache też liczy się do limitu ogólnego.
_analyze_limit = rate_limit(settings.rate_limit_analyze_per_minute)
_refresh_limit = rate_limit_refresh(settings.rate_limit_refresh_per_minute)
_document_limit = rate_limit(settings.rate_limit_report_per_minute)


def authorized_resume_request(
    request: AnalyzeResumeRequest,
    x_analysis_token: Annotated[
        str | None,
        Header(
            alias=ANALYSIS_TOKEN_HEADER,
            description="Token dostępu z odpowiedzi analizy (alternatywa dla pola ``access_token``).",
        ),
    ] = None,
) -> AnalyzeResumeRequest:
    """Zależność: 403, gdy brak poprawnego tokenu analizy (AU-005).

    Wykonuje się przed ``get_db`` i przed jakimkolwiek odczytem bazy, więc 403 jest
    identyczne dla analizy istniejącej i nieistniejącej; 404/409 zwracamy dopiero
    po poprawnym tokenie. Token przyjmujemy w body albo w nagłówku.
    """
    ensure_analysis_access(request.analysis_id, request.access_token, x_analysis_token)
    return request


@router.post(
    "",
    response_model=AnalyzeResponse,
    responses={
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
        501: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
    dependencies=[Depends(_analyze_limit), Depends(_refresh_limit)],
    description=(
        "Uruchamia analizę uwarunkowań przestrzennych działki. Obsługuje trzy "
        "metody wejścia: kliknięcie w mapę, adres lub identyfikator działki ULDK. "
        "Jeżeli gmina nie udostępnia wektorowych danych MPZP, analiza jest "
        "zapisywana ze statusem oczekującym i wymaga wznowienia przez "
        "POST /analyze/resume z symbolem strefy odczytanym z mapy rastrowej. "
        "Błędy domenowe (brak działki 404, błędne dane wejściowe 422, usługa "
        "zewnętrzna 502/503, odrzucony zapis 503 PERSISTENCE_FAILED) mają ciało "
        "ErrorResponse z kodem i request_id; mapowanie wyjątków na kody jest "
        "w app/routers/error_handlers.py."
    ),
)
async def analyze(
    request: Annotated[
        MapAnalyzeRequest | AddressAnalyzeRequest | ParcelIdAnalyzeRequest,
        Body(discriminator="method", title="AnalyzeRequest"),
    ],
    force_refresh: bool = Query(
        False,
        description="Pomija świeży cache i uruchamia nową analizę.",
    ),
    db: Session = Depends(get_db),
) -> AnalyzeResponse:
    return await run_analysis(request, db, force_refresh=force_refresh)


@router.post(
    "/resume",
    response_model=AnalyzeResponse,
    responses={
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
    dependencies=[Depends(_analyze_limit)],
    description=(
        "Wymaga tokenu dostępu analizy (``access_token`` w body albo nagłówek "
        "``X-Analysis-Token``): bez niego 403 — niezależnie od tego, czy analiza "
        "istnieje; 404/409 tylko po poprawnym tokenie. "
        "Wznawia analizę oczekującą na ręczne podanie symbolu strefy MPZP, "
        "odczytanego przez użytkownika z podglądu rastrowego, gdy gmina nie "
        "udostępnia wektorowych danych MPZP. Parametry są odczytywane wyłącznie "
        "z dokumentu przypiętego przy wstrzymaniu analizy (bez ponownego "
        "pobierania). Wynik zawsze ma status partial, a udział strefy w "
        "powierzchni działki pozostaje nieustalony."
    ),
)
async def analyze_resume(
    request: Annotated[AnalyzeResumeRequest, Depends(authorized_resume_request)],
    db: Session = Depends(get_db),
) -> AnalyzeResponse:
    try:
        return await resume_analysis_with_zone(
            request.analysis_id,
            request.zone_symbol,
            db,
        )
    except AnalysisResumeNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail="Analiza o podanym identyfikatorze nie istnieje."
        ) from exc
    except AnalysisResumeStateError as exc:
        raise HTTPException(
            status_code=409,
            detail="Analiza nie czeka na ręczne podanie strefy.",
        ) from exc
    except InvalidZoneSymbolError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except AnalysisResumeDocumentError as exc:
        # Uszkodzony albo nieczytelny artefakt przypięty przy wstrzymaniu: nic
        # nie zapisano, analiza nadal czeka na symbol.
        raise HTTPException(
            status_code=503,
            detail=(
                "Nie udało się odczytać dokumentu MPZP przypiętego przy "
                "wstrzymaniu analizy. Analiza nadal oczekuje na symbol strefy."
            ),
        ) from exc


# Serwowana kopia pochodzi z niezaufanego źródła: bez zgadywania typu, bez
# referera, a HTML wyłącznie jako załącznik z CSP ``sandbox`` (nie renderuje się
# w origin aplikacji). PDF nie dostaje ``sandbox``, bo blokuje on wbudowane
# przeglądarki PDF.
_PENDING_DOCUMENT_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "private, max-age=300",
    "Referrer-Policy": "no-referrer",
}
_UNTRUSTED_HTML_CSP = "sandbox; default-src 'none'"


@router.get(
    "/{analysis_id}/pending-document",
    responses={
        200: {"content": {"application/pdf": {}, "text/html": {}}},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
    },
    dependencies=[Depends(_document_limit), Depends(require_analysis_token)],
    description=(
        "Zwraca kopię dokumentu uchwały przypiętą przy wstrzymaniu analizy "
        "(BK-204) — dokładnie ten artefakt, z którego resume odczyta parametry. "
        "PDF jest serwowany inline, HTML wyłącznie jako załącznik."
    ),
)
def get_pending_document(
    analysis_id: Annotated[int, Path(gt=0)],
    db: Session = Depends(get_db),
) -> Response:
    if db.get(Analysis, analysis_id) is None:
        raise HTTPException(
            status_code=404, detail="Analiza o podanym identyfikatorze nie istnieje."
        )
    document = db.scalar(
        select(AnalysisPendingDocument).where(
            AnalysisPendingDocument.analysis_id == analysis_id
        )
    )
    if document is None:
        raise HTTPException(
            status_code=404,
            detail="Analiza nie ma dokumentu przypiętego przy wstrzymaniu.",
        )
    is_pdf = document.media_type == "application/pdf"
    filename = "uchwala.pdf" if is_pdf else "uchwala.html"
    return Response(
        content=document.content,
        media_type=document.media_type if is_pdf else "application/octet-stream",
        headers={
            **_PENDING_DOCUMENT_HEADERS,
            **({} if is_pdf else {"Content-Security-Policy": _UNTRUSTED_HTML_CSP}),
            "Content-Disposition": (
                f'{"inline" if is_pdf else "attachment"}; filename="{filename}"'
            ),
            "ETag": f'"{document.content_sha256}"',
            "X-Document-SHA256": document.content_sha256,
        },
    )
