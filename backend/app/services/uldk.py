from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Final

import httpx

from app.schemas.analyze import SourceMetadata

ULDK_BASE_URL: Final[str] = "https://uldk.gugik.gov.pl/"
ULDK_TIMEOUT_S: Final[float] = 10.0
ULDK_MAX_RETRIES: Final[int] = 2
ULDK_BACKOFF_S: Final[float] = 1.0

# Format ULDK jest walidowany przed zapytaniem sieciowym, żeby nie obciążać
# publicznej usługi oczywiście błędnymi identyfikatorami.
_PARCEL_ID_RE = re.compile(r"^\d{6}_\d{1,2}\.\d{4}(\.[A-Z]+_\d+)?\.([\d]+(/[\d]+)*)$")


class InvalidParcelIdentifierError(ValueError):
    """Format identyfikatora działki nie jest zgodny z formatem ULDK."""


class ParcelNotFoundError(Exception):
    """Działka o podanym identyfikatorze nie istnieje w rejestrze ULDK."""


class UldkServiceUnavailableError(Exception):
    """Usługa ULDK jest niedostępna lub zwróciła błąd statusu -1."""


class InvalidUldkResponseError(Exception):
    """Odpowiedź ULDK ma nieoczekiwany format lub pusty WKT."""


@dataclass(frozen=True)
class UldkParcelResult:
    """Wewnętrzny wynik parsowania odpowiedzi ULDK, bez metadanych źródła."""

    parcel_identifier: str
    geometry_wkt: str
    teryt: str


@dataclass(frozen=True)
class ParcelLookupResult:
    """Publiczny wynik wyszukiwania działki po współrzędnych, z metadanymi źródła."""

    parcel_identifier: str
    wkt: str
    teryt: str
    source_metadata: SourceMetadata


async def get_parcel_by_id(parcel_identifier: str) -> UldkParcelResult:
    """
    Pobiera z ULDK geometrię działki po identyfikatorze ewidencyjnym.

    Identyfikator pochodzi z ULDK albo ewidencji gruntów i jest walidowany przed
    wysłaniem zapytania HTTP. Wynik zawiera WKT geometrii w EPSG:2180, czyli w
    układzie wymaganym przez dalsze obliczenia metryczne. Funkcja może rzucić:
    InvalidParcelIdentifierError dla błędnego formatu, ParcelNotFoundError dla
    pustego wyniku, UldkServiceUnavailableError dla timeoutu, błędu HTTP albo
    statusu -1 z ULDK oraz InvalidUldkResponseError dla nieoczekiwanego formatu.
    """
    _validate_parcel_identifier(parcel_identifier)

    params = {
        "request": "GetParcelById",
        "id": parcel_identifier,
        "result": "id,geom_wkt,teryt",
    }
    response = await _request_with_retry(params)
    return _parse_uldk_response(response.text, parcel_identifier)


async def get_parcel_by_xy(x: float, y: float) -> ParcelLookupResult:
    """
    Wyszukuje w ULDK działkę zawierającą punkt w układzie EPSG:2180.

    Zapytanie używa parametru ``xy=X,Y,2180`` z jawnym SRID, żeby nie zależeć od
    domyślnego układu usługi GUGiK. Geometria w wyniku jest zwracana jako WKT w
    EPSG:2180 bez prefiksu SRID i zawiera metadane źródła wymagane dla danych
    zewnętrznych. Funkcja może rzucić ParcelNotFoundError dla punktu poza
    działkami, UldkServiceUnavailableError dla timeoutu, błędu HTTP 5xx albo
    statusu -1 z ULDK oraz InvalidUldkResponseError dla nieoczekiwanego formatu.
    """
    params = {
        "request": "GetParcelByXY",
        "xy": f"{x},{y},2180",
        "result": "id,geom_wkt,teryt",
    }
    fetched_at = datetime.now(timezone.utc)
    response = await _request_with_retry(params)
    raw = _parse_uldk_response(response.text, f"punkt ({x}, {y})")
    source = SourceMetadata(
        source_name="ULDK",
        source_url=str(response.url),
        fetched_at=fetched_at,
        confidence=1.0,
        manual_review_required=False,
    )
    return ParcelLookupResult(
        parcel_identifier=raw.parcel_identifier,
        wkt=raw.geometry_wkt,
        teryt=raw.teryt,
        source_metadata=source,
    )


