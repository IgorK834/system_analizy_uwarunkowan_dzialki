"""Globalna obsługa błędów API (AU-001–AU-003): ``ErrorResponse`` z ``request_id`` i CORS także dla 5xx.

Nieobsłużony wyjątek kończył się odpowiedzią ``text/plain`` „Internal Server Error” bez nagłówków CORS, więc
przeglądarka zgłaszała błąd sieci, a interfejs namawiał do ponawiania żądań, które zawsze padną i zużywają
limit. Te testy pilnują, że:

- każdy błąd HTTP ma ciało ``ErrorResponse`` (``error``, ``detail``, ``request_id``) i nagłówek ``X-Request-ID``;
- 5xx z handlera ``Exception`` ma nagłówki CORS (handler działa poza ``CORSMiddleware``);
- ``request_id`` z odpowiedzi znajduje się w logu serwera, razem z pełnym śladem stosu;
- żadna klasa wyjątku modułów ``uldk``/``initiation``/``persistence`` nie kończy się HTTP 500;
- zamrożone odpowiedzi ULDK dają 404/502/503 z kodami z kontraktu, nigdy 500.
"""

from __future__ import annotations

import inspect
import logging
import re
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core.logging import log_analysis_event
from app.core.metrics import upstream_metrics
from app.core.request_id import (
    REQUEST_ID_HEADER,
    RequestIdLogFilter,
    accept_request_id,
    request_id_var,
)
from app.core.settings import settings
from app.main import app
from app.routers.error_handlers import (
    DOMAIN_ERRORS,
    INTERNAL_ERROR_CODE,
    register_error_handlers,
)
from app.schemas.analyze import ErrorResponse
from app.services import initiation, persistence, uldk
from app.services.persistence import PERSISTENCE_FAILED_MESSAGE, PersistenceError
from app.services.uldk import ULDK_BASE_URL, ULDK_NOT_FOUND_MESSAGE

client = TestClient(app, raise_server_exceptions=False)
ORIGIN = "http://localhost:3000"
PARCEL_REQUEST = {"method": "parcel_id", "parcel_identifier": "141801_4.0701.9999/9"}
MAP_REQUEST = {"method": "map", "lon": 18.5, "lat": 54.9}
FROZEN_ULDK = Path(__file__).parent / "fixtures" / "source_contracts" / "uldk"
UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def _post_with_failing_analysis(exc: BaseException, **kwargs):
    with patch("app.routers.analyze.run_analysis", AsyncMock(side_effect=exc)):
        return client.post("/analyze", json=PARCEL_REQUEST, **kwargs)


def _assert_error_response(response, *, status: int, error: str | None = None) -> dict:
    assert response.status_code == status
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    parsed = ErrorResponse.model_validate(body)  # kontrakt: error, detail (str), request_id
    assert parsed.request_id and parsed.request_id == response.headers[REQUEST_ID_HEADER]
    if error is not None:
        assert body["error"] == error
    return body


# --- Nieobsłużony wyjątek: JSON, request_id, CORS, log -----------------------------------------


