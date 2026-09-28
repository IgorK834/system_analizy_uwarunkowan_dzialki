from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal, Self, Union

from pydantic import BaseModel, Field, model_validator

from app.schemas.source import (
    CatalogMetadataSource,
    FormalDocumentSource,
    SourceMetadata,
    WarningMessage,
)
from app.shared.planning_compatibility_text import (
    COMPATIBILITY_INFORMATIONAL_NOTICE,
    LEGACY_AGGREGATION_NOTE,
)
from app.shared.provenance import is_verified_https_url
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

POG_RESULT_SCHEMA_VERSION = "2.3"


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
        max_length=20,
        description="Symbol strefy MPZP odczytany przez użytkownika z mapy rastrowej.",
        json_schema_extra={"example": "230_U"},
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
MPZP_RESULT_SCHEMA_VERSION = "2.1"


class ManualZoneSelection(BaseModel):
    """Decyzja użytkownika o symbolu strefy bez wektora (BK-204).

    Zapis jest częścią snapshotu strefy, więc raport i ponowny odczyt pokazują,
    z jakich kandydatów i z której dokładnie wersji dokumentu wybrano symbol.
    Nie zawiera udziału powierzchniowego — ręczny symbol go nie ustala.
    """

    entered_symbol: str = Field(description="Symbol po walidacji formatu.")
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


class MpzpParameterEvidence(BaseModel):
    """Jedna kandydatura parametru uchwały z pełnym, cytowalnym dowodem (BK-203).

    Sprzeczne kandydatury tego samego parametru w strefie mają wspólne
    ``conflict_group_id`` i nie są automatycznie rozstrzygane.
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
    extraction_method: str | None = Field(default=None, description="pdf_text, html albo ocr.")
    confidence: float = Field(ge=0.0, le=1.0)
    conflict_group_id: str | None = None
    manual_review_required: bool = False


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
    symbol_max_length: int = 20
    symbol_allowed_pattern: str = Field(
        description="Wyrażenie regularne dozwolonych znaków symbolu (walidacja UI = API)."
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

    @model_validator(mode="after")
    def verify_link(self) -> Self:
        self.gml_url_verified = is_verified_https_url(self.gml_url)
        return self


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


class RiskResult(BaseModel):
    risk_type: str = Field(
        description="Typ ryzyka środowiskowego lub przestrzennego.",
        json_schema_extra={"example": "flood_zone"},
    )
    description: str = Field(
        description="Czytelny opis ryzyka dla użytkownika.",
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