def _validate_parcel_identifier(identifier: str) -> None:
    if not _PARCEL_ID_RE.fullmatch(identifier):
        raise InvalidParcelIdentifierError(
            "Identyfikator działki nie jest zgodny z formatem ULDK."
        )


def _parse_uldk_response(text: str, location_hint: str) -> UldkParcelResult:
    lines = text.strip().splitlines()
    if not lines:
        raise InvalidUldkResponseError("Usługa ULDK zwróciła pustą odpowiedź.")

    status = lines[0].strip()
    if status == "-1":
        detail = lines[1].strip() if len(lines) > 1 else "Brak szczegółów błędu ULDK."
        raise UldkServiceUnavailableError(f"Usługa ULDK zwróciła błąd: {detail}")

    if status != "0":
        raise InvalidUldkResponseError(f"Nieoczekiwany kod statusu: {status!r}")

    data_line = lines[1].strip() if len(lines) > 1 else ""
    if not data_line:
        raise ParcelNotFoundError(
            f"Działka dla lokalizacji {location_hint} nie została znaleziona."
        )

    parts = data_line.split("|")
    if len(parts) < 2:
        raise InvalidUldkResponseError("Odpowiedź ULDK nie zawiera separatora danych.")

    geometry_wkt = _strip_srid_prefix(parts[1].strip())
    if not geometry_wkt:
        raise InvalidUldkResponseError("Odpowiedź ULDK zawiera pusty WKT geometrii.")

    normalized_wkt = geometry_wkt.upper()
    if not (
        normalized_wkt.startswith("POLYGON")
        or normalized_wkt.startswith("MULTIPOLYGON")
    ):
        raise InvalidUldkResponseError(
            "Odpowiedź ULDK nie zawiera geometrii POLYGON ani MULTIPOLYGON."
        )

    return UldkParcelResult(
        parcel_identifier=parts[0].strip(),
        geometry_wkt=geometry_wkt,
        teryt=parts[2].strip() if len(parts) > 2 else "",
    )


def _strip_srid_prefix(geometry_wkt: str) -> str:
    if geometry_wkt.upper().startswith("SRID="):
        return geometry_wkt.split(";", maxsplit=1)[-1].strip()
    return geometry_wkt


async def _request_with_retry(params: dict[str, str]) -> httpx.Response:
    """
    Wykonuje GET do ULDK z retry dla timeoutów i błędów HTTP 5xx.

    Między próbami stosuje łagodny backoff liniowy, żeby nie przeciążać publicznej
    usługi GUGiK. Dla błędów HTTP 4xx nie ponawia, bo wskazują na błędne żądanie.
    """
    last_exc: Exception | None = None
    for attempt in range(ULDK_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=ULDK_TIMEOUT_S) as client:
                response = await client.get(ULDK_BASE_URL, params=params)
                response.raise_for_status()
                return response
        except httpx.TimeoutException as exc:
            last_exc = exc
            if attempt < ULDK_MAX_RETRIES:
                await asyncio.sleep(ULDK_BACKOFF_S * (attempt + 1))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500:
                last_exc = exc
                if attempt < ULDK_MAX_RETRIES:
                    await asyncio.sleep(ULDK_BACKOFF_S * (attempt + 1))
            else:
                raise UldkServiceUnavailableError(
                    f"Usługa ULDK zwróciła błąd HTTP {exc.response.status_code}."
                ) from exc

    raise UldkServiceUnavailableError(
        f"Usługa ULDK jest niedostępna po {ULDK_MAX_RETRIES + 1} próbach."
    ) from last_exc
