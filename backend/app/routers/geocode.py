from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.schemas.geocode import GeocodeResponse, GeocodeSuggestionResponse
from app.services.geocoding import (
    GeocodingServiceUnavailableError,
    geocode_address,
)

router = APIRouter(prefix="/geocode", tags=["geocode"])


@router.get(
    "/suggest",
    response_model=GeocodeResponse,
    description=(
        "Zwraca listę sugestii adresowych z punktami w EPSG:2180. Sugestie mogą "
        "być użyte jako wejście do analizy działki. Pusty wynik zwraca status "
        "200 z pustą listą suggestions."
    ),
)
async def suggest(
    q: str = Query(
        min_length=3,
        max_length=500,
        description="Zapytanie adresowe, np. Marki, Andersa 1",
    ),
) -> GeocodeResponse:
    try:
        suggestions = await geocode_address(q)
    except GeocodingServiceUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Usługa geokodowania jest chwilowo niedostępna. "
                "Spróbuj ponownie za chwilę."
            ),
        ) from exc

    converted = [
        GeocodeSuggestionResponse(
            label=suggestion.label,
            x=suggestion.x,
            y=suggestion.y,
            confidence=suggestion.confidence,
            teryt=suggestion.teryt,
        )
        for suggestion in suggestions
    ]
    return GeocodeResponse(
        query=q.strip(),
        suggestions=converted,
        total_returned=len(converted),
    )
