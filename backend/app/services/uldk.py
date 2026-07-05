from __future__ import annotations

from dataclasses import dataclass
import re

import httpx


ULDK_BASE_URL = "https://uldk.gugik.gov.pl/"
ULDK_TIMEOUT_S = 10.0

# Format ULDK jest walidowany przed zapytaniem sieciowym, żeby nie obciążać
# publicznej usługi oczywiście błędnymi identyfikatorami.
_PARCEL_ID_RE = re.compile(
    r"^\d{6}_\d{1,2}\.\d{4}(\.[A-Z]+_\d+)?\.([\d]+(/[\d]+)*)$"
)


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
    parcel_identifier: str
    geometry_wkt: str
    teryt: str


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

    async with httpx.AsyncClient(timeout=ULDK_TIMEOUT_S) as client:
        try:
            response = await client.get(ULDK_BASE_URL, params=params)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise UldkServiceUnavailableError(
                "Usługa ULDK nie odpowiedziała w wymaganym czasie."
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise UldkServiceUnavailableError(
                "Usługa ULDK zwróciła błąd HTTP podczas pobierania działki."
            ) from exc

    return _parse_uldk_response(response.text, parcel_identifier)


def _validate_parcel_identifier(identifier: str) -> None:
    if not _PARCEL_ID_RE.fullmatch(identifier):
        raise InvalidParcelIdentifierError(
            "Identyfikator działki nie jest zgodny z formatem ULDK."
        )


def _parse_uldk_response(text: str, parcel_identifier: str) -> UldkParcelResult:
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
            f"Działka o identyfikatorze {parcel_identifier} nie została znaleziona."
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
