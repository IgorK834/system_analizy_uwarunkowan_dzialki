"""Globalna obsługa błędów API: kontrakt ``ErrorResponse`` z ``request_id`` także dla 5xx (AU-001–003).

Zasady:

- każdy błąd HTTP (``HTTPException``, błędy domenowe, nieobsłużony wyjątek) ma ciało
  ``ErrorResponse`` — ``error`` (stały kod), ``detail`` (komunikat bez stack trace) i
  ``request_id`` z nagłówka ``X-Request-ID``; ``detail`` pozostaje stringiem, jak w dotychczasowym
  ``HTTPException``, więc klienci czytający ``detail`` działają bez zmian;
- błąd domenowy ma jedną regułę w ``DOMAIN_ERRORS`` (klasa → status i kod), więc nowa klasa
  wyjątku w module ``uldk``/``initiation`` bez reguły jest wykrywana przez test architektury, a nie
  przez użytkownika jako HTTP 500;
- nieobsłużony ``Exception`` zwraca JSON z ``INTERNAL_ERROR`` i ``request_id``. Handler
  ``Exception`` działa w ``ServerErrorMiddleware``, czyli POZA ``CORSMiddleware``, więc sam dopisuje
  nagłówki CORS — inaczej przeglądarka widzi błąd sieci zamiast błędu serwera.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from http import HTTPStatus
from typing import Final

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.request_id import (
    REQUEST_ID_HEADER,
    get_request_id,
    request_id_from_scope,
    request_id_var,
)
from app.core.settings import settings
from app.schemas.analyze import ErrorResponse
from app.services.geocoding import GeocodingServiceUnavailableError
from app.services.geometry import (
    CoordinatesOutsidePolandError,
    InvalidParcelGeometryError,
)
from app.services.initiation import AddressNotFoundError
from app.services.mpzp_zones import InvalidZoneSymbolError
from app.services.singleflight import SingleFlightTimeoutError
from app.services.persistence import PERSISTENCE_FAILED_MESSAGE, PersistenceError
from app.services.uldk import (
    InvalidParcelIdentifierError,
    InvalidUldkResponseError,
    ParcelNotFoundError,
    UldkServiceUnavailableError,
)

logger = logging.getLogger(__name__)

INTERNAL_ERROR_CODE: Final[str] = "INTERNAL_ERROR"
INTERNAL_ERROR_DETAIL: Final[str] = (
    "Wystąpił nieoczekiwany błąd po stronie serwera. Podaj kod zgłoszenia operatorowi."
)
UPSTREAM_INVALID_RESPONSE_DETAIL: Final[str] = (
    "Usługa zewnętrzna zwróciła odpowiedź w nieoczekiwanym formacie. "
    "Spróbuj ponownie za chwilę."
)

# Nagłówki odpowiedzi widoczne dla JavaScriptu między originami (CORS ``expose_headers``).
EXPOSED_RESPONSE_HEADERS: Final[tuple[str, ...]] = (REQUEST_ID_HEADER, "Retry-After")

_STATUS_CODES: Final[dict[int, str]] = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    500: INTERNAL_ERROR_CODE,
    501: "NOT_IMPLEMENTED",
    502: "UPSTREAM_ERROR",
    503: "SERVICE_UNAVAILABLE",
    504: "UPSTREAM_TIMEOUT",
}


@dataclass(frozen=True)
class DomainErrorRule:
    """Jak klasa wyjątku domenowego trafia do odpowiedzi HTTP."""

    exception: type[Exception]
    status_code: int
    error: str
    # ``None`` = komunikat wyjątku jest bezpieczny dla użytkownika (tekst pisany w kodzie).
    detail: str | None = None


# Kolejność nie ma znaczenia: Starlette wybiera handler po MRO wyjątku, więc podklasa
# (np. ``UldkNoResultsError``) trafia do reguły klasy bazowej.
DOMAIN_ERRORS: Final[tuple[DomainErrorRule, ...]] = (
    DomainErrorRule(CoordinatesOutsidePolandError, 422, "COORDINATES_OUTSIDE_POLAND"),
    DomainErrorRule(InvalidParcelGeometryError, 422, "INVALID_PARCEL_GEOMETRY"),
    DomainErrorRule(InvalidParcelIdentifierError, 422, "INVALID_PARCEL_IDENTIFIER"),
    DomainErrorRule(InvalidZoneSymbolError, 422, "INVALID_ZONE_SYMBOL"),
    DomainErrorRule(AddressNotFoundError, 404, "ADDRESS_NOT_FOUND"),
    DomainErrorRule(ParcelNotFoundError, 404, "PARCEL_NOT_FOUND"),
    DomainErrorRule(UldkServiceUnavailableError, 503, "UPSTREAM_UNAVAILABLE"),
    DomainErrorRule(GeocodingServiceUnavailableError, 503, "UPSTREAM_UNAVAILABLE"),
    DomainErrorRule(
        InvalidUldkResponseError,
        502,
        "UPSTREAM_INVALID_RESPONSE",
        UPSTREAM_INVALID_RESPONSE_DETAIL,
    ),
    DomainErrorRule(
        PersistenceError, 503, "PERSISTENCE_FAILED", PERSISTENCE_FAILED_MESSAGE
    ),
    # AU-007: ta sama działka jest właśnie analizowana dłużej niż limit oczekiwania; wyjątek niesie ``Retry-After``.
    DomainErrorRule(SingleFlightTimeoutError, 503, "ANALYSIS_IN_PROGRESS"),
)


def status_error_code(status_code: int) -> str:
    return _STATUS_CODES.get(status_code, f"HTTP_{status_code}")


def _cors_headers(request: Request) -> dict[str, str]:
    """Nagłówki CORS dla odpowiedzi tworzonych poza ``CORSMiddleware`` (handler ``Exception``).

    Odzwierciedla konfigurację ``CORSMiddleware`` z ``main.py``: ten sam zbiór originów
    (``settings.backend_cors_origins``), ``allow_credentials`` i ``expose_headers``.
    """
    origin = request.headers.get("origin")
    if not origin:
        return {}
    allowed = settings.backend_cors_origins
    if "*" not in allowed and origin not in allowed:
        return {}
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Credentials": "true",
        "Access-Control-Expose-Headers": ", ".join(EXPOSED_RESPONSE_HEADERS),
        "Vary": "Origin",
    }


def error_response(
    request: Request,
    status_code: int,
    error: str,
    detail: str,
    *,
    section: str | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = request_id_from_scope(request.scope) or get_request_id()
    body = ErrorResponse(
        error=error, detail=detail, section=section, request_id=request_id
    ).model_dump()
    response_headers = dict(headers or {})
    if request_id is not None:
        response_headers.setdefault(REQUEST_ID_HEADER, request_id)
    return JSONResponse(status_code=status_code, content=body, headers=response_headers)


def _http_detail(exc: StarletteHTTPException) -> str:
    if isinstance(exc.detail, str) and exc.detail:
        return exc.detail
    try:
        return HTTPStatus(exc.status_code).phrase
    except ValueError:
        return "Błąd żądania."


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> Response:
    headers = dict(getattr(exc, "headers", None) or {})
    if exc.status_code < 200 or exc.status_code in (204, 205, 304):
        return Response(status_code=exc.status_code, headers=headers)
    return error_response(
        request,
        exc.status_code,
        status_error_code(exc.status_code),
        _http_detail(exc),
        headers=headers,
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> Response:
    # Endpointy /api/v1/* zwracają błędy walidacji przez wspólny kontrakt
    # ErrorResponse (bez stack trace). Starsze endpointy zachowują dotychczasowy
    # format odpowiedzi FastAPI, aby nie zmieniać istniejącego kontraktu.
    if request.url.path.startswith("/api/v1/"):
        return error_response(
            request,
            422,
            "VALIDATION_ERROR",
            "Nieprawidłowe parametry zapytania.",
            section="search",
        )
    return await request_validation_exception_handler(request, exc)


def _domain_error_handler(rule: DomainErrorRule):
    async def handler(request: Request, exc: Exception) -> Response:
        if rule.status_code >= 500:
            logger.warning(
                "domain_error request_id=%s error=%s status=%d exception=%s",
                request_id_from_scope(request.scope) or "-",
                rule.error,
                rule.status_code,
                type(exc).__name__,
            )
        return error_response(
            request,
            rule.status_code,
            rule.error,
            rule.detail if rule.detail is not None else str(exc),
            headers=getattr(exc, "response_headers", None),
        )

    return handler


async def unhandled_exception_handler(request: Request, exc: Exception) -> Response:
    """Nieobsłużony wyjątek: JSON z ``request_id`` zamiast ``text/plain`` bez nagłówków CORS."""
    request_id = request_id_from_scope(request.scope) or get_request_id()
    # Handler działa poza ``RequestIdMiddleware`` (kontekst został już zresetowany), więc
    # przywracamy identyfikator na czas wpisu, żeby filtr logów dopisał go do rekordu.
    token = request_id_var.set(request_id)
    try:
        # Pełny ślad stosu trafia wyłącznie do logu serwera (z ``request_id``), nie do odpowiedzi.
        logger.error(
            "unhandled_exception request_id=%s method=%s path=%s error=%s",
            request_id or "-",
            request.method,
            request.url.path,
            type(exc).__name__,
            exc_info=exc,
        )
    finally:
        request_id_var.reset(token)
    response = error_response(
        request, 500, INTERNAL_ERROR_CODE, INTERNAL_ERROR_DETAIL
    )
    # ``ServerErrorMiddleware`` jest najbardziej zewnętrzny: odpowiedź nie przejdzie przez CORS.
    for name, value in _cors_headers(request).items():
        response.headers[name] = value
    return response


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    for rule in DOMAIN_ERRORS:
        app.add_exception_handler(rule.exception, _domain_error_handler(rule))
    app.add_exception_handler(Exception, unhandled_exception_handler)
