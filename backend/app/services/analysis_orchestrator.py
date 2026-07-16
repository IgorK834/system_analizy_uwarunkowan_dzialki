"""Orkiestracja pełnego, best-effort przepływu analizy działki.

Krytyczne etapy (identyfikacja działki i geometria EPSG:2180) propagują błędy
do routera. KIUT, ISOK, GDOŚ, MPZP i tymczasowy POG degradują wynik do
``partial`` z ostrzeżeniem. ``complete`` jest dozwolone wyłącznie wtedy, gdy
MPZP i wszystkie krytyczne sekcje są dostępne oraz żadne źródło nie wymaga
ręcznej weryfikacji. Przy obecnym stubie POG wynik jest więc świadomie
``partial``.

TODO: docelowy silnik powinien analizować lokalną, wersjonowaną bazę stref MPZP
zgodnie z ADR-004/005, a nie opierać przypisania stref wyłącznie na live
discovery KIMPZP i dokumencie uchwały.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

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
    calculate_geometry_metrics,
    calculate_technical_setback,
    parse_parcel_geometry,
)
from app.services.initiation import resolve_parcel
from app.services.mpzp import MpzpDiscoveryResult, discover_mpzp
from app.services.mpzp_fetch import fetch_mpzp_document
from app.services.mpzp_parser import parse_mpzp_document
from app.services.mpzp_zones import map_parser_zone_to_analyze_response
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
    infrastructure, risks, context_warnings, context_sources = _map_context(context)
    warnings.extend(context_warnings)

    discovery, discovery_warnings = await _discover_mpzp_safely(parcel_geometry)
    warnings.extend(discovery_warnings)
    pog = _analyze_pog_stub(parcel_geometry)
    warnings.append(_pog_not_implemented_warning())

    sources = [lookup.source_metadata, *context_sources, pog.source]
    if discovery is not None:
        sources.append(discovery.source_metadata)

    if discovery is not None and discovery.brak_wektorow:
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
            buildable_area_sqm=setback.buildable_area_sqm,
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
        buildable_area_sqm=setback.buildable_area_sqm,
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
) -> tuple[
    list[InfrastructureResult],
    list[RiskResult],
    list[WarningMessage],
    list[SourceMetadata],
]:
    warnings: list[WarningMessage] = []
    sources: list[SourceMetadata] = []
    for section in (context.kiut, context.isok, context.gdos):
        severity = "error" if section.status in {"unavailable", "error"} else "warning"
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
        infrastructure.append(
            InfrastructureResult(
                network_type=feature.network_type,
                buffer_m=rule.default_buffer_m,
                source=feature.source_metadata,
            )
        )
        sources.append(feature.source_metadata)

    risks = [
        RiskResult(
            risk_type=feature.risk_type,
            description=(
                f"Ryzyko powodziowe: poziom {feature.severity}, "
                f"udział przecięcia {feature.area_ratio:.2f}%."
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
                f"poziom {feature.severity}, udział {feature.area_ratio:.2f}%."
            ),
            source=feature.source_metadata,
        )
        for feature in context.gdos.data
    )
    sources.extend(feature.source_metadata for feature in context.isok.data)
    sources.extend(feature.source_metadata for feature in context.gdos.data)
    return infrastructure, risks, warnings, _unique_sources(sources)


def _analyze_pog_stub(_parcel_geometry: BaseGeometry) -> PogResult:
    """Jawnie sygnalizuje brak klienta POG/OUZ bez udawania sukcesu."""
    return PogResult(
        status="unavailable",
        planning_zone=None,
        ouz_intersection_area_sqm=None,
        ouz_intersection_pct=None,
        touches_ouz_boundary=False,
        source=SourceMetadata(
            source_name="POG",
            source_url=None,
            fetched_at=datetime.now(timezone.utc),
            confidence=0.0,
            manual_review_required=True,
        ),
    )


def _pog_not_implemented_warning() -> WarningMessage:
    return WarningMessage(
        code="POG_NOT_IMPLEMENTED",
        message=(
            "Analiza POG i OUZ nie jest jeszcze zaimplementowana. Brak danych "
            "nie oznacza braku ograniczeń planistycznych."
        ),
        severity="warning",
        source_name="pog",
    )


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
    if not mpzp_zones or pog is None or pog.status == "unavailable":
        return "partial"
    if not critical_context_available:
        return "partial"
    if any(source.manual_review_required for source in sources):
        return "partial"
    return "complete"


def _unique_sources(
    sources: list[SourceMetadata | None],
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
