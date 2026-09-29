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

import asyncio
import json
import time
from datetime import date, datetime, timezone
from typing import Literal, Sequence

from shapely import from_wkt, make_valid
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
    PogActResult,
    PogResult,
    RiskResult,
    UtilitiesPreviewResult,
    WarningMessage,
)
from app.schemas.source import SourceMetadata, warnings_from_domain_messages
from app.services.cache import get_cached_analysis, should_refresh_analysis
from app.services.context import (
    ContextResult,
    ContextSectionResult,
    analyze_context,
)
from app.services.geojson import (
    analysis_layer_geometry_to_geojson,
    buildable_area_geometry_to_geojson,
    parcel_geometry_to_geojson,
)
from app.services.geometry import (
    NetworkGeometryInput,
    calculate_geometry_metrics,
    calculate_network_protection_zones,
    calculate_technical_setback,
    parse_parcel_geometry,
)
from app.services.initiation import resolve_parcel
from app.services.kiut_coverage import (
    check_kiut_coverage_for_geometry,
    unknown_kiut_coverage_result,
)
from app.services.mpzp import MpzpDiscoveryResult, discover_mpzp
from app.services.mpzp_fetch import DocumentBlob, fetch_mpzp_document
from app.schemas.mpzp import MpzpParseResult
from app.services.mpzp_parser import parse_mpzp_document
from app.services.mpzp_zones import (
    DocumentEvidenceContext,
    VectorZoneAssessment,
    apply_parser_zone,
    assess_vector_zones,
    cap_fallback_zone,
    legal_unit_evidence_from_snapshot,
    map_parser_zone_to_analyze_response,
)
from app.services.ouz import OuzStatusResult, calculate_ouz_status
from app.services.pog import PogDiscoveryResult, PogGminaSources, discover_pog
from app.services.pog_analyzer import (
    PogAnalysisResult,
    analyze_pog_vectors,
    evidence_result,
    spatial_feature_count,
    to_pog_result,
    zones_cover_parcel,
)
from app.services.pog_fetch import fetch_pog_vector_data
from app.services.pog_fetch import PogVectorData, PogVectorFeature
from app.services.pog_provenance import act_result_from_provenance, feature_gml_url
from app.services.pog_scenarios import build_pog_scenario_result
from app.services.risks import (
    build_risk_section,
    flood_risk_result,
    nature_risk_result,
)
from app.services.terrain import (
    REASON_UNEXPECTED_ERROR,
    build_terrain_result,
    terrain_sources,
    terrain_warnings,
)
from app.services.persistence import (
    build_analyze_response_from_analysis,
    build_manual_zone_context,
    save_analysis,
)
from app.modules.analysis.application.terrain import ReliefOutcome
from app.modules.analysis.composition import analyze_terrain_relief
from app.modules.documents.composition import (
    build_ocr_provider,
    persist_parser_audit,
)
from app.modules.imports.infrastructure.repository import (
    active_pog_release,
    find_mpzp_zone_intersections,
    find_pog_acts_for_parcel,
    last_confirmed_pog_status,
    load_pog_act_provenance,
    load_pog_release_features,
)
from app.shared.geometry import GeometryPayload
from app.shared.provenance import Provenance
from app.shared.planning_status import (
    ConfirmedPogStatus,
    PogStatusDecision,
    PogStatusObservation,
    StatusEvidence,
    canonical_legal_status,
    is_official_status_code,
    resolve_pog_status,
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
    # Sesja SQLAlchemy jest synchroniczna, więc każde jej użycie biegnie w wątku
    # roboczym (sekwencyjnie, nigdy równolegle) i nie blokuje pętli zdarzeń.
    cached = await asyncio.to_thread(get_cached_analysis, parcel_identifier, db)
    if not should_refresh_analysis(force_refresh, cached):
        assert cached is not None
        response = await asyncio.to_thread(
            build_analyze_response_from_analysis, cached, db
        )
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
        # Techniczne odsunięcie od granicy, nie ostateczna linia zabudowy z
        # MPZP. Gdy bufor ujemny zredukował obszar do zera, buildable_geometry
        # jest pustą geometrią i nie ma sensu wysyłać jej jako warstwy mapowej.
        buildable_area_geojson=(
            buildable_area_geometry_to_geojson(
                setback.buildable_geometry,
                setback.setback_m,
            )
            if not setback.buildable_geometry.is_empty
            and setback.buildable_area_sqm > 0.0
            else None
        ),
    )

    warnings = _geometry_warnings(metrics.repair_warning, setback.warning)
    context, utilities_preview, relief = await asyncio.gather(
        _analyze_context_safely(parcel_geometry),
        _check_kiut_coverage_safely(parcel_geometry, lookup.teryt),
        _analyze_terrain_relief_safely(parcel_geometry),
    )
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
    # Rzeźba terenu jest sekcją informacyjną (BK-301/302): nie wpływa na status,
    # ale jej provenance — także nieudanej próby — trafia do rejestru źródeł.
    terrain = build_terrain_result(context.nmt, relief)
    warnings.extend(terrain_warnings(terrain))
    # Status i provenance sekcji ryzyka niezależnie od listy obiektów (BK-303).
    risk_sections = [
        build_risk_section("flood", context.isok, parcel_geometry),
        build_risk_section("nature", context.gdos, parcel_geometry),
    ]

    # Chwila przypięcia wersji aktów MPZP: ten sam ``as_of`` wyznacza zestaw
    # wydzieleń dla całej analizy, a zapisany snapshot nie zależy od
    # późniejszego przełączenia wydania.
    mpzp_as_of = datetime.now(timezone.utc)
    vector, vector_warnings = await asyncio.to_thread(
        _assess_mpzp_vectors_safely, db, parcel_geometry, mpzp_as_of
    )
    warnings.extend(vector_warnings)
    discovery, discovery_warnings = await _discover_mpzp_safely(parcel_geometry)
    warnings.extend(discovery_warnings)
    pog, ouz_status, pog_warnings, pog_sources = await _analyze_pog_best_effort(
        parcel_geometry,
        lookup.teryt,
        db,
    )
    warnings.extend(pog_warnings)

    sources = [
        lookup.source_metadata,
        utilities_preview.source,
        *context_sources,
        *pog_sources,
    ]
    if discovery is not None:
        sources.append(discovery.source_metadata)

    has_vector_zones = vector is not None and bool(vector.positive_zones)
    if not has_vector_zones and discovery is not None and discovery.brak_wektorow:
        pog, scenario_warnings = _apply_pog_scenario(
            pog, ouz_status, [], as_of=mpzp_as_of, parcel_area_sqm=metrics.area_sqm
        )
        warnings.extend(scenario_warnings)
        pending_document, pending_warnings = await _pin_pending_document(
            discovery.uchwala_url, parcel_identifier
        )
        warnings.extend(pending_warnings)
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
            utilities_preview=utilities_preview,
            risks=risks,
            risk_sections=risk_sections,
            terrain=terrain,
            buildable_area_sqm=buildable_area_sqm,
            manual_zone_required=True,
            warnings=warnings,
            sources=_unique_sources([*sources, *terrain_sources(terrain)]),
        )
        saved = await asyncio.to_thread(
            save_analysis,
            response,
            parcel_identifier,
            parcel_geometry,
            db,
            context_result=context,
            database_status="waiting_for_zone_symbol",
            pending_uchwala_url=discovery.uchwala_url,
            pending_plan_id=discovery.plan_id,
            pending_zone_symbol_candidates=discovery.candidate_zone_symbols,
            pending_document=pending_document,
        )
        # ``pending_document`` jest ładowany leniwie — zapytanie także w wątku.
        manual_zone_context = await asyncio.to_thread(build_manual_zone_context, saved)
        response = response.model_copy(
            update={
                "analysis_id": saved.id,
                "manual_zone_context": manual_zone_context,
            }
        )
        _log_completion(started, parcel_identifier, response)
        return response

    if has_vector_zones:
        assert vector is not None
        mpzp_zones, mpzp_warnings, mpzp_sources, parser_status = (
            await _analyze_mpzp_vector_zones(vector, discovery, parcel_identifier, db)
        )
        mpzp_complete = vector.complete_coverage and vector.overlap_pct <= 0.1
    else:
        mpzp_zones, mpzp_warnings, mpzp_sources, parser_status = (
            await _analyze_mpzp_best_effort(
                discovery,
                metrics.area_sqm,
                parcel_identifier,
                db,
            )
        )
        mpzp_complete = False
        if mpzp_zones:
            mpzp_zones = [
                cap_fallback_zone(zone, assignment_method="document_candidate")
                for zone in mpzp_zones
            ]
            mpzp_sources = [
                source.model_copy(
                    update={
                        "confidence": min(source.confidence, 0.5),
                        "manual_review_required": True,
                    }
                )
                for source in mpzp_sources
            ]
            mpzp_warnings.append(
                WarningMessage(
                    code="MPZP_VECTOR_UNAVAILABLE",
                    message=(
                        "Brak wiarygodnego wektora wydzieleń MPZP dla działki. "
                        "Strefę przypisano z punktowego discovery i dokumentu, "
                        "z obniżoną pewnością — wymaga weryfikacji."
                    ),
                    severity="warning",
                    source_name="mpzp",
                )
            )
    warnings.extend(mpzp_warnings)
    sources.extend(mpzp_sources)
    pog, scenario_warnings = _apply_pog_scenario(
        pog, ouz_status, mpzp_zones, as_of=mpzp_as_of, parcel_area_sqm=metrics.area_sqm
    )
    warnings.extend(scenario_warnings)
    status = _result_status(
        context=context,
        mpzp_zones=mpzp_zones,
        pog=pog,
        sources=_unique_sources(sources),
        mpzp_complete=mpzp_complete,
    )
    response = AnalyzeResponse(
        status=status,
        analyzed_at=datetime.now(timezone.utc),
        parcel=parcel_response,
        mpzp_zones=mpzp_zones,
        pog=pog,
        infrastructure=infrastructure,
        utilities_preview=utilities_preview,
        risks=risks,
        risk_sections=risk_sections,
        terrain=terrain,
        buildable_area_sqm=buildable_area_sqm,
        manual_zone_required=False,
        warnings=warnings,
        sources=_unique_sources([*sources, *terrain_sources(terrain)]),
    )
    saved = await asyncio.to_thread(
        save_analysis,
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
            "KIUT, ISOK, GDOŚ i NMT wymagają ręcznej weryfikacji."
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
            nmt=ContextSectionResult(
                section="nmt", status="error", warnings=[message]
            ),
        )


