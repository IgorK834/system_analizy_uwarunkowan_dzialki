"""Kontrakty realnego parsera dokumentów MPZP.

Uwaga: ``MpzpZoneResult`` w tym module ma INNY kształt niż
``app.schemas.analyze.MpzpZoneResult``. Tamta klasa jest płaskim, z góry
ustalonym kontraktem wczesnej odpowiedzi API. Tutaj wynik parsera przechowuje
generyczne parametry i ślad dowodowy każdej wartości. Kolizja nazw jest
świadoma; scalenie obu kontraktów wymaga przyszłego zadania integracyjnego.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, SerializerFunctionWrapHandler, model_serializer


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


ConditionKind = Literal["building_type", "roof_type", "subzone", "location", "other"]
# Pola provenance wartości z modelu językowego (PV3-13); pomijane w JSON, gdy puste.
LLM_PROVENANCE_FIELDS: tuple[str, ...] = ("review_status", "model_id", "prompt_version", "response_sha256")
ValueKind = Literal["unconditional", "conditional", "conflict"]


class ValueCondition(BaseModel):
    """Warunek, od którego zależy wartość parametru (PV3-08).

    Wysokość dla dachu płaskiego i wysokość dla pozostałych budynków to dwie wartości
    warunkowe, nie sprzeczność. ``quote`` to dosłowny fragment uchwały wskazujący warunek;
    może leżeć w nagłówku nadrzędnej pozycji listy, więc nie musi należeć do ``source_text``.
    """

    kind: ConditionKind = Field(
        description="Rodzaj warunku: building_type, roof_type, subzone, location albo other."
    )
    label: str = Field(description="Znormalizowana, krótka nazwa warunku do wyświetlenia.")
    quote: str = Field(description="Dosłowny cytat z uchwały wskazujący warunek.")


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
        default=None,
        description=(
            "Metoda ekstrakcji: pdf_text, html albo ocr (tekst dokumentu); llm_verified — wartość z "
            "modelu językowego po bramkach deterministycznych (PV3-12)."
        ),
    )
    parser_version: str | None = Field(default=None)
    document_sha256: str | None = Field(default=None)
    conflict_group_id: str | None = Field(
        default=None,
        description="Wspólne ID sprzecznych kandydatur tego samego parametru strefy.",
    )
    char_start: int | None = Field(
        default=None,
        description=(
            "Początek dopasowania wartości w tekście SUROWYM strony ``page_number`` (znaki); "
            "tylko w trybie blokowym (PV3-06)."
        ),
    )
    char_end: int | None = Field(
        default=None, description="Koniec dopasowania (wyłącznie), jak ``char_start``."
    )
    block_id: str | None = Field(
        default=None, description="Blok strefy, z którego pochodzi wartość (tryb blokowy)."
    )
    scope_kind: Literal["zone_section", "general_clause", "residual_clause", "fallback"] | None = Field(
        default=None,
        description=(
            "Zakres wartości: sekcja strefy, klauzula ogólna, klauzula resztowa albo zapas "
            "(zakres nierozstrzygnięty); ``None`` w trybie dotychczasowym."
        ),
    )
    scope_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    scope_strategy: int | None = Field(
        default=None, ge=0, le=6, description="Strategia zakresu 0–6 (Task 20.6)."
    )
    conditions: list[ValueCondition] = Field(
        default_factory=list,
        description=(
            "Warunki wartości (PV3-08); pusta lista oznacza wartość bezwarunkową. Różne warunki "
            "tego samego parametru to wartości warunkowe, nie sprzeczność."
        ),
    )
    value_kind: ValueKind = Field(
        default="unconditional",
        description=(
            "unconditional — wartość bez warunku; conditional — wartość alternatywna z warunkami; "
            "conflict — ta sama przesłanka ma kilka różnych wartości (ręczna weryfikacja)."
        ),
    )
    confidence_band: Literal["low", "medium", "high"] | None = Field(
        default=None,
        description=(
            "Pasmo pewności z artefaktu kalibracji (PV3-09): low/medium/high. ``None`` — wartość bez "
            "skalibrowanej pewności (zapis opisowy albo parametr poza kalibracją)."
        ),
    )
    confidence_calibration: str | None = Field(
        default=None,
        description="Identyfikator artefaktu kalibracji, z którego pochodzi ``confidence`` (wersja + skrót danych).",
    )
    confidence_features: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Cechy dowodu, z których wyliczono pewność (PV3-09): metoda ekstrakcji, jakość OCR, zakres, "
            "weryfikacja cytatu, strategia, flagi, liczba kandydatów, rodzaj wartości. Brak samooceny modelu."
        ),
    )
    extraction_strategy: str | None = Field(
        default=None,
        description=(
            "Strategia dopasowania silnika ilości (PV3-07): adjective, max_min_noun, comparative, "
            "up_to, range_from_to, range_dash, exact_word, bare, frame_setback, frame_parking."
        ),
    )
    normalization_flags: list[str] = Field(
        default_factory=list,
        description=(
            "Przeróbki zapisu wykonane przy normalizacji (PV3-07): ratio_to_percent, "
            "degree_artifact, number_word, inherited_noun, operator_implied itd. Wartość z flagą "
            "nie była zapisana dosłownie, więc ma obniżoną pewność."
        ),
    )

    # PV3-13: provenance wartości z modelu językowego. Pola są emitowane WYŁĄCZNIE dla wartości z modelu
    # (``extraction_method = llm_verified``), więc wynik trybów deterministycznych ma bajtowo ten sam JSON co
    # przed PV3-13. Wartość z modelu jest zawsze kandydatem do ręcznej weryfikacji, nigdy „verified”.
    review_status: Literal["ai_candidate"] | None = Field(
        default=None,
        description=(
            "``ai_candidate`` — wartość z modelu językowego po bramkach deterministycznych (PV3-12); "
            "brak — wartość z silnika deterministycznego."
        ),
    )
    model_id: str | None = Field(default=None, description="Model, który zaproponował wartość.")
    prompt_version: str | None = Field(default=None, description="Wersja instrukcji ekstrakcji.")
    response_sha256: str | None = Field(
        default=None, description="SHA-256 odpowiedzi modelu zapisanej w ``mpzp_llm_extractions``."
    )

    @model_serializer(mode="wrap")
    def _omit_absent_llm_provenance(self, handler: SerializerFunctionWrapHandler) -> Any:
        data = handler(self)
        if isinstance(data, dict):
            for key in LLM_PROVENANCE_FIELDS:
                if key in data and data[key] is None:
                    del data[key]
        return data


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
