"""Cienka warstwa HTTP dla uruchamiania i ręcznego wznawiania analizy."""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.analyze import (
    AddressAnalyzeRequest,
    AnalyzeResponse,
    AnalyzeResumeRequest,
    ErrorResponse,
    MapAnalyzeRequest,
    ParcelIdAnalyzeRequest,
)
from app.services.analysis_resume import (
    AnalysisResumeNotFoundError,
    AnalysisResumeSourceMissingError,
    AnalysisResumeStateError,
    resume_analysis_with_zone,
)
from app.services.geocoding import GeocodingServiceUnavailableError
from app.services.analysis_orchestrator import run_analysis
from app.services.geometry import (
    CoordinatesOutsidePolandError,
    InvalidParcelGeometryError,
)
from app.services.initiation import AddressNotFoundError
from app.services.mpzp_fetch import MpzpDocumentFetchError, MpzpDocumentSecurityError
from app.services.mpzp_zones import (
    InvalidZoneSymbolError,
)
from app.services.uldk import (
    InvalidParcelIdentifierError,
    ParcelNotFoundError,
    UldkServiceUnavailableError,
)

router = APIRouter(prefix="/analyze", tags=["analyze"])


@router.post(
    "",
    response_model=AnalyzeResponse,
    responses={501: {"model": ErrorResponse}},
    description=(
        "Uruchamia analizę uwarunkowań przestrzennych działki. Obsługuje trzy "
        "metody wejścia: kliknięcie w mapę, adres lub identyfikator działki ULDK. "
        "Jeżeli gmina nie udostępnia wektorowych danych MPZP, analiza jest "
        "zapisywana ze statusem oczekującym i wymaga wznowienia przez "
        "POST /analyze/resume z symbolem strefy odczytanym z mapy rastrowej."
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
    try:
        return await run_analysis(request, db, force_refresh=force_refresh)
    except (CoordinatesOutsidePolandError, InvalidParcelGeometryError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (AddressNotFoundError, ParcelNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidParcelIdentifierError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (UldkServiceUnavailableError, GeocodingServiceUnavailableError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post(
    "/resume",
    response_model=AnalyzeResponse,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
    description=(
        "Wznawia analizę oczekującą na ręczne podanie symbolu strefy MPZP, "
        "odczytanego przez użytkownika z mapy rastrowej, gdy gmina nie "
        "udostępnia wektorowych danych MPZP."
    ),
)
async def analyze_resume(
    request: AnalyzeResumeRequest,
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
    except AnalysisResumeSourceMissingError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Analiza nie ma zapisanego adresu dokumentu MPZP do ponownego "
                "pobrania."
            ),
        ) from exc
    except (MpzpDocumentFetchError, MpzpDocumentSecurityError) as exc:
        # Dokument mógł zniknąć między discovery a wznowieniem (BIP zmienia
        # strony, dokumenty bywają przenoszone) — 503, nie 500, bo to
        # niedostępność zewnętrznego źródła, nie błąd naszej aplikacji.
        raise HTTPException(
            status_code=503,
            detail=(
                "Nie udało się ponownie pobrać dokumentu MPZP. Dokument mógł "
                "zniknąć od czasu wstępnego rozpoznania."
            ),
        ) from exc