async def _analyze_terrain_relief_safely(
    parcel_geometry: BaseGeometry,
) -> ReliefOutcome | None:
    """Pochodne rastra NMT (BK-302); ``None``, gdy funkcja jest wyłączona.

    Sekcja jest informacyjna — każdy błąd kończy się jawnym statusem
    ``unavailable`` z provenance próby i nigdy nie przerywa analizy.
    """
    if not settings.terrain_relief_enabled:
        return None
    try:
        return await analyze_terrain_relief(parcel_geometry)
    except Exception as exc:
        log_analysis_event(
            "section_error",
            section="terrain_relief",
            status=type(exc).__name__,
        )
        return ReliefOutcome(
            status="unavailable",
            reason_code=REASON_UNEXPECTED_ERROR,
            provenance=Provenance(
                source_id="nmt_wcs",
                fetched_at=datetime.now(timezone.utc),
                operation="WCS:GetCoverage",
                complete=False,
                error_code=type(exc).__name__,
            ),
            warnings=(
                "Wystąpił nieoczekiwany błąd analizy rastra NMT — spadku i "
                "ekspozycji nie policzono.",
            ),
        )


async def _check_kiut_coverage_safely(
    parcel_geometry: BaseGeometry,
    county_teryt: str | None,
) -> UtilitiesPreviewResult:
    try:
        return await check_kiut_coverage_for_geometry(
            parcel_geometry,
            county_teryt=county_teryt,
        )
    except Exception as exc:
        # Pokrycie jest sekcją prezentacyjną. Błąd konfiguracji lub parsera nie
        # może przerwać analizy i nigdy nie może być przedstawiony jako brak
        # publikacji danych przez powiat.
        log_analysis_event(
            "section_error",
            section="kiut_coverage",
            status=type(exc).__name__,
        )
        return unknown_kiut_coverage_result()


