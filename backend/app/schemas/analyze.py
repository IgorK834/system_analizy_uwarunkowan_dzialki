from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal, Self, Union

from pydantic import (
    BaseModel,
    Field,
    SerializerFunctionWrapHandler,
    computed_field,
    model_serializer,
    model_validator,
)

from app.schemas.mpzp import LLM_PROVENANCE_FIELDS

from app.core.access_control import make_analysis_token

from app.schemas.source import (
    CatalogMetadataSource,
    FormalDocumentSource,
    SectionQualityMatrix,
    SourceMetadata,
    WarningMessage,
)
from app.shared.planning_compatibility_text import (
    COMPATIBILITY_INFORMATIONAL_NOTICE,
    LEGACY_AGGREGATION_NOTE,
)
from app.shared.provenance import is_verified_https_url
from app.shared.zone_symbol import (
    ZONE_SYMBOL_MAX_LENGTH,
    ZONE_SYMBOL_MAX_RAW_LENGTH,
    ZONE_SYMBOL_RULES_VERSION,
)
from app.shared.planning_status import (
    COVERAGE_STATUS_ALIASES,
    LEGACY_LEGAL_STATUSES,
    LEGAL_STATUS_ALIASES,
    LEGAL_STATUS_VALUES,
    CoverageStatus,
    DataAvailability,
    LegalStatus,
    canonical_coverage_status,
    official_status_code,
    upgrade_legacy_legal_status,
)

POG_RESULT_SCHEMA_VERSION = "2.4"


class MapAnalyzeRequest(BaseModel):
    method: Literal["map"] = Field(
        description="Metoda wejścia oparta o punkt kliknięty na mapie.",
        json_schema_extra={"example": "map"},
    )
    lon: float = Field(
        ge=-180.0,
        le=180.0,
        description=(
            "Długość geograficzna punktu kliknięcia w WGS84 (EPSG:4326). "
            "Wartość zostanie przeliczona do EPSG:2180 przed zapytaniem ULDK."
        ),
        json_schema_extra={"example": 19.9449799},
    )
    lat: float = Field(
        ge=-90.0,
        le=90.0,
        description="Szerokość geograficzna punktu kliknięcia w WGS84 (EPSG:4326).",
        json_schema_extra={"example": 50.0646501},
    )


class AddressAnalyzeRequest(BaseModel):
    method: Literal["address"] = Field(
        description="Metoda wejścia oparta o jawnie wybraną sugestię adresową.",
        json_schema_extra={"example": "address"},
    )
    query: str = Field(
        min_length=3,
        max_length=500,
        description=(
            "Etykieta wybranego adresu (do kontekstu i audytu). NIE jest ponownie "
            "geokodowana — analiza używa jawnie wybranych współrzędnych."
        ),
        json_schema_extra={"example": "Marki, Generała Władysława Andersa 1"},
    )
    # Współrzędne DOKŁADNIE wybranej przez użytkownika sugestii (WGS84). Wymagane,
    # aby analiza nigdy nie wybierała po cichu pierwszego wyniku geokodowania.
    selected_lon: float = Field(
        ge=-180.0,
        le=180.0,
        description="Długość geograficzna wybranej sugestii w WGS84 (EPSG:4326).",
        json_schema_extra={"example": 21.105},
    )
    selected_lat: float = Field(
        ge=-90.0,
        le=90.0,
        description="Szerokość geograficzna wybranej sugestii w WGS84 (EPSG:4326).",
        json_schema_extra={"example": 52.32},
    )
    selected_result_id: str | None = Field(
        default=None,
        max_length=200,
        description="Opcjonalny, stabilny identyfikator wybranej sugestii z wyszukiwarki.",
        json_schema_extra={"example": "hash:9f2c1a0b4d5e6f70"},
    )


class ParcelIdAnalyzeRequest(BaseModel):
    method: Literal["parcel_id"] = Field(
        description="Metoda wejścia oparta o identyfikator działki ewidencyjnej.",
        json_schema_extra={"example": "parcel_id"},
    )
    parcel_identifier: str = Field(
        min_length=5,
        max_length=50,
        description=(
            "Identyfikator działki ewidencyjnej w formacie ULDK, "
            "np. 122101_1.0001.1234/2."
        ),
        json_schema_extra={"example": "122101_1.0001.1234/2"},
    )


AnalyzeRequest = Annotated[
    Union[MapAnalyzeRequest, AddressAnalyzeRequest, ParcelIdAnalyzeRequest],
    Field(discriminator="method"),
]


class AnalyzeResumeRequest(BaseModel):
    analysis_id: int = Field(
        gt=0,
        description=(
            "Identyfikator analizy oczekującej na ręczne podanie symbolu strefy "
            "(status='waiting_for_zone_symbol')."
        ),
        json_schema_extra={"example": 123},
    )
    zone_symbol: str = Field(
        min_length=1,
        max_length=ZONE_SYMBOL_MAX_RAW_LENGTH,
        description=(
            "Symbol strefy MPZP odczytany przez użytkownika z mapy rastrowej. "
            "Serwer sprowadza go do formy kanonicznej (NFKC, przycięcie, zwinięcie "
            f"białych znaków) i dopiero ją waliduje: do {ZONE_SYMBOL_MAX_LENGTH} znaków, "
            "litery, cyfry, '. _ / - , + ( )' i pojedyncze spacje wewnętrzne."
        ),
        json_schema_extra={"example": "146 MN"},
    )


class GeometryMetrics(BaseModel):
    area_sqm: float = Field(
        ge=0.0,
        description="Pole powierzchni działki w metrach kwadratowych.",
        json_schema_extra={"example": 1250.5},
    )
    area_ha: float = Field(
        ge=0.0,
        description="Pole powierzchni działki w hektarach.",
        json_schema_extra={"example": 0.12505},
    )
    perimeter_m: float = Field(
        ge=0.0,
        description="Obwód działki w metrach.",
        json_schema_extra={"example": 146.2},
    )
    is_valid: bool = Field(
        description="Czy geometria przeszła walidację Shapely.",
        json_schema_extra={"example": True},
    )
    geometry_repaired: bool = Field(
        description="Czy geometria była naprawiana przed obliczeniami.",
        json_schema_extra={"example": False},
    )


class ParcelGeometryResponse(BaseModel):
    parcel_identifier: str = Field(
        description="Identyfikator działki ewidencyjnej.",
        json_schema_extra={"example": "122101_1.0001.1234/2"},
    )
    geometry_geojson: dict[str, Any] = Field(
        description="Geometria działki w WGS84 (EPSG:4326) jako GeoJSON dla frontendu.",
        json_schema_extra={
            "example": {
                "type": "MultiPolygon",
                "coordinates": [[[[19.94, 50.06], [19.95, 50.06], [19.94, 50.06]]]],
            }
        },
    )
    metrics: GeometryMetrics = Field(
        description="Metryki geometryczne obliczone w EPSG:2180.",
        json_schema_extra={
            "example": {
                "area_sqm": 1250.5,
                "area_ha": 0.12505,
                "perimeter_m": 146.2,
                "is_valid": True,
                "geometry_repaired": False,
            }
        },
    )
    source: SourceMetadata = Field(
        description="Metadane źródła geometrii działki.",
        json_schema_extra={
            "example": {
                "source_name": "ULDK",
                "source_url": "https://uldk.gugik.gov.pl/",
                "fetched_at": "2026-07-02T12:00:00Z",
                "confidence": 0.95,
                "manual_review_required": False,
            }
        },
    )
    buildable_area_geojson: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Obszar zabudowy po technicznym odsunięciu od granicy działki, "
            "jako GeoJSON Feature w WGS84 (EPSG:4326). To techniczne "
            "przybliżenie (is_technical_approximation=True w properties), "
            "nie ostateczna linia zabudowy z MPZP i nie geometria netto po "
            "odjęciu stref ochronnych sieci. None gdy odsunięcie zredukowało "
            "obszar do zera."
        ),
        json_schema_extra={
            "example": {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[19.941, 50.061], [19.949, 50.061], [19.941, 50.061]]],
                },
                "properties": {
                    "layer": "buildable_area",
                    "setback_m": 4.0,
                    "is_technical_approximation": True,
                },
            }
        },
    )


