"""Konfiguracja bezpiecznych logów operacyjnych backendu."""

from __future__ import annotations

import logging
import re
from logging.config import dictConfig
from typing import Final

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


def configure_logging() -> None:
    """Konfiguruje wspólny format bez wyłączania loggerów bibliotek/aplikacji; redaguje sekrety."""
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {"redact_secrets": {"()": SecretRedactionFilter}},
            "formatters": {
                "default": {
                    "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
                }
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                    "stream": "ext://sys.stdout",
                    "filters": ["redact_secrets"],
                }
            },
            "root": {"level": "INFO", "handlers": ["console"]},
        }
    )


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
    details = " ".join(
        f"{key}={value}" for key, value in sorted(safe_fields.items())
    )
    message = f"analysis_event={event}"
    if details:
        message = f"{message} {details}"
    logging.getLogger(ANALYSIS_LOGGER_NAME).info(message)
