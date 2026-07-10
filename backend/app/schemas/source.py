"""Wspólne struktury metadanych źródeł danych i ostrzeżeń.

Modele są używane przez moduły domenowe z Epiku 3 (KIUT, ISOK, GDOŚ) oraz
przez odpowiedź API ``/analyze``. Wydzielenie ich z ``schemas/analyze.py``
pozwala serwisom importować wspólny kontrakt bez zależności od pozostałej
części odpowiedzi endpointu.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SourceMetadata(BaseModel):
    source_name: str = Field(
        description="Nazwa zewnętrznego źródła danych.",
        json_schema_extra={"example": "ULDK"},
    )
    source_url: str | None = Field(
        default=None,
        description="Adres URL źródła danych, jeżeli jest dostępny.",
        json_schema_extra={"example": "https://uldk.gugik.gov.pl/"},
    )
    fetched_at: datetime | None = Field(
        default=None,
        description="Data i czas pobrania danych ze źródła.",
        json_schema_extra={"example": "2026-07-02T12:00:00Z"},
    )
    response_status: int | None = Field(
        default=None,
        description=(
            "Kod odpowiedzi HTTP zewnętrznego źródła, gdy dotyczy. None oznacza, "
            "że zapytania HTTP nie wykonano albo status nie ma zastosowania."
        ),
        json_schema_extra={"example": 200},
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Poziom pewności danych 0-1.",
        json_schema_extra={"example": 0.95},
    )
    manual_review_required: bool = Field(
        description="Czy wynik wymaga ręcznej weryfikacji.",
        json_schema_extra={"example": False},
    )


class WarningMessage(BaseModel):
    code: str = Field(
        description="Krótki identyfikator ostrzeżenia.",
        json_schema_extra={"example": "MPZP_PARTIAL"},
    )
    message: str = Field(
        description="Czytelny komunikat dla użytkownika, bez informacji technicznych.",
        json_schema_extra={
            "example": "Nie udało się pobrać pełnych danych MPZP dla działki."
        },
    )
    severity: Literal["info", "warning", "error"] = Field(
        description=(
            "Poziom istotności: info dla informacji poglądowej, warning dla "
            "danych częściowych, error dla danych niedostępnych lub błędu sekcji."
        ),
        json_schema_extra={"example": "warning"},
    )
    source_name: str | None = Field(
        default=None,
        description="Nazwa sekcji lub źródła, którego dotyczy ostrzeżenie.",
        json_schema_extra={"example": "isok"},
    )


def warnings_from_domain_messages(
    source_name: str,
    messages: list[str],
    severity: Literal["info", "warning", "error"] = "warning",
) -> list[WarningMessage]:
    """Mapuje surowe ostrzeżenia domenowe na kontrakt odpowiedzi ``/analyze``.

    Generyczny kod ``{SOURCE_NAME}_WARNING`` celowo nie rozpoznaje treści
    komunikatu. Szczegółowe kody przypadków domenowych wymagają osobnego,
    jawnego kontraktu i należą do przyszłej orkiestracji analizy.
    """
    return [
        WarningMessage(
            code=f"{source_name.upper()}_WARNING",
            message=message,
            severity=severity,
            source_name=source_name,
        )
        for message in messages
    ]
