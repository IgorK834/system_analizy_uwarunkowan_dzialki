"""Powierzchniowa analiza działki względem wektorów POG i OUZ.

Analiza geometrii nie ustala statusu prawnego aktu (BK-106). Status i pokrycie
rozstrzyga :func:`app.shared.planning_status.resolve_pog_status`, a
:func:`to_pog_result` jedynie składa oba niezależne wyniki w kontrakt API.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Final, Literal

from shapely import make_valid
from shapely.geometry import MultiPolygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from app.core.planning_compatibility import PogPlanningZoneType
from app.schemas.analyze import (
    PogActResult,
    PogAreaResult,
    PogProfileResult,
    PogResult,
    PogStatusEvidence,
    PogZoneResult,
)
from app.schemas.source import SourceMetadata, WarningMessage
from app.services.ouz import OuzStatusResult, calculate_ouz_status
from app.services.pog_fetch import PogVectorData, PogVectorFeature
from app.shared.planning_status import PogStatusDecision, StatusEvidence

# Przecięcia poniżej tolerancji numerycznej nie są interpretowane domenowo jako
# powierzchniowe wejście w strefę. Wartość chroni przed artefaktami obliczeń,
# a nie ustanawia minimalnej prawnej powierzchni.
INTERSECTION_AREA_TOLERANCE_SQM: Final[float] = 1e-6
ZONE_RATIO_SUM_TOLERANCE: Final[float] = 0.001
# Strefy planistyczne POG pokrywają cały obszar gminy. Suma udziałów stref
# poniżej 1 - tolerancji oznacza niepełne dane, a nie „brak strefy”.
ZONE_COVERAGE_TOLERANCE: Final[float] = 0.001


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
    zone_id: str | None = None
    symbol: str | None = None
    label: str | None = None
    primary_profiles: tuple[dict[str, str | None], ...] = ()
    additional_profiles: tuple[dict[str, str | None], ...] = ()
    feature_version: str | None = None
    gml_url: str | None = None
    # Przecięcie strefy z działką w EPSG:2180 — wejście oceny par MPZP–POG
    # (BK-205); nie trafia do kontraktu API.
    intersection_wkt: str | None = None


@dataclass(frozen=True)
class PogAreaIntersection:
    area_id: str
    symbol: str | None
    label: str | None
    area_sqm: float
    area_pct: float
    touches_boundary: bool
    feature_version: str | None = None
    gml_url: str | None = None


@dataclass(frozen=True)
class PogAnalysisResult:
    """Wynik analizy wektorów POG z listą stref oraz przecięciem OUZ.

    ``status`` opisuje wyłącznie wykonanie analizy geometrii (``analyzed``)
    albo jej brak (``unknown``); nie jest statusem prawnym aktu.
    """

    status: Literal["analyzed", "unknown"]
    zones: list[PogZoneIntersection]
    dominant_zone: PogZoneIntersection | None
    ouz_status: OuzStatusResult
    ouz_intersection_area_sqm: float
    ouz_intersection_pct: float
    touches_ouz_boundary: bool
    downtown_intersection_area_sqm: float
    downtown_intersection_pct: float
    source_metadata: SourceMetadata
    warnings: list[WarningMessage] = field(default_factory=list)
    ouz_areas: list[PogAreaIntersection] = field(default_factory=list)
    downtown_areas: list[PogAreaIntersection] = field(default_factory=list)
    social_infrastructure_standard_areas: list[PogAreaIntersection] = field(default_factory=list)
    act_metadata: dict[str, object] = field(default_factory=dict)


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


def analyze_pog_vectors(
    parcel_geometry: BaseGeometry,
    pog_vector_data: PogVectorData,
) -> PogAnalysisResult:
    """Analizuje działkę względem wektorów POG w EPSG:2180.

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
    for feature_index, feature in enumerate(pog_vector_data.planning_zones):
        intersection_geometry = parcel.intersection(feature.geometry)
        intersection_area = intersection_geometry.area
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
                zone_id=_first_string_attribute(
                    feature.attributes,
                    ("feature_id", "id_iip", "idIIP", "identifier", "oznaczenie"),
                ) or f"zone-{feature_index + 1}",
                symbol=_first_string_attribute(feature.attributes, ("symbol", "oznaczenie")),
                label=_first_string_attribute(feature.attributes, ("label", "nazwa")),
                primary_profiles=_profiles_from_attributes(feature.attributes, "primary_profiles"),
                additional_profiles=_profiles_from_attributes(feature.attributes, "additional_profiles"),
                feature_version=_first_string_attribute(feature.attributes, ("feature_version",)),
                gml_url=_first_string_attribute(feature.attributes, ("gml_url",)),
                intersection_wkt=intersection_geometry.wkt,
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

    ouz_status = calculate_ouz_status(parcel, pog_vector_data.ouz_areas)
    downtown_area, downtown_pct, _ = _analyze_area_layer(
        parcel, pog_vector_data.downtown_areas
    )
    ouz_areas = _area_intersections(parcel, pog_vector_data.ouz_areas, "ouz")
    downtown_areas = _area_intersections(parcel, pog_vector_data.downtown_areas, "downtown")
    social_areas = _area_intersections(
        parcel,
        pog_vector_data.social_infrastructure_standard_areas,
        "social-infrastructure-standard",
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
        status="analyzed",
        zones=zone_intersections,
        dominant_zone=dominant_zone,
        ouz_status=ouz_status,
        ouz_intersection_area_sqm=ouz_status.intersection_area_sqm,
        ouz_intersection_pct=ouz_status.area_ratio,
        touches_ouz_boundary=ouz_status.touches_ouz_boundary,
        downtown_intersection_area_sqm=downtown_area,
        downtown_intersection_pct=downtown_pct,
        source_metadata=source_metadata,
        warnings=warnings,
        ouz_areas=ouz_areas,
        downtown_areas=downtown_areas,
        social_infrastructure_standard_areas=social_areas,
        act_metadata=pog_vector_data.app_metadata,
    )


