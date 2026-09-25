"""Kontrakty realnego parsera dokumentów MPZP.

Uwaga: ``MpzpZoneResult`` w tym module ma INNY kształt niż
``app.schemas.analyze.MpzpZoneResult``. Tamta klasa jest płaskim, z góry
ustalonym kontraktem wczesnej odpowiedzi API. Tutaj wynik parsera przechowuje
generyczne parametry i ślad dowodowy każdej wartości. Kolizja nazw jest
świadoma; scalenie obu kontraktów wymaga przyszłego zadania integracyjnego.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ExtractedEvidence(BaseModel):
    """Ślad dowodowy wartości: fragment, szerszy kontekst i numer strony."""

    raw_value: str | None = Field(
        default=None,
        description="Dokładny fragment źródłowy przed normalizacją.",
    )
    source_text: str | None = Field(
        default=None,
        description="Szerszy kontekst zawierający wyekstrahowaną wartość.",
    )
    page_number: int | None = Field(
        default=None,
        description="Numer strony; None dla materiałów bez pojęcia strony.",
    )


class MpzpParameter(BaseModel):
    """Parametr planistyczny z płaskim śladem dowodowym i poziomem pewności."""

    name: str = Field(
        description="Identyfikator znormalizowanego parametru planistycznego.",
        json_schema_extra={"example": "max_building_height_m"},
    )
    normalized_value: float | str | None = Field(
        default=None,
        description="Znormalizowana wartość liczbowa albo kategoryczna.",
    )
    unit: str | None = Field(
        default=None,
        description="Jednostka wartości; None dla wartości tekstowych.",
    )
    raw_value: str | None = Field(
        default=None,
        description="Wartość zapisana dosłownie w dokumencie źródłowym.",
    )
    source_text: str | None = Field(
        default=None,
        description="Fragment uchwały stanowiący dowód wartości.",
    )
    page_number: int | None = Field(
        default=None,
        description="Numer strony dokumentu źródłowego.",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Poziom pewności ekstrakcji tego parametru.",
    )
    manual_review_required: bool = Field(
        description="Czy wartość wymaga ręcznej weryfikacji.",
    )
    segment_id: str | None = Field(
        default=None, description="Segment dokumentu zawierający fragment dowodowy."
    )
    extraction_method: str | None = Field(
        default=None, description="Metoda ekstrakcji tekstu: pdf_text, html albo ocr."
    )
    parser_version: str | None = Field(default=None)
    document_sha256: str | None = Field(default=None)
    conflict_group_id: str | None = Field(
        default=None,
        description="Wspólne ID sprzecznych kandydatur tego samego parametru strefy.",
    )


class MpzpZoneResult(BaseModel):
    """Wynik parsera jednej strefy; NIE jest ``analyze.MpzpZoneResult``.

    Parserowy kontrakt przechowuje generyczną listę parametrów z evidence,
    podczas gdy klasa w ``schemas.analyze`` ma płaskie pola odpowiedzi API.
    """

    zone_symbol: str = Field(
        description="Symbol strefy MPZP.",
        json_schema_extra={"example": "MN"},
    )
    zone_evidence: ExtractedEvidence | None = Field(
        default=None,
        description="Opcjonalny dowód identyfikacji symbolu strefy.",
    )
    parameters: list[MpzpParameter] = Field(
        default_factory=list,
        description="Parametry wyekstrahowane dla tej strefy.",
    )


ParserStage = Literal[
    "classify_document",
    "extract_text",
    "segment_document",
    "extract_parameters",
    "validate_result",
]


class MpzpParserWarning(BaseModel):
    """Ostrzeżenie parsera wskazujące etap i opcjonalny kontekst domenowy."""

    stage: ParserStage = Field(
        description="Etap pipeline'u, na którym powstało ostrzeżenie.",
    )
    code: str = Field(
        description="Krótki identyfikator ostrzeżenia.",
        json_schema_extra={"example": "NEEDS_OCR"},
    )
    message: str = Field(description="Czytelny komunikat dla użytkownika.")
    zone_symbol: str | None = Field(
        default=None,
        description="Symbol strefy, jeżeli ostrzeżenie jej dotyczy.",
    )
    parameter_name: str | None = Field(
        default=None,
        description="Nazwa parametru, jeżeli ostrzeżenie dotyczy wartości.",
    )
    page_number: int | None = Field(default=None)
    severity: Literal["info", "warning", "error"] = Field(
        description="Poziom istotności zgodny z WarningMessage.",
    )


class MpzpParseRequest(BaseModel):
    """Wejście przyszłego endpointu parsera; endpoint nie powstaje w tym zadaniu."""

    uchwala_url: str = Field(description="URL dokumentu MPZP do przetworzenia.")
    plan_id: str | None = Field(
        default=None,
        description="Identyfikator planu znany z etapu discovery.",
    )
    zone_symbols: list[str] = Field(
        default_factory=list,
        description="Pomocnicze symbole kandydatów z MPZP discovery.",
    )


class ParserDocumentPage(BaseModel):
    """Wewnętrzny ślad strony używany do trwałego persistence."""

    page_number: int
    text: str
    ocr_used: bool
    quality: float | None = None
    blocks: list[dict[str, Any]] = Field(default_factory=list)


class ParserDocumentSegment(BaseModel):
    """Wewnętrzny ślad segmentu zachowujący stronę i identyfikator."""

    segment_id: str
    text: str
    page_number: int | None
    heading: str | None
    source: Literal["paragraph", "table"]


class ParserDocumentAudit(BaseModel):
    """Metadane umożliwiające zapis stron i jednostek bez ponownej ekstrakcji."""

    media_type: str
    extraction_method: Literal["pdf_text", "html", "ocr", "unsupported"]
    ocr_engine_version: str | None = None
    quality_score: float
    manual_review_required: bool
    pages: list[ParserDocumentPage] = Field(default_factory=list)
    segments: list[ParserDocumentSegment] = Field(default_factory=list)


class MpzpParseResult(BaseModel):
    """Zagregowany, pełny, częściowy albo nieudany wynik parsowania dokumentu."""

    plan_id: str | None = Field(default=None)
    zones: list[MpzpZoneResult] = Field(default_factory=list)
    status: Literal["complete", "partial", "failed"] = Field(
        description=(
            "Complete oznacza strefę z parametrem, partial strefy bez parametrów, "
            "a failed brak możliwej do wyznaczenia strefy."
        )
    )
    warnings: list[MpzpParserWarning] = Field(default_factory=list)
    conflict_flags: list[str] = Field(
        default_factory=list,
        description=(
            "Krótkie, ludzkie opisy wykrytych sprzeczności parametrów, np. "
            '"230_UMW: max_building_height_m ma sprzeczne wartości (15.0, 13.0)".'
        ),
    )
    document_audit: ParserDocumentAudit | None = Field(
        default=None,
        exclude=True,
        description=(
            "Wewnętrzny ślad persistence; nie jest elementem publicznego "
            "kontraktu odpowiedzi parsera."
        ),
    )
