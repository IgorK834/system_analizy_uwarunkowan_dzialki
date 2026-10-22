"""Strukturalne wyniki powodzi (ISOK) i ochrony przyrody (GDOŚ) — BK-303.

Pola domenowe adapterów są przenoszone do kontraktu bez strat. ``description``
jest wyłącznie tekstem prezentacyjnym budowanym z tych pól, a status sekcji
(``available``/``unavailable``/``error``/``unknown``) jest niezależny od listy
obiektów — pusta lista przy awarii nigdy nie wygląda jak „brak ryzyka”.

Wszystkie pola i udziały liczone są w EPSG:2180. Pole sumy sekcji to pole
sumy mnogościowej przecięć, więc nakładające się formy ochrony (np. park
krajobrazowy i Natura 2000) nie dają pokrycia większego niż 100%.
"""

from __future__ import annotations

from typing import Any, Final, Iterable, Literal, Sequence

from shapely import union_all
from shapely.geometry.base import BaseGeometry

from app.schemas.analyze import (
    PreliminaryFloodRiskAssessment,
    RiskProximityResult,
    RiskResult,
    RiskSectionResult,
)
from app.schemas.source import SourceMetadata
from app.services.context import ContextSectionResult
from app.services.gdos import NatureProtectionFeature
from app.services.geojson import analysis_layer_geometry_to_geojson
from app.services.isok import RiskFeature

REASON_UNEXPECTED_ERROR: Final[str] = "UNEXPECTED_ERROR"
REASON_LEGACY_SNAPSHOT: Final[str] = "LEGACY_SNAPSHOT"
LEGACY_RISK_WARNING: Final[str] = (
    "Zapis sprzed BK-303 nie zawiera statusu sekcji — brak obiektów nie oznacza potwierdzonego braku ryzyka."
)
_SECTION_SOURCES: Final[dict[str, tuple[str, str]]] = {
    "flood": ("isok", "ISOK"),
    "nature": ("gdos", "GDOŚ"),
}
_PROTECTION_LABELS: Final[dict[str, str]] = {
    "park_narodowy": "park narodowy",
    "rezerwat_przyrody": "rezerwat przyrody",
    "natura2000": "obszar Natura 2000",
    "park_krajobrazowy": "park krajobrazowy",
    "obszar_chronionego_krajobrazu": "obszar chronionego krajobrazu",
    "uzytek_ekologiczny": "użytek ekologiczny",
    "zespol_przyrodniczo_krajobrazowy": "zespół przyrodniczo-krajobrazowy",
    "stanowisko_dokumentacyjne": "stanowisko dokumentacyjne",
    "pomnik_przyrody": "pomnik przyrody",
    "obszar_ramsar": "obszar Ramsar",
    "korytarz_ekologiczny": "korytarz ekologiczny",
    "elektrownia_wiatrowa": "elektrownia wiatrowa",
    "unknown": "forma ochrony o nieustalonym typie",
}
_SEVERITY_LABELS: Final[dict[str, str]] = {
    "low": "niski",
    "medium": "średni",
    "high": "wysoki",
}
_ROUND_AREA: Final[int] = 3
_ROUND_PCT: Final[int] = 4


def flood_risk_result(feature: RiskFeature) -> RiskResult:
    """Strefa zagrożenia powodziowego z wszystkimi polami domenowymi."""
    is_near = feature.proximity_status == "near"
    area = None if is_near else round(feature.intersection_area_sqm, _ROUND_AREA)
    pct = None if is_near else _pct(feature.area_ratio)
    return RiskResult(
        risk_type=feature.risk_type,
        section="flood",
        feature_id=feature.feature_id,
        severity=feature.severity,  # type: ignore[arg-type]
        probability_class=feature.probability_class,
        return_period_years=feature.return_period_years,
        intersection_area_sqm=area,
        intersection_pct=pct,
        touches_boundary=False if is_near else feature.touches_boundary,
        proximity_status=feature.proximity_status,  # type: ignore[arg-type]
        distance_m=round(feature.distance_m, 3),
        search_radius_m=feature.search_radius_m,
        layer_name=feature.layer_name,
        description=_with_proximity(
            flood_description(
                feature.probability_class,
                feature.return_period_years,
                feature.severity,
                area,
                pct,
                False if is_near else feature.touches_boundary,
            ),
            feature.proximity_status,
            feature.distance_m,
        ),
        geometry_geojson=analysis_layer_geometry_to_geojson(
            feature.geometry,
            "risk",
            {
                "risk_type": feature.risk_type,
                "severity": feature.severity,
                "feature_id": feature.feature_id,
                "proximity_status": feature.proximity_status,
                "distance_m": round(feature.distance_m, 3),
                "search_radius_m": feature.search_radius_m,
                "layer_name": feature.layer_name,
            },
        ),
        warnings=list(feature.warnings),
        source=feature.source_metadata,
    )


