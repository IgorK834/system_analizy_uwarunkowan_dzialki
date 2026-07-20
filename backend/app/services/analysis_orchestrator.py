"""Orkiestracja pełnego, best-effort przepływu analizy działki.

Krytyczne etapy (identyfikacja działki i geometria EPSG:2180) propagują błędy
do routera. KIUT, ISOK, GDOŚ, MPZP i POG/OUZ degradują wynik do ``partial`` z
ostrzeżeniem. ``complete`` jest dozwolone wyłącznie wtedy, gdy MPZP i wszystkie
krytyczne sekcje są dostępne oraz żadne źródło nie wymaga ręcznej weryfikacji.

TODO: docelowy silnik powinien analizować lokalną, wersjonowaną bazę stref MPZP
zgodnie z ADR-004/005, a nie opierać przypisania stref wyłącznie na live
discovery KIMPZP i dokumencie uchwały.
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timezone
from typing import Literal, Sequence

from shapely.geometry.base import BaseGeometry
from sqlalchemy.orm import Session

from app.core.logging import log_analysis_event
from app.core.network_rules import load_network_rules
from app.core.settings import settings
from app.schemas.analyze import (
    AddressAnalyzeRequest,
    AnalyzeResponse,
    GeometryMetrics,
    InfrastructureResult,
    MapAnalyzeRequest,
    MpzpZoneResult,
    ParcelGeometryResponse,
    ParcelIdAnalyzeRequest,
    PogResult,
    RiskResult,
    WarningMessage,
)
from app.schemas.source import SourceMetadata, warnings_from_domain_messages
from app.services.cache import get_cached_analysis, should_refresh_analysis
from app.services.context import (
    ContextResult,
    ContextSectionResult,
    analyze_context,
)
from app.services.geojson import parcel_geometry_to_geojson
from app.services.geometry import (
    NetworkGeometryInput,
    calculate_geometry_metrics,
    calculate_network_protection_zones,
    calculate_technical_setback,
    parse_parcel_geometry,
)
from app.services.initiation import resolve_parcel
from app.services.mpzp import MpzpDiscoveryResult, discover_mpzp
from app.services.mpzp_fetch import fetch_mpzp_document
from app.services.mpzp_parser import parse_mpzp_document
from app.services.mpzp_zones import map_parser_zone_to_analyze_response
from app.services.ouz import OuzStatusResult, calculate_ouz_status
from app.services.pog import PogDiscoveryResult, PogGminaSources, discover_pog
from app.services.pog_analyzer import (
    PogAnalysisResult,
    analyze_pog_adopted,
    to_pog_result,
)
from app.services.pog_fetch import fetch_pog_vector_data
from app.services.pog_scenarios import build_pog_scenario_result
from app.services.persistence import (
    build_analyze_response_from_analysis,
    save_analysis,
)


async def run_analysis(
    request: MapAnalyzeRequest | AddressAnalyzeRequest | ParcelIdAnalyzeRequest,
    db: Session,
    force_refresh: bool = False,
) -> AnalyzeResponse:
    """Uruchamia identyfikację, cache, GIS, kontekst, MPZP, POG i zapis.

    Metryczne obliczenia oraz zapis geometrii działki odbywają się w EPSG:2180;
    WGS84 jest budowane wyłącznie dla GeoJSON odpowiedzi. Status
    ``waiting_for_user_input`` jest kontraktem API, ale w bazie pozostaje
    ``waiting_for_zone_symbol``, ponieważ na nim opiera się istniejący endpoint
    ``POST /analyze/resume``.
    """
    started = time.monotonic()
    log_analysis_event("start", method=request.method)

    lookup = await resolve_parcel(request)
    parcel_identifier = lookup.parcel_identifier
    cached = get_cached_analysis(parcel_identifier, db)
    if not should_refresh_analysis(force_refresh, cached):
        assert cached is not None
        response = build_analyze_response_from_analysis(cached, db)
        elapsed_ms = _elapsed_ms(started)
        log_analysis_event(
            "cache_hit",
            cache="hit",
            parcel_identifier=parcel_identifier,
            analysis_id=response.analysis_id,
            elapsed_ms=elapsed_ms,
        )
        log_analysis_event(
            "complete",
            parcel_identifier=parcel_identifier,
            analysis_id=response.analysis_id,
            status=response.status,
            elapsed_ms=elapsed_ms,
        )
        return response

    log_analysis_event(
        "cache_miss",
        cache="miss",
        parcel_identifier=parcel_identifier,
    )
    parcel_geometry = parse_parcel_geometry(lookup.wkt)
    metrics = calculate_geometry_metrics(parcel_geometry)
    setback = calculate_technical_setback(
        parcel_geometry,
        settings.default_technical_setback_m,
    )
    parcel_response = ParcelGeometryResponse(
        parcel_identifier=parcel_identifier,
        geometry_geojson=parcel_geometry_to_geojson(
            parcel_geometry,
            parcel_identifier,
        ),
        metrics=GeometryMetrics(
            area_sqm=metrics.area_sqm,
            area_ha=metrics.area_ha,
            perimeter_m=metrics.perimeter_m,
            is_valid=metrics.is_valid,
            geometry_repaired=metrics.geometry_repaired,
        ),
        source=lookup.source_metadata,
    )

    warnings = _geometry_warnings(metrics.repair_warning, setback.warning)
    context = await _analyze_context_safely(parcel_geometry)
    (
        infrastructure,
        risks,
        context_warnings,
        context_sources,
        buildable_area_sqm,
    ) = _map_context(
        context,
        parcel_geometry,
        setback.buildable_geometry,
    )
    warnings.extend(context_warnings)

    discovery, discovery_warnings = await _discover_mpzp_safely(parcel_geometry)
    warnings.extend(discovery_warnings)
    pog, ouz_status, pog_warnings, pog_sources = await _analyze_pog_best_effort(
        parcel_geometry,
        lookup.teryt,
    )
    warnings.extend(pog_warnings)

    sources = [lookup.source_metadata, *context_sources, *pog_sources]
    if discovery is not None:
        sources.append(discovery.source_metadata)

    if discovery is not None and discovery.brak_wektorow:
        pog, scenario_warnings = _apply_pog_scenario(pog, ouz_status, [])
        warnings.extend(scenario_warnings)
        warnings.append(
            WarningMessage(
                code="MPZP_MANUAL_ZONE_REQUIRED",
                message=(
                    "Gmina nie udostępnia wektorowych danych MPZP. Podaj symbol "
                    "strefy odczytany z mapy rastrowej i wznów analizę."
                ),
                severity="warning",
                source_name="mpzp",
            )
        )
        response = AnalyzeResponse(
            status="waiting_for_user_input",
            analyzed_at=datetime.now(timezone.utc),
            parcel=parcel_response,
            mpzp_zones=[],
            pog=pog,
            infrastructure=infrastructure,
            risks=risks,
            buildable_area_sqm=buildable_area_sqm,
            manual_zone_required=True,
            warnings=warnings,
            sources=_unique_sources(sources),
        )
        saved = save_analysis(
            response,
            parcel_identifier,
            parcel_geometry,
            db,
            context_result=context,
            database_status="waiting_for_zone_symbol",
            pending_uchwala_url=discovery.uchwala_url,
            pending_plan_id=discovery.plan_id,
            pending_zone_symbol_candidates=discovery.candidate_zone_symbols,
        )
        response = response.model_copy(update={"analysis_id": saved.id})
        _log_completion(started, parcel_identifier, response)
        return response

    mpzp_zones, mpzp_warnings, mpzp_sources, parser_status = (
        await _analyze_mpzp_best_effort(
            discovery,
            metrics.area_sqm,
            parcel_identifier,
        )
    )
    warnings.extend(mpzp_warnings)
    sources.extend(mpzp_sources)
    pog, scenario_warnings = _apply_pog_scenario(pog, ouz_status, mpzp_zones)
    warnings.extend(scenario_warnings)
    status = _result_status(
        context=context,
        mpzp_zones=mpzp_zones,
        pog=pog,
        sources=_unique_sources(sources),
    )
    response = AnalyzeResponse(
        status=status,
        analyzed_at=datetime.now(timezone.utc),
        parcel=parcel_response,
        mpzp_zones=mpzp_zones,
        pog=pog,
        infrastructure=infrastructure,
        risks=risks,
        buildable_area_sqm=buildable_area_sqm,
        manual_zone_required=False,
        warnings=warnings,
        sources=_unique_sources(sources),
    )
    saved = save_analysis(
        response,
        parcel_identifier,
        parcel_geometry,
        db,
        context_result=context,
    )
    response = response.model_copy(update={"analysis_id": saved.id})
    if parser_status is not None:
        log_analysis_event(
            "mpzp_parser_complete",
            parcel_identifier=parcel_identifier,
            parser_status=parser_status,
        )
    _log_completion(started, parcel_identifier, response)
    return response


async def _analyze_context_safely(parcel_geometry: BaseGeometry) -> ContextResult:
    try:
        return await analyze_context(parcel_geometry)
    except Exception as exc:
        log_analysis_event(
            "section_error",
            section="context",
            status=type(exc).__name__,
        )
        message = (
            "Nie udało się uruchomić analizy kontekstowej. Wszystkie sekcje "
            "KIUT, ISOK i GDOŚ wymagają ręcznej weryfikacji."
        )
        return ContextResult(
            kiut=ContextSectionResult(
                section="kiut", status="error", warnings=[message]
            ),
            isok=ContextSectionResult(
                section="isok", status="error", warnings=[message]
            ),
            gdos=ContextSectionResult(
                section="gdos", status="error", warnings=[message]
            ),
        )


async def _discover_mpzp_safely(
    parcel_geometry: BaseGeometry,
) -> tuple[MpzpDiscoveryResult | None, list[WarningMessage]]:
    try:
        discovery = await discover_mpzp(parcel_geometry)
    except Exception as exc:
        log_analysis_event(
            "section_error",
            section="mpzp_discovery",
            status=type(exc).__name__,
        )
        return None, [
            WarningMessage(
                code="MPZP_DISCOVERY_ERROR",
                message=(
                    "Nie udało się rozpoznać MPZP dla działki. Sekcja wymaga "
                    "ręcznej weryfikacji."
                ),
                severity="error",
                source_name="mpzp",
            )
        ]
    return discovery, warnings_from_domain_messages("mpzp", discovery.warnings)


async def _analyze_mpzp_best_effort(
    discovery: MpzpDiscoveryResult | None,
    parcel_area_sqm: float,
    parcel_identifier: str,
) -> tuple[
    list[MpzpZoneResult],
    list[WarningMessage],
    list[SourceMetadata],
    str | None,
]:
    if discovery is None or discovery.status == "no_mpzp":
        return [], [
            WarningMessage(
                code="MPZP_NOT_FOUND",
                message=(
                    "Nie znaleziono danych MPZP pozwalających przypisać strefę "
                    "do działki."
                ),
                severity="warning",
                source_name="mpzp",
            )
        ], [], None
    if not discovery.uchwala_url or not discovery.candidate_zone_symbols:
        return [], [
            WarningMessage(
                code="MPZP_DOCUMENT_OR_SYMBOL_MISSING",
                message=(
                    "Discovery MPZP nie zwróciło dokumentu i kandydatów symboli "
                    "potrzebnych do analizy best-effort."
                ),
                severity="warning",
                source_name="mpzp",
            )
        ], [], None

    try:
        document = await fetch_mpzp_document(discovery.uchwala_url)
        parsed = await parse_mpzp_document(
            document,
            discovery.candidate_zone_symbols,
        )
    except Exception as exc:
        # Pobieranie dokumentu jest sekcją best-effort. Poza kontrolowanymi
        # MpzpDocumentError chronimy też granicę orchestratora przed przyszłym
        # trybem awarii adaptera, nie ujawniając treści wyjątku ani URL.
        log_analysis_event(
            "section_error",
            section="mpzp_document",
            status=type(exc).__name__,
            parcel_identifier=parcel_identifier,
        )
        return [], [
            WarningMessage(
                code="MPZP_DOCUMENT_UNAVAILABLE",
                message=(
                    "Nie udało się bezpiecznie pobrać dokumentu MPZP. Pozostałe "
                    "sekcje analizy są dostępne."
                ),
                severity="warning",
                source_name="mpzp",
            )
        ], [], None

    # Sam dokument może być wiarygodny, ale przypisanie kandydata strefy nadal
    # pochodzi z punktowego discovery, nie z lokalnego przecięcia wektorowego.
    zone_source = document.source_metadata.model_copy(
        update={
            "confidence": min(
                document.source_metadata.confidence,
                discovery.source_metadata.confidence,
            ),
            "manual_review_required": True,
        }
    )
    zones: list[MpzpZoneResult] = []
    warnings = [
        WarningMessage(
            code=warning.code,
            message=warning.message,
            severity=warning.severity,
            source_name="mpzp",
        )
        for warning in parsed.warnings
    ]
    skipped: list[str] = []
    for parser_zone in parsed.zones:
        mapped, skipped_parameters = map_parser_zone_to_analyze_response(
            parser_zone,
            parcel_area_sqm,
            zone_source,
        )
        zones.append(mapped)
        skipped.extend(skipped_parameters)
    if skipped:
        warnings.append(
            WarningMessage(
                code="MPZP_PARAMETERS_NOT_IN_FLAT_CONTRACT",
                message=(
                    "Parser znalazł dodatkowe parametry bez odpowiednika w "
                    "płaskim kontrakcie API: " + ", ".join(sorted(set(skipped)))
                ),
                severity="warning",
                source_name="mpzp",
            )
        )
    if not zones:
        warnings.append(
            WarningMessage(
                code="MPZP_PARSER_EMPTY",
                message="Parser dokumentu MPZP nie zwrócił żadnej strefy.",
                severity="warning",
                source_name="mpzp",
            )
        )
    return zones, warnings, [zone_source], parsed.status


def _map_context(
    context: ContextResult,
    parcel_geometry: BaseGeometry,
    technical_buildable_geometry: BaseGeometry,
) -> tuple[
    list[InfrastructureResult],
    list[RiskResult],
    list[WarningMessage],
    list[SourceMetadata],
    float,
]:
    warnings: list[WarningMessage] = []
    sources: list[SourceMetadata] = []
    for section in (context.kiut, context.isok, context.gdos):
        severity: Literal["warning", "error"] = (
            "error" if section.status in {"unavailable", "error"} else "warning"
        )
        warnings.extend(
            warnings_from_domain_messages(
                section.section,
                section.warnings,
                severity=severity,
            )
        )
        if section.source_metadata is not None:
            sources.append(section.source_metadata)

    rules = load_network_rules()
    network_result = calculate_network_protection_zones(
        parcel=parcel_geometry,
        buildable_area=technical_buildable_geometry,
        networks=[
            NetworkGeometryInput(
                network_type=feature.network_type,
                geometry=feature.geometry,
            )
            for feature in context.kiut.data
        ],
        rules=rules,
    )
    remaining_zones = list(network_result.zones)
    warnings.extend(
        WarningMessage(
            code="NETWORK_PROTECTION_RULE_WARNING",
            message=message,
            severity="warning",
            source_name="kiut",
        )
        for message in network_result.warnings
    )
    if network_result.zones:
        warnings.append(
            WarningMessage(
                code="NETWORK_PROTECTION_APPROXIMATION",
                message=(
                    "Obszar zabudowy pomniejszono o konfigurowalne, techniczne "
                    "bufory sieci. Nie zastępują one uzgodnień z gestorami ani "
                    "wiążących stref kontrolowanych."
                ),
                severity="warning",
                source_name="kiut",
            )
        )
    infrastructure: list[InfrastructureResult] = []
    for feature in context.kiut.data:
        rule = rules.get(feature.network_type)
        if rule is None:
            warnings.append(
                WarningMessage(
                    code="KIUT_NETWORK_RULE_MISSING",
                    message=(
                        f"Brak jawnej reguły bufora dla sieci "
                        f"{feature.network_type!r}; wpis pominięto."
                    ),
                    severity="warning",
                    source_name="kiut",
                )
            )
            continue
        matching_zone = next(
            (
                zone
                for zone in remaining_zones
                if zone.network_type == feature.network_type
            ),
            None,
        )
        if matching_zone is not None:
            remaining_zones.remove(matching_zone)
        infrastructure.append(
            InfrastructureResult(
                network_type=feature.network_type,
                buffer_m=rule.default_buffer_m,
                zone_area_sqm=(
                    matching_zone.zone_area_sqm if matching_zone is not None else 0.0
                ),
                rule_source=(
                    matching_zone.source if matching_zone is not None else rule.source
                ),
                rule_confidence=(
                    matching_zone.confidence
                    if matching_zone is not None
                    else rule.confidence
                ),
                rule_note=(
                    matching_zone.note if matching_zone is not None else rule.note
                ),
                affects_buildable_area=(
                    matching_zone is not None and matching_zone.zone_area_sqm > 0.0
                ),
                source=feature.source_metadata,
            )
        )
        sources.append(feature.source_metadata)

    risks = [
        RiskResult(
            risk_type=feature.risk_type,
            description=(
                f"Ryzyko powodziowe: poziom {feature.severity}, "
                f"udział przecięcia {feature.area_ratio * 100:.2f}%."
            ),
            source=feature.source_metadata,
        )
        for feature in context.isok.data
    ]
    risks.extend(
        RiskResult(
            risk_type=(
                "natura_2000"
                if feature.protection_type == "natura2000"
                else feature.protection_type
            ),
            description=(
                "Forma ochrony przyrody "
                f"{feature.name or feature.protection_type}; "
                f"poziom {feature.severity}, "
                f"udział {feature.area_ratio * 100:.2f}%."
            ),
            source=feature.source_metadata,
        )
        for feature in context.gdos.data
    )
    sources.extend(feature.source_metadata for feature in context.isok.data)
    sources.extend(feature.source_metadata for feature in context.gdos.data)
    return (
        infrastructure,
        risks,
        warnings,
        _unique_sources(sources),
        network_result.net_buildable_area_sqm,
    )


async def _analyze_pog_best_effort(
    parcel_geometry: BaseGeometry,
    teryt: str | None,
) -> tuple[
    PogResult,
    OuzStatusResult,
    list[WarningMessage],
    list[SourceMetadata],
]:
    """Uruchamia discovery, bezpieczny fetch APP/GML i analizę POG/OUZ.

    Brak skonfigurowanego, potwierdzonego źródła pozostaje wynikiem
    ``unknown``. Nie jest mapowany na brak ograniczeń ani na sukces analizy.
    """
    try:
        discovery = await discover_pog(
            parcel_geometry,
            PogGminaSources(teryt=teryt),
        )
    except Exception as exc:
        log_analysis_event(
            "section_error",
            section="pog_discovery",
            status=type(exc).__name__,
        )
        source = SourceMetadata(
            source_name="POG_DISCOVERY",
            source_url=None,
            fetched_at=datetime.now(timezone.utc),
            confidence=0.0,
            manual_review_required=True,
        )
        ouz_status = calculate_ouz_status(parcel_geometry, None)
        return (
            _pog_from_discovery(None, source, "unknown"),
            ouz_status,
            [
                WarningMessage(
                    code="POG_DISCOVERY_ERROR",
                    message=(
                        "Nie udało się uruchomić discovery POG. Brak wyniku "
                        "nie oznacza braku ograniczeń planistycznych."
                    ),
                    severity="error",
                    source_name="pog",
                ),
                *ouz_status.warnings,
            ],
            [source],
        )

    warnings = list(discovery.warnings)
    sources = [discovery.source_metadata]
    parsed_date, date_warning = _parse_pog_date(discovery.uchwala_date)
    if date_warning is not None:
        warnings.append(date_warning)

    ouz_status = calculate_ouz_status(parcel_geometry, None)
    pog = _pog_from_discovery(discovery, discovery.source_metadata, discovery.status)
    if discovery.status == "adopted" and discovery.links:
        try:
            vector_data = await fetch_pog_vector_data(discovery.links)
            analysis = analyze_pog_adopted(parcel_geometry, vector_data)
            ouz_status = analysis.ouz_status
            raw_attributes = _pog_raw_attributes(discovery, vector_data.app_metadata, analysis)
            pog = to_pog_result(analysis).model_copy(
                update={
                    "uchwala_nr": discovery.uchwala_nr,
                    "uchwala_date": parsed_date,
                    "raw_attributes": raw_attributes,
                    "manual_review_required": (
                        analysis.source_metadata.manual_review_required
                        or ouz_status.manual_review_required
                    ),
                }
            )
            warnings.extend(analysis.warnings)
            warnings.extend(ouz_status.warnings)
            sources.append(analysis.source_metadata)
        except Exception as exc:
            # Adaptery POG są best-effort. Zachowujemy potwierdzony status aktu,
            # ale nie udajemy, że jego geometria została przeanalizowana.
            log_analysis_event(
                "section_error",
                section="pog_vector",
                status=type(exc).__name__,
            )
            warnings.append(
                WarningMessage(
                    code="POG_VECTOR_ANALYSIS_ERROR",
                    message=(
                        "Nie udało się przeanalizować danych wektorowych POG; "
                        "status aktu zachowano, a geometria wymaga weryfikacji."
                    ),
                    severity="error",
                    source_name="pog",
                )
            )
            warnings.extend(ouz_status.warnings)
    else:
        if discovery.status == "adopted":
            warnings.append(
                WarningMessage(
                    code="POG_VECTOR_LINK_MISSING",
                    message=(
                        "Discovery wskazuje uchwalony POG, ale nie zwróciło "
                        "odnośnika do APP/GML potrzebnego do analizy powierzchniowej."
                    ),
                    severity="warning",
                    source_name="pog",
                )
            )
        warnings.extend(ouz_status.warnings)

    if parsed_date is not None and pog.uchwala_date is None:
        pog = pog.model_copy(update={"uchwala_date": parsed_date})
    return pog, ouz_status, warnings, _unique_sources(sources)


def _pog_from_discovery(
    discovery: PogDiscoveryResult | None,
    source: SourceMetadata,
    status: str,
) -> PogResult:
    """Mapuje status aktu bez tworzenia pozornej geometrii POG/OUZ."""
    parsed_date, _ = _parse_pog_date(discovery.uchwala_date if discovery else None)
    return PogResult(
        status=status,
        planning_zone=None,
        zone_type=None,
        in_ouz=False,
        area_ratio=None,
        in_downtown_area=False,
        uchwala_nr=discovery.uchwala_nr if discovery else None,
        uchwala_date=parsed_date,
        manual_review_required=True,
        conflict_with_mpzp=None,
        raw_attributes=(
            _pog_raw_attributes(discovery, None, None) if discovery else None
        ),
        ouz_intersection_area_sqm=None,
        ouz_intersection_pct=None,
        touches_ouz_boundary=False,
        source=source,
    )


def _apply_pog_scenario(
    pog: PogResult,
    ouz_status: OuzStatusResult,
    mpzp_zones: list[MpzpZoneResult],
) -> tuple[PogResult, list[WarningMessage]]:
    """Dołącza wynik jawnej tabeli zgodności i scenariusz okresu przejściowego."""
    dominant_mpzp = next((zone for zone in mpzp_zones if zone.is_dominant), None)
    if dominant_mpzp is None and mpzp_zones:
        dominant_mpzp = max(
            mpzp_zones,
            key=lambda zone: zone.intersection_area_sqm,
        )
    scenario = build_pog_scenario_result(dominant_mpzp, pog, ouz_status)
    compatibility = scenario.compatibility
    conflict = (
        scenario.conflict
        if compatibility is not None
        and compatibility.result in {"compatible", "incompatible"}
        else None
    )
    raw_attributes = dict(pog.raw_attributes or {})
    raw_attributes["scenario"] = {
        "message": scenario.message,
        "legal_disclaimer": scenario.legal_disclaimer,
        "conflict_uncertain": scenario.conflict_uncertain,
        "compatibility": (
            {
                "result": compatibility.result,
                "reasoning": compatibility.reasoning,
                "confidence": compatibility.confidence,
            }
            if compatibility is not None
            else None
        ),
    }
    return (
        pog.model_copy(
            update={
                "conflict_with_mpzp": conflict,
                "manual_review_required": (
                    pog.manual_review_required or scenario.manual_review_required
                ),
                "raw_attributes": _json_safe(raw_attributes),
            }
        ),
        scenario.warnings,
    )


def _pog_raw_attributes(
    discovery: PogDiscoveryResult,
    app_metadata: dict[str, object] | None,
    analysis: PogAnalysisResult | None,
) -> dict[str, object]:
    """Zachowuje dane wejściowe POG bez geometrii i bez obiektów ORM."""
    payload: dict[str, object] = {
        "discovery": {
            "status": discovery.status,
            "uchwala_nr": discovery.uchwala_nr,
            "uchwala_date": discovery.uchwala_date,
            "links": discovery.links,
            "is_discovery_only": discovery.is_discovery_only,
            "layers": {
                name: {
                    "status": section.status,
                    "matched_wms_layer": section.matched_wms_layer,
                    "feature_count": section.feature_count,
                }
                for name, section in (
                    ("planning_act", discovery.planning_act),
                    ("downtown_area", discovery.downtown_area),
                    ("ouz", discovery.ouz),
                    ("planning_zones", discovery.planning_zones),
                )
            },
        }
    }
    if app_metadata is not None:
        payload["app_metadata"] = app_metadata
    if analysis is not None:
        payload["zone_intersections"] = [
            {
                "zone_type": zone.zone_type.value,
                "source_zone_type": zone.source_zone_type,
                "area_sqm": zone.area_sqm,
                "area_ratio": zone.area_ratio,
                "parameters": zone.parameters,
                "parameters_informational": zone.parameters_informational,
            }
            for zone in analysis.zones
        ]
        payload["ouz"] = {
            "status": analysis.ouz_status.status,
            "in_ouz": analysis.ouz_status.in_ouz,
            "intersection_area_sqm": analysis.ouz_status.intersection_area_sqm,
            "area_ratio_pct": analysis.ouz_status.area_ratio,
            "touches_boundary": analysis.ouz_status.touches_ouz_boundary,
            "legal_disclaimer": analysis.ouz_status.legal_disclaimer,
        }
    return _json_safe(payload)


def _parse_pog_date(raw_value: str | None) -> tuple[date | None, WarningMessage | None]:
    if not raw_value:
        return None, None
    try:
        return date.fromisoformat(raw_value), None
    except ValueError:
        try:
            return datetime.fromisoformat(raw_value.replace("Z", "+00:00")).date(), None
        except ValueError:
            return None, WarningMessage(
                code="POG_UCHWALA_DATE_INVALID",
                message=(
                    "Źródło POG zwróciło datę uchwały w nierozpoznanym formacie; "
                    "wartość zachowano wyłącznie w surowych danych audytowych."
                ),
                severity="warning",
                source_name="pog",
            )


def _json_safe(value: dict[str, object]) -> dict[str, object]:
    """Normalizuje wartości źródłowe do typów obsługiwanych przez JSONB."""
    normalized: dict[str, object] = json.loads(
        json.dumps(value, default=str, ensure_ascii=False)
    )
    return normalized


def _geometry_warnings(
    repair_warning: str | None,
    setback_warning: str | None,
) -> list[WarningMessage]:
    warnings: list[WarningMessage] = []
    if repair_warning:
        warnings.append(
            WarningMessage(
                code="PARCEL_GEOMETRY_REPAIRED",
                message=repair_warning,
                severity="warning",
                source_name="parcel",
            )
        )
    if setback_warning:
        warnings.append(
            WarningMessage(
                code="TECHNICAL_SETBACK_WARNING",
                message=setback_warning,
                severity="warning",
                source_name="geometry",
            )
        )
    return warnings


def _result_status(
    *,
    context: ContextResult,
    mpzp_zones: list[MpzpZoneResult],
    pog: PogResult | None,
    sources: list[SourceMetadata],
) -> str:
    critical_context_available = all(
        section.status == "available"
        for section in (context.kiut, context.isok, context.gdos)
    )
    if not mpzp_zones or pog is None or pog.status != "adopted":
        return "partial"
    if not critical_context_available:
        return "partial"
    if any(source.manual_review_required for source in sources):
        return "partial"
    return "complete"


def _unique_sources(
    sources: Sequence[SourceMetadata | None],
) -> list[SourceMetadata]:
    unique: list[SourceMetadata] = []
    keys: set[tuple[str, str | None, datetime | None]] = set()
    for source in sources:
        if source is None:
            continue
        key = (source.source_name, source.source_url, source.fetched_at)
        if key not in keys:
            keys.add(key)
            unique.append(source)
    return unique


def _log_completion(
    started: float,
    parcel_identifier: str,
    response: AnalyzeResponse,
) -> None:
    log_analysis_event(
        "complete",
        parcel_identifier=parcel_identifier,
        analysis_id=response.analysis_id,
        status=response.status,
        elapsed_ms=_elapsed_ms(started),
    )


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)
