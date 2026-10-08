"""Nagłówki ochronne odpowiedzi z tokenami dostępu i raportami (AU-012).

Odpowiedzi ``/report/…`` (PDF, pakiet audytowy), ``/analyze/…`` (w tym ``pending-document``,
``links`` i ``resume``) oraz sama ``POST /analyze`` (zwraca ``access_token``) niosą dane analizy
albo token dostępu. Middleware dopisuje do nich — także do odpowiedzi błędów — dwa nagłówki:

- ``Referrer-Policy: no-referrer`` — adres z ``?access_token=…`` nie wycieka w ``Referer`` do
  zasobów otwieranych z pobranego dokumentu;
- ``Cache-Control: private, no-store`` — ani przeglądarka, ani pośrednik nie zachowuje kopii.

Czysty ASGI (bez ``BaseHTTPMiddleware``), tak jak ``RequestIdMiddleware``: nie opakowuje strumieni.
"""

from __future__ import annotations

from typing import Final

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

SENSITIVE_RESPONSE_HEADERS: Final[dict[str, str]] = {
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "private, no-store",
}
_SENSITIVE_PREFIXES: Final[tuple[str, ...]] = ("/report/", "/analyze/")
_SENSITIVE_EXACT: Final[frozenset[str]] = frozenset({"/analyze"})


def is_sensitive_path(path: str) -> bool:
    return path in _SENSITIVE_EXACT or path.startswith(_SENSITIVE_PREFIXES)


class SensitiveResponseHeadersMiddleware:
    """Wymusza ``Referrer-Policy`` i ``Cache-Control`` na ścieżkach z tokenami i raportami."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not is_sensitive_path(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                headers = MutableHeaders(scope=message)
                for name, value in SENSITIVE_RESPONSE_HEADERS.items():
                    headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_headers)