MpzpAssignmentMethod = Literal[
    "vector_intersection", "document_candidate", "manual_user_input", "legacy"
]
# 2.2 (PV3-04): symbole stref ze spacjami i przecinkami są przyjmowane i dopasowywane
# w tekście tolerancyjnie na odstępy, więc wynik parsera dla tego samego dokumentu
# może się różnić od zapisanego przed zmianą; ręczny dowód niesie też oryginał wpisu.
# 2.3 (PV3-07): parametry parsera pochodzą z silnika leksykonu (``mpzp-parser/3.0-det``): inne
# sformułowania są rozpoznawane, a wartości niosą strategię i flagi przeróbek zapisu; wynik
# zapisany parserem ``2.0`` nie jest serwowany z cache jako trafienie.
# 2.5 (PV3-09): ``confidence`` jest skalibrowanym prawdopodobieństwem poprawności z cech dowodu
# (nie iloczynem stałych mnożników), a ``manual_review_required`` odpowiada zmierzonemu progowi;
# evidence niesie ``confidence_band`` i ``confidence_calibration``. Zapisy 2.4 i starsze mają
# pewność z poprzednich reguł (bez pasma) i nie są serwowane z cache jako trafienie.
# 2.4 (PV3-08): evidence parametru niesie ``conditions`` i ``value_kind``; wartości warunkowe
# (inna wysokość dla dachu płaskiego) nie są już sprzecznością, a płaskie pola strefy są ``null``,
# gdy nie ma jednej wartości bezwarunkowej. Snapshot 2.3 i starsze czytają się jako wartości
# bezwarunkowe; ich wyniki nie są serwowane z cache.
MPZP_RESULT_SCHEMA_VERSION = "2.5"


class ManualZoneSelection(BaseModel):
    """Decyzja użytkownika o symbolu strefy bez wektora (BK-204).

    Zapis jest częścią snapshotu strefy, więc raport i ponowny odczyt pokazują,
    z jakich kandydatów i z której dokładnie wersji dokumentu wybrano symbol.
    Nie zawiera udziału powierzchniowego — ręczny symbol go nie ustala.
    """

    entered_symbol: str = Field(description="Symbol w formie kanonicznej po walidacji.")
    entered_symbol_raw: str | None = Field(
        default=None,
        description=(
            "Symbol dokładnie tak, jak wpisał go użytkownik (przed normalizacją); "
            "dowód wprowadzenia. Brak w zapisach sprzed PV3-04."
        ),
    )
    plan_id: str | None = Field(default=None, description="ID planu z discovery.")
    candidate_zone_symbols: list[str] = Field(
        default_factory=list,
        description="Kandydaci symboli pokazani użytkownikowi przed wyborem.",
    )
    symbol_in_candidates: bool = Field(
        description="Czy wpisany symbol był jednym z pokazanych kandydatów."
    )
    document_url: str | None = Field(
        default=None, description="Adres dokumentu zapisany przy wstrzymaniu analizy."
    )
    document_sha256: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        description="SHA-256 dokumentu przypiętego przy wstrzymaniu analizy.",
    )
    document_version_id: int | None = None
    document_fetched_at: datetime | None = None
    document_pinned: bool = Field(
        description=(
            "Czy parametry odczytano z dokumentu przypiętego przy wstrzymaniu. "
            "False — dokument nie był dostępny i parametry pozostają nieustalone."
        )
    )
    selected_at: datetime


MpzpConditionKind = Literal["building_type", "roof_type", "subzone", "location", "other"]
MpzpValueKind = Literal["unconditional", "conditional", "conflict"]


class MpzpValueCondition(BaseModel):
    """Warunek wartości parametru (PV3-08): rodzaj, nazwa do wyświetlenia i dosłowny cytat."""

    kind: MpzpConditionKind = Field(
        description="building_type, roof_type, subzone, location albo other."
    )
    label: str = Field(description="Znormalizowana, krótka nazwa warunku (np. „dach płaski”).")
    quote: str = Field(
        description=(
            "Dosłowny cytat z uchwały wskazujący warunek. Może leżeć w nagłówku nadrzędnej pozycji "
            "listy, więc nie musi należeć do ``evidence_text``."
        )
    )


class MpzpParameterEvidence(BaseModel):
    """Jedna kandydatura parametru uchwały z pełnym, cytowalnym dowodem (BK-203).

    Sprzeczne kandydatury tego samego parametru w strefie mają wspólne
    ``conflict_group_id`` i nie są automatycznie rozstrzygane. Od PV3-08 kandydatura niesie też
    ``conditions`` i ``value_kind``: różne wartości z różnymi warunkami (inna wysokość dla dachu
    płaskiego) są ``conditional``, a nie sprzeczne; sprzeczność (``conflict``) oznacza kilka
    wartości tej samej przesłanki. Zapisy sprzed PV3-08 nie mają warunków i czytają się jako
    ``unconditional`` (``conflict``, gdy mają ``conflict_group_id``).
    """

    name: str = Field(description="Znormalizowana nazwa parametru parsera.")
    normalized_value: float | str | None = None
    raw_value: str | None = Field(default=None, description="Wartość dosłownie z dokumentu.")
    unit: str | None = None
    evidence_text: str | None = Field(default=None, description="Fragment uchwały będący dowodem.")
    page_number: int | None = None
    segment_id: str | None = Field(default=None, description="Segment dokumentu (paragraf/tabela).")
    legal_unit_id: int | None = Field(default=None, description="Jednostka redakcyjna uchwały w bazie.")
    document_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    document_version_id: int | None = None
    parser_version: str | None = None
    extraction_method: str | None = Field(
        default=None,
        description="pdf_text, html albo ocr; llm_verified — wartość z modelu po bramkach deterministycznych (PV3-12).",
    )
    confidence: float = Field(ge=0.0, le=1.0)
    conflict_group_id: str | None = None
    manual_review_required: bool = False
    conditions: list[MpzpValueCondition] = Field(
        default_factory=list,
        description="Warunki wartości; pusta lista = wartość bezwarunkowa (także w zapisach sprzed PV3-08).",
    )
    value_kind: MpzpValueKind | None = Field(
        default=None,
        description=(
            "unconditional, conditional albo conflict. Brak w zapisie sprzed PV3-08 jest uzupełniany: "
            "conflict przy conflict_group_id, w przeciwnym razie conditional przy warunkach, inaczej "
            "unconditional."
        ),
    )
    confidence_band: Literal["low", "medium", "high"] | None = Field(
        default=None,
        description=(
            "Pasmo pewności z artefaktu kalibracji (PV3-09). ``confidence`` jest prawdopodobieństwem "
            "poprawności wartości wyprowadzonym z cech dowodu, nie stałym mnożnikiem; ``None`` — wartość "
            "bez skalibrowanej pewności (zapis sprzed PV3-09 albo zapis opisowy)."
        ),
    )
    confidence_calibration: str | None = Field(
        default=None, description="Identyfikator artefaktu kalibracji pewności (wersja + skrót danych)."
    )
    extraction_strategy: str | None = Field(
        default=None, description="Strategia dopasowania silnika ilości (PV3-07), np. comparative."
    )
    normalization_flags: list[str] = Field(
        default_factory=list,
        description="Przeróbki zapisu przy normalizacji (np. ratio_to_percent, degree_artifact).",
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

    @model_validator(mode="after")
    def _derive_value_kind(self) -> Self:
        if self.value_kind is None:
            if self.conflict_group_id is not None:
                self.value_kind = "conflict"
            elif self.conditions:
                self.value_kind = "conditional"
            else:
                self.value_kind = "unconditional"
        return self


class MpzpZoneResult(BaseModel):
    zone_symbol: str = Field(
        description="Symbol strefy MPZP przecinającej działkę.",
        json_schema_extra={"example": "MN"},
    )
    primary_use: str | None = Field(
        default=None,
        description="Podstawowe przeznaczenie terenu w strefie MPZP.",
        json_schema_extra={"example": "zabudowa mieszkaniowa jednorodzinna"},
    )
    supplementary_use: str | None = Field(
        default=None,
        description="Uzupełniające przeznaczenie terenu w strefie MPZP.",
        json_schema_extra={"example": "usługi nieuciążliwe"},
    )
    max_building_height_m: float | None = Field(
        default=None,
        ge=0.0,
        description="Maksymalna wysokość zabudowy w metrach.",
        json_schema_extra={"example": 9.0},
    )
    max_floors: int | None = Field(
        default=None,
        ge=0,
        description="Maksymalna liczba kondygnacji.",
        json_schema_extra={"example": 2},
    )
    min_biologically_active_pct: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Minimalny udział powierzchni biologicznie czynnej w procentach.",
        json_schema_extra={"example": 40.0},
    )
    max_floor_area_ratio: float | None = Field(
        default=None,
        ge=0.0,
        description="Maksymalny wskaźnik intensywności zabudowy.",
        json_schema_extra={"example": 0.8},
    )
    min_floor_area_ratio: float | None = Field(
        default=None,
        ge=0.0,
        description="Minimalny wskaźnik intensywności zabudowy.",
        json_schema_extra={"example": 0.01},
    )
    max_building_coverage_pct: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Maksymalna powierzchnia zabudowy w procentach powierzchni działki.",
        json_schema_extra={"example": 30.0},
    )
    intersection_area_sqm: float | None = Field(
        default=None,
        ge=0.0,
        description=(
            "Pole przecięcia działki ze strefą MPZP w metrach kwadratowych. "
            "None — udział nieustalony (brak wektorowej granicy strefy, np. symbol "
            "podany ręcznie); nie oznacza 0 ani całej działki."
        ),
        json_schema_extra={"example": 830.0},
    )
    intersection_pct: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description=(
            "Udział powierzchni działki w tej strefie MPZP; None — udział nieustalony."
        ),
        json_schema_extra={"example": 66.4},
    )
    is_dominant: bool = Field(
        default=False,
        description=(
            "Pole pomocnicze: strefa o największym dodatnim, USTALONYM udziale. "
            "Nie zastępuje pełnej listy stref; bez udziału zawsze False."
        ),
        json_schema_extra={"example": True},
    )
    zone_id: str | None = Field(
        default=None, description="Stabilne ID wydzielenia z wersjonowanego wektora."
    )
    act_identifier: str | None = None
    act_version: str | None = Field(
        default=None, description="Hash snapshotu wersji aktu użytej w analizie."
    )
    act_version_id: int | None = None
    data_release_id: int | None = None
    document_url: str | None = None
    touches_boundary: bool = Field(
        default=False,
        description="Wydzielenie tylko styka się z działką (pole ≤ 1e-6 m²).",
    )
    assignment_method: MpzpAssignmentMethod = Field(
        default="document_candidate",
        description=(
            "vector_intersection — przecięcie wektora; document_candidate — "
            "kandydat z discovery/dokumentu bez wektora; manual_user_input — "
            "symbol podany ręcznie; legacy — snapshot sprzed BK-202."
        ),
    )
    intersection_geojson: dict[str, Any] | None = Field(
        default=None, description="Geometria przecięcia w WGS84 jako GeoJSON Feature."
    )
    parameters: list[MpzpParameterEvidence] = Field(
        default_factory=list,
        description="Wszystkie kandydatury parametrów z evidence, także sprzeczne.",
    )
    manual_review_required: bool = False
    manual_selection: ManualZoneSelection | None = Field(
        default=None,
        description=(
            "Zapis decyzji użytkownika w trybie ręcznym (BK-204): wpisany symbol, "
            "plan, kandydaci i przypięta wersja dokumentu. Obecny wyłącznie dla "
            "assignment_method=manual_user_input."
        ),
    )
    intersection_wkt: str | None = Field(
        default=None,
        exclude=True,
        description=(
            "Wewnętrzna geometria przecięcia w EPSG:2180 do obliczeń par MPZP–POG; "
            "nie jest serializowana do API ani snapshotu."
        ),
    )
    source: SourceMetadata = Field(
        description="Metadane źródła danych MPZP.",
        json_schema_extra={
            "example": {
                "source_name": "KIMPZP",
                "source_url": "https://example.local/mpzp",
                "fetched_at": "2026-07-02T12:00:00Z",
                "confidence": 0.8,
                "manual_review_required": True,
            }
        },
    )


