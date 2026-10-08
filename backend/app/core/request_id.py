"""Identyfikator żądania ``X-Request-ID`` (AU-003): nagłówek, kontekst logowania i odpowiedzi błędów.

Middleware przyjmuje poprawny identyfikator z nagłówka żądania (np. nadany przez proxy), a w
przeciwnym razie generuje UUID4. Identyfikator trafia do:

- ``scope["state"]["request_id"]`` — handler wyjątków ``ServerErrorMiddleware`` działa poza
  middleware aplikacji, więc nie może polegać na ``ContextVar`` zresetowanym po wyjściu z niego;
- ``ContextVar`` — odczytywanej przez filtr logów i ``log_analysis_event``; ``asyncio.to_thread``
  kopiuje kontekst, więc wartość widać także w wątku zapisu analizy;
- nagłówka odpowiedzi ``X-Request-ID`` oraz pola ``request_id`` w ``ErrorResponse``.

Middleware jest czystym ASGI (bez ``BaseHTTPMiddleware``), dzięki czemu nie opakowuje wyjątków
ani strumieni odpowiedzi.
"""

from __future__ import annotations

import logging
import re
import uuid
from contextvars import ContextVar
from typing import Final

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER: Final[str] = "X-Request-ID"
# Dopuszczamy identyfikatory UUID i typowe identyfikatory proxy (krótkie, bez znaków sterujących),
# żeby klient nie mógł wstrzyknąć do logów ani nagłówków dowolnego tekstu.
_VALID_REQUEST_ID: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,63}$")
_NO_REQUEST: Final[str] = "-"

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def new_request_id() -> str:
    return str(uuid.uuid4())


def accept_request_id(raw: str | None) -> str:
    """Zwraca ``raw``, jeśli jest poprawnym identyfikatorem, inaczej nowy UUID4."""
    if raw is not None and _VALID_REQUEST_ID.fullmatch(raw):
        return raw
    return new_request_id()


def get_request_id() -> str | None:
    """Identyfikator bieżącego żądania albo ``None`` poza żądaniem HTTP."""
    return request_id_var.get()


def request_id_from_scope(scope: Scope) -> str | None:
    state = scope.get("state")
    if isinstance(state, dict):
        value = state.get("request_id")
        return value if isinstance(value, str) else None
    return None


class RequestIdMiddleware:
    """Nadaje ``X-Request-ID`` każdemu żądaniu HTTP i dopisuje go do odpowiedzi."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get(REQUEST_ID_HEADER)
        request_id = accept_request_id(incoming)
        scope.setdefault("state", {})["request_id"] = request_id
        token = request_id_var.set(request_id)

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                headers = MutableHeaders(scope=message)
                if REQUEST_ID_HEADER not in headers:
                    headers.append(REQUEST_ID_HEADER, request_id)
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            request_id_var.reset(token)


class RequestIdLogFilter(logging.Filter):
    """Dopisuje ``record.request_id`` (``-`` poza żądaniem) do każdego rekordu logu."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get() or _NO_REQUEST
        return True