async def _pin_pending_document(
    document_url: str | None,
    parcel_identifier: str,
) -> tuple[DocumentBlob | None, list[WarningMessage]]:
    """Pobiera uchwałę w chwili wstrzymania, aby resume nie sięgało do sieci.

    Przypięty artefakt (bajty + SHA-256 + wersja dokumentu) jest jedynym
    wejściem parsera przy wznowieniu, więc inna uchwała opublikowana później
    pod tym samym URL nie zmieni wyniku (BK-204). Błąd pobrania nie blokuje
    wstrzymania — zostaje jawnie opisany, a parametry pozostaną nieustalone.
    """
    if not document_url:
        return None, []
    try:
        return await fetch_mpzp_document(document_url), []
    except Exception as exc:
        log_analysis_event(
            "section_error",
            section="mpzp_pending_document",
            status=type(exc).__name__,
            parcel_identifier=parcel_identifier,
        )
        return None, [
            WarningMessage(
                code="MPZP_PENDING_DOCUMENT_UNAVAILABLE",
                message=(
                    "Nie udało się pobrać i przypiąć dokumentu uchwały przy "
                    "wstrzymaniu analizy. Po podaniu symbolu parametry strefy "
                    "pozostaną nieustalone; system nie pobierze dokumentu ponownie."
                ),
                severity="warning",
                source_name="mpzp",
            )
        ]


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


def _assess_mpzp_vectors_safely(
    db: Session,
    parcel_geometry: BaseGeometry,
    as_of: datetime,
) -> tuple[VectorZoneAssessment | None, list[WarningMessage]]:
    """Przecięcia pełnego obrysu działki z wersjonowanymi wydzieleniami MPZP."""
    # Zapytanie dostaje geometrię naprawioną (make_valid); ostrzeżenie o
    # naprawie dokłada ``assess_vector_zones`` na podstawie oryginału.
    query_geometry = parcel_geometry if parcel_geometry.is_valid else make_valid(parcel_geometry)
    try:
        rows = find_mpzp_zone_intersections(
            db, GeometryPayload(query_geometry.wkt), as_of=as_of
        )
    except Exception as exc:
        db.rollback()
        log_analysis_event(
            "section_error", section="mpzp_vector", status=type(exc).__name__
        )
        return None, [
            WarningMessage(
                code="MPZP_VECTOR_QUERY_FAILED",
                message=(
                    "Nie udało się odczytać lokalnych wydzieleń MPZP; użyto "
                    "ścieżki zastępczej z obniżoną pewnością."
                ),
                severity="warning",
                source_name="mpzp",
            )
        ]
    if not rows:
        return None, []
    assessment = assess_vector_zones(parcel_geometry, rows)
    return assessment, list(assessment.warnings)


async def _parse_document_with_audit(
    document_url: str,
    zone_symbols: list[str],
    *,
    planning_act_identifier: str,
    act_version: str | None,
    parcel_identifier: str,
    db: Session,
) -> tuple[
    MpzpParseResult | None,
    SourceMetadata | None,
    DocumentEvidenceContext,
    list[WarningMessage],
]:
    """Pobiera i parsuje uchwałę dla dokładnych symboli oraz zapisuje audyt.

    Zapis stron i jednostek redakcyjnych daje cytowalny dowód każdej wartości;
    odczyt historyczny analizy korzysta z tego audytu i snapshotu, nie z
    ponownego pobrania dokumentu.
    """
    try:
        document = await fetch_mpzp_document(document_url)
        parsed = await parse_mpzp_document(document, zone_symbols, build_ocr_provider())
    except Exception as exc:
        log_analysis_event(
            "section_error",
            section="mpzp_document",
            status=type(exc).__name__,
            parcel_identifier=parcel_identifier,
        )
        return None, None, DocumentEvidenceContext(act_version=act_version), [
            WarningMessage(
                code="MPZP_DOCUMENT_UNAVAILABLE",
                message=(
                    "Nie udało się bezpiecznie pobrać dokumentu MPZP. Pozostałe "
                    "sekcje analizy są dostępne."
                ),
                severity="warning",
                source_name="mpzp",
            )
        ]

    warnings = [
        WarningMessage(
            code=warning.code,
            message=warning.message,
            severity=warning.severity,
            source_name="mpzp",
        )
        for warning in parsed.warnings
    ]
    snapshot = None
    try:
        snapshot = await asyncio.to_thread(
            persist_parser_audit,
            db,
            planning_act_identifier=planning_act_identifier,
            document=document,
            parse_result=parsed,
        )
    except Exception as exc:
        # Brak zapisu nie może wyglądać jak sukces: wycofujemy niedokończony
        # lineage i dokładamy jawny warning do częściowego wyniku analizy.
        await asyncio.to_thread(db.rollback)
        log_analysis_event(
            "section_error",
            section="mpzp_document_persistence",
            status=type(exc).__name__,
            parcel_identifier=parcel_identifier,
        )
        warnings.append(
            WarningMessage(
                code="MPZP_DOCUMENT_PERSISTENCE_FAILED",
                message=(
                    "Tekst dokumentu został przeanalizowany, ale nie udało się "
                    "zapisać jego cytowalnej struktury. Wynik wymaga ręcznej "
                    "weryfikacji."
                ),
                severity="error",
                source_name="mpzp",
            )
        )
    evidence = DocumentEvidenceContext(
        document_version_id=getattr(snapshot, "document_version_id", None),
        act_version=act_version,
        legal_units=legal_unit_evidence_from_snapshot(snapshot),
    )
    return parsed, document.source_metadata, evidence, warnings