def zones_cover_parcel(analysis: PogAnalysisResult) -> bool:
    """Czy strefy planistyczne pokrywają całą działkę (w tolerancji)."""
    covered = sum(zone.area_ratio for zone in analysis.zones)
    return covered >= 1 - ZONE_COVERAGE_TOLERANCE


def spatial_feature_count(analysis: PogAnalysisResult) -> int:
    return (
        len(analysis.zones)
        + len(analysis.ouz_areas)
        + len(analysis.downtown_areas)
        + len(analysis.social_infrastructure_standard_areas)
    )


def evidence_result(evidence: StatusEvidence | None) -> PogStatusEvidence | None:
    if evidence is None:
        return None
    return PogStatusEvidence(
        source_name=evidence.source_name,
        official=evidence.official,
        reference=evidence.reference,
        source_id=evidence.source_id,
        raw_value=evidence.raw_value,
        confirmed_at=evidence.confirmed_at,
    )


def to_pog_result(
    analysis: PogAnalysisResult,
    decision: PogStatusDecision,
) -> PogResult:
    """Mapuje pełny wynik domenowy na kontrakt POG v2 bez agregacji stref.

    Status prawny, pokrycie i dostępność pochodzą z decyzji BK-106; strefy i
    parametry z analizy geometrii. Wynik niewiążący albo niepełny zawsze
    wymaga ręcznej weryfikacji.
    """
    planning_zone = (
        analysis.dominant_zone.zone_type.value if analysis.dominant_zone else None
    )
    status_needs_review = not (
        decision.legal_status == "binding"
        and decision.coverage_status == "available"
        and decision.data_availability == "current"
    )
    return PogResult(
        legal_status=decision.legal_status,
        coverage_status=decision.coverage_status,
        data_availability=decision.data_availability,
        status_confirmed_at=decision.confirmed_at,
        legal_status_evidence=evidence_result(decision.legal_evidence),
        coverage_evidence=evidence_result(decision.coverage_evidence),
        act=_act_result(analysis),
        zones=[_zone_result(zone, analysis.source_metadata) for zone in analysis.zones],
        dominant_zone_id=(analysis.dominant_zone.zone_id if analysis.dominant_zone else None),
        ouz=[_area_result(item, analysis.source_metadata) for item in analysis.ouz_areas],
        downtown_areas=[_area_result(item, analysis.source_metadata) for item in analysis.downtown_areas],
        social_infrastructure_standard_areas=[
            _area_result(item, analysis.source_metadata)
            for item in analysis.social_infrastructure_standard_areas
        ],
        planning_zone=planning_zone,
        zone_type=planning_zone,
        in_ouz=analysis.ouz_status.in_ouz,
        area_ratio=(
            analysis.dominant_zone.area_ratio if analysis.dominant_zone else None
        ),
        in_downtown_area=analysis.downtown_intersection_area_sqm
        > INTERSECTION_AREA_TOLERANCE_SQM,
        manual_review_required=(
            analysis.source_metadata.manual_review_required
            or analysis.ouz_status.manual_review_required
            or status_needs_review
        ),
        ouz_intersection_area_sqm=analysis.ouz_intersection_area_sqm,
        ouz_intersection_pct=analysis.ouz_intersection_pct,
        touches_ouz_boundary=analysis.touches_ouz_boundary,
        source=analysis.source_metadata,
    )


