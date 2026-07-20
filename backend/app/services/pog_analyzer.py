"""Powierzchniowa analiza działki względem uchwalonego POG i OUZ."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Literal

from shapely import make_valid
from shapely.geometry import MultiPolygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from app.schemas.analyze import PogResult
from app.schemas.source import SourceMetadata, WarningMessage
from app.services.pog_fetch import PogVectorData, PogVectorFeature

# Przecięcia poniżej tolerancji numerycznej nie są interpretowane domenowo jako
# powierzchniowe wejście w strefę. Wartość chroni przed artefaktami obliczeń,
# a nie ustanawia minimalnej prawnej powierzchni.
INTERSECTION_AREA_TOLERANCE_SQM: Final[float] = 1e-6
ZONE_RATIO_SUM_TOLERANCE: Final[float] = 0.001


class PogPlanningZoneType(StrEnum):
    """Ustawowe typy stref planistycznych POG oraz jawny wariant nieznany."""

    MULTIFUNCTIONAL_MULTI_FAMILY = "SW"
    MULTIFUNCTIONAL_SINGLE_FAMILY = "SJ"
    MULTIFUNCTIONAL_FARMSTEAD = "SZ"
    SERVICES = "SU"
    LARGE_FORMAT_RETAIL = "SH"
    ECONOMIC = "SP"
    AGRICULTURAL_PRODUCTION = "SR"
    INFRASTRUCTURE = "SI"
    GREENERY_AND_RECREATION = "SN"
    CEMETERY = "SC"
    MINING = "SG"
    OPEN = "SO"
    TRANSPORT = "SK"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PogZoneIntersection:
    """Powierzchniowy udział pojedynczej strefy POG w działce."""

    zone_type: PogPlanningZoneType
    source_zone_type: str | None
    area_sqm: float
    area_ratio: float
    parameters: dict[str, object]
    parameters_informational: bool
    manual_review_required: bool


@dataclass(frozen=True)
class PogAnalysisResult:
    """Wynik uchwalonego POG z listą stref oraz przecięciem OUZ."""

    status: Literal["adopted", "unknown"]
    zones: list[PogZoneIntersection]
    dominant_zone: PogZoneIntersection | None
    ouz_intersection_area_sqm: float
    ouz_intersection_pct: float
    touches_ouz_boundary: bool
    downtown_intersection_area_sqm: float
    downtown_intersection_pct: float
    source_metadata: SourceMetadata
    warnings: list[WarningMessage] = field(default_factory=list)


_ZONE_ATTRIBUTE_KEYS: Final[tuple[str, ...]] = (
    "zone_type",
    "typ_strefy",
    "kod_strefy",
    "symbol",
    "oznaczenie",
)
_PARAMETER_SOURCE_KEYS: Final[tuple[str, ...]] = (
    "parameter_source",
    "parameters_source",
    "zrodlo_parametrow",
    "źródło_parametrów",
)
_PLANNING_PARAMETER_KEYS: Final[dict[str, tuple[str, ...]]] = {
    "max_overground_floor_area_ratio": (
        "max_overground_floor_area_ratio",
        "maksymalna_nadziemna_intensywnosc_zabudowy",
        "maksymalna_nadziemna_intensywność_zabudowy",
    ),
    "max_building_height_m": (
        "max_building_height_m",
        "maksymalna_wysokosc_zabudowy",
        "maksymalna_wysokość_zabudowy",
    ),
    "max_building_coverage_pct": (
        "max_building_coverage_pct",
        "maksymalny_udzial_powierzchni_zabudowy",
        "maksymalny_udział_powierzchni_zabudowy",
    ),
    "min_biologically_active_pct": (
        "min_biologically_active_pct",
        "minimalny_udzial_powierzchni_biologicznie_czynnej",
        "minimalny_udział_powierzchni_biologicznie_czynnej",
    ),
}

# Pełne polskie nazwy są akceptowane wyłącznie jako jawne aliasy ustawowych
# kodów. Nieznany opis nie jest dopasowywany heurystycznie do "najbliższej"
# strefy, lecz pozostaje UNKNOWN z ostrzeżeniem.
_ZONE_ALIASES: Final[dict[str, PogPlanningZoneType]] = {
    "sw": PogPlanningZoneType.MULTIFUNCTIONAL_MULTI_FAMILY,
    "strefawielofunkcyjnazzabudowamieszkaniowawielorodzinna": PogPlanningZoneType.MULTIFUNCTIONAL_MULTI_FAMILY,
    "sj": PogPlanningZoneType.MULTIFUNCTIONAL_SINGLE_FAMILY,
    "strefawielofunkcyjnazzabudowamieszkaniowajednorodzinna": PogPlanningZoneType.MULTIFUNCTIONAL_SINGLE_FAMILY,
    "sz": PogPlanningZoneType.MULTIFUNCTIONAL_FARMSTEAD,
    "strefawielofunkcyjnazzabudowazagrodowa": PogPlanningZoneType.MULTIFUNCTIONAL_FARMSTEAD,
    "su": PogPlanningZoneType.SERVICES,
    "strefauslugowa": PogPlanningZoneType.SERVICES,
    "sh": PogPlanningZoneType.LARGE_FORMAT_RETAIL,
    "strefahandluwielkopowierzchniowego": PogPlanningZoneType.LARGE_FORMAT_RETAIL,
    "sp": PogPlanningZoneType.ECONOMIC,
    "strefagospodarcza": PogPlanningZoneType.ECONOMIC,
    "sr": PogPlanningZoneType.AGRICULTURAL_PRODUCTION,
    "strefaprodukcjirolniczej": PogPlanningZoneType.AGRICULTURAL_PRODUCTION,
    "si": PogPlanningZoneType.INFRASTRUCTURE,
    "strefainfrastrukturalna": PogPlanningZoneType.INFRASTRUCTURE,
    "sn": PogPlanningZoneType.GREENERY_AND_RECREATION,
    "strefazieleniirekreacji": PogPlanningZoneType.GREENERY_AND_RECREATION,
    "sc": PogPlanningZoneType.CEMETERY,
    "strefacmentarzy": PogPlanningZoneType.CEMETERY,
    "sg": PogPlanningZoneType.MINING,
    "strefagornictwa": PogPlanningZoneType.MINING,
    "so": PogPlanningZoneType.OPEN,
    "strefaotwarta": PogPlanningZoneType.OPEN,
    "sk": PogPlanningZoneType.TRANSPORT,
    "strefakomunikacyjna": PogPlanningZoneType.TRANSPORT,
}


def analyze_pog_adopted(
    parcel_geometry: BaseGeometry,
    pog_vector_data: PogVectorData,
) -> PogAnalysisResult:
    """Analizuje działkę względem wektorów uchwalonego POG w EPSG:2180.

    Dla stref, OUZ i śródmieścia używane są pola przecięcia w m² oraz udziały,
    nigdy sam predykat ``intersects()``. Styczność z granicą OUZ jest osobną
    informacją i nie oznacza powierzchniowego położenia w OUZ. POG jako akt
    prawa miejscowego wpływa na nowe planowanie i decyzje WZ, ale funkcja nie
    wnioskuje automatycznie o relacji lub zgodności istniejącego MPZP z POG.

    Parametry oznaczone jako pochodzące z PDF/uzasadnienia są informacyjne,
    obniżają confidence i wymagają ręcznej weryfikacji. Wynik ``unknown``
    oznacza brak użytecznych wektorów lub błędną geometrię, nie brak POG.
    """
    warnings: list[WarningMessage] = []
    parcel = _validated_polygonal_geometry(parcel_geometry)
    if parcel is None or parcel.area <= INTERSECTION_AREA_TOLERANCE_SQM:
        return _unknown_analysis(
            pog_vector_data.source_metadata,
            "POG_INVALID_PARCEL_GEOMETRY",
            "Geometria działki nie pozwala na powierzchniową analizę POG.",
        )
    if parcel is not parcel_geometry:
        warnings.append(
            _warning(
                "POG_PARCEL_GEOMETRY_REPAIRED",
                "Geometria działki została naprawiona przed analizą POG i wymaga kontroli.",
            )
        )

    if pog_vector_data.wms_fallback_required:
        return _unknown_analysis(
            pog_vector_data.source_metadata,
            "POG_VECTOR_DATA_REQUIRED",
            "Brak danych wektorowych APP/GML; WMS nie wystarcza do precyzyjnej analizy POG.",
            inherited_warnings=pog_vector_data.warnings,
        )

    zone_intersections: list[PogZoneIntersection] = []
    parameters_from_pdf = False
    for feature in pog_vector_data.planning_zones:
        intersection_area = parcel.intersection(feature.geometry).area
        if intersection_area <= INTERSECTION_AREA_TOLERANCE_SQM:
            continue
        source_zone_type = _first_string_attribute(
            feature.attributes, _ZONE_ATTRIBUTE_KEYS
        )
        zone_type = _normalize_zone_type(source_zone_type)
        if zone_type is PogPlanningZoneType.UNKNOWN:
            warnings.append(
                _warning(
                    "POG_UNKNOWN_ZONE_TYPE",
                    "Dane POG zawierają nierozpoznany typ strefy; zachowano go jako unknown.",
                )
            )
        parameters = _extract_planning_parameters(feature.attributes)
        informational = _parameters_are_from_pdf(feature.attributes)
        parameters_from_pdf = parameters_from_pdf or (
            informational and bool(parameters)
        )
        zone_intersections.append(
            PogZoneIntersection(
                zone_type=zone_type,
                source_zone_type=source_zone_type,
                area_sqm=intersection_area,
                area_ratio=intersection_area / parcel.area,
                parameters=parameters,
                parameters_informational=informational,
                manual_review_required=informational,
            )
        )

    zone_intersections.sort(key=lambda zone: zone.area_sqm, reverse=True)
    dominant_zone = zone_intersections[0] if zone_intersections else None
    if not zone_intersections:
        warnings.append(
            _warning(
                "POG_ZONE_NOT_INTERSECTED",
                "Nie znaleziono powierzchniowego przecięcia działki ze strefą planistyczną POG.",
            )
        )
    if (
        sum(zone.area_ratio for zone in zone_intersections)
        > 1 + ZONE_RATIO_SUM_TOLERANCE
    ):
        warnings.append(
            _warning(
                "POG_OVERLAPPING_ZONES",
                "Strefy POG nakładają się, dlatego suma udziałów przekracza powierzchnię działki.",
            )
        )

    ouz_area, ouz_pct, touches_ouz = _analyze_area_layer(
        parcel, pog_vector_data.ouz_areas
    )
    downtown_area, downtown_pct, _ = _analyze_area_layer(
        parcel, pog_vector_data.downtown_areas
    )
    source_metadata = pog_vector_data.source_metadata
    if parameters_from_pdf:
        warnings.append(
            _warning(
                "POG_PARAMETERS_FROM_PDF",
                "Część parametrów pochodzi z PDF lub uzasadnienia i ma charakter informacyjny.",
            )
        )
        source_metadata = source_metadata.model_copy(
            update={
                "confidence": min(source_metadata.confidence, 0.45),
                "manual_review_required": True,
            }
        )

    warnings.extend(pog_vector_data.warnings)
    return PogAnalysisResult(
        status="adopted",
        zones=zone_intersections,
        dominant_zone=dominant_zone,
        ouz_intersection_area_sqm=ouz_area,
        ouz_intersection_pct=ouz_pct,
        touches_ouz_boundary=touches_ouz,
        downtown_intersection_area_sqm=downtown_area,
        downtown_intersection_pct=downtown_pct,
        source_metadata=source_metadata,
        warnings=warnings,
    )


def to_pog_result(analysis: PogAnalysisResult) -> PogResult:
    """Mapuje bogaty wynik domenowy na istniejący, uproszczony kontrakt API.

    Pola powierzchniowe pozostają wartościami obliczonymi w EPSG:2180. Lista
    wszystkich stref i ostrzeżeń nie mieści się w obecnym ``PogResult`` i musi
    być zachowana osobno przez przyszłą orkiestrację lub rozszerzony kontrakt.
    """
    planning_zone = (
        analysis.dominant_zone.zone_type.value if analysis.dominant_zone else None
    )
    return PogResult(
        status=analysis.status,
        planning_zone=planning_zone,
        ouz_intersection_area_sqm=analysis.ouz_intersection_area_sqm,
        ouz_intersection_pct=analysis.ouz_intersection_pct,
        touches_ouz_boundary=analysis.touches_ouz_boundary,
        source=analysis.source_metadata,
    )


def _analyze_area_layer(
    parcel: BaseGeometry,
    features: list[PogVectorFeature],
) -> tuple[float, float, bool]:
    if not features:
        return 0.0, 0.0, False
    combined = unary_union([feature.geometry for feature in features])
    intersection_area = parcel.intersection(combined).area
    has_surface_intersection = intersection_area > INTERSECTION_AREA_TOLERANCE_SQM
    # touches_ouz_boundary oznacza wyłącznie styczność bez dodatniego pola. Dzięki
    # temu działka częściowo leżąca w OUZ nie jest myląco opisana jako "dotykająca".
    boundary_touch = not has_surface_intersection and parcel.touches(combined)
    effective_area = intersection_area if has_surface_intersection else 0.0
    return effective_area, effective_area / parcel.area * 100, boundary_touch


def _normalize_zone_type(raw_value: str | None) -> PogPlanningZoneType:
    if not raw_value:
        return PogPlanningZoneType.UNKNOWN
    return _ZONE_ALIASES.get(_normalize_name(raw_value), PogPlanningZoneType.UNKNOWN)


def _extract_planning_parameters(attributes: dict[str, object]) -> dict[str, object]:
    normalized_attributes = {key.lower(): value for key, value in attributes.items()}
    parameters: dict[str, object] = {}
    for canonical_name, candidates in _PLANNING_PARAMETER_KEYS.items():
        for candidate in candidates:
            if candidate.lower() in normalized_attributes:
                parameters[canonical_name] = normalized_attributes[candidate.lower()]
                break
    nested = attributes.get("parameters")
    if isinstance(nested, dict):
        parameters.update(nested)
    return parameters


def _parameters_are_from_pdf(attributes: dict[str, object]) -> bool:
    source = _first_string_attribute(attributes, _PARAMETER_SOURCE_KEYS)
    if not source:
        return False
    normalized = _normalize_name(source)
    return "pdf" in normalized or "uzasadnienie" in normalized


def _first_string_attribute(
    attributes: dict[str, object],
    candidates: tuple[str, ...],
) -> str | None:
    for candidate in candidates:
        for key, value in attributes.items():
            if key.lower() == candidate.lower() and value is not None:
                text = str(value).strip()
                if text:
                    return text
    return None


def _validated_polygonal_geometry(geometry: BaseGeometry) -> BaseGeometry | None:
    candidate = geometry if geometry.is_valid else make_valid(geometry)
    if candidate.geom_type in {"Polygon", "MultiPolygon"}:
        return candidate
    if candidate.geom_type == "GeometryCollection":
        polygons = [part for part in candidate.geoms if part.geom_type == "Polygon"]
        if polygons:
            return polygons[0] if len(polygons) == 1 else MultiPolygon(polygons)
    return None


def _unknown_analysis(
    source_metadata: SourceMetadata,
    code: str,
    message: str,
    *,
    inherited_warnings: list[WarningMessage] | None = None,
) -> PogAnalysisResult:
    return PogAnalysisResult(
        status="unknown",
        zones=[],
        dominant_zone=None,
        ouz_intersection_area_sqm=0.0,
        ouz_intersection_pct=0.0,
        touches_ouz_boundary=False,
        downtown_intersection_area_sqm=0.0,
        downtown_intersection_pct=0.0,
        source_metadata=source_metadata.model_copy(
            update={"confidence": 0.0, "manual_review_required": True}
        ),
        warnings=[_warning(code, message, severity="error")]
        + list(inherited_warnings or []),
    )


def _normalize_name(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.lower())
    return "".join(
        char
        for char in decomposed
        if not unicodedata.combining(char) and char.isalnum()
    )


def _warning(
    code: str,
    message: str,
    *,
    severity: Literal["info", "warning", "error"] = "warning",
) -> WarningMessage:
    return WarningMessage(
        code=code,
        message=message,
        severity=severity,
        source_name="pog",
    )