class ManualZoneSourceDocument(BaseModel):
    """Dokument przypięty przy wstrzymaniu analizy — dokładnie ten, który przeczyta resume."""

    requested_url: str | None = None
    requested_url_verified: bool = Field(
        default=False,
        description="Czy adres źródłowy jest zweryfikowanym HTTPS (tylko wtedy klikalny).",
    )
    media_type: str
    filename: str | None = None
    sha256: str = Field(min_length=64, max_length=64)
    size_bytes: int = Field(ge=0)
    fetched_at: datetime | None = None
    document_version_id: int | None = None
    preview_path: str = Field(
        description=(
            "Względna ścieżka API serwująca przypiętą kopię dokumentu; nie jest "
            "adresem zewnętrznym."
        )
    )

    @model_validator(mode="after")
    def verify_link(self) -> Self:
        self.requested_url_verified = is_verified_https_url(self.requested_url)
        return self


class ManualZoneContext(BaseModel):
    """Materiał, który użytkownik widzi przed podaniem symbolu strefy (BK-204)."""

    plan_id: str | None = None
    candidate_zone_symbols: list[str] = Field(default_factory=list)
    document_status: Literal["pinned", "unavailable", "not_provided"] = Field(
        description=(
            "pinned — dokument przypięty przy wstrzymaniu; unavailable — nie udało "
            "się go pobrać; not_provided — discovery nie wskazało dokumentu."
        )
    )
    document: ManualZoneSourceDocument | None = None
    raster_preview_source_key: Literal["mpzp"] = Field(
        default="mpzp",
        description="Klucz podglądu WMS (proxy /api/v1/map/tiles) z obrazem planu.",
    )
    symbol_max_length: int = ZONE_SYMBOL_MAX_LENGTH
    symbol_allowed_pattern: str = Field(
        description=(
            "Wyrażenie regularne formy kanonicznej symbolu (walidacja UI = API); "
            "stosuje się je po NFKC, przycięciu i zwinięciu białych znaków."
        )
    )
    symbol_rules_version: str = Field(
        default=ZONE_SYMBOL_RULES_VERSION,
        description="Wersja reguł symbolu (kanoniczna forma i wzorzec).",
    )
    notice: str = Field(
        description="Ograniczenia trybu ręcznego pokazywane przed formularzem."
    )


CompatibilityStatus = Literal[
    "compatible", "incompatible", "uncertain", "not_applicable", "unknown"
]
COMPATIBILITY_ASSESSMENT_SCHEMA_VERSION = "1.0"


class CompatibilitySource(BaseModel):
    """Źródło, na którym opiera się ocena relacji MPZP–POG."""

    kind: Literal["rule_set", "mpzp", "pog"]
    label: str
    reference: str | None = None
    version: str | None = None
    as_of: date | None = None


class CompatibilityZonePair(BaseModel):
    """Para strefa MPZP × strefa POG z jawną regułą i uzasadnieniem (BK-205).

    Para rozstrzygnięta (``compatible``/``incompatible``) zawsze ma ``rule_id``,
    ``rule_version``, ``source`` i ``as_of``. Para bez potwierdzonego przestrzennie
    styku stref nie jest rozstrzygana — pozostaje ``uncertain``/``unknown``.
    """

    mpzp_zone_symbol: str
    mpzp_zone_id: str | None = None
    mpzp_assignment_method: MpzpAssignmentMethod
    mpzp_function: str | None = Field(
        default=None, description="Znormalizowana funkcja MPZP; None — nieustalona."
    )
    pog_zone_id: str
    pog_zone_symbol: str | None = None
    pog_zone_type: str
    spatially_identified: bool = Field(
        description=(
            "True — obie strefy mają geometrię EPSG:2180, a ich przecięcie w "
            "obrębie działki ma dodatnie pole."
        )
    )
    overlap_area_sqm: float | None = Field(default=None, ge=0.0)
    overlap_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    status: CompatibilityStatus
    rule_result: Literal["compatible", "incompatible", "uncertain"] | None = Field(
        default=None,
        description="Wynik samej reguły tabeli, przed uwzględnieniem niepewności przestrzennej.",
    )
    rule_id: str | None = None
    rule_version: str | None = None
    source: str | None = None
    as_of: date | None = None
    rationale: str
    manual_review_required: bool = True

    @model_validator(mode="after")
    def resolved_pair_has_rule(self) -> Self:
        if self.status in {"compatible", "incompatible"}:
            missing = [
                name
                for name in ("rule_id", "rule_version", "source", "as_of")
                if getattr(self, name) is None
            ]
            if missing or not self.spatially_identified:
                raise ValueError(
                    "Rozstrzygnięta para MPZP–POG wymaga reguły (rule_id, "
                    "rule_version, source, as_of) i przestrzennej identyfikacji."
                )
        return self


class LegacyCompatibilityEvidence(BaseModel):
    """Historyczne stwierdzenie sprzed BK-205 — dowód, nie pełna ocena."""

    origin: str = Field(description="Skąd pochodzi zapis, np. pog_data.conflict_with_mpzp.")
    conflict_with_mpzp: bool | None = None
    result: str | None = None
    reasoning: str | None = None
    confidence: float | None = None


class CompatibilityAssessment(BaseModel):
    """Informacyjna ocena relacji MPZP–POG (BK-205), nie opinia prawna.

    Status całości wynika z jawnej reguły agregacji par (najsłabsze ogniwo),
    nigdy ze średniej parametrów różnych stref. ``not_applicable`` opisuje brak
    aktu, który mógłby ustanawiać obowiązek (projekt/procedura, akt nieaktualny,
    potwierdzony brak POG); ``unknown`` — brak danych albo reguły.
    """

    schema_version: str = Field(default=COMPATIBILITY_ASSESSMENT_SCHEMA_VERSION)
    status: CompatibilityStatus
    reason_code: str = Field(description="Stabilny kod ścieżki decyzji.")
    as_of: date | None = Field(
        default=None, description="Data stanu prawnego, do którego odnosi się ocena."
    )
    rule_id: str | None = Field(default=None, description="ID zestawu reguł.")
    rule_version: str | None = None
    aggregation: str = Field(description="Jawny opis reguły agregacji par.")
    sources: list[CompatibilitySource] = Field(default_factory=list)
    rationale: str
    manual_review_required: bool = True
    zone_pairs: list[CompatibilityZonePair] = Field(default_factory=list)
    informational_notice: str
    legacy_evidence: LegacyCompatibilityEvidence | None = None

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        if self.legacy_evidence is not None and self.status != "unknown":
            raise ValueError(
                "Zapis legacy bez danych reguły nie może udawać pełnej oceny zgodności."
            )
        if self.status in {"compatible", "incompatible"}:
            if not self.zone_pairs or self.rule_id is None or self.rule_version is None:
                raise ValueError(
                    "Rozstrzygnięta ocena wymaga zestawu reguł i co najmniej jednej pary."
                )
            if not any(pair.status == self.status for pair in self.zone_pairs):
                raise ValueError("Status oceny musi wynikać z co najmniej jednej pary.")
        return self


