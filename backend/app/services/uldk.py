from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import re
from typing import Final

import httpx

from app.core.metrics import upstream_metrics
from app.schemas.analyze import SourceMetadata

logger = logging.getLogger(__name__)

ULDK_BASE_URL: Final[str] = "https://uldk.gugik.gov.pl/"
ULDK_TIMEOUT_S: Final[float] = 10.0
ULDK_MAX_RETRIES: Final[int] = 2
ULDK_BACKOFF_S: Final[float] = 1.0
# ULDK odpowiada ``-1 brak wyników`` zarówno dla lokalizacji bez działki (Bałtyk, nieistniejący numer),
# jak i dla przejściowego błędu usługi powiatowej za ULDK (zaobserwowano dla istniejącej działki
# ``022104_2.0002.352``). Tych przypadków nie da się odróżnić po odpowiedzi, więc zapytanie jest
# ponawiane raz po krótkiej przerwie; dopiero drugie ``-1`` oznacza brak działki.
ULDK_NO_RESULTS_RETRIES: Final[int] = 1
ULDK_NO_RESULTS_RETRY_DELAY_S: Final[float] = 0.3
ULDK_NO_RESULTS_MARKER: Final[str] = "brak wyników"
ULDK_NOT_FOUND_MESSAGE: Final[str] = (
    "ULDK nie zwróciło działki dla tej lokalizacji "
    "(brak działki albo chwilowy błąd źródła)"
)

# Format ULDK jest walidowany przed zapytaniem sieciowym, żeby nie obciążać
# publicznej usługi oczywiście błędnymi identyfikatorami.
_PARCEL_ID_RE = re.compile(r"^\d{6}_\d{1,2}\.\d{4}(\.[A-Z]+_\d+)?\.([\d]+(/[\d]+)*)$")


class InvalidParcelIdentifierError(ValueError):
    """Format identyfikatora działki nie jest zgodny z formatem ULDK."""


class ParcelNotFoundError(Exception):
    """Działka o podanym identyfikatorze nie istnieje w rejestrze ULDK."""


class UldkNoResultsError(ParcelNotFoundError):
    """ULDK odpowiedziało ``-1 brak wyników`` — brak działki albo przejściowy błąd źródła.

    Wyjątek jest rozpoznawany wyłącznie wewnątrz modułu, żeby zapytanie mogło zostać ponowione raz;
    po drugim takim wyniku ``_fetch_parcel`` zgłasza zwykły ``ParcelNotFoundError`` (HTTP 404).
    """


class UldkServiceUnavailableError(Exception):
    """Usługa ULDK jest niedostępna lub zwróciła błąd statusu -1 inny niż brak wyników."""


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
    pustego wyniku albo ``-1 brak wyników`` potwierdzonego drugim zapytaniem,
    UldkServiceUnavailableError dla błędu transportu, HTTP albo innego statusu
    -1 z ULDK oraz InvalidUldkResponseError dla nieoczekiwanego formatu.
    """
    _validate_parcel_identifier(parcel_identifier)

    params = {
        "request": "GetParcelById",
        "id": parcel_identifier,
        "result": "id,geom_wkt,teryt",
    }
    result, _ = await _fetch_parcel(params, parcel_identifier)
    return result


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
    raw, response = await _fetch_parcel(params, f"punkt ({x}, {y})")
    source = SourceMetadata(
        source_id="uldk",
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


async def _fetch_parcel(
    params: dict[str, str], location_hint: str
) -> tuple[UldkParcelResult, httpx.Response]:
    """Pobiera i parsuje odpowiedź ULDK; ``-1 brak wyników`` ponawia raz po ~300 ms."""
    attempt = 0
    while True:
        response = await _request_with_retry(params)
        try:
            result = _parse_uldk_response(response.text, location_hint)
        except UldkNoResultsError as exc:
            if attempt >= ULDK_NO_RESULTS_RETRIES:
                upstream_metrics.increment("uldk.no_results.confirmed")
                raise ParcelNotFoundError(ULDK_NOT_FOUND_MESSAGE) from exc
            attempt += 1
            upstream_metrics.increment("uldk.no_results.retry")
            await asyncio.sleep(ULDK_NO_RESULTS_RETRY_DELAY_S)
            continue
        if attempt:
            # Drugie zapytanie zwróciło działkę: pierwsze ``-1`` było przejściowym błędem źródła.
            upstream_metrics.increment("uldk.no_results.recovered")
            logger.warning("uldk_no_results_recovered attempts=%d", attempt + 1)
        return result, response


def _count_response_code(code: str) -> None:
    # Etykieta ma ograniczoną kardynalność: kod z odpowiedzi to dowolny tekst.
    label = code if re.fullmatch(r"-?\d{1,3}", code) else "other"
    upstream_metrics.increment(f"uldk.response.{label}")


def _parse_uldk_response(text: str, location_hint: str) -> UldkParcelResult:
    lines = text.lstrip("\ufeff").strip().splitlines()
    if not lines:
        upstream_metrics.increment("uldk.response.empty")
        raise InvalidUldkResponseError("Usługa ULDK zwróciła pustą odpowiedź.")

    # Kod statusu to pierwszy token pierwszej linii, reszta linii to komunikat: ULDK zwraca
    # ``-1 brak wyników`` w jednej linii (starsza forma: ``-1`` i komunikat w drugiej linii).
    code, _, message = lines[0].strip().partition(" ")
    message = message.strip() or (lines[1].strip() if len(lines) > 1 else "")
    _count_response_code(code)

    if code == "-1":
        if ULDK_NO_RESULTS_MARKER in message.casefold():
            raise UldkNoResultsError(ULDK_NOT_FOUND_MESSAGE)
        detail = message or "Brak szczegółów błędu ULDK."
        raise UldkServiceUnavailableError(f"Usługa ULDK zwróciła błąd: {detail}")

    if code != "0":
        raise InvalidUldkResponseError(f"Nieoczekiwany kod statusu: {code!r}")

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
    Wykonuje GET do ULDK z retry dla błędów transportu (timeout, połączenie) i HTTP 5xx.

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
        except httpx.TransportError as exc:
            # Timeout, odmowa połączenia, DNS, zerwane połączenie: dla wywołującego to ta sama
            # „usługa niedostępna”, a nie surowy wyjątek biblioteki HTTP.
            last_exc = exc
            upstream_metrics.increment("uldk.transport_error")
            if attempt < ULDK_MAX_RETRIES:
                await asyncio.sleep(ULDK_BACKOFF_S * (attempt + 1))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500:
                last_exc = exc
                upstream_metrics.increment("uldk.http_5xx")
                if attempt < ULDK_MAX_RETRIES:
                    await asyncio.sleep(ULDK_BACKOFF_S * (attempt + 1))
            else:
                upstream_metrics.increment("uldk.http_4xx")
                raise UldkServiceUnavailableError(
                    f"Usługa ULDK zwróciła błąd HTTP {exc.response.status_code}."
                ) from exc

    raise UldkServiceUnavailableError(
        f"Usługa ULDK jest niedostępna po {ULDK_MAX_RETRIES + 1} próbach."
    ) from last_exc
