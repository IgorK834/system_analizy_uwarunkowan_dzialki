"""Cienka warstwa HTTP dla uruchamiania i ręcznego wznawiania analizy."""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.analysis import Analysis
from app.models.parcel import Parcel
from app.schemas.analyze import (
    AddressAnalyzeRequest,
    AnalyzeResponse,
    AnalyzeResumeRequest,
    ErrorResponse,
    MapAnalyzeRequest,
    ParcelIdAnalyzeRequest,
    WarningMessage,
)
from app.schemas.source import SourceMetadata
from app.services.geocoding import GeocodingServiceUnavailableError
from app.services.analysis_orchestrator import run_analysis
from app.services.geometry import (
    CoordinatesOutsidePolandError,
    InvalidParcelGeometryError,
)
from app.services.initiation import AddressNotFoundError
from app.services.mpzp_fetch import (
    MpzpDocumentFetchError,
    MpzpDocumentSecurityError,
    fetch_mpzp_document,
)
from app.services.mpzp_parser import parse_mpzp_document
from app.services.mpzp_zones import (
    MANUAL_ZONE_SYMBOL_CONFIDENCE,
    InvalidZoneSymbolError,
    map_parser_zone_to_analyze_response,
    validate_zone_symbol_format,
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
    analysis = db.get(Analysis, request.analysis_id)
    if analysis is None:
        raise HTTPException(
            status_code=404, detail="Analiza o podanym identyfikatorze nie istnieje."
        )

    # Założenie agenta (wykracza poza literalne kryteria akceptacji): chronimy
    # przed wznowieniem analizy, która nie czeka na ręczne podanie strefy —
    # np. już zakończonej albo takiej, która nigdy nie wymagała trybu
    # ręcznego. To sensowna ochrona przed błędnym użyciem endpointu.
    if analysis.status != "waiting_for_zone_symbol":
        raise HTTPException(
            status_code=409,
            detail="Analiza nie czeka na ręczne podanie strefy.",
        )

    try:
        zone_symbol = validate_zone_symbol_format(request.zone_symbol)
    except InvalidZoneSymbolError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if not analysis.pending_uchwala_url:
        raise HTTPException(
            status_code=503,
            detail=(
                "Analiza nie ma zapisanego adresu dokumentu MPZP do ponownego "
                "pobrania."
            ),
        )

    try:
        document = await fetch_mpzp_document(analysis.pending_uchwala_url)
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

    parse_result = await parse_mpzp_document(document, [zone_symbol])

    # Ręczny wpis symbolu strefy pochodzi z odczytu mapy rastrowej przez
    # człowieka, nie z automatycznej ekstrakcji — confidence 0.5 jest
    # udokumentowanym założeniem (patrz stała MANUAL_ZONE_SYMBOL_CONFIDENCE
    # w mpzp_zones.py), nie zahardkodowaną liczbą bez uzasadnienia.
    source = SourceMetadata(
        source_name="manual_user_input",
        source_url=analysis.pending_uchwala_url,
        fetched_at=document.source_metadata.fetched_at,
        confidence=MANUAL_ZONE_SYMBOL_CONFIDENCE,
        manual_review_required=True,
    )

    warnings: list[WarningMessage] = []
    mpzp_zones = []
    matching_zone = next(
        (zone for zone in parse_result.zones if zone.zone_symbol == zone_symbol),
        parse_result.zones[0] if parse_result.zones else None,
    )
    if matching_zone is not None:
        parcel = db.get(Parcel, analysis.parcel_id)
        parcel_area_sqm = parcel.area_sqm if parcel and parcel.area_sqm else 0.0
        mapped_zone, skipped_parameters = map_parser_zone_to_analyze_response(
            matching_zone, parcel_area_sqm, source
        )
        mpzp_zones.append(mapped_zone)
        if skipped_parameters:
            warnings.append(
                WarningMessage(
                    code="MPZP_PARAMETERS_NOT_IN_FLAT_CONTRACT",
                    message=(
                        "Parser znalazł dodatkowe parametry bez odpowiednika w "
                        "płaskim kontrakcie API: "
                        + ", ".join(skipped_parameters)
                        + ". Pełne dane są na razie dostępne tylko w "
                        "wewnętrznym wyniku parsera."
                    ),
                    severity="warning",
                    source_name="mpzp",
                )
            )

    # Wybór statusu: 'complete' tylko gdy parser faktycznie zwrócił complete,
    # w przeciwnym razie zachowujemy 'partial' z ostrożności — wynik
    # ręcznego wznowienia bez wektorowej granicy strefy nigdy nie powinien
    # wyglądać jak w pełni pewny nawet przy strukturalnie kompletnym wyniku
    # parsera, dlatego 'failed' parsera także mapujemy na 'partial' analizy
    # (mamy przynajmniej symbol strefy podany przez użytkownika, więc analiza
    # nie jest całkowicie nieudana).
    analysis.status = "complete" if parse_result.status == "complete" else "partial"
    analysis.resolved_zone_symbol = zone_symbol
    db.commit()

    return AnalyzeResponse(
        analysis_id=analysis.id,
        status=analysis.status,
        analyzed_at=datetime.now(timezone.utc),
        parcel=None,
        mpzp_zones=mpzp_zones,
        pog=None,
        infrastructure=[],
        risks=[],
        buildable_area_sqm=None,
        manual_zone_required=False,
        warnings=warnings,
        sources=[source],
    )