def nature_risk_result(feature: NatureProtectionFeature) -> RiskResult:
    """Forma ochrony przyrody z typem, nazwą i polami przecięcia."""
    risk_type = "natura_2000" if feature.protection_type == "natura2000" else feature.protection_type
    is_near = feature.proximity_status == "near"
    area = None if is_near else round(feature.intersection_area_sqm, _ROUND_AREA)
    pct = None if is_near else _pct(feature.area_ratio)
    return RiskResult(
        risk_type=risk_type,
        section="nature",
        feature_id=feature.feature_id,
        severity=feature.severity,  # type: ignore[arg-type]
        protection_type=feature.protection_type,
        name=feature.name,
        intersection_area_sqm=area,
        intersection_pct=pct,
        touches_boundary=False if is_near else feature.touches_boundary,
        proximity_status=feature.proximity_status,  # type: ignore[arg-type]
        distance_m=round(feature.distance_m, 3),
        search_radius_m=feature.search_radius_m,
        layer_name=feature.layer_name,
        informational_only=feature.informational_only,
        description=_with_proximity(
            nature_description(
                feature.protection_type,
                feature.name,
                feature.severity,
                area,
                pct,
                False if is_near else feature.touches_boundary,
            ),
            feature.proximity_status,
            feature.distance_m,
        ),
        geometry_geojson=analysis_layer_geometry_to_geojson(
            feature.geometry,
            "risk",
            {
                "risk_type": risk_type,
                "severity": feature.severity,
                "name": feature.name,
                "feature_id": feature.feature_id,
                "proximity_status": feature.proximity_status,
                "distance_m": round(feature.distance_m, 3),
                "search_radius_m": feature.search_radius_m,
                "layer_name": feature.layer_name,
                "informational_only": feature.informational_only,
            },
        ),
        warnings=list(feature.warnings),
        source=feature.source_metadata,
    )


def build_risk_section(
    name: str,
    context_section: ContextSectionResult,
    parcel_geometry: BaseGeometry,
) -> RiskSectionResult:
    """Status, relacja i provenance sekcji z wyniku kontekstu."""
    if context_section.status != "available":
        reason = (
            REASON_UNEXPECTED_ERROR
            if context_section.status == "error"
            else context_section.reason_code or REASON_UNEXPECTED_ERROR
        )
        return RiskSectionResult(
            section=name,  # type: ignore[arg-type]
            status=context_section.status,
            reason_code=reason,
            relation="unknown",
            source=context_section.source_metadata or _attempt_source(name),
            warnings=list(context_section.warnings),
            preliminary_assessment=(_worp_contract_required() if name == "flood" else None),
        )

    features: Sequence[Any] = context_section.data
    intersecting = [item for item in features if item.proximity_status == "intersection" and not item.touches_boundary]
    boundary = [item for item in features if item.proximity_status == "intersection" and item.touches_boundary]
    near = [item for item in features if item.proximity_status == "near"]
    union_area = _union_area(item.geometry for item in intersecting)
    parcel_area = parcel_geometry.area
    union_pct = min(100.0, round(100.0 * union_area / parcel_area, _ROUND_PCT)) if parcel_area > 0 else 0.0
    relation: Literal["none_within_radius", "near", "intersection", "boundary_only"] = (
        "intersection" if intersecting else "boundary_only" if boundary else "near" if near else "none_within_radius"
    )
    proximities = [_proximity_result(item) for item in context_section.proximities]
    measured_distances = [item.distance_m for item in proximities if item.distance_m is not None]
    return RiskSectionResult(
        section=name,  # type: ignore[arg-type]
        status="available",
        relation=relation,
        feature_count=len(features),
        intersecting_feature_count=len(intersecting),
        boundary_feature_count=len(boundary),
        near_feature_count=len(near),
        union_intersection_area_sqm=round(union_area, _ROUND_AREA),
        union_intersection_pct=union_pct,
        feature_ids=[item.feature_id for item in features if item.feature_id],
        nearest_distance_m=(round(min(measured_distances), 3) if measured_distances else None),
        proximities=proximities,
        preliminary_assessment=(_worp_contract_required() if name == "flood" else None),
        source=context_section.source_metadata or _attempt_source(name),
        warnings=list(context_section.warnings),
    )


