from typing import Annotated

from fastapi import APIRouter, Body, HTTPException

from app.schemas.analyze import (
    AddressAnalyzeRequest,
    AnalyzeResponse,
    ErrorResponse,
    MapAnalyzeRequest,
    ParcelIdAnalyzeRequest,
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
def analyze(
    request: Annotated[
        MapAnalyzeRequest | AddressAnalyzeRequest | ParcelIdAnalyzeRequest,
        Body(discriminator="method", title="AnalyzeRequest"),
    ],
) -> AnalyzeResponse:
    raise HTTPException(
        status_code=501,
        detail="Analiza nie jest jeszcze zaimplementowana.",
    )
