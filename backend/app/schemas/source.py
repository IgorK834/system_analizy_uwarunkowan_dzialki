"""Wspólne struktury metadanych źródeł danych i ostrzeżeń.

Modele są używane przez moduły domenowe z Epiku 3 (KIUT, ISOK, GDOŚ) oraz
przez odpowiedź API ``/analyze``. Wydzielenie ich z ``schemas/analyze.py``
pozwala serwisom importować wspólny kontrakt bez zależności od pozostałej
części odpowiedzi endpointu.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.shared.provenance import is_verified_https_url


class SourceMetadata(BaseModel):
    source_id: str | None = Field(
        default=None, description="Stabilny identyfikator źródła z katalogu."
    )
    source_version: str | None = Field(
        default=None, description="Etykieta wersji źródła lub wydania."
    )
    artifact_sha256: str | None = Field(
        default=None, min_length=64, max_length=64,
        description="SHA-256 zamrożonego artefaktu źródłowego."
    )
    data_release_id: int | None = Field(
        default=None, gt=0, description="Identyfikator wydania przypiętego do analizy."
    )
    act_version: str | None = Field(
        default=None, description="Wersja idIIP aktu planowania przestrzennego."
    )
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


FormalDocumentStatus = Literal["current", "superseded", "unavailable", "unresolved"]


class CatalogMetadataSource(BaseModel):
    """Rekord metadanych CSW RU zamrożony w snapshotcie wyniku (BK-107)."""

    record_id: str = Field(description="gmd:fileIdentifier rekordu CSW.")
    resource_identifier: str | None = Field(
        default=None,
        description="MD_Identifier zbioru — URI przestrzeni nazw aktu; podstawa powiązania.",
    )
    title: str | None = None
    publication_date: date | None = Field(
        default=None, description="Data publikacji zbioru (CI_DateTypeCode=publication)."
    )
    revision_date: date | None = None
    creation_date: date | None = None
    date_stamp: date | None = None
    metadata_url: str | None = Field(
        default=None, description="Oficjalny URL karty metadanych (GetRecordById)."
    )
    metadata_url_verified: bool = False
    references: list[str] = Field(default_factory=list)
    record_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    response_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    fetched_at: datetime | None = None

    @model_validator(mode="after")
    def verify_links(self) -> "CatalogMetadataSource":
        self.metadata_url_verified = is_verified_https_url(self.metadata_url)
        return self


class FormalDocumentSource(BaseModel):
    """Dokument formalny aktu powiązany po identyfikatorze i wersji (BK-107)."""

    document_identifier: str
    document_version: str | None = None
    publication_id: str | None = Field(
        default=None, description="gml:identifier rekordu DokumentFormalny."
    )
    title: str | None = None
    short_name: str | None = None
    identification_number: str | None = None
    relation: str | None = Field(
        default=None, description="Relacja do aktu: przystapienie, uchwala, zmienia, uchyla…"
    )
    document_date: date | None = None
    effective_date: date | None = None
    repeal_date: date | None = None
    link: str | None = None
    link_verified: bool = Field(
        default=False, description="Czy link jest zweryfikowanym HTTPS i może być klikalny."
    )
    record_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    status: FormalDocumentStatus = Field(
        default="current",
        description=(
            "current — aktualny; superseded — uchylony (dataUchylenia); unavailable — "
            "brak rekordu dokumentu; unresolved — powiązanie z wersją aktu nierozstrzygnięte."
        ),
    )
    warning: str | None = Field(
        default=None, description="Widoczne ostrzeżenie dla dokumentu nieaktualnego/niedostępnego."
    )

    @model_validator(mode="after")
    def verify_link(self) -> "FormalDocumentSource":
        # Klikalny może być wyłącznie zweryfikowany HTTPS — niezależnie od danych
        # wejściowych, także przy odczycie starszego snapshotu.
        self.link_verified = bool(self.link_verified and is_verified_https_url(self.link))
        return self


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
