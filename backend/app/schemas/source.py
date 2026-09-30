"""Wspólne struktury metadanych źródeł danych i ostrzeżeń.

Modele są używane przez moduły domenowe z Epiku 3 (KIUT, ISOK, GDOŚ) oraz
przez odpowiedź API ``/analyze``. Wydzielenie ich z ``schemas/analyze.py``
pozwala serwisom importować wspólny kontrakt bez zależności od pozostałej
części odpowiedzi endpointu.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, computed_field, model_validator

from app.shared.data_quality import (
    FRESHNESS_DESCRIPTIONS_PL,
    FRESHNESS_LABELS_PL,
    FRESHNESS_STATES,
    QUALITY_STATUS_DESCRIPTIONS_PL,
    QUALITY_STATUS_LABELS_PL,
    QUALITY_STATUSES,
    FreshnessBasis,
    FreshnessState,
    SectionQualityStatus,
    canonical_sha256,
    reason_label_pl,
)
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


# --- Macierz kompletności i świeżości sekcji (BK-504, ADR-011) ---------------------

QUALITY_RESULT_SCHEMA_VERSION = "1.0"

SectionKey = Literal[
    "parcel",
    "mpzp",
    "pog",
    "pog_overlays",
    "flood",
    "nature",
    "terrain",
    "utilities",
    "transport",
    "mpzp_pog_relation",
]


def _iso_utc(value: datetime | None) -> str | None:
    """Kanoniczny zapis czasu (UTC, ``Z``) — hash nie zależy od strefy zapisu."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.isoformat()
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class FreshnessAssessment(BaseModel):
    """Świeżość danych sekcji względem jawnej reguły źródła i punktu odniesienia."""

    state: FreshnessState = Field(
        description=(
            "fresh — wiek mieści się w regule źródła; stale — przekracza regułę; "
            "unknown — brak reguły, brak czasu pobrania albo czas nieprawidłowy."
        )
    )
    reason_code: str | None = Field(
        default=None,
        description="Kod FRESHNESS_* dla stanu innego niż fresh.",
    )
    reference_at: datetime = Field(
        description=(
            "Punkt odniesienia oceny: chwila analizy (analyzed_at). Ocena zapisana "
            "z analizą jest historyczna i nie zmienia się z upływem czasu."
        )
    )
    age_seconds: int | None = Field(
        default=None,
        ge=0,
        description="Wiek danych w punkcie odniesienia, s; null gdy czas nieprawidłowy.",
    )
    max_age_days: int | None = Field(
        default=None,
        ge=1,
        description="Limit wieku z reguły źródła; null, gdy źródło nie ma reguły.",
    )
    basis: FreshnessBasis | None = Field(
        default=None, description="Podstawa reguły wieku (deklaracja źródła albo decyzja projektowa)."
    )

    def canonical(self) -> dict[str, object]:
        return {
            "state": self.state,
            "reason_code": self.reason_code,
            "reference_at": _iso_utc(self.reference_at),
            "age_seconds": self.age_seconds,
            "max_age_days": self.max_age_days,
            "basis": self.basis,
        }


