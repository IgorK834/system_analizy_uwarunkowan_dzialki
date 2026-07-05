from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Final

import httpx

UUG_BASE_URL: Final[str] = "https://services.gugik.gov.pl/uug/"
UUG_TIMEOUT_S: Final[float] = 10.0
UUG_MAX_RESULTS: Final[int] = 5


class GeocodingServiceUnavailableError(Exception):
    """Usługa geokodowania UUG jest niedostępna lub zwróciła błąd HTTP."""


@dataclass(frozen=True)
class GeocodeSuggestion:
    """Wewnętrzna sugestia adresowa UUG z punktem w EPSG:2180."""

    label: str
    x: float
    y: float
    confidence: float
    teryt: str
    city: str
    street: str | None
    number: str | None


async def geocode_address(query: str) -> list[GeocodeSuggestion]:
    """
    Wyszukuje punkt adresowy w usłudze UUG GUGiK.

    Zwraca listę sugestii z punktami w EPSG:2180, czyli gotowych do przekazania
    do ULDK GetParcelByXY bez dodatkowej transformacji współrzędnych. Pusty wynik
    oznacza, że adres nie został znaleziony. Funkcja rzuca
    GeocodingServiceUnavailableError przy problemach z siecią lub HTTP.
    """
    normalized = _normalize_query(query)
    if not normalized:
        return []

    params = {"request": "GetAddress", "address": normalized}
    async with httpx.AsyncClient(timeout=UUG_TIMEOUT_S) as client:
        try:
            response = await client.get(UUG_BASE_URL, params=params)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise GeocodingServiceUnavailableError(
                "Usługa geokodowania nie odpowiedziała w wymaganym czasie."
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise GeocodingServiceUnavailableError(
                f"Usługa geokodowania zwróciła błąd HTTP {exc.response.status_code}."
            ) from exc
        except httpx.RequestError as exc:
            raise GeocodingServiceUnavailableError(
                "Usługa geokodowania jest niedostępna."
            ) from exc

    return _parse_uug_response(response.json())


def _normalize_query(query: str) -> str:
    return re.sub(r"\s+", " ", query).strip()


def _parse_uug_response(data: dict) -> list[GeocodeSuggestion]:
    returned = data.get("returned objects", 0)
    results_dict = data.get("results", {})
    if returned == 0 or not results_dict:
        return []

    suggestions = [_build_suggestion(result) for result in results_dict.values()]
    return suggestions[:UUG_MAX_RESULTS]


def _build_suggestion(result: dict) -> GeocodeSuggestion:
    x = float(result["x"])
    y = float(result["y"])
    confidence = float(result.get("accuracy", "0"))
    label = _build_label(result)
    return GeocodeSuggestion(
        label=label,
        x=x,
        y=y,
        confidence=confidence,
        teryt=result.get("teryt", ""),
        city=result.get("city", ""),
        street=result.get("street"),
        number=result.get("number"),
    )


def _build_label(result: dict) -> str:
    city = result.get("city", "")
    street = result.get("street")
    number = result.get("number")

    if city and street and number:
        return f"{city}, {street} {number}"
    if city and street:
        return f"{city}, {street}"
    if city:
        return city
    return result.get("teryt", "nieznana lokalizacja")