def _act_result(analysis: PogAnalysisResult) -> PogActResult | None:
    source = analysis.source_metadata
    metadata = analysis.act_metadata
    act_id = str(metadata.get("act_identifier") or "") or source.source_id
    if not act_id and not source.act_version:
        return None
    return PogActResult(
        id=act_id or "pog",
        version=(str(metadata.get("act_version")) if metadata.get("act_version") else source.act_version),
        title=(str(metadata.get("act_name")) if metadata.get("act_name") else source.source_name),
        resolution_number=(str(metadata.get("resolution_number")) if metadata.get("resolution_number") else None),
        resolution_date=metadata.get("resolution_date"),
    )


def _float_parameter(parameters: dict[str, object], name: str) -> float | None:
    value = parameters.get(name)
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("value")
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _profile_results(items: tuple[dict[str, str | None], ...]) -> list[PogProfileResult]:
    return [
        PogProfileResult(
            code=str(item.get("code") or ""),
            label=item.get("label"),
            dictionary_source=str(item.get("dictionary_source") or ""),
        )
        for item in items
        if item.get("code") and item.get("dictionary_source")
    ]


def _zone_result(zone: PogZoneIntersection, source: SourceMetadata) -> PogZoneResult:
    return PogZoneResult(
        id=zone.zone_id or zone.symbol or zone.zone_type.value,
        symbol=zone.symbol,
        type=zone.zone_type.value,
        label=zone.label,
        area_sqm=zone.area_sqm,
        area_pct=zone.area_ratio * 100.0,
        max_overground_floor_area_ratio=_float_parameter(zone.parameters, "max_overground_floor_area_ratio"),
        max_building_height_m=_float_parameter(zone.parameters, "max_building_height_m"),
        max_building_coverage_pct=_float_parameter(zone.parameters, "max_building_coverage_pct"),
        min_biologically_active_pct=_float_parameter(zone.parameters, "min_biologically_active_pct"),
        primary_profile=_profile_results(zone.primary_profiles),
        additional_profiles=_profile_results(zone.additional_profiles),
        source=source,
        feature_version=zone.feature_version,
        gml_url=zone.gml_url,
        intersection_wkt=zone.intersection_wkt,
    )


def _area_result(item: PogAreaIntersection, source: SourceMetadata) -> PogAreaResult:
    return PogAreaResult(
        id=item.area_id,
        symbol=item.symbol,
        label=item.label,
        area_sqm=item.area_sqm,
        area_pct=item.area_pct,
        touches_boundary=item.touches_boundary,
        source=source,
        feature_version=item.feature_version,
        gml_url=item.gml_url,
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


def _area_intersections(
    parcel: BaseGeometry,
    features: list[PogVectorFeature],
    prefix: str,
) -> list[PogAreaIntersection]:
    result: list[PogAreaIntersection] = []
    for index, feature in enumerate(features):
        area = parcel.intersection(feature.geometry).area
        touches = area <= INTERSECTION_AREA_TOLERANCE_SQM and parcel.touches(feature.geometry)
        if area <= INTERSECTION_AREA_TOLERANCE_SQM and not touches:
            continue
        result.append(PogAreaIntersection(
            area_id=_first_string_attribute(
                feature.attributes,
                ("feature_id", "id_iip", "idIIP", "identifier", "oznaczenie"),
            ) or f"{prefix}-{index + 1}",
            symbol=_first_string_attribute(feature.attributes, ("symbol", "oznaczenie")),
            label=_first_string_attribute(feature.attributes, ("label", "nazwa")),
            area_sqm=area if area > INTERSECTION_AREA_TOLERANCE_SQM else 0.0,
            area_pct=(area / parcel.area * 100.0) if area > INTERSECTION_AREA_TOLERANCE_SQM else 0.0,
            touches_boundary=touches,
            feature_version=_first_string_attribute(feature.attributes, ("feature_version",)),
            gml_url=_first_string_attribute(feature.attributes, ("gml_url",)),
        ))
    return result


def _profiles_from_attributes(
    attributes: dict[str, object], key: str
) -> tuple[dict[str, str | None], ...]:
    raw = attributes.get(key)
    if not isinstance(raw, (list, tuple)):
        return ()
    result: list[dict[str, str | None]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        result.append({
            "code": str(item.get("code") or ""),
            "label": str(item["label"]) if item.get("label") is not None else None,
            "dictionary_source": str(item.get("dictionary_source") or ""),
        })
    return tuple(result)


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
        ouz_status=calculate_ouz_status(
            # Brak danych OUZ ma w wyniku nieznanym pozostać jawnie
            # nierozstrzygnięty, niezależnie od przyczyny braku analizy POG.
            MultiPolygon(),
            None,
        ),
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