def test_unhandled_exception_returns_json_with_request_id_and_cors_headers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Odpowiednik ``curl -i -H 'Origin: http://localhost:3000'`` na wymuszony błąd."""
    with caplog.at_level(logging.ERROR, logger="app.routers.error_handlers"):
        response = _post_with_failing_analysis(
            RuntimeError("tajny stan wewnętrzny polygon=POLYGON((1 2))"), headers={"Origin": ORIGIN}
        )

    body = _assert_error_response(response, status=500, error=INTERNAL_ERROR_CODE)
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "x-request-id" in response.headers["access-control-expose-headers"].lower()
    assert response.headers["vary"] == "Origin"
    # Bez stack trace i bez treści wyjątku w odpowiedzi.
    assert "Traceback" not in response.text and "tajny" not in response.text and "POLYGON" not in response.text
    assert body["request_id"] and UUID4.match(body["request_id"])
    # Ten sam identyfikator jest w logu serwera, razem z pełnym śladem stosu i typem błędu.
    record = next(r for r in caplog.records if "unhandled_exception" in r.getMessage())
    assert f"request_id={body['request_id']}" in record.getMessage()
    assert "error=RuntimeError" in record.getMessage() and "method=POST path=/analyze" in record.getMessage()
    # Filtr redakcji sekretów zamienia ``exc_info`` na gotowy tekst śladu (``exc_text``) — jest w logu.
    trace = record.exc_text or "".join(str(item) for item in (record.exc_info or ()))
    assert "Traceback (most recent call last)" in trace and "RuntimeError: tajny stan wewnętrzny" in trace


def test_unhandled_exception_keeps_the_callers_valid_request_id() -> None:
    response = _post_with_failing_analysis(
        ValueError("x"), headers={"Origin": ORIGIN, REQUEST_ID_HEADER: "trace-abcdef-0001"}
    )

    body = _assert_error_response(response, status=500)
    assert body["request_id"] == "trace-abcdef-0001"


def test_cors_headers_on_a_5xx_follow_the_configured_origins() -> None:
    from_other_origin = _post_with_failing_analysis(RuntimeError("x"), headers={"Origin": "https://obcy.example"})
    without_origin = _post_with_failing_analysis(RuntimeError("x"))

    for response in (from_other_origin, without_origin):
        _assert_error_response(response, status=500)
        assert "access-control-allow-origin" not in response.headers
        assert "access-control-allow-credentials" not in response.headers


def test_cors_allows_every_configured_origin_on_a_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "backend_cors_origins", ["http://localhost:3000", "https://app.example.pl"])

    response = _post_with_failing_analysis(RuntimeError("x"), headers={"Origin": "https://app.example.pl"})

    assert response.headers["access-control-allow-origin"] == "https://app.example.pl"


def test_5xx_from_a_domain_error_also_carries_cors_headers_from_the_cors_middleware() -> None:
    response = _post_with_failing_analysis(PersistenceError(), headers={"Origin": ORIGIN})

    _assert_error_response(response, status=503, error="PERSISTENCE_FAILED")
    assert response.headers["access-control-allow-origin"] == ORIGIN


# --- X-Request-ID ------------------------------------------------------------------------------


def test_request_id_is_generated_when_the_caller_sends_none() -> None:
    first, second = client.get("/health/live"), client.get("/health/live")

    ids = {first.headers[REQUEST_ID_HEADER], second.headers[REQUEST_ID_HEADER]}
    assert len(ids) == 2 and all(UUID4.match(value) for value in ids)


@pytest.mark.parametrize("valid", ["0123456789abcdef", "5d0c2f3e-6f0e-4b61-9d57-0c5c1f4a1b3e", "req_1.2-3.x1"])
def test_valid_incoming_request_id_is_accepted(valid: str) -> None:
    assert client.get("/health/live", headers={REQUEST_ID_HEADER: valid}).headers[REQUEST_ID_HEADER] == valid


@pytest.mark.parametrize(
    "invalid",
    ["short", "a" * 65, "ma spację w srodku", "średnik;DROP", "<script>alert(1)</script>", "-zaczyna-sie-od-myslnika"],
)
def test_invalid_incoming_request_id_is_replaced_with_a_generated_one(invalid: str) -> None:
    assert accept_request_id(invalid) != invalid
    assert UUID4.match(accept_request_id(invalid))


def test_request_id_is_present_on_every_kind_of_response() -> None:
    preflight = client.options(
        "/analyze",
        headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST"},
    )

    assert preflight.status_code == 200 and preflight.headers[REQUEST_ID_HEADER]
    assert client.get("/health/live").headers[REQUEST_ID_HEADER]
    assert client.get("/nie-ma-takiej-sciezki").headers[REQUEST_ID_HEADER]
    assert client.post("/analyze", json={"method": "x"}).headers[REQUEST_ID_HEADER]


def test_cors_exposes_the_headers_the_frontend_needs_to_read() -> None:
    response = client.get("/health/live", headers={"Origin": ORIGIN})

    exposed = {item.strip().lower() for item in response.headers["access-control-expose-headers"].split(",")}
    assert {"x-request-id", "retry-after"} <= exposed


def test_request_id_appears_in_analysis_events_and_is_added_to_every_log_record(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("app.analysis")
    token = request_id_var.set("req-log-12345678")
    try:
        with caplog.at_level(logging.INFO, logger=logger.name):
            log_analysis_event("start", method="map")
        record = logging.LogRecord("x", logging.INFO, __file__, 1, "msg", None, None)
        assert RequestIdLogFilter().filter(record) and record.request_id == "req-log-12345678"
    finally:
        request_id_var.reset(token)

    assert "analysis_event=start" in caplog.text and "request_id=req-log-12345678" in caplog.text
    outside = logging.LogRecord("x", logging.INFO, __file__, 1, "msg", None, None)
    RequestIdLogFilter().filter(outside)
    assert outside.request_id == "-"
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=logger.name):
        log_analysis_event("start", method="map")
    assert "request_id=" not in caplog.text  # poza żądaniem nie ma czego dopisać


def test_console_log_format_prints_the_request_id_next_to_every_entry() -> None:
    from unittest.mock import patch as mock_patch

    from app.core.logging import configure_logging

    with mock_patch("app.core.logging.dictConfig") as dict_config:
        configure_logging()
    config = dict_config.call_args.args[0]
    console = config["handlers"]["console"]
    assert "request_id" in console["filters"] and "redact_secrets" in console["filters"]

    record = logging.LogRecord("app.x", logging.INFO, __file__, 1, "wpis", None, None)
    token = request_id_var.set("req-format-1234")
    try:
        RequestIdLogFilter().filter(record)
    finally:
        request_id_var.reset(token)
    line = logging.Formatter(config["formatters"]["default"]["format"]).format(record)
    assert "[req-format-1234] wpis" in line


def test_request_id_reaches_the_analysis_thread_through_the_context() -> None:
    """``asyncio.to_thread`` (zapis analizy) kopiuje kontekst, więc log persistence widzi ``request_id``."""
    import asyncio

    seen: list[str | None] = []

    async def scenario() -> None:
        token = request_id_var.set("req-thread-1234")
        try:
            await asyncio.to_thread(lambda: seen.append(request_id_var.get()))
        finally:
            request_id_var.reset(token)

    asyncio.run(scenario())
    assert seen == ["req-thread-1234"]


# --- Kontrakt ErrorResponse dla HTTPException i walidacji --------------------------------------


def test_rate_limit_429_is_an_error_response_with_retry_after_and_cors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    with patch("app.routers.analyze.run_analysis", AsyncMock(side_effect=uldk.ParcelNotFoundError("brak"))):
        statuses = [
            client.post("/analyze", json=PARCEL_REQUEST, headers={"Origin": ORIGIN}).status_code
            for _ in range(settings.rate_limit_analyze_per_minute)
        ]
        limited = client.post("/analyze", json=PARCEL_REQUEST, headers={"Origin": ORIGIN})

    assert set(statuses) == {404}
    body = _assert_error_response(limited, status=429, error="RATE_LIMITED")
    assert int(limited.headers["retry-after"]) >= 1
    assert limited.headers["access-control-allow-origin"] == ORIGIN
    assert "limit" in body["detail"].lower()


def _tiny_app() -> FastAPI:
    tiny = FastAPI()
    register_error_handlers(tiny)

    @tiny.get("/teapot")
    def teapot() -> None:
        raise HTTPException(status_code=418, detail="Jestem czajniczkiem.", headers={"X-Extra": "1"})

    @tiny.get("/no-detail")
    def no_detail() -> None:
        raise HTTPException(status_code=409)

    @tiny.get("/structured")
    def structured() -> None:
        raise HTTPException(status_code=400, detail={"pole": "nie string"})

    @tiny.get("/unknown-status")
    def unknown_status() -> None:
        raise HTTPException(status_code=499, detail="Nietypowy kod.")

    @tiny.get("/not-modified")
    def not_modified() -> None:
        raise HTTPException(status_code=304)

    return tiny


def test_every_http_exception_becomes_an_error_response_keeping_detail_and_headers() -> None:
    tiny = TestClient(_tiny_app(), raise_server_exceptions=False)

    teapot = tiny.get("/teapot")
    assert teapot.status_code == 418 and teapot.headers["x-extra"] == "1"
    assert teapot.json()["detail"] == "Jestem czajniczkiem." and teapot.json()["error"] == "HTTP_418"

    no_detail = tiny.get("/no-detail")
    assert (no_detail.status_code, no_detail.json()["error"], no_detail.json()["detail"]) == (
        409, "CONFLICT", "Conflict",
    )
    # ``detail`` w ErrorResponse jest stringiem: struktura nie wycieka jako obiekt.
    structured = tiny.get("/structured")
    assert structured.status_code == 400 and isinstance(structured.json()["detail"], str)
    assert tiny.get("/unknown-status").json()["error"] == "HTTP_499"
    not_modified = tiny.get("/not-modified")
    assert not_modified.status_code == 304 and not_modified.content == b""


@pytest.mark.parametrize(
    ("method", "path", "status", "error"),
    [
        ("get", "/nie-ma-takiej-sciezki", 404, "NOT_FOUND"),
        ("get", "/analyze", 405, "METHOD_NOT_ALLOWED"),
        ("get", "/report/1", 403, "FORBIDDEN"),
        ("get", "/analyze/1/pending-document", 403, "FORBIDDEN"),
        ("get", "/health/llm", 401, "UNAUTHORIZED"),
    ],
)
def test_errors_of_the_real_routers_use_the_error_response_contract(
    method: str, path: str, status: int, error: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "admin_api_keys", "ops:klucz-ops")

    response = getattr(client, method)(path, headers={"Origin": ORIGIN})

    _assert_error_response(response, status=status, error=error)
    assert response.headers["access-control-allow-origin"] == ORIGIN


def test_v1_validation_error_has_request_id_and_legacy_validation_format_is_kept() -> None:
    v1 = client.get("/api/v1/search/addresses")
    legacy = client.post("/analyze", json={"method": "x"})

    _assert_error_response(v1, status=422, error="VALIDATION_ERROR")
    # Starsze endpointy zachowują format FastAPI (lista błędów); identyfikator jest w nagłówku.
    assert legacy.status_code == 422 and isinstance(legacy.json()["detail"], list)
    assert legacy.headers[REQUEST_ID_HEADER]


def test_every_declared_error_response_in_openapi_uses_the_error_response_schema() -> None:
    """Architektura: routery deklarują błędy domenowe wyłącznie jako ``ErrorResponse``."""
    offenders: list[str] = []
    for path, operations in app.openapi()["paths"].items():
        for method, operation in operations.items():
            for status, response in operation.get("responses", {}).items():
                if not status.isdigit() or int(status) < 400 or status == "422":
                    continue
                schema = response.get("content", {}).get("application/json", {}).get("schema")
                if schema is not None and schema.get("$ref") != "#/components/schemas/ErrorResponse":
                    offenders.append(f"{method.upper()} {path} {status}: {schema}")
    assert not offenders, "\n".join(offenders)


def test_the_main_app_registers_every_global_and_domain_handler() -> None:
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from fastapi.exceptions import RequestValidationError

    registered = set(app.exception_handlers)
    assert {Exception, StarletteHTTPException, RequestValidationError} <= registered
    assert {rule.exception for rule in DOMAIN_ERRORS} <= registered


def test_analyze_router_does_not_catch_domain_errors_itself_so_the_mapping_has_one_source() -> None:
    source = (Path(__file__).parents[1] / "app" / "routers" / "analyze.py").read_text(encoding="utf-8")
    analyze_body = source.split("async def analyze(", 1)[1].split("@router.post(\n    \"/resume\"", 1)[0]
    assert "except" not in analyze_body and "raise HTTPException" not in analyze_body


# --- AU-002: żadna klasa wyjątku modułów uldk/initiation/persistence nie daje HTTP 500 ---------


def _exception_classes(*modules) -> list[type[Exception]]:
    found: dict[str, type[Exception]] = {}
    for module in modules:
        for _, member in inspect.getmembers(module, inspect.isclass):
            if issubclass(member, Exception) and member.__module__ == module.__name__:
                found[f"{member.__module__}.{member.__name__}"] = member
    return list(found.values())


_MODULE_EXCEPTIONS = _exception_classes(uldk, initiation, persistence)


def test_the_discovery_of_module_exceptions_finds_the_known_classes() -> None:
    names = {cls.__name__ for cls in _MODULE_EXCEPTIONS}
    assert {
        "InvalidParcelIdentifierError", "ParcelNotFoundError", "UldkNoResultsError",
        "UldkServiceUnavailableError", "InvalidUldkResponseError", "AddressNotFoundError", "PersistenceError",
    } <= names


@pytest.mark.parametrize("exception_class", _MODULE_EXCEPTIONS, ids=lambda cls: cls.__name__)
def test_no_exception_class_of_the_uldk_initiation_persistence_modules_ends_in_http_500(
    exception_class: type[Exception],
) -> None:
    response = _post_with_failing_analysis(exception_class("komunikat testowy"), headers={"Origin": ORIGIN})

    assert response.status_code != 500, f"{exception_class.__name__} → HTTP 500"
    body = _assert_error_response(response, status=response.status_code)
    assert body["error"] != INTERNAL_ERROR_CODE
    assert response.headers["access-control-allow-origin"] == ORIGIN


@pytest.mark.parametrize(
    ("exception", "status", "error"),
    [
        (uldk.InvalidParcelIdentifierError("zły format"), 422, "INVALID_PARCEL_IDENTIFIER"),
        (uldk.ParcelNotFoundError("brak działki"), 404, "PARCEL_NOT_FOUND"),
        (uldk.UldkNoResultsError("brak wyników"), 404, "PARCEL_NOT_FOUND"),
        (initiation.AddressNotFoundError("brak adresu"), 404, "ADDRESS_NOT_FOUND"),
        (uldk.UldkServiceUnavailableError("usługa padła"), 503, "UPSTREAM_UNAVAILABLE"),
        (uldk.InvalidUldkResponseError("śmieci"), 502, "UPSTREAM_INVALID_RESPONSE"),
        (PersistenceError(), 503, "PERSISTENCE_FAILED"),
    ],
    ids=lambda value: type(value).__name__ if isinstance(value, Exception) else str(value),
)
def test_domain_errors_map_to_the_documented_status_and_code(exception: Exception, status: int, error: str) -> None:
    response = _post_with_failing_analysis(exception)

    body = _assert_error_response(response, status=status, error=error)
    if isinstance(exception, uldk.InvalidUldkResponseError):
        assert "śmieci" not in body["detail"]  # szczegóły odpowiedzi usługi nie trafiają do użytkownika
    if isinstance(exception, PersistenceError):
        assert body["detail"] == PERSISTENCE_FAILED_MESSAGE
    if status in (404, 422) and not isinstance(exception, uldk.InvalidUldkResponseError):
        assert body["detail"] == str(exception)


# --- AU-002 end-to-end: zamrożone odpowiedzi ULDK przez cały stos HTTP -------------------------


def _frozen(name: str) -> str:
    return (FROZEN_ULDK / name).read_text(encoding="utf-8")


@pytest.fixture
def no_sleep():
    with patch("app.services.uldk.asyncio.sleep", new_callable=AsyncMock) as mock:
        yield mock


@pytest.mark.parametrize("payload", [PARCEL_REQUEST, MAP_REQUEST], ids=["parcel_id", "map_click_baltic"])
@pytest.mark.parametrize(
    ("fixture", "status", "error"),
    [
        ("brak_wynikow_baltyk.txt", 404, "PARCEL_NOT_FOUND"),
        ("brak_wynikow_nieistniejaca_dzialka.txt", 404, "PARCEL_NOT_FOUND"),
        ("zero_bez_danych.txt", 404, "PARCEL_NOT_FOUND"),
        ("odpowiedz_pusta.txt", 502, "UPSTREAM_INVALID_RESPONSE"),
        ("niepoprawny_parametr.txt", 502, "UPSTREAM_INVALID_RESPONSE"),
        ("blad_z_komunikatem.txt", 503, "UPSTREAM_UNAVAILABLE"),
    ],
)
@respx.mock
def test_frozen_uldk_responses_never_end_in_a_500_through_the_http_stack(
    payload: dict, fixture: str, status: int, error: str, no_sleep
) -> None:
    upstream_metrics.reset()
    respx.get(ULDK_BASE_URL).mock(return_value=httpx.Response(200, text=_frozen(fixture)))

    response = client.post("/analyze", json=payload, headers={"Origin": ORIGIN})

    body = _assert_error_response(response, status=status, error=error)
    assert response.headers["access-control-allow-origin"] == ORIGIN
    if fixture.startswith("brak_wynikow"):
        assert body["detail"] == ULDK_NOT_FOUND_MESSAGE
        assert upstream_metrics.snapshot()["uldk.response.-1"] == 2  # odpowiedź + jedno ponowienie
    if status == 502:
        assert uuid.UUID(body["request_id"]) and "niepoprawny" not in body["detail"]


@respx.mock
def test_uldk_connection_failure_is_a_503_not_a_raw_httpx_500(no_sleep) -> None:
    respx.get(ULDK_BASE_URL).mock(side_effect=httpx.ConnectError("odmowa połączenia"))

    response = client.post("/analyze", json=PARCEL_REQUEST, headers={"Origin": ORIGIN})

    _assert_error_response(response, status=503, error="UPSTREAM_UNAVAILABLE")


# --- Metryki ULDK dla operatora ----------------------------------------------------------------


def test_upstream_metrics_endpoint_requires_the_admin_key_and_lists_counters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "admin_api_keys", "ops:klucz-ops")
    upstream_metrics.reset()
    upstream_metrics.increment("uldk.response.-1", 2)

    assert client.get("/health/upstream").status_code == 401
    assert client.get("/health/upstream", headers={"X-Admin-Key": "zly"}).status_code == 401
    response = client.get("/health/upstream", headers={"X-Admin-Key": "klucz-ops"})
    assert response.status_code == 200
    assert response.json() == {
        "counters": {"uldk.response.-1": 2},
        "gauges": {"analysis_singleflight_waiters": 0, "lookup_singleflight_waiters": 0},
    }
    upstream_metrics.reset()
