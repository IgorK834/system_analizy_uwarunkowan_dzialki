"""Konfiguracja bezpiecznych logów operacyjnych backendu."""

from __future__ import annotations

import logging
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


def configure_logging() -> None:
    """Konfiguruje wspólny format bez wyłączania loggerów bibliotek/aplikacji."""
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
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
