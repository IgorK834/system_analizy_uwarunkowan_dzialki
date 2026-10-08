"""Konfiguracja bezpiecznych logów operacyjnych backendu."""

from __future__ import annotations

import logging
import re
from logging.config import dictConfig
from typing import Final
from urllib.parse import unquote_plus

from app.core.request_id import RequestIdLogFilter, get_request_id

ANALYSIS_LOGGER_NAME: Final[str] = "app.analysis"

_ALLOWED_ANALYSIS_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "method",
        "parcel_identifier",
        "analysis_id",
        "status",
        "elapsed_ms",
        "cache",
        "section",
        "parser_status",
        "warning_code",
        "request_id",
        # AU-007: rola żądania w single-flight (``leader`` | ``wait``), czas oczekiwania i zakres blokady.
        "singleflight",
        "waited_ms",
        "scope",
    }
)


# Redakcja sekretów w logach (PV3-16): klucze API Google (``AIza…``), nagłówek klucza dostawcy,
# nagłówek ``Authorization``, tokeny ``Bearer`` i JWT, parametry ``key=``/``token=`` w adresach oraz
# podpisy dostępu do raportów. Redakcja działa na gotowym komunikacie (po sformatowaniu argumentów) i na
# tekście wyjątku, więc obejmuje także echo sekretu w komunikacie biblioteki.
REDACTED: Final[str] = "<redacted>"
_SECRET_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"(?i)(x-goog-api-key[\"']?\s*[:=]\s*[\"']?)[^\s\"',;}]+"),
    re.compile(r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?)(?:bearer\s+)?[^\s\"',;}]+"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)([?&](?:key|api_key|apikey|token|access_token|signature)=)[^&\s\"']+"),
    # Lista kluczy ``kid:sekret,kid2:sekret2`` — przecinek należy do wartości (AU-012).
    re.compile(r"(?i)(access_token_secrets[\"']?\s*[:=]\s*[\"']?)[^\s\"';}]+"),
    re.compile(r"(?i)((?:gemini_api_key|api_key|access_token_secret|admin_api_keys)[\"']?\s*[:=]\s*[\"']?)[^\s\"',;}]+"),
)


def redact_secrets(text: str) -> str:
    """Zastępuje znane postacie sekretów znacznikiem ``<redacted>`` (prefiks klucza zostaje)."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda m: (m.group(1) if m.lastindex else "") + REDACTED, text)
    return text


class SecretRedactionFilter(logging.Filter):
    """Filtr handlera: redaguje komunikat i tekst wyjątku przed zapisem."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - błędne argumenty nie mogą zatrzymać logowania
            message = str(record.msg)
        redacted = redact_secrets(message)
        if redacted != message or record.args:
            record.msg, record.args = redacted, None
        if record.exc_info and record.exc_info[1] is not None:
            text = logging.Formatter().formatException(record.exc_info)
            record.exc_text = redact_secrets(text)
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = redact_secrets(record.exc_text)
        return True


# AU-012: dziennik dostępu uvicorna (``uvicorn.access``) zapisuje pełną ścieżkę żądania wraz z query-stringiem,
# czyli ``GET /report/1?access_token=…``. Uvicorn ma własny handler (``propagate=False``), więc filtr
# handlera aplikacji go nie obejmuje — maskujemy wartość filtrem samego loggera, jako ``access_token=***``.
ACCESS_TOKEN_MASK: Final[str] = "***"
UVICORN_ACCESS_LOGGER_NAME: Final[str] = "uvicorn.access"
# Para ``?nazwa=wartość`` / ``&nazwa=wartość``; nazwę rozkodowujemy, bo serwer też ją rozkoduje
# (``access%5Ftoken=…`` uwierzytelnia tak samo jak ``access_token=…``).
_QUERY_PAIR: Final[re.Pattern[str]] = re.compile(r"([?&;])([^=&;\s\"'#]+)=([^&;\s\"'#]*)")


def mask_access_tokens(text: str) -> str:
    """Zastępuje wartość parametru ``access_token`` w adresie znacznikiem ``***``."""

    def mask(match: re.Match[str]) -> str:
        separator, name, value = match.groups()
        if unquote_plus(name).strip().lower() != "access_token":
            return match.group(0)
        return f"{separator}{name}={ACCESS_TOKEN_MASK}"

    return _QUERY_PAIR.sub(mask, text)


class AccessTokenMaskFilter(logging.Filter):
    """Filtr loggera: maskuje ``access_token=…`` w komunikacie i argumentach (ścieżka żądania)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = mask_access_tokens(record.msg)
        args = record.args
        if isinstance(args, tuple):
            record.args = tuple(
                mask_access_tokens(arg) if isinstance(arg, str) else arg for arg in args
            )
        elif isinstance(args, dict):
            record.args = {
                key: mask_access_tokens(value) if isinstance(value, str) else value
                for key, value in args.items()
            }
        return True


def install_access_log_mask() -> None:
    """Dodaje filtr maskujący do ``uvicorn.access`` (idempotentnie, bez ruszania handlerów uvicorna)."""
    access_logger = logging.getLogger(UVICORN_ACCESS_LOGGER_NAME)
    if not any(isinstance(item, AccessTokenMaskFilter) for item in access_logger.filters):
        access_logger.addFilter(AccessTokenMaskFilter())


def configure_logging() -> None:
    """Konfiguruje wspólny format bez wyłączania loggerów bibliotek/aplikacji; redaguje sekrety."""
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {
                "redact_secrets": {"()": SecretRedactionFilter},
                "request_id": {"()": RequestIdLogFilter},
            },
            "formatters": {
                "default": {
                    # AU-003: ``request_id`` łączy wpis logu z odpowiedzią błędu widzianą przez klienta.
                    "format": "%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s",
                }
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                    "stream": "ext://sys.stdout",
                    "filters": ["request_id", "redact_secrets"],
                }
            },
            "root": {"level": "INFO", "handlers": ["console"]},
        }
    )
    install_access_log_mask()


def log_analysis_event(event: str, **fields: object) -> None:
    """Loguje zdarzenie analizy, przepuszczając tylko bezpieczne pola.

    Whitelist celowo odrzuca m.in. ``database_url``, surowe dokumenty oraz
    WKT/GeoJSON. Nazwa zdarzenia pochodzi wyłącznie ze stałych w kodzie
    orchestratora, a nie z danych użytkownika.

    Gdy powstanie raport PDF — logowanie startu i końca generowania należy
    podłączyć przez ten sam bezpieczny mechanizm, bez treści dokumentu.
    """
    safe_fields = {
        key: value
        for key, value in fields.items()
        if key in _ALLOWED_ANALYSIS_FIELDS and value is not None
    }
    # AU-003: zdarzenie analizy niesie identyfikator żądania, który użytkownik widzi w błędzie.
    request_id = get_request_id()
    if request_id is not None:
        safe_fields.setdefault("request_id", request_id)
    details = " ".join(
        f"{key}={value}" for key, value in sorted(safe_fields.items())
    )
    message = f"analysis_event={event}"
    if details:
        message = f"{message} {details}"
    logging.getLogger(ANALYSIS_LOGGER_NAME).info(message)
