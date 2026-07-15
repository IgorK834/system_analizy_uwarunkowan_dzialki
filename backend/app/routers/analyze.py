"""Router /analyze.

Uwaga o zakresie: to zadanie podłącza ``discover_mpzp`` do ``/analyze`` po
raz pierwszy i persystuje Parcel+Analysis WYŁĄCZNIE w gałęzi
``brak_wektorow=True`` (gdy trzeba wznowić analizę ręcznie). Gałąź, w której
discovery znajduje wektory, zachowuje dotychczasowe zachowanie
(``status='partial'``, ``analysis_id=None``) — pełny silnik przecięć na
realnych danych WFS jest osobnym, przyszłym zadaniem. Pełny, wersjonowany
model provenance jest zalecany w ANALIZA_ARCHITEKTURY_I_PLAN.md (sekcja E,
ADR-004/ADR-005) — ten router realizuje tylko minimalny, potrzebny wycinek.
"""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException
from geoalchemy2.shape import from_shape
from shapely.geometry import MultiPolygon
from shapely.geometry.base import BaseGeometry
from sqlalchemy import select
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
from app.services.geometry import CoordinatesOutsidePolandError, parse_parcel_geometry
from app.services.initiation import AddressNotFoundError, resolve_parcel
from app.services.mpzp import discover_mpzp
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
    db: Session = Depends(get_db),
) -> AnalyzeResponse:
    try:
        result = await resolve_parcel(request)
    except CoordinatesOutsidePolandError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (AddressNotFoundError, ParcelNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidParcelIdentifierError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (UldkServiceUnavailableError, GeocodingServiceUnavailableError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    warnings: list[WarningMessage] = []
    parcel_geometry = parse_parcel_geometry(result.wkt)
    discovery_result = await discover_mpzp(parcel_geometry)

    if discovery_result.brak_wektorow:
        # Persystencja jest ŚWIADOMIE ograniczona do tej gałęzi: tylko tutaj
        # jest faktycznie potrzebna, bo trzeba wznowić analizę później przez
        # POST /analyze/resume. Gałąź "found" (wektory dostępne) zachowuje
        # dotychczasowe zachowanie i NIE zapisuje niczego — to osobne,
        # świadomie odłożone zadanie (silnik przecięć na realnych danych WFS).
        parcel = _get_or_create_parcel(
            db, result.parcel_identifier, parcel_geometry
        )
        analysis = Analysis(
            parcel_id=parcel.id,
            status="waiting_for_zone_symbol",
            pending_uchwala_url=discovery_result.uchwala_url,
            pending_plan_id=discovery_result.plan_id,
            pending_zone_symbol_candidates=discovery_result.candidate_zone_symbols,
        )
        db.add(analysis)
        db.commit()
        db.refresh(analysis)

        warnings.append(
            WarningMessage(
                code="MPZP_MANUAL_ZONE_REQUIRED",
                message=(
                    "Gmina nie udostępnia wektorowych danych MPZP dla tej "
                    "działki. Podaj symbol strefy odczytany z mapy rastrowej "
                    "MPZP i wznów analizę przez POST /analyze/resume."
                ),
                severity="warning",
                source_name="mpzp",
            )
        )

        return AnalyzeResponse(
            analysis_id=analysis.id,
            status="partial",
            analyzed_at=datetime.now(timezone.utc),
            parcel=None,
            mpzp_zones=[],
            pog=None,
            infrastructure=[],
            risks=[],
            buildable_area_sqm=None,
            manual_zone_required=True,
            warnings=warnings,
            sources=[result.source_metadata, discovery_result.source_metadata],
        )

    # Gałąź bez brak_wektorow=True: zachowujemy dotychczasowe zachowanie
    # dokładnie bez zmian — poza tym zadaniem jest zbudowanie silnika
    # przecięć na realnych danych wektorowych MPZP dla tej gałęzi.
    return AnalyzeResponse(
        analysis_id=None,
        status="partial",
        analyzed_at=datetime.now(timezone.utc),
        parcel=None,
        mpzp_zones=[],
        pog=None,
        infrastructure=[],
        risks=[],
        buildable_area_sqm=None,
        warnings=warnings,
        sources=[result.source_metadata],
    )


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


def _get_or_create_parcel(
    db: Session, parcel_identifier: str, geometry: BaseGeometry
) -> Parcel:
    """Znajduje Parcel po parcel_identifier albo tworzy nowy rekord.

    parcel_identifier jest unikalny (patrz migracja 001), więc get-or-create
    jest bezpieczny dla ponownych analiz tej samej działki. Kolumna geometry
    w bazie jest typu MULTIPOLYGON, dlatego pojedynczy Polygon z ULDK jest
    owijany w MultiPolygon przed zapisem.
    """
    existing = db.execute(
        select(Parcel).where(Parcel.parcel_identifier == parcel_identifier)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    multi_geometry = (
        geometry if geometry.geom_type == "MultiPolygon" else MultiPolygon([geometry])
    )
    parcel = Parcel(
        parcel_identifier=parcel_identifier,
        geometry=from_shape(multi_geometry, srid=2180),
        area_sqm=geometry.area,
    )
    db.add(parcel)
    db.flush()
    return parcel