async def _analyze_mpzp_vector_zones(
    vector: VectorZoneAssessment,
    discovery: MpzpDiscoveryResult | None,
    parcel_identifier: str,
    db: Session,
) -> tuple[list[MpzpZoneResult], list[WarningMessage], list[SourceMetadata], str | None]:
    """Parametry uchwały dla stref wyznaczonych geometrią (BK-202 → BK-203).

    Symbole stref z wektora są wejściem parsera — osobno dla każdego aktu i
    wersji. Parametr trafia wyłącznie do strefy o identycznym symbolu w tym
    samym akcie. Brak dokumentu zostawia parametry ``null`` z ostrzeżeniem.
    """
    zones = list(vector.zones)
    warnings: list[WarningMessage] = []
    sources: list[SourceMetadata] = [zone.source for zone in vector.positive_zones]
    statuses: list[str] = []
    groups: dict[tuple[str, str | None], list[int]] = {}
    for index, zone in enumerate(zones):
        if zone.touches_boundary:
            continue
        groups.setdefault((zone.act_identifier or "", zone.act_version), []).append(index)

    for (act_identifier, act_version), indexes in groups.items():
        document_url = zones[indexes[0]].document_url
        from_discovery = False
        if not document_url and len(groups) == 1 and discovery and discovery.uchwala_url:
            document_url = discovery.uchwala_url
            from_discovery = True
        if not document_url:
            warnings.append(
                WarningMessage(
                    code="MPZP_ACT_DOCUMENT_MISSING",
                    message=(
                        f"Brak dokumentu uchwały dla planu {act_identifier}; "
                        "parametry stref pozostają nieustalone (null)."
                    ),
                    severity="warning",
                    source_name="mpzp",
                )
            )
            continue
        symbols = sorted({zones[index].zone_symbol for index in indexes})
        parsed, document_source, evidence, parse_warnings = await _parse_document_with_audit(
            document_url,
            symbols,
            planning_act_identifier=act_identifier,
            act_version=act_version,
            parcel_identifier=parcel_identifier,
            db=db,
        )
        warnings.extend(parse_warnings)
        if parsed is None:
            continue
        statuses.append(parsed.status)
        if document_source is not None:
            sources.append(
                document_source.model_copy(update={"manual_review_required": True})
                if from_discovery
                else document_source
            )
        if from_discovery:
            warnings.append(
                WarningMessage(
                    code="MPZP_DOCUMENT_FROM_DISCOVERY",
                    message=(
                        "Dokument uchwały wskazało punktowe discovery, a nie wersja "
                        "aktu z wektora. Powiązanie wymaga weryfikacji."
                    ),
                    severity="warning",
                    source_name="mpzp",
                )
            )
        parser_zones = {zone.zone_symbol: zone for zone in parsed.zones}
        for index in indexes:
            parser_zone = parser_zones.get(zones[index].zone_symbol)
            if parser_zone is None or not parser_zone.parameters:
                warnings.append(
                    WarningMessage(
                        code="MPZP_ZONE_PARAMETERS_NOT_FOUND",
                        message=(
                            f"Nie znaleziono parametrów strefy {zones[index].zone_symbol} "
                            "w dokumencie uchwały."
                        ),
                        severity="warning",
                        source_name="mpzp",
                    )
                )
                continue
            updated, _skipped, conflicts = apply_parser_zone(zones[index], parser_zone, evidence)
            if from_discovery:
                updated = updated.model_copy(update={"manual_review_required": True})
            zones[index] = updated
            for field_name in conflicts:
                warnings.append(
                    WarningMessage(
                        code="MPZP_PARAMETER_CONFLICT",
                        message=(
                            f"Strefa {updated.zone_symbol}: parametr {field_name} ma "
                            "sprzeczne wartości w uchwale; żadna nie została wybrana "
                            "automatycznie."
                        ),
                        severity="warning",
                        source_name="mpzp",
                    )
                )
    parser_status = (
        "complete" if statuses and all(status == "complete" for status in statuses)
        else (statuses[0] if statuses else None)
    )
    return zones, warnings, sources, parser_status


