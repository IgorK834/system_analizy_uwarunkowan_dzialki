"""Generator raportu PDF analizy uwarunkowań przestrzennych działki.

Raport jest budowany wyłącznie z zapisanego snapshotu analizy — nie uruchamia
ponownie analizy ani nie odpytuje usług domenowych (ULDK, WFS, BIP). Dane
pochodzą z ``build_analyze_response_from_analysis``, czyli z tego samego
kontraktu, który zasila API i frontend. Jedyny opcjonalny outbound HTTP to
podkład miniatury mapy (WMS GetMap, krótki timeout); awaria podkładu nie
blokuje raportu — miniatura powstaje na tle fallback z ostrzeżeniem po polsku
(TODO ADR-009: świadomy wyjątek UX względem offline snapshotu).

Odpowiedzialności są rozdzielone na małe funkcje: pobranie danych, przygotowanie
kontekstu prezentacyjnego, wygenerowanie miniatury mapy, renderowanie HTML i
utworzenie PDF. Dane z bazy są escapowane automatycznie przez Jinja2
(``autoescape=True``) przed umieszczeniem w HTML.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from jinja2 import Environment, select_autoescape
from sqlalchemy.orm import Session

from app.core.planning_compatibility import COMPATIBILITY_STATUS_LABELS_PL
from app.core.report_config import (
    REPORT_DISCLAIMER,
    REPORT_SYSTEM_NAME,
    REPORT_TITLE,
    describe_confidence,
)
from app.models.analysis import Analysis
from app.schemas.analyze import AnalyzeResponse, TerrainReliefResult, TerrainResult
from app.schemas.source import SourceMetadata, WarningMessage
from app.services.persistence import build_analyze_response_from_analysis
from app.services.pog_provenance import RELATION_LABELS_PL
from app.services.terrain import terrain_from_snapshot
from app.services.report_map import png_to_data_uri, render_analysis_map_png
from app.shared.planning_status import (
    COVERAGE_STATUS_LABELS_PL,
    DATA_AVAILABILITY_LABELS_PL,
    LEGAL_STATUS_LABELS_PL,
    pog_status_notes_pl,
)

logger = logging.getLogger(__name__)


class AnalysisReportNotFoundError(Exception):
    """Analiza o podanym identyfikatorze nie istnieje w bazie."""


class AnalysisReportRenderError(Exception):
    """Nie udało się wygenerować dokumentu PDF z gotowego szablonu HTML."""


def generate_analysis_report_pdf(analysis_id: int, db: Session) -> bytes:
    """Generuje kompletny raport PDF dla zapisanej analizy.

    Nie uruchamia ponownie analizy — czyta istniejący snapshot z bazy. Brak
    pojedynczej sekcji obniża kompletność raportu, ale nie przerywa generowania:
    niedostępne sekcje są jawnie opisane jako brak danych. Zgłasza
    ``AnalysisReportNotFoundError``, gdy analiza nie istnieje, oraz
    ``AnalysisReportRenderError`` przy błędzie samego renderowania PDF.
    """
    analysis = db.get(Analysis, analysis_id)
    if analysis is None:
        raise AnalysisReportNotFoundError(
            f"Analiza o identyfikatorze {analysis_id} nie istnieje."
        )

    response = build_analyze_response_from_analysis(analysis, db)

    map_data_uri, map_warning, map_kiut_overlay = _render_map_data_uri(response)
    context = _build_report_context(
        response, map_data_uri, map_warning, map_kiut_overlay
    )
    html = _render_report_html(context)

    logger.info("Wygenerowano HTML raportu dla analizy %s", analysis_id)
    return _html_to_pdf(html)


def _render_map_data_uri(
    response: AnalyzeResponse,
) -> tuple[str | None, str | None, bool]:
    """Renderuje miniaturę mapy i zwraca (data_uri, ostrzeżenie, nakładka KIUT).

    Awaria opcjonalnego renderera mapy albo brak geometrii nie może zablokować
    raportu. W obu przypadkach zwracamy ``data_uri=None`` i czytelne ostrzeżenie
    dołączane później do sekcji ograniczeń raportu.
    """
    try:
        map_result = render_analysis_map_png(response)
    except Exception:  # noqa: BLE001 - granica orkiestracji: mapa jest opcjonalna
        logger.exception("Renderowanie miniatury mapy nie powiodło się")
        return None, (
            "Nie udało się wygenerować miniatury mapy. Pozostała część raportu "
            "jest kompletna."
        ), False

    if map_result.png_bytes is None:
        return None, (
            "Miniatura mapy jest niedostępna, ponieważ analiza nie zawiera "
            "geometrii możliwej do narysowania."
        ), False
    return (
        png_to_data_uri(map_result.png_bytes),
        map_result.warning,
        map_result.kiut_overlay_used,
    )


def _build_report_context(
    response: AnalyzeResponse,
    map_data_uri: str | None,
    map_warning: str | None,
    map_kiut_overlay: bool = False,
) -> dict[str, Any]:
    """Przygotowuje dane prezentacyjne raportu, spójne z formatowaniem frontendu."""
    parcel = response.parcel
    limitations = _build_limitations(response, map_warning)

    return {
        "title": REPORT_TITLE,
        "system_name": REPORT_SYSTEM_NAME,
        "disclaimer": REPORT_DISCLAIMER,
        "generated_at": _format_datetime(datetime.now(response.analyzed_at.tzinfo)),
        "analysis_id": response.analysis_id,
        "status": response.status,
        "analyzed_at": _format_datetime(response.analyzed_at),
        "parcel_identifier": parcel.parcel_identifier if parcel else None,
        "map_data_uri": map_data_uri,
        "map_warning": map_warning,
        "map_kiut_overlay": map_kiut_overlay,
        "geometry": _geometry_context(response),
        "mpzp_zones": [_mpzp_context(zone) for zone in response.mpzp_zones],
        "manual_zone_declared": any(
            zone.assignment_method == "manual_user_input" for zone in response.mpzp_zones
        ),
        "pog": _pog_context(response.pog),
        "compatibility": _compatibility_context(
            response.pog.compatibility_assessment if response.pog else None
        ),
        "infrastructure": [
            _infrastructure_context(item) for item in response.infrastructure
        ],
        "utilities_preview": _utilities_preview_context(response),
        "risks": [_risk_context(item) for item in response.risks],
        "terrain": _terrain_context(response.terrain),
        "sources": [_source_context(source) for source in response.sources],
        "warnings": [_warning_context(warning) for warning in response.warnings],
        "manual_zone_required": response.manual_zone_required,
        "limitations": limitations,
    }


def _geometry_context(response: AnalyzeResponse) -> dict[str, Any] | None:
    parcel = response.parcel
    if parcel is None:
        return None
    metrics = parcel.metrics
    return {
        "area_sqm": _format_number(metrics.area_sqm),
        "area_ha": _format_number(metrics.area_ha, decimals=4),
        "perimeter_m": _format_number(metrics.perimeter_m),
        "is_valid": _format_bool(metrics.is_valid),
        "geometry_repaired": _format_bool(metrics.geometry_repaired),
        "buildable_area_sqm": (
            _format_number(response.buildable_area_sqm)
            if response.buildable_area_sqm is not None
            else None
        ),
    }


_MPZP_ASSIGNMENT_LABELS: dict[str, str] = {
    "vector_intersection": "przecięcie z wektorem wydzieleń",
    "document_candidate": "kandydat z discovery/dokumentu (bez wektora)",
    "manual_user_input": "symbol podany ręcznie z mapy rastrowej",
    "legacy": "snapshot sprzed wersjonowania stref",
}
_EXTRACTION_LABELS: dict[str, str] = {"pdf_text": "tekst PDF", "html": "HTML", "ocr": "OCR"}


def _mpzp_parameter_context(parameter: Any) -> dict[str, Any]:
    value = parameter.normalized_value
    return {
        "name": parameter.name,
        "value": _format_number(value) if isinstance(value, (int, float)) else value,
        "raw_value": parameter.raw_value,
        "unit": parameter.unit,
        "page_number": parameter.page_number,
        "segment_id": parameter.segment_id,
        "legal_unit_id": parameter.legal_unit_id,
        "evidence_text": parameter.evidence_text,
        "document_sha256": parameter.document_sha256,
        "extraction": _EXTRACTION_LABELS.get(
            parameter.extraction_method or "", parameter.extraction_method
        ),
        "parser_version": parameter.parser_version,
        "confidence_pct": _format_percent(parameter.confidence * 100.0),
        "conflict": parameter.conflict_group_id is not None,
        "manual_review_required": parameter.manual_review_required,
    }


def _manual_selection_context(selection: Any) -> dict[str, Any] | None:
    if selection is None:
        return None
    return {
        "entered_symbol": selection.entered_symbol,
        "plan_id": selection.plan_id,
        "candidates": ", ".join(selection.candidate_zone_symbols) or "brak kandydatów",
        "symbol_in_candidates": selection.symbol_in_candidates,
        "document_pinned": selection.document_pinned,
        "document_sha256": selection.document_sha256,
        "document_fetched_at": (
            _format_datetime(selection.document_fetched_at)
            if selection.document_fetched_at
            else None
        ),
        "selected_at": _format_datetime(selection.selected_at),
    }


def _mpzp_context(zone: Any) -> dict[str, Any]:
    share_known = zone.intersection_pct is not None and zone.intersection_area_sqm is not None
    return {
        "share_known": share_known,
        "is_manual": zone.assignment_method == "manual_user_input",
        "manual_selection": _manual_selection_context(zone.manual_selection),
        "zone_id": zone.zone_id,
        "act_identifier": zone.act_identifier,
        "act_version": zone.act_version,
        "data_release_id": zone.data_release_id,
        "touches_boundary": zone.touches_boundary,
        "assignment_label": _MPZP_ASSIGNMENT_LABELS.get(
            zone.assignment_method, zone.assignment_method
        ),
        "is_vector": zone.assignment_method == "vector_intersection",
        "manual_review_required": zone.manual_review_required,
        "parameters": [_mpzp_parameter_context(item) for item in zone.parameters],
        "zone_symbol": zone.zone_symbol,
        "primary_use": zone.primary_use,
        "supplementary_use": zone.supplementary_use,
        "is_dominant": zone.is_dominant,
        "intersection_pct": _format_percent(zone.intersection_pct),
        "intersection_area_sqm": _format_number(zone.intersection_area_sqm),
        "max_building_height_m": _format_optional_number(zone.max_building_height_m, "m"),
        "max_floors": (str(zone.max_floors) if zone.max_floors is not None else None),
        "min_biologically_active_pct": _format_optional_percent(
            zone.min_biologically_active_pct
        ),
        "max_floor_area_ratio": _format_optional_number(zone.max_floor_area_ratio),
        "min_floor_area_ratio": _format_optional_number(zone.min_floor_area_ratio),
        "max_building_coverage_pct": _format_optional_percent(
            zone.max_building_coverage_pct
        ),
        "confidence": _confidence_context(zone.source),
    }


def _pog_context(pog: Any) -> dict[str, Any] | None:
    if pog is None:
        return None
    evidence = pog.legal_status_evidence
    coverage_evidence = pog.coverage_evidence
    return {
        "schema_version": pog.schema_version,
        "legal_status": pog.legal_status,
        "legal_status_label": LEGAL_STATUS_LABELS_PL[pog.legal_status],
        "coverage_status": pog.coverage_status,
        "coverage_status_label": COVERAGE_STATUS_LABELS_PL[pog.coverage_status],
        "data_availability": pog.data_availability,
        "data_availability_label": DATA_AVAILABILITY_LABELS_PL[pog.data_availability],
        "status_confirmed_at": (
            _format_datetime(pog.status_confirmed_at) if pog.status_confirmed_at else None
        ),
        "status_evidence": (
            {
                "source_name": evidence.source_name,
                "raw_value": evidence.raw_value,
                "reference": evidence.reference,
            }
            if evidence is not None
            else None
        ),
        "coverage_evidence": (
            {
                "source_name": coverage_evidence.source_name,
                "reference": coverage_evidence.reference,
            }
            if coverage_evidence is not None
            else None
        ),
        "status_notes": pog_status_notes_pl(
            pog.legal_status,
            pog.coverage_status,
            pog.data_availability,
            pog.status_confirmed_at,
        ),
        "planning_zone": pog.zone_type or pog.planning_zone,
        "in_ouz": _format_bool(pog.in_ouz),
        "in_downtown_area": _format_bool(pog.in_downtown_area),
        "touches_ouz_boundary": _format_bool(pog.touches_ouz_boundary),
        "ouz_intersection_pct": (
            _format_percent(pog.ouz_intersection_pct)
            if pog.ouz_intersection_pct is not None
            else None
        ),
        "ouz_intersection_area_sqm": (
            _format_number(pog.ouz_intersection_area_sqm)
            if pog.ouz_intersection_area_sqm is not None
            else None
        ),
        "uchwala_nr": pog.uchwala_nr,
        "uchwala_date": _format_date(pog.uchwala_date),
        "manual_review_required": pog.manual_review_required,
        "confidence": _confidence_context(pog.source),
        "zones": [
            {
                "id": zone.id,
                "symbol": zone.symbol,
                "type": zone.type,
                "label": zone.label,
                "area_sqm": _format_number(zone.area_sqm),
                "area_pct": _format_percent(zone.area_pct),
                "max_overground_floor_area_ratio": _format_optional_number(zone.max_overground_floor_area_ratio),
                "max_building_height_m": _format_optional_number(zone.max_building_height_m, "m"),
                "max_building_coverage_pct": _format_optional_percent(zone.max_building_coverage_pct),
                "min_biologically_active_pct": _format_optional_percent(zone.min_biologically_active_pct),
                "primary_profile": ", ".join(
                    profile.label or profile.code for profile in zone.primary_profile
                ) or "—",
                "additional_profiles": ", ".join(
                    profile.label or profile.code for profile in zone.additional_profiles
                ) or "—",
                "gml_url": zone.gml_url if zone.gml_url_verified else None,
                "feature_version": zone.feature_version,
            }
            for zone in pog.zones
        ],
        "act": _pog_act_context(pog.act),
        "ouz": [_pog_area_context(item) for item in pog.ouz],
        "downtown_areas": [_pog_area_context(item) for item in pog.downtown_areas],
        "social_areas": [
            _pog_area_context(item)
            for item in pog.social_infrastructure_standard_areas
        ],
    }


def _compatibility_context(assessment: Any) -> dict[str, Any] | None:
    """Ocena relacji MPZP–POG jako osobna, informacyjna sekcja raportu (BK-205)."""
    if assessment is None:
        return None
    return {
        "status": assessment.status,
        "status_label": COMPATIBILITY_STATUS_LABELS_PL[assessment.status],
        "reason_code": assessment.reason_code,
        "as_of": _format_date(assessment.as_of),
        "rule_id": assessment.rule_id,
        "rule_version": assessment.rule_version,
        "aggregation": assessment.aggregation,
        "rationale": assessment.rationale,
        "manual_review_required": assessment.manual_review_required,
        "informational_notice": assessment.informational_notice,
        "sources": [
            {
                "kind": source.kind,
                "label": source.label,
                "reference": source.reference,
                "version": source.version,
                "as_of": _format_date(source.as_of),
            }
            for source in assessment.sources
        ],
        "pairs": [
            {
                "label": f"{pair.mpzp_zone_symbol} × {pair.pog_zone_symbol or pair.pog_zone_type}",
                "status_label": COMPATIBILITY_STATUS_LABELS_PL[pair.status],
                "spatial": pair.spatially_identified,
                "overlap": (
                    f"{_format_number(pair.overlap_area_sqm)} m²"
                    + (
                        f" ({_format_percent(pair.overlap_pct)})"
                        if pair.overlap_pct is not None
                        else ""
                    )
                    if pair.overlap_area_sqm is not None
                    else "nieustalone"
                ),
                "rule": (
                    f"{pair.rule_id} v{pair.rule_version}" if pair.rule_id else "brak reguły"
                ),
                "as_of": _format_date(pair.as_of),
                "rationale": pair.rationale,
            }
            for pair in assessment.zone_pairs
        ],
        "legacy": (
            {
                "origin": assessment.legacy_evidence.origin,
                "conflict": assessment.legacy_evidence.conflict_with_mpzp,
                "result": assessment.legacy_evidence.result,
            }
            if assessment.legacy_evidence is not None
            else None
        ),
    }


def _pog_act_context(act: Any) -> dict[str, Any] | None:
    """Łańcuch provenance aktu; linki wyłącznie dla zweryfikowanych HTTPS."""
    if act is None:
        return None
    metadata = act.metadata
    return {
        "identifier": act.act_identifier or act.id,
        "version": act.act_version or act.version,
        "title": act.title,
        "publication_id": act.publication_id,
        "version_started_at": (
            _format_datetime(act.version_started_at) if act.version_started_at else None
        ),
        "valid_from": _format_date(act.valid_from),
        "valid_to": _format_date(act.valid_to),
        "publication_date": _format_date(act.publication_date),
        "gml_url": act.gml_url if act.gml_url_verified else None,
        "card_url": act.card_url if act.card_url_verified else None,
        "data_release_id": act.data_release_id,
        "release_label": act.release_label,
        "artifact_sha256": act.artifact_sha256,
        "fetched_at": _format_datetime(act.fetched_at) if act.fetched_at else None,
        "metadata": (
            {
                "record_id": metadata.record_id,
                "title": metadata.title,
                "record_sha256": metadata.record_sha256,
                "date_stamp": _format_date(metadata.date_stamp),
            }
            if metadata is not None
            else None
        ),
        "documents": [
            {
                "title": document.title or document.short_name or document.document_identifier,
                "identifier": document.document_identifier,
                "version": document.document_version,
                "relation": RELATION_LABELS_PL.get(document.relation or "", document.relation),
                "document_date": _format_date(document.document_date),
                "effective_date": _format_date(document.effective_date),
                "repeal_date": _format_date(document.repeal_date),
                "record_sha256": document.record_sha256,
                "link": document.link if document.link_verified else None,
                "raw_link": None if document.link_verified else document.link,
                "status": document.status,
                "warning": document.warning,
            }
            for document in act.formal_documents
        ],
    }


def _pog_area_context(area: Any) -> dict[str, Any]:
    return {
        "id": area.id,
        "symbol": area.symbol,
        "label": area.label,
        "area_sqm": _format_number(area.area_sqm),
        "area_pct": _format_percent(area.area_pct),
        "touches_boundary": _format_bool(area.touches_boundary),
    }


def _infrastructure_context(item: Any) -> dict[str, Any]:
    return {
        "network_type": item.network_type,
        "buffer_m": _format_number(item.buffer_m),
        "zone_area_sqm": _format_number(item.zone_area_sqm),
        "affects_buildable_area": item.affects_buildable_area,
        "rule_source": item.rule_source,
        "rule_note": item.rule_note,
        "confidence": _confidence_context(item.source),
    }


def _utilities_preview_context(response: AnalyzeResponse) -> dict[str, Any] | None:
    preview = response.utilities_preview
    if preview is None:
        return None
    status_labels = {
        "covered": "powiat publikuje dane GESUT w KIUT",
        "not_covered": "KIUT nie potwierdził publikacji danych GESUT przez powiat",
        "unknown": "nie udało się sprawdzić pokrycia powiatu",
    }
    return {
        "coverage_status": preview.coverage_status,
        "coverage_label": status_labels[preview.coverage_status],
        "county_name": preview.county_name,
        "layer_available": _format_bool(preview.layer_available),
        "note": preview.note,
        "style_legend": (
            "Kolory poglądowego obrazu WMS (styl GUGiK): energetyka — czerwony, "
            "woda — niebieski, kanalizacja — brązowy, gaz — żółty."
        ),
        "source_name": preview.source.source_name,
        "fetched_at": (
            _format_datetime(preview.source.fetched_at)
            if preview.source.fetched_at
            else None
        ),
    }


_TERRAIN_STATUS_LABELS: dict[str, str] = {
    "available": "zmierzono",
    "no_coverage": "brak pokrycia danymi NMT",
    "unavailable": "pomiar niedostępny",
    "unknown": "brak informacji w zapisanym wyniku",
}
_TERRAIN_STATUS_NOTES: dict[str, str] = {
    "no_coverage": (
        "Źródło potwierdziło brak danych wysokościowych dla obszaru działki. "
        "Deniwelacja jest nieznana — brak pokrycia nie oznacza płaskiego terenu."
    ),
    "unavailable": (
        "Nie udało się uzyskać danych wysokościowych. Deniwelacja jest nieznana — "
        "wynik nie oznacza płaskiego terenu."
    ),
    "unknown": (
        "Zapisany wynik pochodzi sprzed wprowadzenia sekcji rzeźby terenu i nie "
        "zawiera pomiaru NMT. Brak informacji nie oznacza płaskiego terenu."
    ),
}
_ASPECT_LABELS: dict[str, str] = {
    "N": "północna",
    "NE": "północno-wschodnia",
    "E": "wschodnia",
    "SE": "południowo-wschodnia",
    "S": "południowa",
    "SW": "południowo-zachodnia",
    "W": "zachodnia",
    "NW": "północno-zachodnia",
}
_PROFILE_WIDTH = 600.0
_PROFILE_HEIGHT = 150.0
_PROFILE_PADDING = 8.0


def _terrain_context(terrain: TerrainResult | None) -> dict[str, Any]:
    """Sekcja rzeźby terenu — cztery statusy prezentowane rozłącznie."""
    terrain = terrain or terrain_from_snapshot(None)
    note = _TERRAIN_STATUS_NOTES.get(terrain.status)
    if terrain.status == "available" and terrain.height_difference_m == 0:
        note = (
            "Zmierzona deniwelacja wynosi 0 m — w siatce próbkowania teren w "
            "obrysie działki jest płaski."
        )
    source = terrain.source
    return {
        "status": terrain.status,
        "status_label": _TERRAIN_STATUS_LABELS[terrain.status],
        "note": note,
        "reason_code": terrain.reason_code,
        "min_height": _format_meters(terrain.min_height_m),
        "max_height": _format_meters(terrain.max_height_m),
        "height_difference": _format_meters(terrain.height_difference_m),
        "grid_size": _format_meters(terrain.grid_size_m),
        "sampled_points": terrain.sampled_points,
        "source": _terrain_source_context(source),
        "warnings": list(terrain.warnings),
        "relief": _relief_context(terrain.relief),
    }


def _relief_context(relief: TerrainReliefResult | None) -> dict[str, Any] | None:
    if relief is None:
        return None
    slope = relief.slope
    aspect = relief.aspect
    raster = relief.raster
    return {
        "status": relief.status,
        "status_label": _TERRAIN_STATUS_LABELS[relief.status],
        "reason_code": relief.reason_code,
        "algorithm_version": relief.algorithm_version,
        "slope_classes_version": relief.slope_classes_version,
        "resolution": _format_meters(relief.resolution_m),
        "valid_share": _format_percent(relief.valid_area_share_pct),
        "valid_pixel_count": relief.valid_pixel_count,
        "parcel_pixel_count": relief.parcel_pixel_count,
        "min_height": _format_meters(relief.min_height_m),
        "max_height": _format_meters(relief.max_height_m),
        "mean_height": _format_meters(relief.mean_height_m),
        "slope": (
            [
                ("Średni", slope.mean_deg, slope.mean_pct),
                ("Mediana", slope.median_deg, slope.median_pct),
                ("P90", slope.p90_deg, slope.p90_pct),
                ("Maksymalny", slope.max_deg, slope.max_pct),
            ]
            if slope is not None
            else []
        ),
        "classes": [
            {
                "label": item.label,
                "area": _format_number(item.area_sqm, decimals=0),
                "share": _format_percent(item.share_pct),
            }
            for item in relief.slope_classes
        ],
        "aspect": (
            {
                "status": aspect.status,
                "direction": (
                    _ASPECT_LABELS.get(aspect.dominant_direction or "", None)
                ),
                "azimuth": _format_trimmed(aspect.mean_azimuth_deg, 1),
                "resultant": _format_trimmed(aspect.resultant_length, 2),
                "non_flat_share": _format_percent(aspect.non_flat_share_pct),
                "flat_threshold": _format_trimmed(aspect.flat_threshold_pct, 1),
            }
            if aspect is not None
            else None
        ),
        "profile": _profile_context(relief),
        "raster": (
            {
                "coverage_id": raster.coverage_id,
                "size": f"{raster.width_px} × {raster.height_px} px",
                "bbox": ", ".join(_format_trimmed(value, 3) or "" for value in raster.bbox),
                "buffer": _format_meters(raster.buffer_m),
                "vertical_datum": raster.vertical_datum,
                "gdal_version": raster.gdal_version,
                "nodata_policy": raster.nodata_policy,
            }
            if raster is not None
            else None
        ),
        "source": _terrain_source_context(relief.source),
        "warnings": list(relief.warnings),
    }


def _profile_context(relief: TerrainReliefResult) -> dict[str, Any] | None:
    """Profil jako liczby do inline SVG — bez surowego HTML w kontekście."""
    profile = relief.profile
    if profile is None:
        return None
    heights = [sample.height_m for sample in profile.samples if sample.height_m is not None]
    context: dict[str, Any] = {
        "start": f"{_format_trimmed(profile.start[0], 3)}, {_format_trimmed(profile.start[1], 3)}",
        "end": f"{_format_trimmed(profile.end[0], 3)}, {_format_trimmed(profile.end[1], 3)}",
        "length": _format_meters(profile.length_m),
        "step": _format_meters(profile.step_m),
        "sample_count": len(profile.samples),
        "missing_count": len(profile.samples) - len(heights),
        "width": _PROFILE_WIDTH,
        "height": _PROFILE_HEIGHT,
        "segments": [],
        "min_label": None,
        "max_label": None,
    }
    if not heights or profile.length_m <= 0:
        return context
    low, high = min(heights), max(heights)
    span = high - low or 1.0
    usable_w = _PROFILE_WIDTH - 2 * _PROFILE_PADDING
    usable_h = _PROFILE_HEIGHT - 2 * _PROFILE_PADDING
    segments: list[str] = []
    current: list[str] = []
    for sample in profile.samples:
        if sample.height_m is None:
            if len(current) > 1:
                segments.append(" ".join(current))
            current = []
            continue
        x = _PROFILE_PADDING + usable_w * sample.distance_m / profile.length_m
        y = _PROFILE_PADDING + usable_h * (1 - (sample.height_m - low) / span)
        current.append(f"{x:.1f},{y:.1f}")
    if len(current) > 1:
        segments.append(" ".join(current))
    context.update(
        segments=segments,
        min_label=_format_meters(low),
        max_label=_format_meters(high),
    )
    return context


def _terrain_source_context(source: SourceMetadata | None) -> dict[str, Any] | None:
    if source is None:
        return None
    return {
        "name": source.source_name,
        "version": source.source_version,
        "url": source.source_url,
        "fetched_at": _format_datetime(source.fetched_at) if source.fetched_at else None,
        "response_status": source.response_status,
        "sha256": source.artifact_sha256,
        "confidence": _confidence_context(source),
    }


def _risk_context(item: Any) -> dict[str, Any]:
    return {
        "risk_type": item.risk_type,
        "description": item.description,
        "confidence": _confidence_context(item.source),
    }


def _source_context(source: SourceMetadata) -> dict[str, Any]:
    return {
        "source_name": source.source_name,
        "source_url": source.source_url,
        "fetched_at": _format_datetime(source.fetched_at) if source.fetched_at else None,
        "response_status": (
            str(source.response_status)
            if source.response_status is not None
            else "brak danych"
        ),
        "confidence_pct": _format_percent(source.confidence * 100.0),
        "confidence_label": describe_confidence(
            source.confidence, source.manual_review_required
        ),
        "manual_review_required": source.manual_review_required,
    }


def _warning_context(warning: WarningMessage) -> dict[str, Any]:
    labels = {"info": "Informacja", "warning": "Ostrzeżenie", "error": "Błąd"}
    return {
        "code": warning.code,
        "message": warning.message,
        "severity": warning.severity,
        "severity_label": labels.get(warning.severity, warning.severity),
        "source_name": warning.source_name,
    }


def _confidence_context(source: SourceMetadata | None) -> dict[str, Any] | None:
    if source is None:
        return None
    return {
        "pct": _format_percent(source.confidence * 100.0),
        "label": describe_confidence(source.confidence, source.manual_review_required),
        "manual_review_required": source.manual_review_required,
    }


def _build_limitations(
    response: AnalyzeResponse,
    map_warning: str | None,
) -> list[str]:
    """Buduje sekcję ograniczeń, spójną z zapisanymi ostrzeżeniami analizy.

    Ograniczenia wynikają wprost z danych snapshotu — brak sekcji, dane ręczne
    MPZP, fallback WMS, ``manual_review_required`` i niepewny parser dokumentów.
    Nie tworzymy sprzecznych statusów: informacje pochodzą z tych samych pól,
    które napędzają panel wyników.
    """
    limitations: list[str] = []

    if map_warning is not None:
        limitations.append(map_warning)

    if response.parcel is None:
        limitations.append("Nie ustalono geometrii działki — analiza jest niepełna.")
    if not response.mpzp_zones:
        limitations.append(
            "Brak stref MPZP w wyniku — nie sprawdzono planu albo działka nie "
            "przecina wektorowych danych MPZP."
        )
    if response.manual_zone_required:
        limitations.append(
            "Gmina nie udostępnia wektorowych danych MPZP. Symbol strefy wymaga "
            "ręcznego odczytania z mapy rastrowej (fallback WMS)."
        )
    if response.pog is None:
        limitations.append(
            "Snapshot nie zawiera wyniku Planu Ogólnego Gminy (POG); brak wyniku "
            "nie oznacza braku planu."
        )
    else:
        if response.pog.legal_status != "binding":
            limitations.append(
                "Status prawny POG: "
                f"{LEGAL_STATUS_LABELS_PL[response.pog.legal_status]}."
            )
        if response.pog.manual_review_required:
            limitations.append("Wynik POG wymaga ręcznej weryfikacji.")

    if response.utilities_preview is None:
        limitations.append(
            "Snapshot nie zawiera wyniku sprawdzenia pokrycia KIUT; brak danych "
            "nie może być interpretowany jako brak sieci."
        )
    elif response.utilities_preview.coverage_status == "unknown":
        limitations.append(
            "Nie udało się sprawdzić pokrycia KIUT. Pusty podgląd nie oznacza "
            "braku sieci."
        )
    elif response.utilities_preview.coverage_status == "not_covered":
        limitations.append(
            "KIUT nie potwierdził publikacji GESUT przez powiat. Nie jest to "
            "potwierdzenie braku sieci na działce."
        )

    if any(zone.assignment_method == "document_candidate" for zone in response.mpzp_zones):
        limitations.append(
            "Strefę MPZP przypisano bez wektora wydzieleń (punktowe discovery i "
            "dokument); przypisanie ma obniżoną pewność."
        )
    if any(
        parameter.conflict_group_id
        for zone in response.mpzp_zones
        for parameter in zone.parameters
    ):
        limitations.append(
            "Uchwała zawiera sprzeczne wartości parametrów MPZP; żadna nie została "
            "wybrana automatycznie."
        )

    manual_mpzp_zones = [
        zone
        for zone in response.mpzp_zones
        if zone.assignment_method == "manual_user_input"
        or zone.source.source_name.casefold() == "manual_user_input"
    ]
    if manual_mpzp_zones:
        limitations.append(
            "Symbol strefy podano ręcznie na podstawie podglądu rastrowego (WMS), "
            "bez wektorowej granicy strefy. Każdy parametr zależny od tego symbolu "
            "wymaga weryfikacji w materiale źródłowym, a wynik nie może być pełny."
        )
    if any(
        zone.intersection_pct is None and not zone.touches_boundary
        for zone in response.mpzp_zones
    ):
        limitations.append(
            "Udział strefy MPZP w powierzchni działki jest nieustalony (brak "
            "wektorowej granicy strefy) — raport nie przyjmuje, że strefa obejmuje "
            "całą działkę."
        )
    if response.pog is not None and response.pog.compatibility_assessment is not None:
        limitations.append(
            "Ocena relacji MPZP–POG jest analizą informacyjną według jawnej tabeli "
            "reguł; nie stwierdza prawnej możliwości zabudowy."
        )

    if any(
        zone.source.manual_review_required
        and zone.assignment_method != "manual_user_input"
        and zone.source.source_name.casefold() != "manual_user_input"
        for zone in response.mpzp_zones
    ):
        limitations.append(
            "Część parametrów MPZP pochodzi z niepewnego parsera dokumentów i "
            "wymaga ręcznej weryfikacji."
        )
    if any(item.source.manual_review_required for item in response.infrastructure):
        limitations.append(
            "Dane o uzbrojeniu terenu wymagają ręcznej weryfikacji u gestora sieci."
        )
    if any(item.source.manual_review_required for item in response.risks):
        limitations.append("Dane o ryzykach wymagają ręcznej weryfikacji.")

    limitations.extend(_terrain_limitations(response.terrain))
    return limitations


def _terrain_limitations(terrain: TerrainResult | None) -> list[str]:
    terrain = terrain or terrain_from_snapshot(None)
    limitations: list[str] = []
    if terrain.status == "unknown":
        limitations.append(
            "Zapisany wynik nie zawiera pomiaru NMT — rzeźba terenu jest nieznana."
        )
    elif terrain.status == "no_coverage":
        limitations.append(
            "Brak pokrycia danymi NMT — deniwelacja nieznana; nie oznacza to "
            "płaskiego terenu."
        )
    elif terrain.status == "unavailable":
        limitations.append(
            "Pomiar NMT był niedostępny — deniwelacja nieznana; nie oznacza to "
            "płaskiego terenu."
        )
    relief = terrain.relief
    if relief is not None and relief.status != "available":
        limitations.append(
            "Spadku, ekspozycji i profilu nie policzono "
            f"({relief.reason_code or relief.status}); brak statystyk nie oznacza "
            "płaskiego terenu."
        )
    elif relief is not None:
        limitations.append(
            "Spadek i ekspozycja pochodzą z rastra NMT metodą Horna "
            f"({relief.algorithm_version}); klasy nachylenia "
            f"({relief.slope_classes_version}) są konwencją systemu, nie normą "
            "prawną, a wynik nie zastępuje mapy do celów projektowych."
        )
    return limitations


# --- Renderowanie HTML i PDF ---

_JINJA_ENV = Environment(
    autoescape=select_autoescape(default=True, default_for_string=True),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _render_report_html(context: dict[str, Any]) -> str:
    """Renderuje szablon HTML raportu z automatycznym escapowaniem danych."""
    template = _JINJA_ENV.from_string(_REPORT_TEMPLATE)
    return template.render(**context)


def _html_to_pdf(html: str) -> bytes:
    """Zamienia HTML na PDF przez WeasyPrint.

    WeasyPrint jest importowany leniwie, ponieważ wymaga bibliotek systemowych
    (Pango/Cairo) obecnych w obrazie Docker, ale nie zawsze na maszynie
    developera. Dzięki temu import modułu i całej aplikacji nie zależy od tych
    bibliotek. Szczegóły wyjątku WeasyPrint nie są propagowane do warstwy HTTP.
    """
    try:
        from weasyprint import HTML  # import leniwy: patrz docstring
    except (ImportError, OSError) as exc:  # pragma: no cover - zależne od środowiska
        raise AnalysisReportRenderError(
            "Biblioteka WeasyPrint lub jej zależności systemowe nie są dostępne."
        ) from exc

    try:
        return HTML(string=html).write_pdf()
    except Exception as exc:  # noqa: BLE001 - granica: nie ujawniamy detali WeasyPrint
        logger.exception("WeasyPrint nie wygenerował PDF")
        raise AnalysisReportRenderError(
            "Nie udało się wygenerować dokumentu PDF."
        ) from exc


def _format_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.strftime("%d.%m.%Y %H:%M")


def _format_date(value: date | None) -> str | None:
    if value is None:
        return None
    return value.strftime("%d.%m.%Y")


def _format_number(value: float | None, decimals: int = 2) -> str | None:
    """Formatuje liczbę ze spacją jako separatorem tysięcy, spójnie z pl-PL."""
    if value is None:
        return None
    formatted = f"{value:,.{decimals}f}"
    # pl-PL: separator tysięcy to spacja, separator dziesiętny to przecinek.
    return formatted.replace(",", "\u00a0").replace(".", ",")


def _format_trimmed(value: float | None, max_decimals: int = 3) -> str | None:
    """Liczba bez zbędnych zer (112.3 → 112,3; 0.0 → 0), pl-PL."""
    if value is None:
        return None
    formatted = _format_number(value, decimals=max_decimals) or ""
    if "," in formatted:
        formatted = formatted.rstrip("0").rstrip(",")
    return "0" if formatted in {"-0", ""} else formatted


def _format_meters(value: float | None) -> str | None:
    trimmed = _format_trimmed(value)
    return f"{trimmed}\u00a0m" if trimmed is not None else None


def _format_optional_number(value: float | None, unit: str | None = None) -> str | None:
    if value is None:
        return None
    number = _format_number(value)
    return f"{number}\u00a0{unit}" if unit else number


def _format_percent(value: float | None) -> str | None:
    if value is None:
        return None
    return f"{_format_number(value, decimals=1)}%"


def _format_optional_percent(value: float | None) -> str | None:
    return _format_percent(value) if value is not None else None


def _format_bool(value: bool) -> str:
    return "Tak" if value else "Nie"


# Szablon HTML raportu. Wszystkie wartości dynamiczne przechodzą przez
# autoescape Jinja2. Układ jest zoptymalizowany pod A4 i czytelność w druku.
_REPORT_TEMPLATE = """<!DOCTYPE html>
<html lang="pl">
<head>
<meta charset="utf-8" />
<title>{{ title }}</title>
<style>
  @page {
    size: A4;
    margin: 18mm 16mm 20mm 16mm;
    @bottom-center {
      content: "{{ system_name }} — strona " counter(page) " z " counter(pages);
      font-size: 8pt;
      color: #667085;
    }
  }
  body {
    font-family: "DejaVu Sans", "Liberation Sans", sans-serif;
    color: #1a2027;
    font-size: 10pt;
    line-height: 1.45;
  }
  h1 { font-size: 20pt; margin: 0 0 4mm 0; color: #0d5137; }
  h2 { font-size: 13pt; margin: 6mm 0 2mm 0; color: #0d5137;
       border-bottom: 1px solid #d0d5dd; padding-bottom: 1mm; }
  .cover { text-align: center; padding: 30mm 0 10mm 0; }
  .cover .subtitle { font-size: 12pt; color: #475467; margin-top: 2mm; }
  .cover .meta { margin-top: 12mm; font-size: 11pt; }
  .cover .meta div { margin: 1.5mm 0; }
  .status-badge {
    display: inline-block; padding: 1mm 3mm; border-radius: 3mm;
    background: #eef3f0; color: #0d5137; font-weight: bold;
  }
  table { width: 100%; border-collapse: collapse; margin: 2mm 0; }
  th, td { text-align: left; padding: 1.5mm 2mm; border-bottom: 1px solid #e4e7ec;
           vertical-align: top; }
  th { background: #f8f9fb; font-weight: bold; width: 42%; }
  .section { margin-top: 4mm; }
  .empty { color: #98a2b3; font-style: italic; }
  .map-figure { text-align: center; margin: 3mm 0; }
  .map-figure img { max-width: 100%; border: 1px solid #d0d5dd; }
  .map-caption { font-size: 8.5pt; color: #475467; margin: 1.5mm 0 0; text-align: left; }
  .item { border: 1px solid #e4e7ec; border-radius: 2mm; padding: 2mm 3mm;
          margin: 2mm 0; break-inside: avoid; }
  .item .item-title { font-weight: bold; font-size: 11pt; }
  .tag { display: inline-block; padding: 0.3mm 2mm; border-radius: 2mm;
         font-size: 8pt; margin-left: 2mm; }
  .tag-dominant { background: #eef3f0; color: #0d5137; }
  .tag-review { background: #fdeceb; color: #8f2525; }
  .confidence { font-size: 8.5pt; color: #475467; margin-top: 1mm; }
  .status-note { font-size: 9pt; color: #6b3a10; margin: 1mm 0; }
  .mono { font-family: "DejaVu Sans Mono", monospace; font-size: 7.5pt; word-break: break-all; }
  table.provenance td, table.provenance th { font-size: 8.5pt; }
  table.zones { table-layout: fixed; }
  table.evidence { table-layout: fixed; }
  table.evidence th, table.evidence td { width: auto; font-size: 8pt; padding: 1mm; overflow-wrap: anywhere; }
  table.zones th, table.zones td { width: auto; font-size: 8pt; padding: 1mm; }
  a { color: #0d5137; }
  .warning-info { border-left: 3px solid #1769aa; }
  .warning-warning { border-left: 3px solid #c96a1f; }
  .warning-error { border-left: 3px solid #c43d3d; }
  ul.limitations { margin: 2mm 0; padding-left: 6mm; }
  ul.limitations li { margin: 1mm 0; }
  .disclaimer {
    margin-top: 8mm; padding: 3mm; border: 1px solid #c96a1f;
    background: #fff7ef; color: #6b3a10; font-size: 9.5pt; break-inside: avoid;
  }
  .page-break { break-before: page; }
  .manual-banner {
    margin: 8mm auto 0; padding: 3mm; max-width: 150mm; text-align: left;
    border: 2px solid #c43d3d; background: #fdeceb; color: #8f2525;
    font-weight: bold; font-size: 10pt;
  }
  .informational { font-size: 9pt; color: #475467; font-style: italic; }
</style>
</head>
<body>

<section class="cover">
  <h1>{{ title }}</h1>
  <div class="subtitle">{{ system_name }}</div>
  <div class="meta">
    <div><strong>Działka:</strong> {{ parcel_identifier if parcel_identifier else "nieustalona" }}</div>
    <div><strong>Identyfikator analizy:</strong> {{ analysis_id if analysis_id is not none else "—" }}</div>
    <div><strong>Data analizy:</strong> {{ analyzed_at if analyzed_at else "—" }}</div>
    <div><strong>Status:</strong> <span class="status-badge">{{ status }}</span></div>
    <div><strong>Raport wygenerowano:</strong> {{ generated_at if generated_at else "—" }}</div>
  </div>
  {% if manual_zone_declared %}
  <div class="manual-banner">UWAGA: symbol strefy podano ręcznie (odczyt użytkownika z podglądu rastrowego), bez wektorowej granicy strefy. Udział strefy w powierzchni działki jest nieustalony, a wszystkie zależne parametry wymagają weryfikacji.</div>
  {% endif %}
</section>

<section class="section">
  <h2>Mapa działki</h2>
  {% if map_data_uri %}
  <div class="map-figure">
    <img src="{{ map_data_uri }}" alt="Miniatura mapy działki i warstw analizy" />
    <p class="map-caption">
      Miniatura poglądowa: obrys działki na podkładzie mapowym
      {%- if map_kiut_overlay %}
      z nakładką uzbrojenia terenu (KIUT) pobraną przy generowaniu raportu
      {%- endif -%}.
      Nakładka WMS nie jest geometrią sieci ze snapshotu analizy i nie pozwala
      stwierdzić, czy dana sieć leży na działce.
    </p>
  </div>
  {% else %}
  <p class="empty">{{ map_warning if map_warning else "Miniatura mapy jest niedostępna." }}</p>
  {% endif %}
</section>

<section class="section">
  <h2>Parametry geometryczne</h2>
  {% if geometry %}
  <table>
    <tr><th>Powierzchnia</th><td>{{ geometry.area_sqm }} m²</td></tr>
    <tr><th>Powierzchnia (ha)</th><td>{{ geometry.area_ha }} ha</td></tr>
    <tr><th>Obwód</th><td>{{ geometry.perimeter_m }} m</td></tr>
    <tr><th>Geometria poprawna</th><td>{{ geometry.is_valid }}</td></tr>
    <tr><th>Geometria naprawiana</th><td>{{ geometry.geometry_repaired }}</td></tr>
    {% if geometry.buildable_area_sqm %}
    <tr><th>Szacowany obszar zabudowy (przybliżenie techniczne)</th><td>{{ geometry.buildable_area_sqm }} m²</td></tr>
    {% endif %}
  </table>
  {% else %}
  <p class="empty">Dane niedostępne — nie ustalono geometrii działki.</p>
  {% endif %}
</section>

<section class="section">
  <h2>Miejscowy Plan Zagospodarowania Przestrzennego (MPZP)</h2>
  {% if mpzp_zones %}
    {% for zone in mpzp_zones %}
    <div class="item">
      <div class="item-title">{{ zone.zone_symbol }}
        {% if zone.is_dominant %}<span class="tag tag-dominant">największy udział</span>{% endif %}
        {% if zone.touches_boundary %}<span class="tag tag-review">tylko styczność granicy</span>{% endif %}
        {% if zone.manual_review_required %}<span class="tag tag-review">wymaga weryfikacji</span>{% endif %}
      </div>
      {% if zone.is_manual %}<p class="status-note"><strong>Symbol strefy podano ręcznie</strong> — każdy parametr tej strefy wymaga weryfikacji.</p>{% endif %}
      <table>
        <tr><th>Sposób przypisania</th><td>{{ zone.assignment_label }}</td></tr>
        {% if zone.manual_selection %}
        <tr><th>Decyzja użytkownika</th><td>wpisany symbol {{ zone.manual_selection.entered_symbol }}{% if not zone.manual_selection.symbol_in_candidates %} <span class="tag tag-review">spoza kandydatów</span>{% endif %}<br><small>plan: {{ zone.manual_selection.plan_id or "nieustalony" }}; kandydaci: {{ zone.manual_selection.candidates }}; wybrano {{ zone.manual_selection.selected_at }}</small></td></tr>
        <tr><th>Dokument użyty przy wznowieniu</th><td>{% if zone.manual_selection.document_pinned %}kopia przypięta przy wstrzymaniu analizy{% if zone.manual_selection.document_fetched_at %} (pobrano {{ zone.manual_selection.document_fetched_at }}){% endif %}<br><small class="mono">SHA-256: {{ zone.manual_selection.document_sha256 }}</small>{% else %}<span class="empty">dokument nie został przypięty — parametry nieustalone</span>{% endif %}</td></tr>
        {% endif %}
        {% if zone.zone_id %}<tr><th>ID wydzielenia</th><td class="mono">{{ zone.zone_id }}</td></tr>{% endif %}
        {% if zone.act_identifier %}<tr><th>Plan (akt) i wersja</th><td>{{ zone.act_identifier }}{% if zone.act_version %}<br><small class="mono">wersja {{ zone.act_version }}</small>{% endif %}{% if zone.data_release_id %}<br><small>wydanie danych #{{ zone.data_release_id }}</small>{% endif %}</td></tr>{% endif %}
        {% if zone.primary_use %}<tr><th>Przeznaczenie podstawowe</th><td>{{ zone.primary_use }}</td></tr>{% endif %}
        {% if zone.supplementary_use %}<tr><th>Przeznaczenie uzupełniające</th><td>{{ zone.supplementary_use }}</td></tr>{% endif %}
        <tr><th>Udział w powierzchni działki</th><td>{% if zone.share_known %}{{ zone.intersection_pct }} ({{ zone.intersection_area_sqm }} m²){% else %}nieustalony — brak wektorowej granicy strefy{% endif %}</td></tr>
        {% if zone.max_building_height_m %}<tr><th>Maks. wysokość zabudowy</th><td>{{ zone.max_building_height_m }}</td></tr>{% endif %}
        {% if zone.max_floors %}<tr><th>Maks. liczba kondygnacji</th><td>{{ zone.max_floors }}</td></tr>{% endif %}
        {% if zone.min_biologically_active_pct %}<tr><th>Min. pow. biologicznie czynna</th><td>{{ zone.min_biologically_active_pct }}</td></tr>{% endif %}
        {% if zone.max_floor_area_ratio %}<tr><th>Maks. wskaźnik intensywności zabudowy</th><td>{{ zone.max_floor_area_ratio }}</td></tr>{% endif %}
        {% if zone.min_floor_area_ratio %}<tr><th>Min. wskaźnik intensywności zabudowy</th><td>{{ zone.min_floor_area_ratio }}</td></tr>{% endif %}
        {% if zone.max_building_coverage_pct %}<tr><th>Maks. powierzchnia zabudowy</th><td>{{ zone.max_building_coverage_pct }}</td></tr>{% endif %}
      </table>
      {% if zone.parameters %}
      <table class="evidence">
        <thead><tr><th>Parametr</th><th>Wartość</th><th>Źródło w uchwale</th><th>Pewność</th></tr></thead>
        <tbody>
        {% for parameter in zone.parameters %}
          <tr>
            <td>{{ parameter.name }}{% if parameter.conflict %}<br><span class="tag tag-review">sprzeczna kandydatura</span>{% endif %}</td>
            <td>{{ parameter.value if parameter.value is not none else "—" }}{% if parameter.unit %} {{ parameter.unit }}{% endif %}{% if parameter.raw_value %}<br><small>dosłownie: „{{ parameter.raw_value }}”</small>{% endif %}</td>
            <td>{% if parameter.page_number %}str. {{ parameter.page_number }}{% endif %}{% if parameter.segment_id %}, segment {{ parameter.segment_id }}{% endif %}{% if parameter.legal_unit_id %}, jednostka #{{ parameter.legal_unit_id }}{% endif %}
              {% if parameter.evidence_text %}<br><small>„{{ parameter.evidence_text }}”</small>{% endif %}
              {% if parameter.document_sha256 %}<br><small class="mono">SHA-256 dokumentu: {{ parameter.document_sha256 }}</small>{% endif %}
              <br><small>{{ parameter.extraction or "metoda nieznana" }}{% if parameter.parser_version %}, {{ parameter.parser_version }}{% endif %}</small></td>
            <td>{{ parameter.confidence_pct }}</td>
          </tr>
        {% endfor %}
        </tbody>
      </table>
      {% endif %}
      {% if zone.confidence %}
      <div class="confidence">Pewność przypisania: {{ zone.confidence.pct }} ({{ zone.confidence.label }})</div>
      {% endif %}
    </div>
    {% endfor %}
  {% else %}
  <p class="empty">Dane niedostępne — nie znaleziono ani nie sprawdzono stref MPZP przecinających działkę.</p>
  {% endif %}
</section>

<section class="section">
  <h2>Plan Ogólny Gminy (POG) i Obszar Uzupełnienia Zabudowy (OUZ)</h2>
  {% if pog %}
  <table>
    <tr><th>Status prawny aktu</th><td>{{ pog.legal_status_label }}</td></tr>
    <tr><th>Zakres danych przestrzennych</th><td>{{ pog.coverage_status_label }}</td></tr>
    <tr><th>Aktualność źródła</th><td>{{ pog.data_availability_label }}{% if pog.status_confirmed_at %} (potwierdzono {{ pog.status_confirmed_at }}){% endif %}</td></tr>
    {% if pog.status_evidence %}<tr><th>Podstawa statusu</th><td>{{ pog.status_evidence.source_name }}{% if pog.status_evidence.raw_value %} — kod {{ pog.status_evidence.raw_value }}{% endif %}{% if pog.status_evidence.reference %}<br><small class="mono">{{ pog.status_evidence.reference }}</small>{% endif %}</td></tr>{% endif %}
    {% if pog.coverage_evidence %}<tr><th>Potwierdzenie braku aktu</th><td>{{ pog.coverage_evidence.source_name }}{% if pog.coverage_evidence.reference %}<br><small class="mono">{{ pog.coverage_evidence.reference }}</small>{% endif %}</td></tr>{% endif %}
    <tr><th>Strefa planistyczna</th><td>{{ pog.planning_zone if pog.planning_zone else "—" }}</td></tr>
    <tr><th>Obszar Uzupełnienia Zabudowy (OUZ)</th><td>{{ pog.in_ouz }}</td></tr>
    {% if pog.ouz_intersection_pct %}<tr><th>Udział OUZ w działce</th><td>{{ pog.ouz_intersection_pct }} ({{ pog.ouz_intersection_area_sqm }} m²)</td></tr>{% endif %}
    <tr><th>Dotyka granicy OUZ</th><td>{{ pog.touches_ouz_boundary }}</td></tr>
    <tr><th>Obszar śródmiejski</th><td>{{ pog.in_downtown_area }}</td></tr>
    {% if pog.uchwala_nr %}<tr><th>Numer uchwały</th><td>{{ pog.uchwala_nr }}</td></tr>{% endif %}
    {% if pog.uchwala_date %}<tr><th>Data uchwały</th><td>{{ pog.uchwala_date }}</td></tr>{% endif %}
  </table>
  {% if pog.zones %}
  <h3>Strefy planistyczne przecinające działkę</h3>
  <table class="zones">
    <thead><tr><th>Strefa</th><th>Powierzchnia</th><th>Udział</th><th>Intensywność</th><th>Wysokość</th><th>Zabudowa</th><th>Biologicznie czynna</th><th>Źródło</th></tr></thead>
    <tbody>
    {% for zone in pog.zones %}
      <tr>
        <td>{{ zone.symbol or zone.type }}{% if zone.label %}<br><small>{{ zone.label }}</small>{% endif %}</td>
        <td>{{ zone.area_sqm }} m²</td><td>{{ zone.area_pct }}</td>
        <td>{{ zone.max_overground_floor_area_ratio if zone.max_overground_floor_area_ratio is not none else "—" }}</td>
        <td>{{ zone.max_building_height_m if zone.max_building_height_m is not none else "—" }}</td>
        <td>{{ zone.max_building_coverage_pct if zone.max_building_coverage_pct is not none else "—" }}</td>
        <td>{{ zone.min_biologically_active_pct if zone.min_biologically_active_pct is not none else "—" }}</td>
        <td>{% if zone.gml_url %}<a href="{{ zone.gml_url }}">GML</a>{% if zone.feature_version %}<br><small class="mono">{{ zone.feature_version }}</small>{% endif %}{% else %}—{% endif %}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  {% endif %}
  {% if pog.social_areas %}
  <h3>Standardy dostępności infrastruktury społecznej</h3>
  <table><thead><tr><th>Obszar</th><th>Powierzchnia</th><th>Udział</th></tr></thead><tbody>
  {% for area in pog.social_areas %}<tr><td>{{ area.symbol or area.label or area.id }}</td><td>{{ area.area_sqm }} m²</td><td>{{ area.area_pct }}</td></tr>{% endfor %}
  </tbody></table>
  {% endif %}
  {% if pog.act %}
  <h3>Źródła urzędowe aktu</h3>
  <table class="provenance">
    <tr><th>Identyfikator aktu</th><td>{{ pog.act.identifier }}{% if pog.act.title %}<br><small>{{ pog.act.title }}</small>{% endif %}</td></tr>
    <tr><th>Wersja aktu</th><td>{{ pog.act.version if pog.act.version else "—" }}{% if pog.act.version_started_at %} (początek wersji {{ pog.act.version_started_at }}){% endif %}</td></tr>
    {% if pog.act.publication_id %}<tr><th>Identyfikator publikacji</th><td class="mono">{{ pog.act.publication_id }}</td></tr>{% endif %}
    {% if pog.act.valid_from %}<tr><th>Obowiązuje od (wg APP)</th><td>{{ pog.act.valid_from }}{% if pog.act.valid_to %} do {{ pog.act.valid_to }}{% endif %}</td></tr>{% endif %}
    {% if pog.act.publication_date %}<tr><th>Data publikacji zbioru (CSW)</th><td>{{ pog.act.publication_date }}</td></tr>{% endif %}
    <tr><th>Wydanie danych</th><td>{% if pog.act.data_release_id %}#{{ pog.act.data_release_id }}{% endif %}{% if pog.act.release_label %} ({{ pog.act.release_label }}){% endif %}{% if pog.act.fetched_at %}, pobrano {{ pog.act.fetched_at }}{% endif %}</td></tr>
    {% if pog.act.artifact_sha256 %}<tr><th>SHA-256 artefaktu</th><td class="mono">{{ pog.act.artifact_sha256 }}</td></tr>{% endif %}
    <tr><th>GML wersji aktu</th><td>{% if pog.act.gml_url %}<a href="{{ pog.act.gml_url }}">Rejestr Urbanistyczny — WFS APP</a>{% else %}<span class="empty">brak zweryfikowanego odnośnika</span>{% endif %}</td></tr>
    <tr><th>Karta metadanych (CSW)</th><td>{% if pog.act.card_url %}<a href="{{ pog.act.card_url }}">rekord {{ pog.act.metadata.record_id if pog.act.metadata else "" }}</a>{% else %}<span class="empty">metadane CSW niedostępne</span>{% endif %}{% if pog.act.metadata and pog.act.metadata.record_sha256 %}<br><small class="mono">SHA-256 rekordu: {{ pog.act.metadata.record_sha256 }}</small>{% endif %}</td></tr>
  </table>
  {% if pog.act.documents %}
  <table class="provenance">
    <thead><tr><th>Dokument formalny</th><th>Relacja i daty</th><th>SHA-256 rekordu</th></tr></thead>
    <tbody>
    {% for document in pog.act.documents %}
      <tr>
        <td>{% if document.link %}<a href="{{ document.link }}">{{ document.title }}</a>{% else %}{{ document.title }}{% endif %}<br><small class="mono">{{ document.identifier }}{% if document.version %} / {{ document.version }}{% endif %}</small>
          {% if document.raw_link %}<br><small>{{ document.raw_link }}</small>{% endif %}
          {% if document.warning %}<br><span class="tag tag-review">{{ document.warning }}</span>{% endif %}</td>
        <td>{{ document.relation if document.relation else "—" }}{% if document.document_date %}<br>data: {{ document.document_date }}{% endif %}{% if document.effective_date %}<br>w życie: {{ document.effective_date }}{% endif %}{% if document.repeal_date %}<br>uchylony: {{ document.repeal_date }}{% endif %}</td>
        <td class="mono">{{ document.record_sha256 if document.record_sha256 else "—" }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  {% endif %}
  {% endif %}
  {% for note in pog.status_notes %}<p class="status-note">{{ note }}</p>{% endfor %}
  {% if pog.manual_review_required %}<p><span class="tag tag-review">wynik POG wymaga ręcznej weryfikacji</span></p>{% endif %}
  {% if pog.confidence %}<div class="confidence">Pewność danych: {{ pog.confidence.pct }} ({{ pog.confidence.label }})</div>{% endif %}
  {% else %}
  <p class="empty">Dane niedostępne — brak danych POG/OUZ dla tej działki.</p>
  {% endif %}
</section>

<section class="section">
  <h2>Relacja MPZP–POG — analiza informacyjna</h2>
  <p class="informational">Ustalenia MPZP i POG są przedstawione osobno w sekcjach powyżej. Ta sekcja nie jest opinią prawną i nie stwierdza prawnej możliwości zabudowy.</p>
  {% if compatibility %}
  <table>
    <tr><th>Wynik oceny</th><td>{{ compatibility.status_label }} <small class="mono">({{ compatibility.status }}, {{ compatibility.reason_code }})</small>{% if compatibility.manual_review_required %}<br><span class="tag tag-review">wymaga weryfikacji</span>{% endif %}</td></tr>
    <tr><th>Stan prawny na dzień</th><td>{{ compatibility.as_of if compatibility.as_of else "nieustalony" }}</td></tr>
    <tr><th>Zestaw reguł</th><td>{% if compatibility.rule_id %}{{ compatibility.rule_id }} v{{ compatibility.rule_version }}{% else %}brak — zapis historyczny{% endif %}</td></tr>
    <tr><th>Uzasadnienie</th><td>{{ compatibility.rationale }}</td></tr>
    <tr><th>Reguła agregacji</th><td><small>{{ compatibility.aggregation }}</small></td></tr>
    {% if compatibility.legacy %}<tr><th>Zapis historyczny (legacy)</th><td>{{ compatibility.legacy.origin }}: {{ "stwierdzono konflikt" if compatibility.legacy.conflict else ("brak konfliktu" if compatibility.legacy.conflict is sameas false else "brak rozstrzygnięcia") }}{% if compatibility.legacy.result %} ({{ compatibility.legacy.result }}){% endif %} — bez danych reguły, nie jest pełną oceną</td></tr>{% endif %}
  </table>
  {% if compatibility.pairs %}
  <table class="evidence">
    <thead><tr><th>Para stref</th><th>Wynik</th><th>Wspólna część działki</th><th>Reguła i stan prawny</th><th>Uzasadnienie</th></tr></thead>
    <tbody>
    {% for pair in compatibility.pairs %}
      <tr><td>{{ pair.label }}</td><td>{{ pair.status_label }}</td><td>{{ pair.overlap }}{% if not pair.spatial %}<br><small>para niezidentyfikowana przestrzennie</small>{% endif %}</td><td>{{ pair.rule }}{% if pair.as_of %}<br><small>stan na {{ pair.as_of }}</small>{% endif %}</td><td><small>{{ pair.rationale }}</small></td></tr>
    {% endfor %}
    </tbody>
  </table>
  {% endif %}
  {% if compatibility.sources %}
  <table class="provenance">
    <thead><tr><th>Źródło oceny</th><th>Odniesienie i wersja</th></tr></thead>
    <tbody>
    {% for source in compatibility.sources %}
      <tr><td>{{ source.label }}</td><td><small>{{ source.reference or "—" }}</small>{% if source.version %}<br><small class="mono">wersja {{ source.version }}</small>{% endif %}{% if source.as_of %}<br><small>stan na {{ source.as_of }}</small>{% endif %}</td></tr>
    {% endfor %}
    </tbody>
  </table>
  {% endif %}
  <p class="informational">{{ compatibility.informational_notice }}</p>
  {% else %}
  <p class="empty">Nie wykonano oceny relacji MPZP–POG dla tej analizy.</p>
  {% endif %}
</section>

<section class="section">
  <h2>Podgląd uzbrojenia terenu (KIUT)</h2>
  {% if utilities_preview %}
  <table>
    <tr><th>Status pokrycia</th><td>{{ utilities_preview.coverage_label }}</td></tr>
    {% if utilities_preview.county_name %}<tr><th>Powiat</th><td>{{ utilities_preview.county_name }}</td></tr>{% endif %}
    <tr><th>Warstwa podglądowa potwierdzona</th><td>{{ utilities_preview.layer_available }}</td></tr>
    <tr><th>Źródło</th><td>{{ utilities_preview.source_name }}</td></tr>
    {% if utilities_preview.fetched_at %}<tr><th>Sprawdzono</th><td>{{ utilities_preview.fetched_at }}</td></tr>{% endif %}
  </table>
  <p>{{ utilities_preview.note }}</p>
  {% if utilities_preview.style_legend %}<p>{{ utilities_preview.style_legend }}</p>{% endif %}
  <p><strong>Raport nie zawiera odległości ani liczby sieci wyliczonych z podglądu WMS.</strong></p>
  <p><strong>Nie da się na podstawie podglądu WMS stwierdzić, czy konkretna sieć leży na działce.</strong>
     Brak linii na obrazie nie oznacza braku sieci, a linia na obrazie nie jest geometrią do pomiaru przecięcia.</p>
  {% else %}
  <p class="empty">Nie sprawdzono pokrycia KIUT dla tego snapshotu. Brak danych nie oznacza braku sieci.</p>
  {% endif %}
</section>

<section class="section">
  <h2>Infrastruktura i sieci uzbrojenia terenu</h2>
  {% if infrastructure %}
    {% for item in infrastructure %}
    <div class="item">
      <div class="item-title">{{ item.network_type }}</div>
      <table>
        <tr><th>Bufor techniczny</th><td>{{ item.buffer_m }} m</td></tr>
        {% if item.affects_buildable_area %}<tr><th>Wpływ na obszar zabudowy</th><td>pomniejszył o {{ item.zone_area_sqm }} m²</td></tr>{% endif %}
        {% if item.rule_source %}<tr><th>Podstawa reguły bufora</th><td>{{ item.rule_source }}</td></tr>{% endif %}
        {% if item.rule_note %}<tr><th>Uwagi</th><td>{{ item.rule_note }}</td></tr>{% endif %}
      </table>
      {% if item.confidence %}<div class="confidence">Pewność danych: {{ item.confidence.pct }} ({{ item.confidence.label }})</div>{% endif %}
    </div>
    {% endfor %}
  {% else %}
  <p class="empty">Dane niedostępne — nie wykryto ani nie sprawdzono sieci uzbrojenia terenu.</p>
  {% endif %}
</section>

<section class="section">
  <h2>Ryzyka i formy ochrony</h2>
  {% if risks %}
    {% for item in risks %}
    <div class="item">
      <div class="item-title">{{ item.risk_type }}</div>
      <p>{{ item.description }}</p>
      {% if item.confidence %}<div class="confidence">Pewność danych: {{ item.confidence.pct }} ({{ item.confidence.label }})</div>{% endif %}
    </div>
    {% endfor %}
  {% else %}
  <p class="empty">Dane niedostępne — nie wykryto ani nie sprawdzono ryzyk ani form ochrony.</p>
  {% endif %}
</section>

<section class="section">
  <h2>Rzeźba terenu (NMT)</h2>
  <table>
    <tr><th>Status pomiaru</th><td>{{ terrain.status_label }}{% if terrain.reason_code %} <small class="mono">({{ terrain.reason_code }})</small>{% endif %}</td></tr>
    {% if terrain.status == "available" %}
    <tr><th>Najniższa wysokość (Hmin)</th><td>{{ terrain.min_height }}</td></tr>
    <tr><th>Najwyższa wysokość (Hmax)</th><td>{{ terrain.max_height }}</td></tr>
    <tr><th>Deniwelacja (Hmax − Hmin)</th><td><strong>{{ terrain.height_difference }}</strong></td></tr>
    {% endif %}
    {% if terrain.grid_size %}<tr><th>Siatka próbkowania usługi</th><td>{{ terrain.grid_size }}</td></tr>{% endif %}
    {% if terrain.sampled_points is not none %}<tr><th>Liczba punktów siatki</th><td>{{ terrain.sampled_points }}</td></tr>{% endif %}
    {% if terrain.source %}
    <tr><th>Źródło</th><td>{{ terrain.source.name }}{% if terrain.source.fetched_at %} — pobrano {{ terrain.source.fetched_at }}{% endif %}{% if terrain.source.response_status %} (HTTP {{ terrain.source.response_status }}){% endif %}{% if terrain.source.sha256 %}<br><small class="mono">SHA-256 odpowiedzi: {{ terrain.source.sha256 }}</small>{% endif %}</td></tr>
    {% endif %}
  </table>
  {% if terrain.note %}<p class="informational">{{ terrain.note }}</p>{% endif %}
  {% for warning in terrain.warnings %}<p class="informational">{{ warning }}</p>{% endfor %}

  {% set relief = terrain.relief %}
  <h3>Spadek, ekspozycja i profil (raster NMT)</h3>
  {% if not relief %}
  <p class="empty">Nie liczono pochodnych rastra NMT dla tej analizy.</p>
  {% else %}
  <table>
    <tr><th>Status obliczeń</th><td>{{ relief.status_label }}{% if relief.reason_code %} <small class="mono">({{ relief.reason_code }})</small>{% endif %}</td></tr>
    {% if relief.resolution %}<tr><th>Rozdzielczość danych źródłowych</th><td>{{ relief.resolution }}</td></tr>{% endif %}
    {% if relief.status == "available" %}
    <tr><th>Zmierzona część działki</th><td>{{ relief.valid_share }} ({{ relief.valid_pixel_count }} z {{ relief.parcel_pixel_count }} pikseli)</td></tr>
    <tr><th>Wysokości z rastra (min / śr. / max)</th><td>{{ relief.min_height }} / {{ relief.mean_height }} / {{ relief.max_height }}</td></tr>
    {% endif %}
    <tr><th>Algorytm</th><td class="mono">{{ relief.algorithm_version }}; klasy {{ relief.slope_classes_version }}</td></tr>
  </table>
  {% if relief.slope %}
  <table class="evidence">
    <thead><tr><th>Spadek</th><th>Stopnie</th><th>Procent</th></tr></thead>
    <tbody>
    {% for label, deg, pct in relief.slope %}
      <tr><td>{{ label }}</td><td>{{ "%.2f"|format(deg)|replace(".", ",") }}°</td><td>{{ "%.2f"|format(pct)|replace(".", ",") }}%</td></tr>
    {% endfor %}
    </tbody>
  </table>
  {% endif %}
  {% if relief.classes %}
  <table class="evidence">
    <thead><tr><th>Klasa nachylenia</th><th>Powierzchnia</th><th>Udział</th></tr></thead>
    <tbody>
    {% for item in relief.classes %}<tr><td>{{ item.label }}</td><td>{{ item.area }} m²</td><td>{{ item.share }}</td></tr>{% endfor %}
    </tbody>
  </table>
  {% endif %}
  {% if relief.aspect %}
  <p>Ekspozycja:
    {% if relief.aspect.status == "flat" %}nie wyznaczono — teren płaski (nachylone ≥ {{ relief.aspect.flat_threshold }}% jest {{ relief.aspect.non_flat_share }} powierzchni).
    {% elif relief.aspect.status == "dispersed" %}rozproszona — brak dominującego kierunku (średni azymut {{ relief.aspect.azimuth }}°, wypadkowa {{ relief.aspect.resultant }}).
    {% else %}<strong>{{ relief.aspect.direction }}</strong> (średni azymut spadku {{ relief.aspect.azimuth }}°, wypadkowa {{ relief.aspect.resultant }}).{% endif %}
  </p>
  {% endif %}
  {% if relief.profile %}
  <p>Profil wysokościowy: od {{ relief.profile.start }} do {{ relief.profile.end }} (EPSG:2180), długość {{ relief.profile.length }}, krok {{ relief.profile.step }}, {{ relief.profile.sample_count }} próbek{% if relief.profile.missing_count %}, w tym {{ relief.profile.missing_count }} bez danych{% endif %}.</p>
  {% if relief.profile.segments %}
  <svg class="profile" viewBox="0 0 {{ relief.profile.width }} {{ relief.profile.height }}" width="170mm" height="42mm" xmlns="http://www.w3.org/2000/svg">
    <rect x="0" y="0" width="{{ relief.profile.width }}" height="{{ relief.profile.height }}" fill="#f9fafb" stroke="#d0d5dd"/>
    {% for points in relief.profile.segments %}<polyline points="{{ points }}" fill="none" stroke="#155eef" stroke-width="2"/>{% endfor %}
    <text x="10" y="18" font-size="11" fill="#475467">{{ relief.profile.max_label }}</text>
    <text x="10" y="{{ relief.profile.height - 10 }}" font-size="11" fill="#475467">{{ relief.profile.min_label }}</text>
  </svg>
  {% endif %}
  {% endif %}
  {% if relief.raster %}
  <table class="provenance">
    <tr><th>Pokrycie WCS</th><td class="mono">{{ relief.raster.coverage_id }}{% if relief.raster.vertical_datum %} — układ wysokości {{ relief.raster.vertical_datum }}{% endif %}</td></tr>
    <tr><th>Okno rastra</th><td>{{ relief.raster.size }}, bufor {{ relief.raster.buffer }}<br><small class="mono">bbox EPSG:2180: {{ relief.raster.bbox }}</small></td></tr>
    <tr><th>NoData</th><td><small>{{ relief.raster.nodata_policy }}</small></td></tr>
    {% if relief.raster.gdal_version %}<tr><th>Środowisko</th><td class="mono">{{ relief.raster.gdal_version }}</td></tr>{% endif %}
  </table>
  {% endif %}
  {% if relief.source %}
  <p><small>Źródło: {{ relief.source.name }}{% if relief.source.version %} ({{ relief.source.version }}){% endif %}{% if relief.source.fetched_at %}, pobrano {{ relief.source.fetched_at }}{% endif %}{% if relief.source.sha256 %}<br><span class="mono">SHA-256 GeoTIFF: {{ relief.source.sha256 }}</span>{% endif %}</small></p>
  {% endif %}
  {% for warning in relief.warnings %}<p class="informational">{{ warning }}</p>{% endfor %}
  {% endif %}
</section>

<section class="section">
  <h2>Źródła danych</h2>
  {% if sources %}
  <table>
    <tr>
      <th style="width:18%">Źródło</th>
      <th style="width:22%">Data pobrania</th>
      <th style="width:20%">Pewność</th>
      <th style="width:15%">Status</th>
      <th style="width:25%">Weryfikacja</th>
    </tr>
    {% for source in sources %}
    <tr>
      <td>{{ source.source_name }}</td>
      <td>{{ source.fetched_at if source.fetched_at else "brak danych" }}</td>
      <td>{{ source.confidence_pct }} ({{ source.confidence_label }})</td>
      <td>{{ source.response_status }}</td>
      <td>{{ "wymaga ręcznej weryfikacji" if source.manual_review_required else "nie wymaga" }}</td>
    </tr>
    {% endfor %}
  </table>
  {% else %}
  <p class="empty">Dane niedostępne — brak zarejestrowanych źródeł dla tej analizy.</p>
  {% endif %}
</section>

<section class="section">
  <h2>Ostrzeżenia</h2>
  {% if warnings %}
    {% for warning in warnings %}
    <div class="item warning-{{ warning.severity }}">
      <div class="item-title">{{ warning.severity_label }}{% if warning.source_name %} — {{ warning.source_name }}{% endif %}</div>
      <p>{{ warning.message }}</p>
    </div>
    {% endfor %}
  {% else %}
  <p class="empty">Brak ostrzeżeń dla tej analizy.</p>
  {% endif %}
</section>

<section class="section">
  <h2>Ograniczenia analizy</h2>
  {% if limitations %}
  <ul class="limitations">
    {% for limitation in limitations %}<li>{{ limitation }}</li>{% endfor %}
  </ul>
  {% else %}
  <p class="empty">Nie zidentyfikowano dodatkowych ograniczeń dla tej analizy.</p>
  {% endif %}
</section>

<div class="disclaimer">{{ disclaimer }}</div>

</body>
</html>
"""
