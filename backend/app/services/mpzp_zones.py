"""Przecięcia działki ze strefami MPZP, wybór strefy dominującej i tryb ręczny.

Ten moduł łączy dwie potrzeby, które w praktyce współdzielą tę samą logikę
"cała działka = jedna strefa": tryb ręcznego wznowienia analizy (gdy
użytkownik odczytuje symbol strefy z mapy rastrowej) oraz fallback dla
brakujących wektorowych granic stref MPZP w ogóle. Zamiast duplikować ten
fallback w routerze i tutaj, ``calculate_single_symbol_fallback`` jest
jedynym miejscem, które go implementuje — router (patrz
``app/routers/analyze.py``) tylko go wywołuje.

Pełny, wersjonowany model provenance i silnik przecięć na realnych danych
WFS jest poza zakresem tego modułu — zalecany w
ANALIZA_ARCHITEKTURY_I_PLAN.md (sekcja E, ADR-004/ADR-005). Ten moduł
realizuje wyłącznie minimalny, potrzebny wycinek: przecięcia na geometriach
już dostępnych w pamięci procesu i fallback bez wektorów.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final

from shapely.geometry.base import BaseGeometry

from app.schemas import analyze as analyze_schemas
from app.schemas import mpzp as mpzp_schemas
from app.schemas.source import SourceMetadata

# Powyżej tego udziału druga (lub kolejna) strefa liczy się jako realny
# multi_zone, nie tylko dotknięcie granicy. 5% jest świadomym progiem
# produktowym: mniejszy udział prawie zawsze wynika z niedokładności
# geometrii wejściowej (np. lekko przesunięta granica działki z ULDK), a nie
# z faktycznego podziału działki między dwie strefy planistyczne.
_MULTI_ZONE_AREA_RATIO_THRESHOLD_PERCENT: Final[float] = 5.0

# Poniżej tego udziału przecięcie jest traktowane jako dotknięcie granicy
# (boundary_touch), nie realna strefa — analogicznie do istniejącego wzorca
# w isok.py (Rule 10: styk brzegowy nie oznacza rzeczywistego nakładania
# powierzchni). 0.1% odpowiada rzędowi wielkości błędów numerycznych Shapely
# przy operacjach na geometriach z ULDK, nie realnemu, celowemu podziałowi.
_BOUNDARY_TOUCH_AREA_RATIO_THRESHOLD_PERCENT: Final[float] = 0.1

# Symbol strefy z mapy rastrowej jest wpisywany przez człowieka, dlatego
# walidujemy tylko długość i brak znaków kontrolnych/whitespace, a NIE
# strukturę symbolu — polskie gminy nie mają jednego wspólnego wzorca
# (widziane w tym projekcie: '230_U', '230_UMW', 'MN.1', 'MN.11', '1UZ',
# '6.8.MW/U', '2.UP'). Zestaw znaków jest permisywny celowo.
_ZONE_SYMBOL_MIN_LENGTH: Final[int] = 1
_ZONE_SYMBOL_MAX_LENGTH: Final[int] = 20
_ZONE_SYMBOL_ALLOWED_CHARS_RE: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9ĄąĆćĘęŁłŃńÓóŚśŹźŻż._/-]+$"
)

# Ręczny wpis symbolu strefy pochodzi z odczytu mapy rastrowej przez
# człowieka, nie z automatycznej ekstrakcji dokumentu — confidence 0.5 jest
# udokumentowanym, świadomym kompromisem: wyższe niż "brak jakichkolwiek
# danych" (bo symbol pochodzi od użytkownika znającego działkę), ale niższe
# niż automatyczna ekstrakcja z tekstu uchwały o wysokiej pewności.
MANUAL_ZONE_SYMBOL_CONFIDENCE: Final[float] = 0.5

# Mapa nazw parametrów parsera MPZP (schemas/mpzp.py.MpzpParameter.name) na
# płaskie pola odpowiedzi API (schemas/analyze.py.MpzpZoneResult). Kolizja
# nazw dwóch klas MpzpZoneResult jest świadoma (patrz docstring
# schemas/mpzp.py) — to jest jedyny, jawny most między nimi, nie ogólny
# mechanizm migracji modelu.
_PARSER_TO_API_PARAMETER_MAP: Final[dict[str, str]] = {
    "max_building_height_m": "max_building_height_m",
    "max_storeys": "max_floors",
    "min_biologically_active_percent": "min_biologically_active_pct",
    "max_intensity": "max_floor_area_ratio",
    "min_intensity": "min_floor_area_ratio",
    "max_building_coverage_percent": "max_building_coverage_pct",
    "primary_use": "primary_use",
    "supplementary_use": "supplementary_use",
}


class InvalidZoneSymbolError(ValueError):
    """Symbol strefy podany przez użytkownika nie przechodzi walidacji formatu.

    Analogicznie do ``InvalidParcelIdentifierError`` w ``uldk.py`` — błąd
    dotyczy tylko formatu wejścia, nie treści planistycznej symbolu.
    """


def validate_zone_symbol_format(zone_symbol: str) -> str:
    """Waliduje i normalizuje symbol strefy wpisany ręcznie przez użytkownika.

    Regex jest CELOWO permisywny co do znaków — żadna polska gmina nie ma
    jednego wspólnego wzorca symbolu strefy. Walidujemy tylko długość (1-20
    znaków po ``strip()``) i odrzucamy whitespace wewnętrzny, znaki
    kontrolne oraz pusty ciąg. Nie próbujemy walidować struktury symbolu.
    """
    stripped = zone_symbol.strip()
    if not stripped:
        raise InvalidZoneSymbolError("Symbol strefy nie może być pusty.")

    if not (_ZONE_SYMBOL_MIN_LENGTH <= len(stripped) <= _ZONE_SYMBOL_MAX_LENGTH):
        raise InvalidZoneSymbolError(
            "Symbol strefy musi mieć od "
            f"{_ZONE_SYMBOL_MIN_LENGTH} do {_ZONE_SYMBOL_MAX_LENGTH} znaków."
        )

    if any(ord(ch) < 32 or ord(ch) == 127 for ch in stripped):
        raise InvalidZoneSymbolError(
            "Symbol strefy nie może zawierać znaków kontrolnych."
        )

    if not _ZONE_SYMBOL_ALLOWED_CHARS_RE.fullmatch(stripped):
        raise InvalidZoneSymbolError(
            "Symbol strefy zawiera niedozwolone znaki (dozwolone są litery, "
            "cyfry oraz '.', '_', '/', '-')."
        )

    return stripped


@dataclass(frozen=True)
class LegalUnitEvidence:
    """Liść jednostki redakcyjnej uchwały zapisanej w bazie (DocumentVersion)."""

    legal_unit_id: int
    text: str
    page_from: int | None = None
    page_to: int | None = None


@dataclass(frozen=True)
class DocumentEvidenceContext:
    """Kontekst dowodowy dokumentu, z którego pochodzą parametry strefy.

    ``act_version`` i ``document_version_id`` wiążą grupę konfliktu z dokładną
    wersją aktu i dokumentu, więc te same symbole w innej wersji nie tworzą
    wspólnej grupy.
    """

    document_version_id: int | None = None
    act_version: str | None = None
    legal_units: tuple[LegalUnitEvidence, ...] = ()


_NUMERIC_API_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "max_building_height_m",
        "max_floors",
        "min_biologically_active_pct",
        "max_floor_area_ratio",
        "min_floor_area_ratio",
        "max_building_coverage_pct",
    }
)


def map_parser_zone_to_analyze_response(
    parser_zone: mpzp_schemas.MpzpZoneResult,
    parcel_area_sqm: float,
    source: SourceMetadata,
    *,
    evidence: DocumentEvidenceContext | None = None,
) -> tuple[analyze_schemas.MpzpZoneResult, list[str]]:
    """Mapuje wynik parsera na strefę API bez wektora (fallback i resume).

    Bez geometrii wektorowej przyjmujemy udokumentowane założenie, że cała
    działka leży w strefie (``intersection_pct=100``); przypisanie oznacza
    ``assignment_method`` i obniżone confidence nadane przez wywołującego.
    Parametry są przenoszone przez :func:`apply_parser_zone` — z pełnym
    evidence i bez automatycznego rozstrzygania konfliktów.
    """
    base = analyze_schemas.MpzpZoneResult(
        zone_symbol=parser_zone.zone_symbol,
        intersection_area_sqm=parcel_area_sqm,
        intersection_pct=100.0,
        is_dominant=True,
        source=source,
    )
    zone, skipped, _conflicts = apply_parser_zone(base, parser_zone, evidence)
    return zone, skipped


def apply_parser_zone(
    zone: analyze_schemas.MpzpZoneResult,
    parser_zone: mpzp_schemas.MpzpZoneResult,
    evidence: DocumentEvidenceContext | None = None,
) -> tuple[analyze_schemas.MpzpZoneResult, list[str], list[str]]:
    """Dołącza parametry uchwały do strefy (BK-203).

    - Wszystkie kandydatury trafiają do ``parameters`` z evidence.
    - Płaskie pole API dostaje wartość tylko, gdy parser znalazł jedną
      dystynktywną wartość. Sprzeczne wartości zostawiają pole ``None``
      (brak rozstrzygnięcia, nie zero) i wymuszają ręczną weryfikację.
    - Symbol parsera musi być dokładnie symbolem strefy; podobne symbole nie
      są łączone.

    Zwraca (strefę, pominięte nazwy parametrów, nazwy parametrów w konflikcie).
    """
    if parser_zone.zone_symbol != zone.zone_symbol:
        raise ValueError(
            f"Symbol parsera {parser_zone.zone_symbol!r} nie jest symbolem strefy "
            f"{zone.zone_symbol!r}."
        )
    evidence = evidence or DocumentEvidenceContext()
    parameters = [
        _parameter_evidence(parameter, evidence) for parameter in parser_zone.parameters
    ]
    values_by_field: dict[str, list[float | str]] = {}
    skipped: list[str] = []
    for parameter in parser_zone.parameters:
        api_field = _PARSER_TO_API_PARAMETER_MAP.get(parameter.name)
        if api_field is None:
            skipped.append(parameter.name)
            continue
        if parameter.normalized_value is not None:
            values_by_field.setdefault(api_field, []).append(parameter.normalized_value)

    updates: dict[str, object] = {}
    conflicts: list[str] = []
    for api_field, values in values_by_field.items():
        distinct = list(dict.fromkeys(values))
        if api_field in _NUMERIC_API_FIELDS:
            if len(distinct) == 1:
                updates[api_field] = (
                    _as_int_or_none(distinct[0])
                    if api_field == "max_floors"
                    else _as_float_or_none(distinct[0])
                )
            else:
                conflicts.append(api_field)
                updates[api_field] = None
        elif api_field == "primary_use" and zone.primary_use:
            # Kategoria z urzędowego wektora ma pierwszeństwo; opis z uchwały
            # pozostaje w liście parametrów jako dowód.
            continue
        else:
            # Parametry opisowe są listami współistniejących ustaleń uchwały.
            updates[api_field] = "; ".join(str(value) for value in distinct)

    manual_review = (
        zone.manual_review_required
        or bool(conflicts)
        or any(parameter.manual_review_required for parameter in parameters)
    )
    return (
        zone.model_copy(
            update={
                **updates,
                "parameters": parameters,
                "manual_review_required": manual_review,
            }
        ),
        skipped,
        conflicts,
    )


def _parameter_evidence(
    parameter: mpzp_schemas.MpzpParameter,
    evidence: DocumentEvidenceContext,
) -> analyze_schemas.MpzpParameterEvidence:
    group = parameter.conflict_group_id
    if group is not None:
        scope = evidence.act_version or (parameter.document_sha256 or "")[:16]
        group = f"{scope}:{group}" if scope else group
    return analyze_schemas.MpzpParameterEvidence(
        name=parameter.name,
        normalized_value=parameter.normalized_value,
        raw_value=parameter.raw_value,
        unit=parameter.unit,
        evidence_text=parameter.source_text,
        page_number=parameter.page_number,
        segment_id=parameter.segment_id,
        legal_unit_id=_legal_unit_for(parameter, evidence.legal_units),
        document_sha256=parameter.document_sha256,
        document_version_id=evidence.document_version_id,
        parser_version=parameter.parser_version,
        extraction_method=parameter.extraction_method,
        confidence=parameter.confidence,
        conflict_group_id=group,
        manual_review_required=parameter.manual_review_required or group is not None,
    )


def _legal_unit_for(
    parameter: mpzp_schemas.MpzpParameter,
    units: tuple[LegalUnitEvidence, ...],
) -> int | None:
    """Najmniejsza jednostka redakcyjna zawierająca fragment dowodowy."""
    if not parameter.source_text or not units:
        return None
    needle = " ".join(parameter.source_text.split())
    matches = [unit for unit in units if needle in unit.text]
    if not matches:
        return None
    if parameter.page_number is not None:
        on_page = [
            unit for unit in matches
            if (unit.page_from or 0) <= parameter.page_number <= (unit.page_to or unit.page_from or 10**6)
        ]
        matches = on_page or matches
    return min(matches, key=lambda unit: (len(unit.text), unit.legal_unit_id)).legal_unit_id


def legal_unit_evidence_from_snapshot(snapshot) -> tuple[LegalUnitEvidence, ...]:  # noqa: ANN001
    """Liście jednostek redakcyjnych zapisanego dokumentu jako kontekst dowodowy."""
    if snapshot is None:
        return ()
    parent_ids = {unit.parent_id for unit in snapshot.legal_units if unit.parent_id is not None}
    return tuple(
        LegalUnitEvidence(
            legal_unit_id=unit.id,
            text=" ".join(unit.source_text.split()),
            page_from=unit.page_from,
            page_to=unit.page_to,
        )
        for unit in snapshot.legal_units
        if unit.id is not None and unit.id not in parent_ids and unit.source_text.strip()
    )


def _as_str_or_none(value: float | int | str | None) -> str | None:
    return str(value) if value is not None else None


def _as_float_or_none(value: float | int | str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int_or_none(value: float | int | str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class ZoneGeometryCandidate:
    """Kandydat strefy MPZP z geometrią wektorową do przecięcia z działką."""

    zone_symbol: str
    geometry: BaseGeometry
    source_metadata: SourceMetadata


@dataclass(frozen=True)
class ZoneIntersection:
    """Wynik przecięcia jednej strefy MPZP z geometrią działki.

    ``area_ratio`` jest w konwencji 0-100 (zgodnie z ``intersection_pct`` w
    ``schemas/analyze.py``), NIE 0-1.
    """

    zone_symbol: str
    intersection_area_sqm: float
    area_ratio: float
    is_dominant: bool
    source_metadata: SourceMetadata


@dataclass(frozen=True)
class MpzpZoneIntersectionResult:
    """Zagregowany wynik przecięć działki z wieloma strefami MPZP."""

    dominant_zone: ZoneIntersection | None
    zones: list[ZoneIntersection]
    multi_zone: bool
    manual_review_required: bool
    warnings: list[str] = field(default_factory=list)


def calculate_mpzp_zone_intersections(
    parcel_geometry: BaseGeometry,
    zone_geometries: list[ZoneGeometryCandidate],
) -> MpzpZoneIntersectionResult:
    """Liczy przecięcia działki z wieloma strefami MPZP i wybiera dominującą.

    Obliczenia zakładają geometrię działki i stref już w EPSG:2180 (metryczne
    pole przecięcia). Dominująca strefa jest wybierana po polu przecięcia, NIE
    po centroidzie — działka w kształcie L może mieć centroid w jednej
    strefie, a większość powierzchni w innej (patrz zasada 5 w context.md).

    Przecięcia poniżej ``_BOUNDARY_TOUCH_AREA_RATIO_THRESHOLD_PERCENT`` są
    oznaczane jako dotknięcie granicy (warning), ale NIE są usuwane z listy —
    użytkownik powinien wiedzieć, że strefa jest w pobliżu, analogicznie do
    ``isok.py``. ``multi_zone`` jest True, gdy więcej niż jedna strefa
    (ponad strefę dominującą) przekracza próg
    ``_MULTI_ZONE_AREA_RATIO_THRESHOLD_PERCENT``.
    """
    if not zone_geometries:
        return MpzpZoneIntersectionResult(
            dominant_zone=None,
            zones=[],
            multi_zone=False,
            manual_review_required=True,
            warnings=[
                "NO_ZONE_GEOMETRIES: brak wektorowych granic stref MPZP — "
                "wymagana ręczna weryfikacja."
            ],
        )

    parcel_area = parcel_geometry.area
    warnings: list[str] = []
    raw_intersections: list[tuple[str, float, float, SourceMetadata]] = []

    for candidate in zone_geometries:
        intersection_geometry = parcel_geometry.intersection(candidate.geometry)
        intersection_area_sqm = intersection_geometry.area
        area_ratio = (
            (intersection_area_sqm / parcel_area) * 100.0 if parcel_area > 0 else 0.0
        )
        raw_intersections.append(
            (
                candidate.zone_symbol,
                intersection_area_sqm,
                area_ratio,
                candidate.source_metadata,
            )
        )
        if area_ratio < _BOUNDARY_TOUCH_AREA_RATIO_THRESHOLD_PERCENT:
            warnings.append(
                f"boundary_touch: strefa {candidate.zone_symbol!r} tylko dotyka "
                "granicy działki, bez rzeczywistego nakładania powierzchni."
            )

    above_boundary_touch = [
        item
        for item in raw_intersections
        if item[2] >= _BOUNDARY_TOUCH_AREA_RATIO_THRESHOLD_PERCENT
    ]

    dominant_index: int | None = None
    if above_boundary_touch:
        # Indeks w raw_intersections, nie tylko symbol — dwie strefy mogłyby
        # teoretycznie mieć ten sam symbol (np. wielopoligonowa strefa
        # zaimportowana jako osobne rekordy), a wybór po indeksie gwarantuje,
        # że dominująca jest oznaczona dokładnie jedna, konkretna strefa.
        best_area = max(item[1] for item in above_boundary_touch)
        for idx, item in enumerate(raw_intersections):
            if item[1] == best_area and item[2] >= _BOUNDARY_TOUCH_AREA_RATIO_THRESHOLD_PERCENT:
                dominant_index = idx
                break
    else:
        warnings.append(
            "Wszystkie przecięcia stref są poniżej progu dotknięcia granicy — "
            "nie można wyznaczyć strefy dominującej."
        )

    dominant: ZoneIntersection | None = None
    zones: list[ZoneIntersection] = []
    for idx, (zone_symbol, area_sqm, ratio, source_metadata) in enumerate(
        raw_intersections
    ):
        is_dominant = idx == dominant_index
        zones.append(
            ZoneIntersection(
                zone_symbol=zone_symbol,
                intersection_area_sqm=area_sqm,
                area_ratio=ratio,
                is_dominant=is_dominant,
                source_metadata=source_metadata,
            )
        )
        if is_dominant:
            dominant = zones[-1]

    zones_above_multi_zone_threshold = [
        item for item in above_boundary_touch
        if item[2] > _MULTI_ZONE_AREA_RATIO_THRESHOLD_PERCENT
    ]
    multi_zone = len(zones_above_multi_zone_threshold) > 1
    manual_review_required = multi_zone or dominant is None

    return MpzpZoneIntersectionResult(
        dominant_zone=dominant,
        zones=zones,
        multi_zone=multi_zone,
        manual_review_required=manual_review_required,
        warnings=warnings,
    )


def calculate_single_symbol_fallback(
    parcel_geometry: BaseGeometry,
    zone_symbol: str,
    source_metadata: SourceMetadata,
) -> MpzpZoneIntersectionResult:
    """Fallback "cała działka = jedna strefa" przy braku wektorów MPZP.

    Realizuje wymóg: gdy brak wektorów, użyj kandydata (z KIMPZP discovery
    albo z ręcznego wpisu, patrz ``app/routers/analyze.py``) jako fallback z
    niższym confidence, bez duplikowania tej logiki w dwóch miejscach.
    ``source_metadata.confidence`` i ``.manual_review_required`` MUSZĄ
    pochodzić od wywołującego — ta funkcja tylko przekazuje je dalej, nie
    decyduje o nich (np. 0.5 dla ręcznego wpisu z resume, inna wartość dla
    surowego kandydata KIMPZP bez potwierdzenia użytkownika).

    ``manual_review_required`` w wyniku jest ZAWSZE True — to fallback bez
    realnej geometrii wektorowej stref, nigdy nie jest to "pewne" przypisanie.
    """
    zone = ZoneIntersection(
        zone_symbol=zone_symbol,
        intersection_area_sqm=parcel_geometry.area,
        area_ratio=100.0,
        is_dominant=True,
        source_metadata=source_metadata,
    )
    return MpzpZoneIntersectionResult(
        dominant_zone=zone,
        zones=[zone],
        multi_zone=False,
        manual_review_required=True,
        warnings=[
            "SINGLE_ZONE_FALLBACK: przypisanie całej działki do strefy oparte "
            "na kandydacie bez wektorowej granicy."
        ],
    )


# --- Wersjonowany wektor MPZP (BK-202) --------------------------------------

# Przecięcie o polu nie większym niż ta tolerancja numeryczna jest stycznością
# granic, nie realnym udziałem strefy w działce.
MPZP_INTERSECTION_AREA_TOLERANCE_SQM: Final[float] = 1e-6
# Tolerancja pokrycia/nakładania wyrażona w procentach powierzchni działki.
MPZP_COVERAGE_TOLERANCE_PCT: Final[float] = 0.1
# Wydzielenie z przejętego QA, wersjonowanego wektora — parametry i tak mają
# własne confidence z parsera, więc przypisanie przestrzenne nie jest 1.0.
VECTOR_ZONE_CONFIDENCE: Final[float] = 0.95
# Bez wiarygodnego wektora przypisanie strefy pochodzi z punktowego discovery
# albo odczytu ręcznego: confidence nie może przekroczyć tego sufitu.
FALLBACK_ZONE_CONFIDENCE_CAP: Final[float] = 0.5


@dataclass(frozen=True)
class VectorZoneAssessment:
    """Wynik przypisania stref z wektora dla pełnego obrysu działki."""

    zones: list[analyze_schemas.MpzpZoneResult]
    warnings: list[analyze_schemas.WarningMessage]
    covered_pct: float
    overlap_pct: float
    act_identifiers: tuple[str, ...]

    @property
    def positive_zones(self) -> list[analyze_schemas.MpzpZoneResult]:
        return [zone for zone in self.zones if not zone.touches_boundary]

    @property
    def complete_coverage(self) -> bool:
        return self.covered_pct >= 100.0 - MPZP_COVERAGE_TOLERANCE_PCT


def assess_vector_zones(
    parcel_geometry: BaseGeometry,
    rows: list[dict[str, object]],
) -> VectorZoneAssessment:
    """Buduje pełną listę stref z wierszy ``find_mpzp_zone_intersections``.

    Udział każdej strefy to pole przecięcia z całym obrysem działki
    (``ST_Area(ST_Intersection)`` w EPSG:2180), nie przynależność centroidu.
    Styczności są osobnymi wpisami z ``touches_boundary=True``. Sumy udziałów
    nie są normalizowane: nakładanie wydzieleń (np. dwóch planów) i niepełne
    pokrycie działki są jawnie raportowane ostrzeżeniami.
    """
    from shapely import from_wkt, make_valid
    from shapely.ops import unary_union

    from app.services.geojson import analysis_layer_geometry_to_geojson

    parcel = parcel_geometry if parcel_geometry.is_valid else make_valid(parcel_geometry)
    parcel_area = parcel.area
    warnings: list[analyze_schemas.WarningMessage] = []
    positive: list[tuple[dict[str, object], BaseGeometry]] = []
    touching: list[dict[str, object]] = []
    for row in rows:
        area = float(row["intersection_area_sqm"])  # type: ignore[arg-type]
        wkt = row.get("intersection_wkt")
        geometry = from_wkt(str(wkt)) if wkt else None
        if area > MPZP_INTERSECTION_AREA_TOLERANCE_SQM and geometry is not None:
            positive.append((row, geometry))
        else:
            touching.append(row)

    ordered = sorted(
        positive,
        key=lambda item: (-float(item[0]["intersection_area_sqm"]), str(item[0]["zone_identifier"])),  # type: ignore[arg-type]
    )
    dominant_id = str(ordered[0][0]["zone_identifier"]) if ordered else None
    zones: list[analyze_schemas.MpzpZoneResult] = []
    for row, geometry in ordered:
        area = float(row["intersection_area_sqm"])  # type: ignore[arg-type]
        pct = min(100.0, area / parcel_area * 100.0) if parcel_area > 0 else 0.0
        zones.append(
            _vector_zone_result(
                row,
                area_sqm=area,
                pct=pct,
                is_dominant=str(row["zone_identifier"]) == dominant_id,
                touches=False,
                geojson=analysis_layer_geometry_to_geojson(
                    geometry,
                    "mpzp_zone",
                    {
                        "zone_id": row["zone_identifier"],
                        "zone_symbol": row["symbol"],
                        "act_identifier": row["act_identifier"],
                    },
                ),
            )
        )
    for row in sorted(touching, key=lambda item: str(item["zone_identifier"])):
        zones.append(
            _vector_zone_result(
                row, area_sqm=0.0, pct=0.0, is_dominant=False, touches=True, geojson=None
            )
        )

    union = unary_union([geometry for _row, geometry in positive]) if positive else None
    covered_area = parcel.intersection(union).area if union is not None else 0.0
    covered_pct = covered_area / parcel_area * 100.0 if parcel_area > 0 else 0.0
    summed_pct = sum(zone.intersection_pct for zone in zones)
    overlap_pct = max(0.0, summed_pct - covered_pct)
    act_identifiers = tuple(sorted({str(row["act_identifier"]) for row, _ in positive}))

    if not parcel_geometry.is_valid:
        warnings.append(_mpzp_warning(
            "MPZP_PARCEL_GEOMETRY_REPAIRED",
            "Geometria działki była niepoprawna i została naprawiona przed "
            "przecięciem z wydzieleniami MPZP.",
        ))
    if touching:
        warnings.append(_mpzp_warning(
            "MPZP_ZONE_BOUNDARY_TOUCH",
            "Część wydzieleń MPZP jedynie styka się z granicą działki; nie mają "
            "udziału powierzchniowego i są oznaczone osobno.",
            severity="info",
        ))
    if overlap_pct > MPZP_COVERAGE_TOLERANCE_PCT:
        warnings.append(_mpzp_warning(
            "MPZP_ZONES_OVERLAP",
            f"Wydzielenia MPZP nakładają się na {overlap_pct:.1f}% powierzchni "
            "działki; suma udziałów przekracza pokrycie i nie została "
            "znormalizowana. Wymagana weryfikacja obowiązującego ustalenia.",
        ))
    if len(act_identifiers) > 1:
        warnings.append(_mpzp_warning(
            "MPZP_MULTIPLE_ACTS",
            "Działka leży w zasięgu więcej niż jednego planu miejscowego: "
            + ", ".join(act_identifiers) + ".",
        ))
    if positive and covered_pct < 100.0 - MPZP_COVERAGE_TOLERANCE_PCT:
        warnings.append(_mpzp_warning(
            "MPZP_PARTIAL_COVERAGE",
            f"Wektorowe wydzielenia MPZP pokrywają {covered_pct:.1f}% działki. "
            "Pozostała część nie ma danych wektorowych — brak danych nie oznacza "
            "braku planu ani braku ograniczeń.",
        ))
    return VectorZoneAssessment(
        zones=zones,
        warnings=warnings,
        covered_pct=covered_pct,
        overlap_pct=overlap_pct,
        act_identifiers=act_identifiers,
    )


def cap_fallback_zone(
    zone: analyze_schemas.MpzpZoneResult,
    *,
    assignment_method: str,
) -> analyze_schemas.MpzpZoneResult:
    """Oznacza przypisanie bez wektora i obniża jego pewność (BK-202)."""
    source = zone.source.model_copy(
        update={
            "confidence": min(zone.source.confidence, FALLBACK_ZONE_CONFIDENCE_CAP),
            "manual_review_required": True,
        }
    )
    return zone.model_copy(
        update={
            "assignment_method": assignment_method,
            "manual_review_required": True,
            "source": source,
        }
    )


def _vector_zone_result(
    row: dict[str, object],
    *,
    area_sqm: float,
    pct: float,
    is_dominant: bool,
    touches: bool,
    geojson: dict[str, object] | None,
) -> analyze_schemas.MpzpZoneResult:
    fetched_at = row.get("fetched_at")
    source_uri = str(row.get("artifact_uri") or "")
    source = SourceMetadata(
        source_id=str(row["source_id"]),
        source_version=str(row.get("version_label") or "") or None,
        artifact_sha256=str(row.get("artifact_sha256") or "") or None,
        data_release_id=int(row["data_release_id"]),  # type: ignore[arg-type]
        act_version=str(row.get("act_version") or "") or None,
        source_name=f"MPZP_WEKTOR:{row['source_id']}",
        source_url=source_uri if source_uri.startswith(("http://", "https://")) else None,
        fetched_at=fetched_at,  # type: ignore[arg-type]
        confidence=VECTOR_ZONE_CONFIDENCE,
        manual_review_required=False,
    )
    return analyze_schemas.MpzpZoneResult(
        zone_symbol=str(row["symbol"] or "UNKNOWN"),
        primary_use=(str(row["normalized_category"]) if row.get("normalized_category") else None),
        intersection_area_sqm=area_sqm,
        intersection_pct=pct,
        is_dominant=is_dominant,
        zone_id=str(row["zone_identifier"]),
        act_identifier=str(row["act_identifier"]),
        act_version=str(row.get("act_version") or "") or None,
        act_version_id=int(row["act_version_id"]),  # type: ignore[arg-type]
        data_release_id=int(row["data_release_id"]),  # type: ignore[arg-type]
        document_url=(str(row["document_url"]) if row.get("document_url") else None),
        touches_boundary=touches,
        assignment_method="vector_intersection",
        intersection_geojson=geojson,  # type: ignore[arg-type]
        source=source,
    )


def _mpzp_warning(
    code: str, message: str, *, severity: str = "warning"
) -> analyze_schemas.WarningMessage:
    return analyze_schemas.WarningMessage(
        code=code, message=message, severity=severity, source_name="mpzp"  # type: ignore[arg-type]
    )