async def _analyze_mpzp_best_effort(
    discovery: MpzpDiscoveryResult | None,
    parcel_area_sqm: float,
    parcel_identifier: str,
    db: Session,
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

    parsed, document_source, evidence, warnings = await _parse_document_with_audit(
        discovery.uchwala_url,
        discovery.candidate_zone_symbols,
        planning_act_identifier=(
            discovery.plan_id or f"mpzp-document:{discovery.uchwala_url}"
        ),
        act_version=None,
        parcel_identifier=parcel_identifier,
        db=db,
    )
    if parsed is None or document_source is None:
        return [], warnings, [], None

    # Sam dokument może być wiarygodny, ale przypisanie kandydata strefy nadal
    # pochodzi z punktowego discovery, nie z lokalnego przecięcia wektorowego.
    zone_source = document_source.model_copy(
        update={
            "confidence": min(
                document_source.confidence,
                discovery.source_metadata.confidence,
            ),
            "manual_review_required": True,
        }
    )
    zones: list[MpzpZoneResult] = []
    skipped: list[str] = []
    for parser_zone in parsed.zones:
        mapped, skipped_parameters = map_parser_zone_to_analyze_response(
            parser_zone,
            parcel_area_sqm,
            zone_source,
            evidence=evidence,
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
    for section in context.sections():
        # Znana luka źródła (KIUT bez potwierdzonego kontraktu) to informacja,
        # nie awaria — nie może wyglądać jak błąd przy każdej analizie.
        severity: Literal["warning", "error"] = (
            "error"
            if section.status in {"unavailable", "error"}
            and not section.is_known_source_gap
            else "warning"
        )
        warnings.extend(
            warnings_from_domain_messages(
                section.section,
                section.warnings,
                severity=severity,
            )
        )
        # Provenance nieudanej próby (np. timeout NMT) jest zapisywane w sekcji
        # i rejestrze źródeł, ale nie wchodzi do źródeł oceniających status.
        if section.status == "available" and section.source_metadata is not None:
            sources.append(section.source_metadata)

    rules = load_network_rules()
    network_result = calculate_network_protection_zones(
        parcel=parcel_geometry,
        buildable_area=technical_buildable_geometry,
        networks=[
            NetworkGeometryInput(
                network_type=feature.network_type,
                geometry=feature.geometry,
                input_index=index,
            )
            for index, feature in enumerate(context.kiut.data)
        ],
        rules=rules,
    )
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
    for index, feature in enumerate(context.kiut.data):
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
            infrastructure.append(
                InfrastructureResult(
                    network_type=feature.network_type,
                    buffer_m=0.0,
                    zone_area_sqm=0.0,
                    rule_source=None,
                    rule_confidence=None,
                    rule_note="Brak skonfigurowanej reguły bufora.",
                    affects_buildable_area=False,
                    network_geometry_geojson=analysis_layer_geometry_to_geojson(
                        feature.geometry,
                        "network",
                        {
                            "network_type": feature.network_type,
                            "input_index": index,
                        },
                    ),
                    protection_zone_geojson=None,
                    source=feature.source_metadata,
                )
            )
            sources.append(feature.source_metadata)
            continue
        matching_zone = next(
            (
                zone
                for zone in network_result.zones
                if zone.input_index == index
            ),
            None,
        )
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
                # Strefa reguły symulacyjnej (bez zweryfikowanej podstawy) jest tylko
                # przybliżeniem — nie pomniejsza obszaru zabudowy (BK-306).
                affects_buildable_area=(
                    matching_zone is not None
                    and matching_zone.affects_buildable_area
                    and matching_zone.zone_area_sqm > 0.0
                ),
                network_geometry_geojson=analysis_layer_geometry_to_geojson(
                    feature.geometry,
                    "network",
                    {
                        "network_type": feature.network_type,
                        "input_index": index,
                    },
                ),
                protection_zone_geojson=(
                    analysis_layer_geometry_to_geojson(
                        matching_zone.geometry,
                        "protection_zone",
                        {
                            "network_type": feature.network_type,
                            "buffer_m": matching_zone.buffer_m,
                            "rule_source": matching_zone.source,
                            "rule_confidence": matching_zone.confidence,
                        },
                    )
                    if matching_zone is not None
                    else None
                ),
                source=feature.source_metadata,
            )
        )
        sources.append(feature.source_metadata)

    # BK-303: obiekty ryzyka niosą wszystkie pola domenowe; opis jest tylko
    # prezentacją zbudowaną z tych pól.
    risks = [flood_risk_result(feature) for feature in context.isok.data]
    risks.extend(nature_risk_result(feature) for feature in context.gdos.data)
    sources.extend(feature.source_metadata for feature in context.isok.data)
    sources.extend(feature.source_metadata for feature in context.gdos.data)
    return (
        infrastructure,
        risks,
        warnings,
        _unique_sources(sources),
        network_result.net_buildable_area_sqm,
    )


# Komunikaty dla kodów decyzji BK-106. Żaden nie opisuje braku geometrii ani
# pustej odpowiedzi jako braku planu.
_POG_STATUS_REASON_WARNINGS: dict[str, tuple[str, str]] = {
    "POG_STATUS_STALE": (
        "warning",
        "Źródło POG było niedostępne; pokazano ostatni potwierdzony status wraz z datą potwierdzenia.",
    ),
    "POG_SOURCE_UNAVAILABLE": (
        "error",
        "Źródło POG było niedostępne. Brak wyniku nie oznacza braku planu ani braku ograniczeń.",
    ),
    "POG_STATUS_NOT_OFFICIAL": (
        "warning",
        "Źródło nie podało urzędowego kodu statusu aktu; status prawny pozostaje nieustalony.",
    ),
    "POG_COVERAGE_PARTIAL": (
        "warning",
        "Dane przestrzenne POG są niepełne dla działki; brak strefy nie oznacza braku planu.",
    ),
    "POG_ACT_WITHOUT_PARCEL_FEATURES": (
        "warning",
        "Akt POG ma dane przestrzenne, ale żaden obiekt nie przecina działki; dane wymagają weryfikacji.",
    ),
    "POG_ACT_WITHOUT_SPATIAL_DATA": (
        "warning",
        "Akt POG istnieje w rejestrze, ale nie ma danych przestrzennych dla działki. Brak geometrii nie oznacza braku planu.",
    ),
    "POG_EMPTY_RESPONSE_NOT_ABSENCE": (
        "warning",
        "Źródło nie zwróciło obiektów POG. Pusta odpowiedź nie jest urzędowym potwierdzeniem braku planu.",
    ),
}
_LOCAL_ACT_PRIORITY: dict[str, int] = {
    "binding": 0,
    "project": 1,
    "in_progress": 2,
    "unknown": 3,
    "superseded": 4,
}


def _status_warnings(decision: PogStatusDecision) -> list[WarningMessage]:
    warnings: list[WarningMessage] = []
    for code in decision.reasons:
        severity, message = _POG_STATUS_REASON_WARNINGS[code]
        warnings.append(
            WarningMessage(
                code=code,
                message=message,
                severity=severity,  # type: ignore[arg-type]
                source_name="pog",
            )
        )
    return warnings