class PogActResult(BaseModel):
    """Akt i jego dokładna wersja z łańcuchem provenance (BK-107).

    Wszystkie pola są zapisywane w snapshotcie analizy w chwili wykonania —
    odczyt starej analizy nie pobiera bieżącej wersji z katalogu.
    """

    id: str
    version: str | None = None
    title: str | None = None
    resolution_number: str | None = None
    resolution_date: date | None = None
    act_identifier: str | None = Field(
        default=None, description="Stabilny idIIP aktu (przestrzeń nazw/lokalnyId)."
    )
    act_version: str | None = Field(default=None, description="wersjaId idIIP aktu.")
    publication_id: str | None = Field(
        default=None, description="gml:identifier opublikowanej wersji aktu w RU."
    )
    version_started_at: datetime | None = Field(
        default=None, description="poczatekWersjiObiektu z APP."
    )
    publication_date: date | None = Field(
        default=None, description="Data publikacji zbioru danych aktu z metadanych CSW."
    )
    valid_from: date | None = Field(default=None, description="obowiazujeOd z APP.")
    valid_to: date | None = Field(default=None, description="obowiazujeDo z APP.")
    gml_url: str | None = Field(
        default=None, description="Oficjalny URL GML dokładnej wersji aktu (WFS RU)."
    )
    gml_url_verified: bool = False
    card_url: str | None = Field(
        default=None, description="Oficjalny URL karty metadanych aktu (CSW RU)."
    )
    card_url_verified: bool = False
    data_release_id: int | None = None
    release_label: str | None = None
    artifact_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    fetched_at: datetime | None = None
    metadata: CatalogMetadataSource | None = None
    formal_documents: list[FormalDocumentSource] = Field(default_factory=list)

    @model_validator(mode="after")
    def verify_links(self) -> Self:
        self.gml_url_verified = is_verified_https_url(self.gml_url)
        self.card_url_verified = is_verified_https_url(self.card_url)
        return self


class PogProfileResult(BaseModel):
    code: str
    label: str | None = None
    dictionary_source: str


class PogZoneResult(BaseModel):
    id: str
    symbol: str | None = None
    type: str
    label: str | None = None
    area_sqm: float = Field(ge=0.0)
    area_pct: float = Field(ge=0.0, le=100.0)
    max_overground_floor_area_ratio: float | None = Field(default=None, ge=0.0)
    max_building_height_m: float | None = Field(default=None, ge=0.0)
    max_building_coverage_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    min_biologically_active_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    primary_profile: list[PogProfileResult] = Field(default_factory=list)
    additional_profiles: list[PogProfileResult] = Field(default_factory=list)
    source: SourceMetadata | None = None
    feature_version: str | None = Field(default=None, description="wersjaId obiektu strefy.")
    gml_url: str | None = Field(
        default=None, description="Oficjalny URL GML obiektu strefy — źródło parametrów."
    )
    gml_url_verified: bool = False
    geometry_geojson: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Przecięcie strefy z działką jako GeoJSON EPSG:4326 — wyłącznie do "
            "prezentacji (mapa raportu, wykres udziałów); pola liczone są w 2180."
        ),
    )
    intersection_wkt: str | None = Field(
        default=None,
        exclude=True,
        description=(
            "Wewnętrzne przecięcie strefy z działką w EPSG:2180 do oceny par "
            "MPZP–POG; nie jest serializowane."
        ),
    )

    @model_validator(mode="after")
    def verify_link(self) -> Self:
        self.gml_url_verified = is_verified_https_url(self.gml_url)
        return self


class PogAreaResult(BaseModel):
    id: str
    symbol: str | None = None
    label: str | None = None
    area_sqm: float = Field(ge=0.0)
    area_pct: float = Field(ge=0.0, le=100.0)
    touches_boundary: bool = False
    source: SourceMetadata | None = None
    feature_version: str | None = None
    gml_url: str | None = None
    gml_url_verified: bool = False
    geometry_geojson: dict[str, Any] | None = Field(
        default=None,
        description="Przecięcie obszaru z działką jako GeoJSON EPSG:4326 (prezentacja).",
    )

    @model_validator(mode="after")
    def verify_link(self) -> Self:
        self.gml_url_verified = is_verified_https_url(self.gml_url)
        return self


class PogPresentationStyle(BaseModel):
    """Zamrożona wersja stylu POG użyta przy analizie (BK-403).

    Snapshot przechowuje wersję i SHA-256 artefaktu ``shared/pog-presentation.json``
    oraz style potrzebne raportowi, aby stary raport rysował się zapisanym
    stylem, a nie bieżącą paletą.
    """

    style_version: str
    style_sha256: str
    zones: dict[str, dict[str, str]]
    unknown_zone: dict[str, Any]
    null_style: dict[str, Any]
    overlays: dict[str, dict[str, Any]]


class PogStatusEvidence(BaseModel):
    """Wskazanie źródła, które potwierdziło status prawny albo pokrycie."""

    source_name: str
    official: bool = Field(
        description="Czy źródło jest właściwym źródłem urzędowym (RU/organ gminy)."
    )
    reference: str | None = Field(
        default=None,
        description="URL, identyfikator wydania albo sygnatura urzędowego potwierdzenia.",
    )
    source_id: str | None = None
    raw_value: str | None = Field(
        default=None, description="Surowy urzędowy kod statusu, np. INSPIRE legalForce."
    )
    confirmed_at: datetime | None = None


