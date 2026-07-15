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


def map_parser_zone_to_analyze_response(
    parser_zone: mpzp_schemas.MpzpZoneResult,
    parcel_area_sqm: float,
    source: SourceMetadata,
) -> tuple[analyze_schemas.MpzpZoneResult, list[str]]:
    """Mapuje wynik parsera (generyczna lista parametrów) na płaski kontrakt API.

    ``schemas.mpzp.MpzpZoneResult`` (parser) i ``schemas.analyze.MpzpZoneResult``
    (odpowiedź API) są ŚWIADOMIE różnymi kontraktami — patrz docstring modułu
    ``schemas/mpzp.py``. To jest pierwszy realny konsument potrzebujący mostu
    między nimi, więc most jest minimalny i jawny, nie ogólnym mechanizmem.

    Bez geometrii wektorowej strefy (jesteśmy w gałęzi ręcznego wznowienia bez
    wektorów) przyjmujemy, że CAŁA działka leży w podanej strefie:
    ``intersection_area_sqm=parcel_area_sqm``, ``intersection_pct=100.0``,
    ``is_dominant=True``. To jest udokumentowane założenie — dokładna granica
    strefy w obrębie działki nie jest znana do czasu importu wektorów.

    Zwraca tuple (wynik API, lista nazw pominiętych parametrów) — parametry
    parsera bez odpowiednika w płaskim kontrakcie (np. ``prohibition``,
    ``permission``, ``roof_geometry``, ``large_retail_restriction``) NIE mogą
    zostać cicho utracone. Wywołujący (router) powinien zamienić tę listę na
    ``WarningMessage``, żeby użytkownik wiedział, że pełne dane istnieją tylko
    w wewnętrznym wyniku parsera, na razie niedostępnym przez płaskie API.
    """
    api_fields: dict[str, float | int | str | None] = {}
    skipped_parameter_names: list[str] = []

    for parameter in parser_zone.parameters:
        api_field_name = _PARSER_TO_API_PARAMETER_MAP.get(parameter.name)
        if api_field_name is None:
            skipped_parameter_names.append(parameter.name)
            continue
        api_fields[api_field_name] = parameter.normalized_value

    result = analyze_schemas.MpzpZoneResult(
        zone_symbol=parser_zone.zone_symbol,
        primary_use=_as_str_or_none(api_fields.get("primary_use")),
        supplementary_use=_as_str_or_none(api_fields.get("supplementary_use")),
        max_building_height_m=_as_float_or_none(
            api_fields.get("max_building_height_m")
        ),
        max_floors=_as_int_or_none(api_fields.get("max_floors")),
        min_biologically_active_pct=_as_float_or_none(
            api_fields.get("min_biologically_active_pct")
        ),
        max_floor_area_ratio=_as_float_or_none(
            api_fields.get("max_floor_area_ratio")
        ),
        min_floor_area_ratio=_as_float_or_none(
            api_fields.get("min_floor_area_ratio")
        ),
        max_building_coverage_pct=_as_float_or_none(
            api_fields.get("max_building_coverage_pct")
        ),
        intersection_area_sqm=parcel_area_sqm,
        intersection_pct=100.0,
        is_dominant=True,
        source=source,
    )
    return result, skipped_parameter_names


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