async def _analyze_pog_best_effort(
    parcel_geometry: BaseGeometry,
    teryt: str | None,
    db: Session | None = None,
) -> tuple[
    PogResult,
    OuzStatusResult,
    list[WarningMessage],
    list[SourceMetadata],
]:
    """Rozstrzyga POG: lokalne wydanie → discovery RU → ostatnia potwierdzona.

    Status prawny, pokrycie i dostępność są ustalane wspólną tabelą decyzyjną
    BK-106. Brak potwierdzonego źródła daje ``unknown``/``unavailable``, a
    awaria źródła przy znanej wcześniejszej wartości — ``stale`` z datą. Żadna
    ścieżka nie zamienia braku danych na brak ograniczeń ani sukces analizy.
    """
    # Wydanie jest przypinane raz przed odczytem cech. Kolejne publikacje nie
    # zmieniają release_id ani SHA historycznego wyniku tej analizy.
    if db is not None:
        try:
            local = await asyncio.to_thread(
                _analyze_pog_local_release, db, parcel_geometry, teryt
            )
        except Exception as exc:
            log_analysis_event(
                "section_error", section="pog_local_release", status=type(exc).__name__
            )
            local = None
        if local is not None:
            return local

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
        discovery = None

    if discovery is None or not discovery.source_responded:
        return await asyncio.to_thread(
            _pog_upstream_failure, parcel_geometry, teryt, db, discovery
        )

    warnings = list(discovery.warnings)
    sources = [discovery.source_metadata]
    parsed_date, date_warning = _parse_pog_date(discovery.uchwala_date)
    if date_warning is not None:
        warnings.append(date_warning)

    checked_at = discovery.source_metadata.fetched_at or datetime.now(timezone.utc)
    ouz_status = calculate_ouz_status(parcel_geometry, None)
    base_observation = PogStatusObservation(
        source_responded=True,
        checked_at=checked_at,
        raw_legal_status=discovery.raw_legal_status,
        legal_evidence=discovery.legal_evidence,
        act_found=discovery.act_found,
        act_has_spatial_data=discovery.preview_features_found > 0,
        # WMS jest podglądem: obiekty z obrazu nie są wektorami do obliczeń,
        # więc pokrycie z samego discovery nigdy nie jest „available”.
        spatial_features_on_parcel=discovery.preview_features_found,
        zones_cover_parcel=False,
    )
    decision = resolve_pog_status(base_observation)
    pog = _pog_from_decision(decision, discovery, discovery.source_metadata)
    status_warnings = _status_warnings(decision)
    if discovery.legal_status == "binding" and discovery.links:
        try:
            vector_data = await fetch_pog_vector_data(discovery.links)
            analysis = analyze_pog_vectors(parcel_geometry, vector_data)
            if analysis.status != "analyzed":
                raise ValueError("Analiza wektorów POG nie została wykonana.")
            ouz_status = analysis.ouz_status
            decision = resolve_pog_status(
                PogStatusObservation(
                    source_responded=True,
                    checked_at=checked_at,
                    raw_legal_status=discovery.raw_legal_status,
                    legal_evidence=discovery.legal_evidence,
                    act_found=True,
                    act_has_spatial_data=True,
                    spatial_features_on_parcel=spatial_feature_count(analysis),
                    zones_cover_parcel=zones_cover_parcel(analysis),
                    response_complete=vector_data.status == "available",
                )
            )
            status_warnings = _status_warnings(decision)
            raw_attributes = _pog_raw_attributes(discovery, vector_data.app_metadata, analysis)
            vector_pog = to_pog_result(analysis, decision)
            pog = vector_pog.model_copy(
                update={
                    "uchwala_nr": discovery.uchwala_nr,
                    "uchwala_date": parsed_date,
                    "raw_attributes": raw_attributes,
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
        if discovery.legal_status == "binding":
            warnings.append(
                WarningMessage(
                    code="POG_VECTOR_LINK_MISSING",
                    message=(
                        "Discovery potwierdza obowiązujący POG, ale nie zwróciło "
                        "odnośnika do APP/GML potrzebnego do analizy powierzchniowej."
                    ),
                    severity="warning",
                    source_name="pog",
                )
            )
        warnings.extend(ouz_status.warnings)
    warnings.extend(status_warnings)

    if parsed_date is not None and pog.uchwala_date is None:
        pog = pog.model_copy(update={"uchwala_date": parsed_date})
    return pog, ouz_status, warnings, _unique_sources(sources)


def _pog_upstream_failure(
    parcel_geometry: BaseGeometry,
    teryt: str | None,
    db: Session | None,
    discovery: PogDiscoveryResult | None,
) -> tuple[PogResult, OuzStatusResult, list[WarningMessage], list[SourceMetadata]]:
    """Awaria źródła: ostatnia potwierdzona wartość jako ``stale`` albo ``unknown``."""
    checked_at = datetime.now(timezone.utc)
    previous = _previous_confirmed_status(db, parcel_geometry, teryt)
    decision = resolve_pog_status(
        PogStatusObservation(
            source_responded=False,
            checked_at=checked_at,
            previous=previous,
        )
    )
    source = (
        discovery.source_metadata
        if discovery is not None
        else SourceMetadata(
            source_name="POG_DISCOVERY",
            source_url=None,
            fetched_at=checked_at,
            confidence=0.0,
            manual_review_required=True,
        )
    )
    ouz_status = calculate_ouz_status(parcel_geometry, None)
    warnings: list[WarningMessage] = list(discovery.warnings) if discovery else [
        WarningMessage(
            code="POG_DISCOVERY_ERROR",
            message=(
                "Nie udało się uruchomić discovery POG. Brak wyniku "
                "nie oznacza braku ograniczeń planistycznych."
            ),
            severity="error",
            source_name="pog",
        )
    ]
    warnings.extend(ouz_status.warnings)
    warnings.extend(_status_warnings(decision))
    return (
        _pog_from_decision(decision, discovery, source),
        ouz_status,
        warnings,
        [source],
    )


def _previous_confirmed_status(
    db: Session | None,
    parcel_geometry: BaseGeometry,
    teryt: str | None,
) -> ConfirmedPogStatus | None:
    if db is None:
        return None
    try:
        row = last_confirmed_pog_status(
            db, GeometryPayload(parcel_geometry.wkt), teryt=teryt
        )
    except Exception as exc:
        log_analysis_event(
            "section_error", section="pog_last_confirmed", status=type(exc).__name__
        )
        return None
    if row is None or row.get("status_confirmed_at") is None:
        return None
    raw = row.get("raw_legal_status")
    legal = canonical_legal_status(row.get("legal_status"))
    if legal == "unknown" or not is_official_status_code(raw):
        return None
    evidence = StatusEvidence(
        source_name="Rejestr Urbanistyczny (ostatnie potwierdzone wydanie)",
        source_id="pog_app",
        official=True,
        reference=(
            f"data_release:{row.get('data_release_id')};sha256:{row.get('content_hash')}"
        ),
        raw_value=str(raw),
        confirmed_at=row["status_confirmed_at"],
    )
    return ConfirmedPogStatus(
        legal_status=legal,
        # Pokrycie działki nie zostało ponownie sprawdzone — zachowujemy tylko
        # potwierdzony status prawny aktu.
        coverage_status="unknown",
        confirmed_at=row["status_confirmed_at"],
        legal_evidence=evidence,
    )


def _analyze_pog_local_release(
    db: Session,
    parcel_geometry: BaseGeometry,
    teryt: str | None,
) -> tuple[PogResult, OuzStatusResult, list[WarningMessage], list[SourceMetadata]] | None:
    """Analiza z przypiętego lokalnego wydania; ``None`` gdy wydanie nie obejmuje działki."""
    pinned = active_pog_release(db)
    if pinned is None:
        return None
    release_id = int(pinned["id"])
    parcel_payload = GeometryPayload(parcel_geometry.wkt)
    acts = find_pog_acts_for_parcel(
        db, parcel_payload, data_release_id=release_id, teryt=teryt
    )
    if not acts:
        return None
    acts.sort(
        key=lambda act: (
            _LOCAL_ACT_PRIORITY.get(canonical_legal_status(act.get("legal_status")), 9),
            -int(act.get("features_on_parcel") or 0),
            str(act.get("act_identifier")),
        )
    )
    primary = acts[0]
    rows = [
        row
        for row in load_pog_release_features(
            db, parcel_payload, data_release_id=release_id
        )
        if row.get("act_version_id") == primary["act_version_id"]
    ]
    vector_data = _local_pog_vector_data(rows, pinned, primary)
    provenance = load_pog_act_provenance(db, int(primary["act_version_id"]))
    act_result = act_result_from_provenance(provenance) if provenance else None
    raw_status = primary.get("raw_legal_status")
    confirmed_at = primary.get("status_confirmed_at") or pinned.get("fetched_at")
    evidence = StatusEvidence(
        source_name="Rejestr Urbanistyczny (lokalne wydanie)",
        source_id="pog_app",
        official=is_official_status_code(raw_status),
        reference=f"data_release:{release_id};sha256:{pinned['content_hash']}",
        raw_value=raw_status,
        confirmed_at=confirmed_at,
    )
    warnings: list[WarningMessage] = []
    if len(acts) > 1:
        warnings.append(
            WarningMessage(
                code="POG_MULTIPLE_ACTS",
                message=(
                    "Działki dotyczy więcej niż jeden akt POG w wydaniu (np. "
                    "obowiązujący i projekt zmiany); wynik przedstawia akt "
                    f"{primary['act_identifier']}, pozostałe wymagają weryfikacji."
                ),
                severity="warning",
                source_name="pog",
            )
        )

    if rows:
        analysis = analyze_pog_vectors(parcel_geometry, vector_data)
        analyzed = analysis.status == "analyzed"
        decision = resolve_pog_status(
            PogStatusObservation(
                source_responded=True,
                checked_at=confirmed_at or datetime.now(timezone.utc),
                raw_legal_status=raw_status,
                legal_evidence=evidence,
                act_found=True,
                act_has_spatial_data=True,
                spatial_features_on_parcel=(
                    spatial_feature_count(analysis) if analyzed else 0
                ),
                zones_cover_parcel=analyzed and zones_cover_parcel(analysis),
                # Import publikuje wyłącznie wydania z potwierdzoną pełną
                # paginacją (PogSourceBatch.complete) i przejściem QA.
                response_complete=True,
            )
        )
        pog = to_pog_result(analysis, decision)
        if act_result is not None:
            pog = pog.model_copy(update={"act": act_result})
        warnings.extend(analysis.warnings)
        warnings.extend(_status_warnings(decision))
        warnings.extend(_provenance_warnings(act_result))
        return pog, analysis.ouz_status, warnings, [analysis.source_metadata]

    decision = resolve_pog_status(
        PogStatusObservation(
            source_responded=True,
            checked_at=confirmed_at or datetime.now(timezone.utc),
            raw_legal_status=raw_status,
            legal_evidence=evidence,
            act_found=True,
            act_has_spatial_data=bool(primary.get("has_spatial_data")),
            spatial_features_on_parcel=0,
            response_complete=True,
        )
    )
    ouz_status = calculate_ouz_status(parcel_geometry, None)
    source = vector_data.source_metadata.model_copy(
        update={"confidence": 0.5, "manual_review_required": True}
    )
    pog = _pog_from_decision(decision, None, source).model_copy(
        update={"act": act_result}
    )
    warnings.extend(ouz_status.warnings)
    warnings.extend(_status_warnings(decision))
    warnings.extend(_provenance_warnings(act_result))
    return pog, ouz_status, warnings, [source]


def _provenance_warnings(act: PogActResult | None) -> list[WarningMessage]:
    """Jawne ostrzeżenia o nieaktualnych/niedostępnych dokumentach i braku CSW."""
    if act is None:
        return []
    warnings: list[WarningMessage] = []
    if act.metadata is None:
        warnings.append(
            WarningMessage(
                code="POG_CSW_METADATA_MISSING",
                message=(
                    "Wydanie nie zawiera metadanych CSW aktu; karta metadanych i "
                    "data publikacji zbioru są niedostępne."
                ),
                severity="warning",
                source_name="pog",
            )
        )
    for document in act.formal_documents:
        if document.status != "current" or not (document.link_verified or not document.link):
            label = document.title or document.document_identifier
            warnings.append(
                WarningMessage(
                    code=f"POG_DOCUMENT_{document.status.upper()}",
                    message=f"{label}: {document.warning}",
                    severity="warning",
                    source_name="pog",
                )
            )
    return warnings


def _local_pog_vector_data(
    rows: list[dict[str, object]],
    pinned: dict[str, object],
    act: dict[str, object],
) -> PogVectorData:
    release_id = int(pinned["id"])  # type: ignore[call-overload]
    buckets: dict[str, list[PogVectorFeature]] = {
        "planning_zone": [],
        "ouz": [],
        "downtown_area": [],
        "social_infrastructure_standard": [],
    }
    app_metadata: dict[str, object] = {
        "act_identifier": act.get("act_identifier"),
        "act_version": act.get("object_version_id"),
        "legal_status": act.get("legal_status"),
        "raw_legal_status": act.get("raw_legal_status"),
    }
    for row in rows:
        feature_type = str(row["feature_type"])
        if feature_type not in buckets:
            continue
        attributes = dict(row.get("raw_attributes") or {})  # type: ignore[call-overload]
        attributes.update({
            "feature_id": row.get("feature_identifier"),
            "feature_version": row.get("feature_version"),
            "gml_url": feature_gml_url(
                row.get("source_reference"),  # type: ignore[arg-type]
                feature_type,
                row.get("feature_identifier"),  # type: ignore[arg-type]
                row.get("feature_version"),  # type: ignore[arg-type]
            ),
            "symbol": row.get("symbol"),
            "label": row.get("label"),
            "primary_profiles": row.get("primary_profiles") or [],
            "additional_profiles": row.get("additional_profiles") or [],
        })
        attributes.update(dict(row.get("parameters") or {}))  # type: ignore[call-overload]
        buckets[feature_type].append(PogVectorFeature(
            geometry=from_wkt(str(row["geometry_wkt"])),
            attributes=attributes,
            source_crs="EPSG:2180",
            layer_type=feature_type,  # type: ignore[arg-type]
        ))
        app_metadata.update({
            "act_name": row.get("act_name"),
            "resolution_number": row.get("resolution_number"),
            "resolution_date": (
                row["resolution_date"].isoformat()  # type: ignore[attr-defined]
                if row.get("resolution_date") else None
            ),
        })
    binding = canonical_legal_status(str(act.get("legal_status"))) == "binding"
    source = SourceMetadata(
        source_id="pog_app",
        source_version=str(pinned["version_label"]),
        artifact_sha256=str(pinned["content_hash"]),
        data_release_id=release_id,
        act_version=(str(app_metadata["act_version"]) if app_metadata.get("act_version") else None),
        source_name="POG_APP_LOCAL_POSTGIS",
        source_url=None,
        fetched_at=pinned.get("fetched_at"),  # type: ignore[arg-type]
        response_status=None,
        confidence=1.0 if binding else 0.6,
        manual_review_required=not binding,
    )
    return PogVectorData(
        planning_zones=buckets["planning_zone"],
        ouz_areas=buckets["ouz"],
        downtown_areas=buckets["downtown_area"],
        app_metadata=app_metadata,
        status="available" if rows else "partial",
        wms_fallback_required=not bool(rows),
        source_metadata=source,
        social_infrastructure_standard_areas=buckets["social_infrastructure_standard"],
        warnings=[],
    )


def _pog_from_decision(
    decision: PogStatusDecision,
    discovery: PogDiscoveryResult | None,
    source: SourceMetadata,
) -> PogResult:
    """Mapuje decyzję statusu bez tworzenia pozornej geometrii POG/OUZ."""
    parsed_date, _ = _parse_pog_date(discovery.uchwala_date if discovery else None)
    return PogResult(
        legal_status=decision.legal_status,
        coverage_status=decision.coverage_status,
        data_availability=decision.data_availability,
        status_confirmed_at=decision.confirmed_at,
        legal_status_evidence=evidence_result(decision.legal_evidence),
        coverage_evidence=evidence_result(decision.coverage_evidence),
        planning_zone=None,
        zone_type=None,
        in_ouz=False,
        area_ratio=None,
        in_downtown_area=False,
        uchwala_nr=discovery.uchwala_nr if discovery else None,
        uchwala_date=parsed_date,
        manual_review_required=True,
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
    *,
    as_of: datetime,
    parcel_area_sqm: float,
) -> tuple[PogResult, list[WarningMessage]]:
    """Dołącza informacyjną ocenę relacji MPZP–POG jako dane pierwszoklasowe.

    Ocena obejmuje wszystkie pary stref (bez wyboru strefy dominującej i bez
    uśredniania) i trafia do ``compatibility_assessment``, nie do
    ``raw_attributes``.
    """
    scenario = build_pog_scenario_result(
        mpzp_zones,
        pog,
        ouz_status,
        as_of=as_of,
        parcel_area_sqm=parcel_area_sqm,
    )
    return (
        pog.model_copy(
            update={
                "compatibility_assessment": scenario.assessment,
                "manual_review_required": (
                    pog.manual_review_required or scenario.manual_review_required
                ),
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
            "legal_status": discovery.legal_status,
            "raw_legal_status": discovery.raw_legal_status,
            "source_responded": discovery.source_responded,
            "uchwala_nr": discovery.uchwala_nr,
            "uchwala_date": discovery.uchwala_date,
            "links": discovery.links,
            "is_discovery_only": discovery.is_discovery_only,
            "layers": {
                name: {
                    "status": section.status,
                    "raw_legal_status": section.raw_legal_status,
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
    mpzp_complete: bool = True,
) -> str:
    # NMT jest pominięty świadomie: brak danych o rzeźbie terenu nie oznacza,
    # że analiza ograniczeń prawnych jest niepełna (patrz ContextResult).
    critical_context_available = all(
        section.status == "available" for section in context.critical_sections()
    )
    if not mpzp_zones or pog is None:
        return "partial"
    if not mpzp_complete or any(zone.manual_review_required for zone in mpzp_zones):
        return "partial"
    # Strefa bez przecięcia wektorowego (discovery albo symbol podany ręcznie)
    # nigdy nie daje ``complete`` — niezależnie od kompletności parametrów.
    if any(zone.assignment_method != "vector_intersection" for zone in mpzp_zones):
        return "partial"
    if pog.legal_status != "binding" or pog.coverage_status != "available":
        return "partial"
    if pog.data_availability != "current":
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