class PogResult(BaseModel):
    """Wynik POG v2 z rozdzielonym statusem prawnym i pokryciem (BK-106).

    ``legal_status`` opisuje akt, ``coverage_status`` — dostępność danych
    przestrzennych dla działki, a ``data_availability`` — stan operacyjny źródła
    w chwili analizy. Żadne z tych pól nie zastępuje pozostałych.
    """

    schema_version: str = Field(default=POG_RESULT_SCHEMA_VERSION)
    legal_status: LegalStatus = Field(
        default="unknown",
        description="Status prawny aktu z urzędowego źródła: binding|project|in_progress|superseded|unknown.",
    )
    coverage_status: CoverageStatus = Field(
        default="unknown",
        description=(
            "Pokrycie danymi przestrzennymi: available|partial|act_without_spatial_data|"
            "no_act_confirmed|unknown. Brak geometrii nie oznacza braku aktu."
        ),
    )
    data_availability: DataAvailability = Field(
        default="unavailable",
        description="Stan operacyjny źródła: current|stale|unavailable; nie jest statusem prawnym.",
    )
    status_confirmed_at: datetime | None = Field(
        default=None,
        description="Chwila potwierdzenia statusu w źródle; dla stale — data ostatniego potwierdzenia.",
    )
    legal_status_evidence: PogStatusEvidence | None = None
    coverage_evidence: PogStatusEvidence | None = None
    act: PogActResult | None = None
    zones: list[PogZoneResult] = Field(default_factory=list)
    dominant_zone_id: str | None = None
    ouz: list[PogAreaResult] = Field(default_factory=list)
    downtown_areas: list[PogAreaResult] = Field(default_factory=list)
    social_infrastructure_standard_areas: list[PogAreaResult] = Field(default_factory=list)
    status: str = Field(
        default="unknown",
        description=(
            "Przestarzałe lustro legal_status zachowane dla klientów POG v1; "
            "zawsze równe legal_status."
        ),
        json_schema_extra={"example": "binding"},
    )
    planning_zone: str | None = Field(
        default=None,
        description="Strefa planistyczna POG, jeżeli została ustalona.",
        json_schema_extra={"example": "SJ"},
    )
    zone_type: str | None = Field(
        default=None,
        description="Znormalizowany typ dominującej strefy POG.",
        json_schema_extra={"example": "SJ"},
    )
    in_ouz: bool = Field(
        default=False,
        description="Czy działka ma istotne powierzchniowe przecięcie z OUZ.",
    )
    area_ratio: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Udział dominującej strefy POG w powierzchni działki, w skali 0-1.",
    )
    in_downtown_area: bool = Field(
        default=False,
        description="Czy działka ma powierzchniowe przecięcie z obszarem śródmiejskim POG.",
    )
    uchwala_nr: str | None = Field(
        default=None,
        description="Numer uchwały POG, jeżeli jest dostępny.",
    )
    uchwala_date: date | None = Field(
        default=None,
        description="Data uchwalenia POG, jeżeli jest dostępna.",
    )
    manual_review_required: bool = Field(
        default=False,
        description="Czy wynik POG wymaga ręcznej weryfikacji.",
    )
    compatibility_assessment: CompatibilityAssessment | None = Field(
        default=None,
        description=(
            "Informacyjna ocena relacji MPZP–POG z datą stanu prawnego, regułami, "
            "źródłami, uzasadnieniem i parami stref (BK-205). Zastępuje "
            "przestarzałe pole conflict_with_mpzp. Nie przesądza o prawnej "
            "możliwości zabudowy."
        ),
    )
    raw_attributes: dict[str, Any] | None = Field(
        default=None,
        description="Surowe atrybuty APP/GML lub WMS zachowane dla audytu parsera.",
    )
    ouz_intersection_area_sqm: float | None = Field(
        default=None,
        ge=0.0,
        description="Pole przecięcia działki z OUZ w metrach kwadratowych.",
        json_schema_extra={"example": 420.0},
    )
    ouz_intersection_pct: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Udział powierzchni działki przecinającej OUZ.",
        json_schema_extra={"example": 33.6},
    )
    touches_ouz_boundary: bool = Field(
        description="Czy działka jedynie dotyka granicy OUZ.",
        json_schema_extra={"example": False},
    )
    presentation_style: PogPresentationStyle | None = Field(
        default=None,
        description=(
            "Wersja i zamrożony podzbiór stylu POG z chwili analizy; brak oznacza "
            "snapshot sprzed BK-403 (raport użyje bieżącego stylu z adnotacją)."
        ),
    )
    source: SourceMetadata | None = Field(
        default=None,
        description="Metadane źródła POG/OUZ, jeżeli dane były dostępne.",
        json_schema_extra={
            "example": {
                "source_name": "PlanyOgolneGmin",
                "source_url": "https://example.local/pog",
                "fetched_at": "2026-07-02T12:00:00Z",
                "confidence": 0.75,
                "manual_review_required": False,
            }
        },
    )


    @model_validator(mode="before")
    @classmethod
    def upgrade_legacy_statuses(cls, data: Any) -> Any:
        """Wsteczna zgodność: aliasy i wartości sprzed BK-106.

        ``adopted`` staje się ``binding`` tylko z zachowanym potwierdzeniem
        (urzędowy kod w ``raw_attributes`` albo przypięte wydanie z SHA i wersją
        aktu); w przeciwnym razie ``unknown``. ``complete`` → ``available``.
        """
        if not isinstance(data, dict):
            return data
        payload = dict(data)
        legal = payload.get("legal_status")
        legacy = legal if legal is not None else payload.get("status")
        if legal not in LEGAL_STATUS_VALUES and (
            legacy is None
            or legacy in LEGACY_LEGAL_STATUSES
            or legacy in LEGAL_STATUS_ALIASES
            or legacy in LEGAL_STATUS_VALUES
        ):
            evidence = _legacy_status_evidence(payload)
            payload["legal_status"] = upgrade_legacy_legal_status(
                legacy, confirmed=evidence is not None
            )
            if payload["legal_status"] == "binding" and not payload.get(
                "legal_status_evidence"
            ):
                payload["legal_status_evidence"] = evidence
            if "data_availability" not in payload:
                payload["data_availability"] = (
                    "unavailable" if legacy in {None, "unknown"} else "current"
                )
            if "status_confirmed_at" not in payload and evidence is not None:
                payload["status_confirmed_at"] = evidence.get("confirmed_at")
        coverage = payload.get("coverage_status")
        if coverage in COVERAGE_STATUS_ALIASES:
            payload["coverage_status"] = canonical_coverage_status(coverage)
        legacy_conflict = payload.pop("conflict_with_mpzp", None)
        if payload.get("compatibility_assessment") is None:
            payload["compatibility_assessment"] = legacy_compatibility_assessment(
                legacy_conflict, payload.get("raw_attributes")
            )
        return payload

    @model_validator(mode="after")
    def validate_status_contract(self) -> Self:
        # ``status`` jest wyłącznie lustrem kanonicznego statusu prawnego.
        self.status = self.legal_status
        if self.legal_status == "binding" and not (
            self.legal_status_evidence is not None
            and self.legal_status_evidence.official
        ):
            raise ValueError(
                "legal_status=binding wymaga urzędowego potwierdzenia statusu"
            )
        if self.coverage_status == "no_act_confirmed" and not (
            self.coverage_evidence is not None
            and self.coverage_evidence.official
            and self.coverage_evidence.reference
        ):
            raise ValueError(
                "coverage_status=no_act_confirmed wymaga wskazania urzędowego potwierdzenia"
            )
        if self.data_availability == "stale" and self.status_confirmed_at is None:
            raise ValueError("data_availability=stale wymaga daty ostatniego potwierdzenia")
        return self


def legacy_compatibility_assessment(
    conflict_with_mpzp: bool | None,
    raw_attributes: Any,
) -> dict[str, Any] | None:
    """Zamienia boolean sprzed BK-205 na jawne ``legacy`` evidence.

    Stary zapis nie ma identyfikatora ani wersji reguły, pary stref ani daty
    stanu prawnego, więc nie może udawać pełnej oceny: status zawsze ``unknown``,
    a historyczne stwierdzenie zostaje zachowane jako dowód. Brak jakiegokolwiek
    zapisu zwraca ``None`` (ocena nie była wykonywana).
    """
    scenario = raw_attributes.get("scenario") if isinstance(raw_attributes, dict) else None
    compatibility = scenario.get("compatibility") if isinstance(scenario, dict) else None
    if not isinstance(compatibility, dict):
        compatibility = None
    if conflict_with_mpzp is None and compatibility is None:
        return None
    return {
        "status": "unknown",
        "reason_code": "LEGACY_BOOLEAN_ONLY",
        "aggregation": LEGACY_AGGREGATION_NOTE,
        "rationale": (
            "Snapshot sprzed BK-205 zawiera wyłącznie uproszczone stwierdzenie bez "
            "identyfikatora reguły, par stref i daty stanu prawnego; zachowano je "
            "jako historyczny dowód, a nie pełną ocenę."
        ),
        "manual_review_required": True,
        "zone_pairs": [],
        "sources": [],
        "informational_notice": COMPATIBILITY_INFORMATIONAL_NOTICE,
        "legacy_evidence": {
            "origin": (
                "pog_data.conflict_with_mpzp"
                if compatibility is None
                else "raw_attributes.scenario.compatibility"
            ),
            "conflict_with_mpzp": conflict_with_mpzp,
            "result": (compatibility or {}).get("result"),
            "reasoning": (compatibility or {}).get("reasoning"),
            "confidence": (compatibility or {}).get("confidence"),
        },
    }


