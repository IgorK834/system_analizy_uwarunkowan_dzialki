from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Body, HTTPException

from app.schemas.analyze import (
    AddressAnalyzeRequest,
    AnalyzeResponse,
    ErrorResponse,
    MapAnalyzeRequest,
    ParcelIdAnalyzeRequest,
    WarningMessage,
)
from app.services.geocoding import GeocodingServiceUnavailableError
from app.services.geometry import CoordinatesOutsidePolandError
from app.services.initiation import AddressNotFoundError, resolve_parcel
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
        "metody wejścia: kliknięcie w mapę, adres lub identyfikator działki ULDK."
    ),
)
async def analyze(
    request: Annotated[
        MapAnalyzeRequest | AddressAnalyzeRequest | ParcelIdAnalyzeRequest,
        Body(discriminator="method", title="AnalyzeRequest"),
    ],
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