def risk_sections_from_snapshot(raw: list[dict[str, Any]] | None) -> list[RiskSectionResult]:
    """Odczyt historyczny: brak zapisu sekcji to ``unknown`` dla obu źródeł."""
    if raw is None:
        return [
            RiskSectionResult(
                section=name,  # type: ignore[arg-type]
                status="unknown",
                reason_code=REASON_LEGACY_SNAPSHOT,
                relation="unknown",
                warnings=[LEGACY_RISK_WARNING],
            )
            for name in ("flood", "nature")
        ]
    return [RiskSectionResult.model_validate(item) for item in raw]


def flood_description(
    probability_class: str | None,
    return_period_years: int | None,
    severity: str | None,
    area_sqm: float | None,
    pct: float | None,
    touches_boundary: bool | None,
) -> str:
    parts = [
        "Strefa zagrożenia powodziowego",
        probability_class or "klasa prawdopodobieństwa nieustalona",
    ]
    if return_period_years is not None:
        parts.append(f"okres powtarzalności {return_period_years} lat (ze źródła)")
    return _with_measurement(parts, severity, area_sqm, pct, touches_boundary)


def nature_description(
    protection_type: str | None,
    name: str | None,
    severity: str | None,
    area_sqm: float | None,
    pct: float | None,
    touches_boundary: bool | None,
) -> str:
    label = _PROTECTION_LABELS.get(protection_type or "unknown", protection_type or "")
    parts = [f"Forma ochrony przyrody: {label}"]
    if name:
        parts.append(f"„{name}”")
    return _with_measurement(parts, severity, area_sqm, pct, touches_boundary)


def _with_measurement(
    parts: list[str],
    severity: str | None,
    area_sqm: float | None,
    pct: float | None,
    touches_boundary: bool | None,
) -> str:
    if severity:
        parts.append(f"poziom {_SEVERITY_LABELS.get(severity, severity)}")
    if touches_boundary:
        parts.append("wyłącznie styk z granicą działki (bez wspólnej powierzchni)")
    elif area_sqm is not None and pct is not None:
        parts.append(f"przecięcie {_pl(area_sqm, 2)} m² ({_pl(pct, 2)}% działki)")
    return "; ".join(parts) + "."


def _with_proximity(
    description: str,
    status: str,
    distance_m: float,
) -> str:
    if status != "near":
        return description
    return f"{description.rstrip('.')} Najbliższy obiekt: {_pl(distance_m, 1)} m od granicy działki."


def _proximity_result(item: Any) -> RiskProximityResult:
    return RiskProximityResult(
        category=item.category,
        label=item.label,
        layer_name=item.layer_name,
        status=item.status,
        search_radius_m=item.search_radius_m,
        distance_m=(round(item.distance_m, 3) if item.distance_m is not None else None),
        feature_id=item.feature_id,
        name=item.name,
        probability_class=item.probability_class,
        return_period_years=item.return_period_years,
        informational_only=item.informational_only,
    )


def _worp_contract_required() -> PreliminaryFloodRiskAssessment:
    return PreliminaryFloodRiskAssessment(
        status="contract_required",
        reason_code="WORP_CONTRACT_NOT_CONFIRMED",
        source_layers=["nz-core:RiskZone", "nz-core:ExposedElement"],
        note=(
            "Warstwy INSPIRE opisują strefę ryzyka i elementy narażone, ale ich "
            "semantyka nie została potwierdzona jako produkt WORP porównywalny z "
            "oceną OnGeo. Ocena nie jest wyliczana."
        ),
    )


def _union_area(geometries: Iterable[BaseGeometry]) -> float:
    items = [geometry for geometry in geometries if not geometry.is_empty]
    if not items:
        return 0.0
    return float(union_all(items).area)


def _pct(area_ratio: float) -> float:
    return min(100.0, round(area_ratio * 100.0, _ROUND_PCT))


def _pl(value: float, digits: int) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def _attempt_source(name: str) -> SourceMetadata:
    source_id, source_name = _SECTION_SOURCES[name]
    return SourceMetadata(
        source_id=source_id,
        source_name=source_name,
        confidence=0.0,
        manual_review_required=True,
    )