def _legacy_status_evidence(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Odtwarza potwierdzenie statusu z zachowanego snapshotu sprzed BK-106."""
    raw_attributes = payload.get("raw_attributes") or {}
    app_metadata = (
        raw_attributes.get("app_metadata") if isinstance(raw_attributes, dict) else None
    ) or {}
    raw_status = app_metadata.get("raw_legal_status") if isinstance(app_metadata, dict) else None
    source = payload.get("source") or {}
    if hasattr(source, "model_dump"):
        source = source.model_dump()
    if not isinstance(source, dict):
        source = {}
    act = payload.get("act") or {}
    if hasattr(act, "model_dump"):
        act = act.model_dump()
    if not isinstance(act, dict):
        act = {}
    if raw_status and official_status_code(str(raw_status)) == "binding":
        return {
            "source_name": source.get("source_name") or "POG",
            "source_id": source.get("source_id"),
            "official": True,
            "reference": source.get("source_url"),
            "raw_value": str(raw_status),
            "confirmed_at": source.get("fetched_at"),
        }
    release_id = source.get("data_release_id")
    sha = source.get("artifact_sha256")
    act_version = act.get("version") or source.get("act_version")
    if release_id and sha and act_version:
        return {
            "source_name": source.get("source_name") or "POG",
            "source_id": source.get("source_id"),
            "official": True,
            "reference": f"data_release:{release_id};sha256:{sha};act_version:{act_version}",
            "raw_value": None,
            "confirmed_at": source.get("fetched_at"),
        }
    return None


class InfrastructureResult(BaseModel):
    network_type: str = Field(
        description="Typ sieci uzbrojenia terenu.",
        json_schema_extra={"example": "water"},
    )
    buffer_m: float = Field(
        ge=0.0,
        description="Bufor techniczny wokół sieci w metrach.",
        json_schema_extra={"example": 4.0},
    )
    zone_area_sqm: float = Field(
        default=0.0,
        ge=0.0,
        description=(
            "Powierzchnia technicznego obszaru zabudowy odjęta przez tę "
            "strefę ochronną, w metrach kwadratowych."
        ),
    )
    rule_source: str | None = Field(
        default=None,
        description="Źródło lub podstawa konfiguracji reguły bufora.",
    )
    rule_confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Pewność technicznej reguły bufora w skali 0-1.",
    )
    rule_note: str | None = Field(
        default=None,
        description="Uwagi i ograniczenia zastosowanej reguły bufora.",
    )
    affects_buildable_area: bool = Field(
        default=False,
        description="Czy strefa faktycznie pomniejszyła obszar zabudowy.",
    )
    network_geometry_geojson: dict[str, Any] | None = Field(
        default=None,
        description="Geometria sieci w WGS84 jako GeoJSON Feature.",
    )
    protection_zone_geojson: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Efektywna strefa ochronna pomniejszająca obszar zabudowy, "
            "w WGS84 jako GeoJSON Feature."
        ),
    )
    source: SourceMetadata = Field(
        description="Metadane źródła danych o uzbrojeniu terenu.",
        json_schema_extra={
            "example": {
                "source_name": "KIUT",
                "source_url": "https://example.local/kiut",
                "fetched_at": "2026-07-02T12:00:00Z",
                "confidence": 0.7,
                "manual_review_required": True,
            }
        },
    )


class UtilitiesPreviewResult(BaseModel):
    coverage_status: Literal["covered", "not_covered", "unknown"] = Field(
        description=(
            "Czy powiat publikuje dane GESUT w publicznej warstwie podglądowej "
            "KIUT. Wartość unknown oznacza, że nie udało się tego rozstrzygnąć."
        ),
        json_schema_extra={"example": "covered"},
    )
    county_name: str | None = Field(
        default=None,
        description="Nazwa powiatu zwrócona przez KIUT, jeżeli była dostępna.",
        json_schema_extra={"example": "powiat krakowski"},
    )
    layer_available: bool = Field(
        description=(
            "True wyłącznie wtedy, gdy KIUT potwierdził publikację danych GESUT "
            "dla powiatu. Nie opisuje obecności sieci na samej działce."
        ),
        json_schema_extra={"example": True},
    )
    note: str = Field(
        description=(
            "Nota wyjaśniająca ograniczenia podglądu. Nie zawiera odległości "
            "ani liczby obiektów sieciowych."
        ),
    )
    source: SourceMetadata = Field(
        description="Metadane zapytania do publicznej warstwy WMS KIUT.",
    )

    @model_validator(mode="after")
    def validate_layer_available(self) -> Self:
        if self.layer_available != (self.coverage_status == "covered"):
            raise ValueError(
                "layer_available musi być True wyłącznie dla statusu covered"
            )
        return self


RISK_RESULT_SCHEMA_VERSION = "1.0"
# Tolerancja udziałów w procentach (zaokrąglenia do 1e-4 pp).
_RISK_PCT_TOLERANCE = 0.01

RiskSectionName = Literal["flood", "nature"]
RiskSeverity = Literal["low", "medium", "high"]
RiskSectionStatus = Literal["available", "unavailable", "error", "unknown"]
RiskRelation = Literal["no_match", "boundary_only", "intersection", "unknown"]


class RiskResult(BaseModel):
    """Pojedynczy obiekt ryzyka (strefa powodziowa lub forma ochrony przyrody).

    Pola strukturalne (BK-303) są jedynym nośnikiem danych — ``description``
    to wyłącznie tekst prezentacyjny budowany z tych pól. ``None`` oznacza
    wartość nieznaną (np. snapshot sprzed BK-303), nigdy 0.
    """

    risk_type: str = Field(
        description="Typ ryzyka środowiskowego lub przestrzennego.",
        json_schema_extra={"example": "flood_zone"},
    )
    section: RiskSectionName | None = Field(
        default=None,
        description="Sekcja: flood (ISOK) albo nature (GDOŚ); None dla starego zapisu.",
    )
    feature_id: str | None = Field(
        default=None,
        max_length=500,
        description="Unikalny identyfikator obiektu w źródle (gml:id).",
    )
    severity: RiskSeverity | None = Field(
        default=None, description="Poziom istotności low/medium/high."
    )
    probability_class: str | None = Field(
        default=None,
        description="Klasa prawdopodobieństwa powodzi dosłownie ze źródła.",
        json_schema_extra={"example": "scenariusz Q 1% (raz na 100 lat)"},
    )
    return_period_years: int | None = Field(
        default=None,
        gt=0,
        description=(
            "Okres powtarzalności z atrybutu returnPeriod źródła; nigdy nie "
            "wyliczany z tekstu klasy prawdopodobieństwa."
        ),
    )
    protection_type: str | None = Field(
        default=None, description="Rodzaj formy ochrony przyrody (warstwa GDOŚ)."
    )
    name: str | None = Field(default=None, description="Nazwa formy ochrony.")
    intersection_area_sqm: float | None = Field(
        default=None, ge=0.0, description="Pole przecięcia z działką w m² (EPSG:2180)."
    )
    intersection_pct: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0 + _RISK_PCT_TOLERANCE,
        description="Udział przecięcia w powierzchni działki, %.",
    )
    touches_boundary: bool | None = Field(
        default=None,
        description="True: obiekt wyłącznie styka się z granicą działki (pole ≈ 0).",
    )
    description: str = Field(
        description="Tekst prezentacyjny zbudowany z pól strukturalnych.",
        json_schema_extra={
            "example": "Część działki znajduje się w obszarze zagrożenia powodziowego."
        },
    )
    geometry_geojson: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Geometria przecięcia ryzyka z działką w WGS84 jako GeoJSON Feature."
        ),
    )
    warnings: list[str] = Field(default_factory=list)
    source: SourceMetadata = Field(
        description="Metadane źródła danych o ryzyku.",
        json_schema_extra={
            "example": {
                "source_name": "ISOK",
                "source_url": "https://example.local/isok",
                "fetched_at": "2026-07-02T12:00:00Z",
                "confidence": 0.85,
                "manual_review_required": False,
            }
        },
    )

    @model_validator(mode="after")
    def validate_boundary_consistency(self) -> Self:
        if self.touches_boundary is True and (self.intersection_area_sqm or 0.0) > 1e-3:
            raise ValueError("Styk granicy nie może mieć dodatniego pola przecięcia.")
        return self


class RiskSectionResult(BaseModel):
    """Status i provenance sekcji ryzyka niezależnie od listy obiektów.

    ``features=[]`` z ``status=available`` to potwierdzony brak obiektów;
    ``unavailable``/``error`` to awaria źródła (nigdy „brak ryzyka”), a
    ``unknown`` — zapis sprzed BK-303. Liczniki i pola są ``None``, gdy sekcji
    nie sprawdzono.
    """

    schema_version: str = RISK_RESULT_SCHEMA_VERSION
    section: RiskSectionName
    status: RiskSectionStatus
    reason_code: str | None = Field(
        default=None, description="Kod przyczyny statusu innego niż available."
    )
    relation: RiskRelation = Field(
        description=(
            "no_match — brak obiektów; boundary_only — wyłącznie styk granicy; "
            "intersection — wspólna powierzchnia; unknown — nie sprawdzono."
        )
    )
    feature_count: int | None = Field(default=None, ge=0)
    intersecting_feature_count: int | None = Field(default=None, ge=0)
    boundary_feature_count: int | None = Field(default=None, ge=0)
    union_intersection_area_sqm: float | None = Field(
        default=None,
        ge=0.0,
        description=(
            "Pole sumy mnogościowej przecięć (bez podwójnego liczenia "
            "nakładających się obiektów), m²."
        ),
    )
    union_intersection_pct: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0 + _RISK_PCT_TOLERANCE,
        description="Udział sumy mnogościowej przecięć w działce, % (≤ 100).",
    )
    feature_ids: list[str] = Field(
        default_factory=list, description="Unikalne ID obiektów sekcji."
    )
    source: SourceMetadata | None = Field(
        default=None,
        description="Provenance zapytania — także dla pustego i nieudanego wyniku.",
    )
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        measured = (
            self.feature_count,
            self.intersecting_feature_count,
            self.boundary_feature_count,
            self.union_intersection_area_sqm,
            self.union_intersection_pct,
        )
        if self.status == "available":
            if any(value is None for value in measured):
                raise ValueError("Sekcja available wymaga liczników i pola sumy.")
            if self.source is None:
                raise ValueError("Sekcja available wymaga provenance źródła.")
            expected = (
                "no_match"
                if self.feature_count == 0
                else "intersection"
                if (self.intersecting_feature_count or 0) > 0
                else "boundary_only"
            )
            if self.relation != expected:
                raise ValueError("Relacja sekcji nie odpowiada licznikom obiektów.")
        else:
            if self.relation != "unknown" or any(value is not None for value in measured):
                raise ValueError(
                    "Sekcja niesprawdzona nie może mieć relacji ani pomiarów (null, nie 0)."
                )
            if self.feature_ids:
                raise ValueError("Sekcja niesprawdzona nie może mieć obiektów.")
        return self


TERRAIN_RESULT_SCHEMA_VERSION = "1.0"
# Tolerancja spójności ``height_difference_m == max - min``. Usługa podaje
# wysokości z dokładnością 0,1 m, a różnica jest zaokrąglana do 1 mm, więc
# tolerancja pokrywa wyłącznie szum zmiennoprzecinkowy, nie inną wartość.
HEIGHT_DIFFERENCE_TOLERANCE_M = 0.0015
# Tolerancja sumy udziałów klas nachylenia (zaokrąglenia do 0,01 pp).
_SLOPE_CLASS_SHARE_TOLERANCE_PCT = 0.1

TerrainStatus = Literal["available", "no_coverage", "unavailable", "unknown"]
_TERRAIN_STATUS_DESCRIPTION = (
    "available — pomiar wykonany; no_coverage — źródło potwierdziło brak danych "
    "wysokościowych dla obszaru (to NIE jest płaski teren); unavailable — próba "
    "pomiaru nie powiodła się (timeout, błąd usługi, niepoprawny raster); "
    "unknown — snapshot nie zawiera informacji o NMT (np. zapis sprzed BK-301)."
)


class TerrainSlopeStatistics(BaseModel):
    """Statystyki spadku terenu z pikseli, których środek leży w działce."""

    mean_deg: float = Field(ge=0.0, le=90.0, description="Średni spadek w stopniach.")
    median_deg: float = Field(ge=0.0, le=90.0, description="Mediana spadku w stopniach.")
    p90_deg: float = Field(
        ge=0.0,
        le=90.0,
        description="90. percentyl spadku w stopniach (interpolacja liniowa, R-7).",
    )
    max_deg: float = Field(ge=0.0, le=90.0, description="Maksymalny spadek w stopniach.")
    mean_pct: float = Field(ge=0.0, description="Średni spadek w procentach.")
    median_pct: float = Field(ge=0.0, description="Mediana spadku w procentach.")
    p90_pct: float = Field(ge=0.0, description="90. percentyl spadku w procentach.")
    max_pct: float = Field(ge=0.0, description="Maksymalny spadek w procentach.")


class TerrainSlopeClass(BaseModel):
    """Udział powierzchni w jednej jawnej, wersjonowanej klasie nachylenia."""

    class_id: str = Field(description="Stabilny identyfikator klasy, np. gentle.")
    label: str = Field(description="Etykieta klasy po polsku.")
    min_pct: float = Field(ge=0.0, description="Dolna granica klasy (włącznie), %.")
    max_pct: float | None = Field(
        default=None,
        description="Górna granica klasy (rozłącznie), %; None — klasa otwarta.",
    )
    pixel_count: int = Field(ge=0, description="Liczba pikseli klasy w działce.")
    area_sqm: float = Field(ge=0.0, description="Powierzchnia klasy w m².")
    share_pct: float = Field(
        ge=0.0, le=100.0, description="Udział w zmierzonej powierzchni działki, %."
    )


AspectDirection = Literal["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


class TerrainAspectResult(BaseModel):
    """Ekspozycja stoku jako statystyka kołowa kierunków spadku.

    ``flat`` — udział terenu nachylonego jest zbyt mały, by kierunek miał sens
    (wszystkie wartości kierunku są null, a nie 0°). ``dispersed`` — teren jest
    nachylony, ale kierunki są rozproszone (brak dominującej ekspozycji).
    """

    status: Literal["defined", "dispersed", "flat"]
    mean_azimuth_deg: float | None = Field(
        default=None,
        ge=0.0,
        lt=360.0,
        description="Średni azymut kierunku spadku (0° = północ, zgodnie z zegarem).",
    )
    resultant_length: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Długość wypadkowej jednostkowych wektorów (0 — brak kierunku, 1 — jednolity).",
    )
    dominant_direction: AspectDirection | None = Field(
        default=None, description="Sektor 45° średniego azymutu; tylko dla defined."
    )
    sector_shares_pct: dict[str, float] = Field(
        default_factory=dict,
        description="Udział pikseli nachylonych w 8 sektorach ekspozycji, %.",
    )
    non_flat_share_pct: float = Field(
        ge=0.0, le=100.0, description="Udział pikseli ze spadkiem ≥ progu płaskości, %."
    )
    flat_threshold_pct: float = Field(
        ge=0.0, description="Próg spadku, poniżej którego piksel jest płaski, %."
    )

    @model_validator(mode="after")
    def validate_direction_nulls(self) -> Self:
        if self.status == "flat" and (
            self.mean_azimuth_deg is not None
            or self.resultant_length is not None
            or self.dominant_direction is not None
        ):
            raise ValueError("Ekspozycja płaskiego terenu musi mieć null, nie 0°.")
        if self.status != "flat" and (
            self.mean_azimuth_deg is None or self.resultant_length is None
        ):
            raise ValueError("Ekspozycja terenu nachylonego wymaga azymutu i wypadkowej.")
        if (self.status == "defined") != (self.dominant_direction is not None):
            raise ValueError("Kierunek dominujący jest dozwolony wyłącznie dla defined.")
        return self


class TerrainProfileSample(BaseModel):
    distance_m: float = Field(ge=0.0, description="Odległość od początku linii, m.")
    x: float = Field(description="Easting EPSG:2180.")
    y: float = Field(description="Northing EPSG:2180.")
    height_m: float | None = Field(
        default=None,
        description="Wysokość (interpolacja dwuliniowa); null dla NoData, nigdy 0.",
    )
    inside_parcel: bool = Field(description="Czy próbka leży w obrysie działki.")


class TerrainProfileResult(BaseModel):
    """Deterministyczny profil wysokościowy wzdłuż zapisanej linii."""

    method: str = Field(description="Reguła wyznaczenia linii profilu.")
    crs: Literal["EPSG:2180"] = "EPSG:2180"
    start: tuple[float, float] = Field(description="Początek linii (easting, northing).")
    end: tuple[float, float] = Field(description="Koniec linii (easting, northing).")
    length_m: float = Field(ge=0.0)
    step_m: float = Field(gt=0.0, description="Krok próbkowania, m.")
    interpolation: Literal["bilinear"] = "bilinear"
    samples: list[TerrainProfileSample] = Field(default_factory=list)
    line_geojson: dict[str, Any] | None = Field(
        default=None, description="Linia profilu w WGS84 jako GeoJSON Feature (prezentacja)."
    )


class TerrainRasterMetadata(BaseModel):
    """Parametry rastra źródłowego, na którym wykonano obliczenia."""

    coverage_id: str
    crs: Literal["EPSG:2180"] = "EPSG:2180"
    resolution_m: float = Field(gt=0.0, description="Rozmiar piksela (kwadratowego), m.")
    width_px: int = Field(gt=0)
    height_px: int = Field(gt=0)
    bbox: tuple[float, float, float, float] = Field(
        description="Zakres pobranego rastra (minx, miny, maxx, maxy) EPSG:2180."
    )
    buffer_m: float = Field(ge=0.0, description="Bufor wokół bbox działki (kernel 3×3).")
    size_bytes: int = Field(ge=0, description="Rozmiar pobranego GeoTIFF.")
    nodata_value: float | None = Field(
        default=None, description="NoData zadeklarowane w GeoTIFF (GDAL_NODATA)."
    )
    nodata_policy: str = Field(description="Jawna reguła maskowania NoData.")
    vertical_datum: str | None = Field(default=None, description="Układ wysokości.")
    gdal_version: str | None = Field(
        default=None, description="Wersja GDAL użyta do dekodowania rastra."
    )


class TerrainReliefResult(BaseModel):
    """Pochodne rastra NMT (BK-302): spadek, klasy, ekspozycja i profil."""

    schema_version: str = TERRAIN_RESULT_SCHEMA_VERSION
    algorithm_version: str = Field(description="Wersja algorytmu pochodnych.")
    slope_classes_version: str = Field(description="Wersja tabeli klas nachylenia.")
    status: TerrainStatus = Field(description=_TERRAIN_STATUS_DESCRIPTION)
    reason_code: str | None = Field(
        default=None, description="Kod przyczyny statusu innego niż available."
    )
    resolution_m: float | None = Field(
        default=None, gt=0.0, description="Rozdzielczość danych źródłowych, m."
    )
    parcel_pixel_count: int | None = Field(
        default=None, ge=0, description="Piksele, których środek leży w działce."
    )
    valid_pixel_count: int | None = Field(
        default=None, ge=0, description="Piksele działki z poprawnym oknem 3×3."
    )
    nodata_pixel_count: int | None = Field(
        default=None, ge=0, description="Piksele działki bez danych (maskowane)."
    )
    valid_area_share_pct: float | None = Field(
        default=None, ge=0.0, le=100.0, description="Udział zmierzonych pikseli działki, %."
    )
    min_height_m: float | None = None
    max_height_m: float | None = None
    mean_height_m: float | None = None
    slope: TerrainSlopeStatistics | None = None
    slope_classes: list[TerrainSlopeClass] = Field(default_factory=list)
    aspect: TerrainAspectResult | None = None
    profile: TerrainProfileResult | None = None
    raster: TerrainRasterMetadata | None = None
    source: SourceMetadata | None = Field(
        default=None, description="Provenance zapytania WCS — także dla wyniku pustego."
    )
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_status_consistency(self) -> Self:
        measured = (
            self.slope,
            self.aspect,
            self.min_height_m,
            self.max_height_m,
            self.mean_height_m,
        )
        if self.status == "available":
            if self.slope is None or self.aspect is None or not self.slope_classes:
                raise ValueError("Wynik available wymaga spadku, klas i ekspozycji.")
            if self.resolution_m is None or self.raster is None or self.source is None:
                raise ValueError("Wynik available wymaga rozdzielczości, rastra i źródła.")
            total = sum(item.share_pct for item in self.slope_classes)
            if abs(total - 100.0) > _SLOPE_CLASS_SHARE_TOLERANCE_PCT:
                raise ValueError("Udziały klas nachylenia muszą sumować się do 100%.")
        else:
            if any(value is not None for value in measured) or self.slope_classes:
                raise ValueError(
                    "Wynik bez pomiaru nie może zawierać statystyk (null, nie 0)."
                )
            if self.status in {"no_coverage", "unavailable"} and self.source is None:
                raise ValueError("Pusty wynik musi zachować provenance źródła.")
        return self


class TerrainResult(BaseModel):
    """Rzeźba terenu działki jako pierwszoklasowa sekcja wyniku (BK-301)."""

    schema_version: str = TERRAIN_RESULT_SCHEMA_VERSION
    status: TerrainStatus = Field(description=_TERRAIN_STATUS_DESCRIPTION)
    reason_code: str | None = Field(
        default=None,
        description="Kod przyczyny statusu innego niż available, np. SERVICE_TIMEOUT.",
    )
    min_height_m: float | None = Field(
        default=None,
        description="Najniższa wysokość terenu w obrysie działki, m n.p.m. (może być ujemna).",
        json_schema_extra={"example": 112.3},
    )
    max_height_m: float | None = Field(
        default=None,
        description="Najwyższa wysokość terenu w obrysie działki, m n.p.m.",
        json_schema_extra={"example": 115.7},
    )
    height_difference_m: float | None = Field(
        default=None,
        ge=0.0,
        description="Deniwelacja max − min w metrach; 0 oznacza zmierzony płaski teren.",
        json_schema_extra={"example": 3.4},
    )
    grid_size_m: float | None = Field(
        default=None,
        gt=0.0,
        description="Rozdzielczość siatki próbkowania raportowana przez usługę, m.",
        json_schema_extra={"example": 4.0},
    )
    sampled_points: int | None = Field(
        default=None,
        ge=0,
        description="Liczba punktów siatki próbkowania raportowana przez usługę.",
        json_schema_extra={"example": 676},
    )
    source: SourceMetadata | None = Field(
        default=None,
        description=(
            "Provenance zapytania NMT. Obecne także dla braku pokrycia i "
            "niedostępności; null tylko dla statusu unknown."
        ),
    )
    warnings: list[str] = Field(default_factory=list)
    relief: TerrainReliefResult | None = Field(
        default=None,
        description="Spadek, ekspozycja i profil z rastra NMT (BK-302), jeżeli liczone.",
    )

    @model_validator(mode="after")
    def validate_measurement(self) -> Self:
        heights = (self.min_height_m, self.max_height_m, self.height_difference_m)
        if self.status == "available":
            if any(value is None for value in heights):
                raise ValueError("Wynik available wymaga Hmin, Hmax i deniwelacji.")
            assert self.min_height_m is not None and self.max_height_m is not None
            assert self.height_difference_m is not None
            if self.max_height_m < self.min_height_m:
                raise ValueError("max_height_m nie może być mniejsze od min_height_m.")
            expected = self.max_height_m - self.min_height_m
            if abs(self.height_difference_m - expected) > HEIGHT_DIFFERENCE_TOLERANCE_M:
                raise ValueError("height_difference_m musi równać się max − min.")
        elif any(value is not None for value in heights):
            raise ValueError(
                "Bez pomiaru wysokości muszą być null — brak danych to nie 0 m."
            )
        if self.status != "unknown" and self.source is None:
            raise ValueError("Wynik NMT musi zachować provenance źródła.")
        return self


class AnalyzeResponse(BaseModel):
    analysis_id: int | None = Field(
        default=None,
        description="Identyfikator analizy, None gdy wynik nie został zapisany.",
        json_schema_extra={"example": 123},
    )
    status: str = Field(
        description="Status wykonania analizy.",
        json_schema_extra={"example": "partial"},
    )
    analyzed_at: datetime = Field(
        description="Data i czas wykonania analizy.",
        json_schema_extra={"example": "2026-07-02T12:00:00Z"},
    )
    parcel: ParcelGeometryResponse | None = Field(
        default=None,
        description="Geometria i metryki działki, jeżeli udało się ją ustalić.",
        json_schema_extra={"example": None},
    )
    mpzp_zones: list[MpzpZoneResult] = Field(
        description="Lista stref MPZP przecinających działkę.",
        json_schema_extra={"example": []},
    )
    pog: PogResult | None = Field(
        default=None,
        description="Wynik analizy POG i OUZ, jeżeli dane są dostępne.",
        json_schema_extra={"example": None},
    )
    infrastructure: list[InfrastructureResult] = Field(
        description="Lista wykrytych sieci uzbrojenia terenu.",
        json_schema_extra={"example": []},
    )
    utilities_preview: UtilitiesPreviewResult | None = Field(
        default=None,
        description=(
            "Trzystanowy wynik sprawdzenia, czy powiat publikuje podgląd GESUT "
            "w KIUT. Pole prezentacyjne nie jest analizą odległości do sieci."
        ),
        json_schema_extra={"example": None},
    )
    risks: list[RiskResult] = Field(
        description="Lista ryzyk środowiskowych i przestrzennych.",
        json_schema_extra={"example": []},
    )
    risk_sections: list[RiskSectionResult] = Field(
        default_factory=list,
        description=(
            "Status i provenance sekcji flood (ISOK) i nature (GDOŚ) niezależnie "
            "od listy risks; pusta lista tylko w odpowiedziach sprzed BK-303."
        ),
    )
    terrain: TerrainResult | None = Field(
        default=None,
        description=(
            "Rzeźba terenu z NMT: Hmin, Hmax, deniwelacja i jakość pomiaru. "
            "Status odróżnia brak pokrycia, niedostępność i snapshot bez danych "
            "od zmierzonej zerowej deniwelacji."
        ),
        json_schema_extra={"example": None},
    )
    section_quality: SectionQualityMatrix | None = Field(
        default=None,
        description=(
            "Macierz kompletności i świeżości sekcji zapisana z analizą (BK-504): "
            "status według kontraktu źródła, źródło, czas pobrania, wydanie, "
            "manual review i świeżość wg reguły źródła w chwili analizy. Ta sama "
            "macierz trafia do UI i raportu PDF. null tylko dla wyniku jeszcze "
            "niezapisanego."
        ),
    )
    buildable_area_sqm: float | None = Field(
        default=None,
        ge=0.0,
        description="Szacowana powierzchnia możliwa do zabudowy w metrach kwadratowych.",
        json_schema_extra={"example": 560.0},
    )
    manual_zone_required: bool = Field(
        default=False,
        description=(
            "Czy analiza wymaga ręcznego podania symbolu strefy z mapy "
            "rastrowej (gmina nie udostępnia wektorowych danych MPZP). Jeżeli "
            "True, wznów analizę przez POST /analyze/resume z analysis_id "
            "i odczytanym symbolem strefy."
        ),
        json_schema_extra={"example": False},
    )
    manual_zone_context: ManualZoneContext | None = Field(
        default=None,
        description=(
            "Obecne tylko przy manual_zone_required=True: plan, kandydaci "
            "symboli, przypięty dokument i klucz podglądu rastrowego, które UI "
            "pokazuje przed formularzem symbolu."
        ),
    )
    warnings: list[WarningMessage] = Field(
        description="Ostrzeżenia o niepewności lub brakujących sekcjach analizy.",
        json_schema_extra={"example": []},
    )
    sources: list[SourceMetadata] = Field(
        description="Wszystkie zewnętrzne źródła danych użyte w analizie.",
        json_schema_extra={"example": []},
    )

    @computed_field(  # type: ignore[prop-decorator]
        description=(
            "Token dostępu do raportu PDF i dokumentu tej analizy (parametr "
            "``access_token``). Wyliczany z ``analysis_id``; None dla wyniku "
            "niezapisanego."
        ),
    )
    @property
    def access_token(self) -> str | None:
        if self.analysis_id is None:
            return None
        return make_analysis_token(self.analysis_id)


class ErrorResponse(BaseModel):
    error: str = Field(
        description="Krótki kod błędu API.",
        json_schema_extra={"example": "VALIDATION_ERROR"},
    )
    detail: str = Field(
        description="Czytelny komunikat dla użytkownika, bez stack trace.",
        json_schema_extra={"example": "Nie udało się zwalidować danych wejściowych."},
    )
    section: str | None = Field(
        default=None,
        description="Sekcja analizy, której dotyczy błąd.",
        json_schema_extra={"example": "parcel"},
    )