class SectionQuality(BaseModel):
    """Jakość jednej sekcji analizy: status, źródło, czas, wydanie i świeżość."""

    section: SectionKey
    report_section: str = Field(
        description="Identyfikator sekcji raportu PDF, do której należy sekcja analizy."
    )
    status: SectionQualityStatus = Field(
        description="Status kompletności według kontraktu źródła (nie świeżości)."
    )
    source_id: str | None = Field(
        default=None,
        description="Identyfikator źródła z katalogu; null, gdy sekcja nie ma źródła.",
    )
    source_name: str | None = Field(
        default=None, description="Nazwa źródła użyta w wyniku sekcji (prezentacja)."
    )
    fetched_at: datetime | None = Field(
        default=None,
        description="Czas pobrania danych (nie data wejścia aktu w życie).",
    )
    data_release_id: int | None = Field(default=None, gt=0)
    source_version: str | None = None
    manual_review_required: bool
    freshness: FreshnessAssessment
    policy_version: str = Field(
        description="Wersja polityki jakości (format + odcisk reguł katalogu)."
    )
    reason_codes: list[str] = Field(
        default_factory=list,
        description="Kody powodów statusu i nieprawidłowości czasu; puste tylko przy pełnym wyniku.",
    )

    def canonical(self) -> dict[str, object]:
        return {
            "section": self.section,
            "report_section": self.report_section,
            "status": self.status,
            "source_id": self.source_id,
            "source_name": self.source_name,
            "fetched_at": _iso_utc(self.fetched_at),
            "data_release_id": self.data_release_id,
            "source_version": self.source_version,
            "manual_review_required": self.manual_review_required,
            "freshness": self.freshness.canonical(),
            "policy_version": self.policy_version,
            "reason_codes": list(self.reason_codes),
        }


class QualityLegendItem(BaseModel):
    id: str
    label: str
    description: str


class QualityLegendReason(BaseModel):
    code: str
    label: str


class SectionQualityLegend(BaseModel):
    """Legenda macierzy: to samo znaczenie statusów w API, UI i PDF."""

    statuses: list[QualityLegendItem]
    freshness: list[QualityLegendItem]
    reasons: list[QualityLegendReason]


class SectionQualityMatrix(BaseModel):
    """Trwała macierz jakości sekcji zapisana z analizą.

    ``matrix_sha256`` obejmuje wyłącznie treść oceny (sekcje, wersję polityki,
    punkt odniesienia). Nie zależy od chwili eksportu ani od pochodzenia macierzy
    (``origin``), więc komunikat o wieku na dzień eksportu jej nie zmienia.
    """

    schema_version: str = QUALITY_RESULT_SCHEMA_VERSION
    policy_version: str
    reference_at: datetime = Field(
        description="Punkt odniesienia świeżości zapisanej oceny (chwila analizy)."
    )
    origin: Literal["stored", "reconstructed"] = Field(
        default="stored",
        description=(
            "stored — ocena wystawiona i zapisana razem z analizą; reconstructed — "
            "zapis sprzed BK-504 (brak macierzy), oceniony przy odczycie bieżącą "
            "polityką i niezapisany."
        ),
    )
    sections: list[SectionQuality]
    matrix_sha256: str = Field(min_length=64, max_length=64)

    def compute_sha256(self) -> str:
        return canonical_sha256(
            {
                "schema_version": self.schema_version,
                "policy_version": self.policy_version,
                "reference_at": _iso_utc(self.reference_at),
                "sections": [item.canonical() for item in self.sections],
            }
        )

    def integrity_ok(self) -> bool:
        return self.matrix_sha256 == self.compute_sha256()

    @computed_field(  # type: ignore[prop-decorator]
        description="Legenda statusów, świeżości i kodów powodów obecnych w macierzy."
    )
    @property
    def legend(self) -> SectionQualityLegend:
        codes: list[str] = []
        for item in self.sections:
            for code in [*item.reason_codes, item.freshness.reason_code or ""]:
                if code and code not in codes:
                    codes.append(code)
        return SectionQualityLegend(
            statuses=[
                QualityLegendItem(
                    id=key,
                    label=QUALITY_STATUS_LABELS_PL[key],
                    description=QUALITY_STATUS_DESCRIPTIONS_PL[key],
                )
                for key in QUALITY_STATUSES
            ],
            freshness=[
                QualityLegendItem(
                    id=key,
                    label=FRESHNESS_LABELS_PL[key],
                    description=FRESHNESS_DESCRIPTIONS_PL[key],
                )
                for key in FRESHNESS_STATES
            ],
            reasons=[QualityLegendReason(code=code, label=reason_label_pl(code)) for code in codes],
        )
