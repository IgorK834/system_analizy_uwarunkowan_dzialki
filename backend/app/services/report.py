"""Generator raportu PDF v2 analizy uwarunkowań przestrzennych działki.

Raport (BK-501–503, ADR-010) jest budowany wyłącznie z zapisanego snapshotu
analizy (``build_analyze_response_from_analysis``) i zamrożonego snapshotu map
(``analyses.report_map_snapshot``). Nie uruchamia analizy, nie odpytuje usług
domenowych i nie pobiera WMS: mapy są renderowane lokalnie, a WeasyPrint
dostaje ``url_fetcher`` odrzucający każdy zasób poza ``data:``.

Struktura: dziesięć sekcji z ``reporting.domain.sections`` w stałej kolejności
oraz załącznik z tabelą mapowania pól API. Każda wartość jest oznaczona jako
fakt źródłowy, wynik obliczenia, przybliżenie albo dane ręczne; ``null`` jest
pokazywany jako „nie określono” (różne od 0), a brak danych — jawnie, z
powodem. Raport nie zawiera syntetycznego scoringu ani uśredniania parametrów
różnych stref. Szablon HTML (``app/templates/report.html``) jest renderowany
przez Jinja2 z ``autoescape``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Final, Literal

from geoalchemy2.shape import to_shape
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from sqlalchemy.orm import Session

from app.core.planning_compatibility import COMPATIBILITY_STATUS_LABELS_PL
from app.core.report_config import (
    REPORT_DISCLAIMER,
    REPORT_SYSTEM_NAME,
    REPORT_TITLE,
    describe_confidence,
    pog_zone_layer_style,
    report_pog_style,
)
from app.models.analysis import Analysis
from app.modules.reporting.domain.field_mapping import MAPPING_KIND_LABELS
from app.modules.reporting.domain.sections import (
    FINDING_KIND_DESCRIPTIONS,
    FINDING_KIND_LABELS,
    NOT_SPECIFIED,
    QUALITY_SECTIONS,
    REPORT_LAYOUT_VERSION,
    REPORT_SECTIONS,
    SECTION_BY_ID,
    SECTION_STATUS_LABELS,
)
from app.schemas.analyze import (
    AnalyzeResponse,
    MpzpZoneResult,
    PogResult,
    RiskResult,
    RiskSectionResult,
    TerrainReliefResult,
    TerrainResult,
)
from app.schemas.source import (
    SectionQuality,
    SectionQualityMatrix,
    SourceMetadata,
    WarningMessage,
)
from app.services.mpzp_zones import PARSER_TO_API_PARAMETER_MAP
from app.services.persistence import build_analyze_response_from_analysis
from app.services.pog_provenance import RELATION_LABELS_PL
from app.services.report_fields import field_presence
from app.services.report_map import RenderedMap, ReportMaps, format_length_m, render_report_maps
from app.services.report_map_snapshot import build_report_map_snapshot
from app.services.risks import risk_sections_from_snapshot
from app.services.section_quality import build_section_quality
from app.services.terrain import terrain_from_snapshot
from app.shared.model_reading import (
    EXTRACTION_METHOD_LLM_VERIFIED,
    MODEL_READING_DISCLAIMER,
    MODEL_READING_MARK,
    MODEL_READING_SHORT,
    NO_DATA_NOT_NO_RESTRICTION,
    NULL_NOT_ZERO,
    is_model_reading,
)
from app.shared.data_quality import (
    FRESHNESS_LABELS_PL,
    FRESHNESS_NO_POLICY,
    FreshnessRule,
    evaluate_freshness,
    format_age_pl,
    reason_label_pl,
)
from app.shared.planning_status import (
    COVERAGE_STATUS_LABELS_PL,
    DATA_AVAILABILITY_LABELS_PL,
    LEGAL_STATUS_LABELS_PL,
    pog_status_notes_pl,
)

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
REPORT_TEMPLATE_NAME = "report.html"


class AnalysisReportNotFoundError(Exception):
    """Analiza o podanym identyfikatorze nie istnieje w bazie."""


class AnalysisReportRenderError(Exception):
    """Nie udało się wygenerować dokumentu PDF z gotowego szablonu HTML."""


@dataclass(frozen=True)
class AnalysisRecordMeta:
    """Metadane wiersza analizy spoza kontraktu API (wersje, wydania)."""

    result_contract_version: str | None = None
    data_release_ids: tuple[int, ...] = ()
    cache_signature: str | None = None


def generate_analysis_report_pdf(
    analysis_id: int, db: Session, *, export_at: datetime | None = None
) -> bytes:
    """Generuje raport PDF dla zapisanej analizy wyłącznie z jej snapshotu.

    Zgłasza ``AnalysisReportNotFoundError``, gdy analiza nie istnieje, oraz
    ``AnalysisReportRenderError`` przy błędzie samego renderowania PDF. Brak
    danych pojedynczej sekcji nie przerywa generowania — sekcja opisuje powód.

    ``export_at`` (domyślnie teraz) jest punktem odniesienia *dodatkowego*
    ostrzeżenia o wieku danych na dzień eksportu (BK-504). Nie wpływa na zapisaną
    macierz jakości ani jej ``matrix_sha256``.
    """
    analysis = db.get(Analysis, analysis_id)
    if analysis is None:
        raise AnalysisReportNotFoundError(
            f"Analiza o identyfikatorze {analysis_id} nie istnieje."
        )

    response = build_analyze_response_from_analysis(analysis, db)
    maps = _render_maps(analysis, response)
    record = AnalysisRecordMeta(
        result_contract_version=analysis.result_contract_version,
        data_release_ids=tuple(analysis.data_release_ids or ()),
        cache_signature=analysis.cache_signature,
    )
    html = _render_report_html(
        _build_report_context(response, maps, record, export_at=export_at)
    )
    logger.info("Wygenerowano HTML raportu dla analizy %s", analysis_id)
    return _html_to_pdf(html)


def _render_maps(analysis: Analysis, response: AnalyzeResponse) -> ReportMaps | None:
    """Mapy z zamrożonego snapshotu; dla starych analiz — odtworzone lokalnie.

    Awaria renderera nie blokuje raportu: sekcje pokażą powód braku mapy.
    """
    try:
        stored = analysis.report_map_snapshot
        if stored:
            return render_report_maps(stored, from_snapshot=True)
        rebuilt = build_report_map_snapshot(
            response,
            to_shape(analysis.parcel.geometry),
            frozen_at=response.analyzed_at,
        )
        return render_report_maps(rebuilt, from_snapshot=False)
    except Exception:  # noqa: BLE001 - mapa jest opcjonalna (także MapSnapshotError)
        logger.exception("Renderowanie map raportu nie powiodło się")
        return None


# --- Kontekst raportu -------------------------------------------------------------


def _build_report_context(
    response: AnalyzeResponse,
    maps: ReportMaps | None = None,
    record: AnalysisRecordMeta | None = None,
    export_at: datetime | None = None,
) -> dict[str, Any]:
    """Kontekst prezentacyjny dziesięciu sekcji raportu v2."""
    record = record or AnalysisRecordMeta()
    parcel = response.parcel
    export_time = export_at or datetime.now(timezone.utc)
    # Wynik bez zapisanej macierzy (np. niezapisany) jest oceniany na potrzeby
    # raportu jako odtworzony — raport nie udaje oceny z chwili analizy.
    quality = response.section_quality or build_section_quality(response, origin="reconstructed")
    domains = _domain_assessments(response, quality, export_time)
    map_unavailable = (
        "Nie udało się wygenerować map raportu. Pozostała część raportu jest kompletna."
        if maps is None
        else None
    )
    return {
        "title": REPORT_TITLE,
        "system_name": REPORT_SYSTEM_NAME,
        "disclaimer": REPORT_DISCLAIMER,
        "layout_version": REPORT_LAYOUT_VERSION,
        "not_specified": NOT_SPECIFIED,
        "sections": [
            {"id": item.id, "number": item.number, "title": item.title} for item in REPORT_SECTIONS
        ],
        "section": {item.id: {"number": item.number, "title": item.title} for item in REPORT_SECTIONS},
        "finding_kinds": [
            {"id": key, "label": FINDING_KIND_LABELS[key], "description": FINDING_KIND_DESCRIPTIONS[key]}
            for key in FINDING_KIND_LABELS
        ],
        "generated_at": _format_datetime(export_time),
        "analysis_id": response.analysis_id,
        "status": response.status,
        "status_label": _ANALYSIS_STATUS_LABELS.get(response.status, response.status),
        "analyzed_at": _format_datetime(response.analyzed_at),
        "parcel_identifier": parcel.parcel_identifier if parcel else None,
        "manual_zone_declared": any(
            zone.assignment_method == "manual_user_input" for zone in response.mpzp_zones
        ),
        "map_unavailable": map_unavailable,
        "maps": _maps_context(maps),
        "parcel": _parcel_context(response),
        "summary": {
            "rows": domains,
            "manual_flags": _manual_flags(response),
        },
        "mpzp": _mpzp_section_context(response),
        "pog": _pog_context(response.pog),
        "compatibility": _compatibility_context(
            response.pog.compatibility_assessment if response.pog else None
        ),
        "risk_sections": _risk_sections_context(response),
        "terrain": _terrain_context(response.terrain),
        "utilities_preview": _utilities_preview_context(response),
        "infrastructure": [_infrastructure_context(item) for item in response.infrastructure],
        "quality": _quality_context(response, quality, domains, export_time),
        "sources": [_source_context(source) for source in response.sources],
        "provenance": _provenance_context(response, maps, record, quality),
        "limitations": _build_limitations(response, maps),
        "field_mapping": _field_mapping_context(response),
    }


_ANALYSIS_STATUS_LABELS: dict[str, str] = {
    "complete": "analiza kompletna",
    "partial": "analiza częściowa — część sekcji ma status inny niż „sprawdzono”",
    "waiting_for_user_input": "analiza wstrzymana — wymaga symbolu strefy MPZP",
    "waiting_for_zone_symbol": "analiza wstrzymana — wymaga symbolu strefy MPZP",
    "failed": "analiza zakończona błędem",
}


def _maps_context(maps: ReportMaps | None) -> dict[str, Any]:
    if maps is None:
        return {}
    frame = maps.frame
    bar = frame.get("scale_bar", {})
    common = {
        "config_version": maps.config_version,
        "crs": frame.get("crs", "EPSG:2180"),
        "scale_text": (
            f"podziałka {format_length_m(float(bar['length_m']))}; 1 piksel obrazu = "
            f"{_format_trimmed(float(frame['meters_per_pixel']), 3)} m w terenie"
            if bar
            else None
        ),
        "extent": (
            f"{_format_trimmed(frame['min_x'], 2)}–{_format_trimmed(frame['max_x'], 2)} m (X), "
            f"{_format_trimmed(frame['min_y'], 2)}–{_format_trimmed(frame['max_y'], 2)} m (Y)"
        ),
        "semantic_short": maps.semantic_sha256[:16],
        "from_snapshot": maps.from_snapshot,
        "integrity_ok": maps.integrity_ok,
        "basemap_used": maps.basemap.get("used", False),
        "basemap_license": maps.basemap.get("license"),
        "warnings": maps.warnings,
    }
    return {item.id: {**common, **_rendered_map_context(item, maps)} for item in maps.maps}


def _rendered_map_context(item: RenderedMap, maps: ReportMaps) -> dict[str, Any]:
    return {
        "id": item.id,
        "title": item.title,
        "mode": item.mode,
        "mode_label": item.mode_label,
        "status": item.status,
        "empty_reason": item.empty_reason,
        "data_uri": item.data_uri,
        "png_sha_short": (item.png_sha256 or "")[:16] or None,
        "legend": item.legend,
        "notes": item.notes,
        "data_dates": [_format_iso(value) for value in item.data_dates],
        "data_release_ids": item.data_release_ids,
        "style_version": maps.pog_style_version if item.id == "pog" else None,
        "style_from_analysis": maps.pog_style_from_analysis,
    }


# --- 1. Identyfikacja i geometria ------------------------------------------------


def _parcel_context(response: AnalyzeResponse) -> dict[str, Any] | None:
    parcel = response.parcel
    if parcel is None:
        return None
    metrics = parcel.metrics
    return {
        "identifier": parcel.parcel_identifier,
        "source": _source_brief(parcel.source),
        "rows": [
            ("Pole powierzchni działki", _area(metrics.area_sqm), "computed"),
            ("Pole powierzchni działki [ha]", f"{_format_number(metrics.area_ha, 4)} ha", "computed"),
            ("Obwód", _meters(metrics.perimeter_m, 2), "computed"),
            ("Geometria poprawna (OGC)", _format_bool(metrics.is_valid), "computed"),
            ("Geometria naprawiana przed obliczeniami", _format_bool(metrics.geometry_repaired), "computed"),
            (
                "Szacowany obszar zabudowy po technicznym odsunięciu od granic",
                _area(response.buildable_area_sqm),
                "approximation",
            ),
        ],
    }


# --- 2 i 8. Podsumowanie i macierz jakości --------------------------------------


def _domain_assessments(
    response: AnalyzeResponse,
    quality: SectionQualityMatrix,
    export_at: datetime,
) -> list[dict[str, Any]]:
    """Jeden wiersz na sekcję analizy — ten sam dla podsumowania i macierzy jakości.

    Status, źródło, czas pobrania, wydanie, flaga weryfikacji, świeżość i powody
    pochodzą z zapisanej macierzy (BK-504); raport dokłada tylko treść ustalenia i
    rodzaj. Wiek na dzień eksportu jest liczony osobno (``export_*``) i nie
    zmienia zapisanej oceny.
    """
    findings = {
        "parcel": _parcel_finding(response),
        "mpzp": _mpzp_finding(response),
        "pog": _pog_finding(response.pog),
        "pog_overlays": _overlays_finding(response.pog),
        **{
            section.section: _risk_finding(section)
            for section in _risk_sections(response)
        },
        "terrain": _terrain_finding(response.terrain),
        "utilities": _utilities_finding(response),
        "transport": (
            "Nie analizowano — brak potwierdzonego kontraktu źródła (BK-305).",
            None,
        ),
        "mpzp_pog_relation": _compatibility_finding(response.pog),
    }
    by_key = {item.section: item for item in quality.sections}
    rows: list[dict[str, Any]] = []
    for spec in QUALITY_SECTIONS:
        item = by_key[spec.key]
        finding, kind = findings[spec.key]
        rows.append(
            {
                "key": spec.key,
                "domain": spec.label,
                "section": SECTION_BY_ID[spec.report_section].number,
                "status": item.status,
                "status_label": SECTION_STATUS_LABELS[item.status],
                "finding": _sentence(finding),
                "kind": kind,
                "kind_label": FINDING_KIND_LABELS.get(kind or "", "—"),
                "source_id": item.source_id,
                "source_name": item.source_name,
                "fetched_at": _format_datetime(item.fetched_at) if item.fetched_at else None,
                "release": item.data_release_id,
                "version": item.source_version,
                "manual_review": item.manual_review_required,
                "freshness_state": item.freshness.state,
                "freshness_label": FRESHNESS_LABELS_PL[item.freshness.state],
                "freshness_detail": _freshness_detail(item),
                "reason_codes": [
                    {"code": code, "label": reason_label_pl(code)} for code in item.reason_codes
                ],
                "reason": "; ".join(reason_label_pl(code) for code in item.reason_codes) or None,
                "export": _export_age(item, export_at),
            }
        )
    return rows


def _freshness_detail(item: SectionQuality) -> str:
    freshness = item.freshness
    parts = [f"wiek {format_age_pl(freshness.age_seconds)}"]
    if freshness.max_age_days is not None:
        parts.append(f"reguła źródła: {freshness.max_age_days} dni")
    else:
        parts.append("brak reguły wieku")
    # „Brak reguły wieku” jest już w opisie; dopisujemy tylko inne powody (stary
    # pomiar, czas z przyszłości/bez strefy, brak czasu, brak źródła).
    if freshness.reason_code and freshness.state != "fresh" and freshness.reason_code != FRESHNESS_NO_POLICY:
        parts.append(reason_label_pl(freshness.reason_code))
    return "; ".join(parts)


def _export_age(item: SectionQuality, export_at: datetime) -> dict[str, Any]:
    """Wiek danych na dzień eksportu wg reguły zapisanej z oceną (tylko odczyt)."""
    freshness = item.freshness
    rule = (
        FreshnessRule(
            max_age_days=freshness.max_age_days,
            basis=freshness.basis or "project_decision",
            rationale="reguła zapisana z oceną jakości",
        )
        if freshness.max_age_days is not None
        else None
    )
    verdict = evaluate_freshness(item.fetched_at, export_at, rule)
    return {
        "state": verdict.state,
        "label": FRESHNESS_LABELS_PL[verdict.state],
        "age": format_age_pl(verdict.age_seconds),
        "stale": verdict.state == "stale",
        "note": (
            "starsze niż reguła wieku źródła w dniu eksportu"
            if verdict.state == "stale"
            else reason_label_pl(verdict.reason_code)
            if verdict.reason_code
            else "w granicach reguły wieku źródła"
        ),
    }


def _quality_context(
    response: AnalyzeResponse,
    quality: SectionQualityMatrix,
    rows: list[dict[str, Any]],
    export_at: datetime,
) -> dict[str, Any]:
    stale_rows = [row for row in rows if row["export"]["stale"]]
    used_statuses = {row["status"] for row in rows}
    used_freshness = {row["freshness_state"] for row in rows}
    legend = quality.legend
    return {
        "rows": rows,
        "matrix_sha256": quality.matrix_sha256,
        "policy_version": quality.policy_version,
        "reference_at": _format_datetime(quality.reference_at),
        "origin": quality.origin,
        "integrity_ok": quality.integrity_ok(),
        "legend": {
            "statuses": [
                {"id": item.id, "label": item.label, "description": item.description,
                 "used": item.id in used_statuses}
                for item in legend.statuses
            ],
            "freshness": [
                {"id": item.id, "label": item.label, "description": item.description,
                 "used": item.id in used_freshness}
                for item in legend.freshness
            ],
        },
        "export_at": _format_datetime(export_at),
        "export_stale": [row["domain"] for row in stale_rows],
        "warnings": [_warning_context(warning) for warning in response.warnings],
    }


def _parcel_finding(response: AnalyzeResponse) -> tuple[str, str | None]:
    parcel = response.parcel
    if parcel is None:
        return "Nie ustalono geometrii działki.", None
    return (
        f"Pole {_area(parcel.metrics.area_sqm)}, obwód {_meters(parcel.metrics.perimeter_m, 2)}.",
        "computed",
    )


def _mpzp_finding(response: AnalyzeResponse) -> tuple[str, str | None]:
    zones = response.mpzp_zones
    if response.manual_zone_required:
        return "Analiza wstrzymana — symbol strefy wymaga ręcznego podania.", "manual"
    if not zones:
        return "Brak stref MPZP w wyniku.", None
    parts = [
        f"{zone.zone_symbol} ({_share(zone.intersection_pct) if zone.intersection_pct is not None else 'udział nieustalony'})"
        for zone in zones
    ]
    manual = any(zone.assignment_method == "manual_user_input" for zone in zones)
    return (
        f"{len(zones)} {_plural(len(zones), 'strefa', 'strefy', 'stref')}: {', '.join(parts)}.",
        "manual" if manual else "source_fact",
    )


def _pog_finding(pog: PogResult | None) -> tuple[str, str | None]:
    if pog is None:
        return "Snapshot nie zawiera wyniku POG.", None
    zones = ", ".join(
        f"{zone.symbol or zone.type} ({_share(zone.area_pct)})" for zone in pog.zones
    ) or "brak stref przecinających działkę w danych"
    parts = [f"Status aktu: {LEGAL_STATUS_LABELS_PL[pog.legal_status]}. Strefy: {zones}."]
    reason_parts = []
    if pog.legal_status != "binding":
        reason_parts.append(LEGAL_STATUS_LABELS_PL[pog.legal_status])
    if pog.coverage_status != "available":
        reason_parts.append(COVERAGE_STATUS_LABELS_PL[pog.coverage_status])
    if pog.data_availability != "current":
        reason_parts.append(DATA_AVAILABILITY_LABELS_PL[pog.data_availability])
    if reason_parts:
        parts.append("Zastrzeżenia: " + "; ".join(reason_parts) + ".")
    return " ".join(parts), "source_fact"


def _overlays_finding(pog: PogResult | None) -> tuple[str, str | None]:
    if pog is None:
        return "Nie ustalono (brak wyniku POG).", None
    parts = [
        f"OUZ: {_overlay_brief(pog.ouz, pog)}",
        f"OZS: {_overlay_brief(pog.downtown_areas, pog)}",
        f"OSDIS: {_overlay_brief(pog.social_infrastructure_standard_areas, pog)}",
    ]
    return "; ".join(parts) + ".", "computed"


def _overlay_brief(items: list[Any], pog: PogResult) -> str:
    if items:
        return ", ".join(f"{item.symbol or item.label or item.id} {_share(item.area_pct)}" for item in items)
    if pog.coverage_status in {"available", "no_act_confirmed"}:
        return "brak obszaru przecinającego działkę"
    return "nie ustalono"


def _risk_sections(response: AnalyzeResponse) -> list[RiskSectionResult]:
    sections = {section.section: section for section in response.risk_sections}
    if not sections:
        sections = {section.section: section for section in risk_sections_from_snapshot(None)}
    return [sections[name] for name in _RISK_SECTION_NAMES if name in sections]


def _risk_finding(section: RiskSectionResult) -> tuple[str, str | None]:
    if section.status == "available":
        finding = RISK_RELATION_LABELS[section.relation]
        if section.union_intersection_pct is not None and section.relation == "intersection":
            finding += f" — łącznie {_area(section.union_intersection_area_sqm)} ({_share(section.union_intersection_pct)} działki)"
        return finding + ".", "computed"
    return "Nie ustalono — brak obiektów nie oznacza braku ograniczeń.", None


def _terrain_finding(terrain: TerrainResult | None) -> tuple[str, str | None]:
    terrain = terrain or terrain_from_snapshot(None)
    if terrain.status == "available":
        return (
            f"Deniwelacja {_meters(terrain.height_difference_m)} (Hmin {_meters(terrain.min_height_m)}, "
            f"Hmax {_meters(terrain.max_height_m)}).",
            "computed",
        )
    return (
        f"{_TERRAIN_STATUS_LABELS[terrain.status]} — deniwelacja nieznana, nie oznacza to płaskiego terenu.",
        None,
    )


def _utilities_finding(response: AnalyzeResponse) -> tuple[str, str | None]:
    preview = response.utilities_preview
    buffers = len(response.infrastructure)
    suffix = f" Bufory techniczne sieci: {buffers} (przybliżenie)." if buffers else ""
    if preview is None:
        return "Nie sprawdzono pokrycia KIUT." + suffix + " Brak danych nie oznacza braku sieci.", None
    return f"{_UTILITIES_LABELS[preview.coverage_status]}.{suffix}", "source_fact"


def _compatibility_finding(pog: PogResult | None) -> tuple[str, str | None]:
    assessment = pog.compatibility_assessment if pog else None
    if assessment is None:
        return "Nie wykonano oceny relacji MPZP–POG.", None
    return (
        f"{COMPATIBILITY_STATUS_LABELS_PL[assessment.status]} — analiza informacyjna, nie opinia prawna.",
        "computed",
    )


def _has_model_reading(response: AnalyzeResponse) -> bool:
    return any(is_model_reading(parameter) for zone in response.mpzp_zones for parameter in zone.parameters)


def _manual_flags(response: AnalyzeResponse) -> list[str]:
    flags = []
    if response.status != "complete":
        flags.append(f"Status analizy: {_ANALYSIS_STATUS_LABELS.get(response.status, response.status)}.")
    if any(zone.assignment_method == "manual_user_input" for zone in response.mpzp_zones):
        flags.append("Symbol strefy MPZP podano ręcznie — parametry zależne wymagają weryfikacji.")
    if _has_model_reading(response):
        flags.append(f"Część parametrów MPZP to {MODEL_READING_SHORT} — wymaga potwierdzenia w uchwale.")
    if response.manual_zone_required:
        flags.append("Analiza oczekuje na ręczne podanie symbolu strefy MPZP.")
    return flags


# --- 3. MPZP ---------------------------------------------------------------------


_MPZP_ASSIGNMENT_LABELS: dict[str, str] = {
    "vector_intersection": "przecięcie z wektorem wydzieleń",
    "document_candidate": "kandydat z discovery/dokumentu (bez wektora)",
    "manual_user_input": "symbol podany ręcznie z mapy rastrowej",
    "legacy": "snapshot sprzed wersjonowania stref",
}
_EXTRACTION_LABELS: dict[str, str] = {
    "pdf_text": "tekst PDF",
    "html": "HTML",
    "ocr": "OCR",
    EXTRACTION_METHOD_LLM_VERIFIED: MODEL_READING_SHORT,
}
# (pole API, etykieta, jednostka prezentacji). Wskaźnik intensywności jest
# bezwymiarowy; kondygnacje są liczbą całkowitą.
MPZP_PARAMETER_ROWS: tuple[tuple[str, str, str | None], ...] = (
    ("max_building_height_m", "Maksymalna wysokość zabudowy", "m"),
    ("max_floors", "Maksymalna liczba kondygnacji nadziemnych", "kondygn."),
    ("max_building_coverage_pct", "Maksymalny udział powierzchni zabudowy", "%"),
    ("min_biologically_active_pct", "Minimalny udział powierzchni biologicznie czynnej", "%"),
    ("max_floor_area_ratio", "Maksymalny wskaźnik intensywności zabudowy", None),
    ("min_floor_area_ratio", "Minimalny wskaźnik intensywności zabudowy", None),
)
_MPZP_TEXT_ROWS: tuple[tuple[str, str], ...] = (
    ("primary_use", "Przeznaczenie podstawowe"),
    ("supplementary_use", "Przeznaczenie uzupełniające"),
)
_UNIT_LABELS: dict[str, str] = {"percent": "%", "m": "m", "%": "%"}


_CONDITION_KIND_LABELS: dict[str, str] = {
    "building_type": "rodzaj zabudowy",
    "roof_type": "rodzaj dachu",
    "subzone": "podstrefa",
    "location": "położenie",
    "other": "inny warunek",
}


def _conditions_text(parameter: Any) -> str:
    return "; ".join(condition.label for condition in parameter.conditions)


class _EvidenceRegistry:
    """Numeruje dowody [E#] i dokumenty [D#] w kolejności pierwszego użycia."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.documents: list[dict[str, Any]] = []
        self._doc_refs: dict[str, str] = {}

    def document_ref(self, sha256: str | None, version_id: int | None) -> str | None:
        if not sha256:
            return None
        if sha256 not in self._doc_refs:
            ref = f"D{len(self._doc_refs) + 1}"
            self._doc_refs[sha256] = ref
            self.documents.append({"ref": ref, "sha256": sha256, "version_id": version_id})
        return self._doc_refs[sha256]

    def add(self, zone: MpzpZoneResult, parameter: Any, label: str) -> str:
        ref = f"E{len(self.rows) + 1}"
        value = parameter.normalized_value
        unit = _UNIT_LABELS.get(parameter.unit or "", parameter.unit)
        self.rows.append(
            {
                "ref": ref,
                "zone": zone.zone_symbol,
                "parameter": label,
                "value": (
                    f"{_format_trimmed(float(value), 3)}{_unit_suffix(unit)}"
                    if isinstance(value, (int, float))
                    else value if value is not None else NOT_SPECIFIED
                ),
                "raw_value": parameter.raw_value,
                "page": parameter.page_number,
                "segment": parameter.segment_id,
                "legal_unit": parameter.legal_unit_id,
                "document_ref": self.document_ref(parameter.document_sha256, parameter.document_version_id),
                "extraction": _EXTRACTION_LABELS.get(parameter.extraction_method or "", parameter.extraction_method),
                "parser_version": parameter.parser_version,
                "confidence": _format_percent(parameter.confidence * 100.0),
                "conflict": parameter.value_kind == "conflict",
                "value_kind": parameter.value_kind,
                "conditions": [
                    {"kind": condition.kind, "kind_label": _CONDITION_KIND_LABELS[condition.kind],
                     "label": condition.label, "quote": condition.quote}
                    for condition in parameter.conditions
                ],
                "manual_review": parameter.manual_review_required,
                "text": parameter.evidence_text,
                # PV3-18: provenance odczytu modelu — tylko dla wartości z modelu (``ai_candidate``); wartość
                # deterministyczna nie niesie tych pól i nie jest oznaczana jako odczyt modelu.
                "model_reading": is_model_reading(parameter),
                "model": (
                    {
                        "model_id": parameter.model_id,
                        "prompt_version": parameter.prompt_version,
                        "response_sha256": parameter.response_sha256,
                    }
                    if is_model_reading(parameter)
                    else None
                ),
            }
        )
        return ref


def _mpzp_section_context(response: AnalyzeResponse) -> dict[str, Any]:
    registry = _EvidenceRegistry()
    zones = []
    parameters = []
    for index, zone in enumerate(response.mpzp_zones, start=1):
        zones.append(_mpzp_zone_row(index, zone, registry))
        parameters.extend(_mpzp_parameter_rows(zone, registry))
    manual = [
        {"zone": zone.zone_symbol, **_manual_selection_context(zone.manual_selection)}
        for zone in response.mpzp_zones
        if zone.manual_selection is not None
    ]
    context = response.manual_zone_context
    return {
        "zones": zones,
        "parameters": parameters,
        "evidence": registry.rows,
        "documents": registry.documents,
        "manual_selections": manual,
        "waiting": (
            {
                "plan_id": context.plan_id,
                "candidates": ", ".join(context.candidate_zone_symbols) or "brak kandydatów",
                "document_status": context.document_status,
                "document_sha256": context.document.sha256 if context.document else None,
                "document_fetched_at": (
                    _format_datetime(context.document.fetched_at)
                    if context.document and context.document.fetched_at
                    else None
                ),
                "notice": context.notice,
            }
            if response.manual_zone_required and context is not None
            else None
        ),
        "discovery": _mpzp_discovery_context(response),
        "shares_sum": _shares_sum([zone.intersection_pct for zone in response.mpzp_zones]),
        # PV3-18: oznaczenie odczytu automatycznego i komunikaty o braku danych / null — stałe teksty z jednego miejsca.
        "model_reading": {
            "present": _has_model_reading(response),
            "mark": MODEL_READING_MARK,
            "short": MODEL_READING_SHORT,
            "disclaimer": MODEL_READING_DISCLAIMER,
        },
        "no_data_note": f"{NO_DATA_NOT_NO_RESTRICTION} {NULL_NOT_ZERO}",
        "empty_reason": (
            None
            if response.mpzp_zones
            else "Analiza oczekuje na ręczne podanie symbolu strefy MPZP."
            if response.manual_zone_required
            else "Nie znaleziono ani nie sprawdzono stref MPZP przecinających działkę — "
            "nie potwierdza to braku planu."
        ),
    }


_DISCOVERY_STATUS_LABELS: Final[dict[str, str]] = {
    "available": "KIMPZP wskazało akty w punktach działki",
    "no_match": "usługa gminna nie zwróciła planu w punktach działki (nie dowodzi braku planu)",
    "no_coverage": "KIMPZP nie ma usługi gminnej dla obszaru — brak danych, nie brak planu",
    "unavailable": "usługa MPZP gminy zwróciła błąd — nie ustalono, czy obowiązuje plan",
    "unknown": "nie ustalono (nierozpoznana odpowiedź albo discovery nie zostało wykonane)",
}
_ACT_LEGAL_STATUS_LABELS: Final[dict[str, str]] = {
    "binding": "obowiązujący",
    "not_binding": "nieobowiązujący",
    "unknown": "status nieustalony",
}
_INFORMATIZATION_LABELS: Final[dict[str, str | None]] = {
    "vector": "plan wektorowy",
    "raster": "plan rastrowy",
    "unknown": None,
}
_AMENDMENT_KIND_LABELS: Final[dict[str, str]] = {
    "text_change": "zmiana tekstowa",
    "change": "zmiana",
    "note": "opis zmian",
}


def _mpzp_discovery_context(response: AnalyzeResponse) -> dict[str, Any] | None:
    """Tabela 3.5 — akty wskazane przez KIMPZP (AU-004); ``None`` dla zapisu sprzed AU-004."""
    discovery = response.mpzp_discovery
    if discovery is None:
        return None
    return {
        "status": discovery.status,
        "status_label": _DISCOVERY_STATUS_LABELS.get(discovery.status, discovery.status),
        "reason_codes": ", ".join(discovery.reason_codes) or None,
        "multiple_acts": discovery.multiple_acts_at_point or discovery.multiple_acts_on_parcel,
        "multiple_at_point": discovery.multiple_acts_at_point,
        "selected_act": discovery.selected_act,
        "points": f"{discovery.sampled_points} (nieudane: {discovery.failed_points})",
        "candidates": ", ".join(discovery.candidate_zone_symbols) or None,
        "source": _source_brief(discovery.source) if discovery.source else None,
        "acts": [
            {
                "number": act.resolution_number,
                "name": act.name,
                "resolution_date": _format_date(act.resolution_date),
                "valid_from": _format_date(act.valid_from),
                "repealed_on": _format_date(act.repealed_on),
                "legal_status": _ACT_LEGAL_STATUS_LABELS.get(act.legal_status, act.legal_status),
                "text_url": act.text_url,
                "legend_url": act.legend_url,
                "drawing_url": act.drawing_url,
                "bip_url": act.bip_url,
                "www_url": act.www_url,
                "journal": act.journal,
                "zone_symbols": ", ".join(act.zone_symbols) or None,
                "informatization": _INFORMATIZATION_LABELS.get(act.informatization),
                "source_format": act.source_format,
                "amendments": [
                    {
                        "kind": _AMENDMENT_KIND_LABELS.get(amendment.kind, amendment.kind),
                        "number": amendment.resolution_number,
                        "valid_from": _format_date(amendment.valid_from),
                        "adopted_on": _format_date(amendment.adopted_on),
                        "text": amendment.raw_text,
                        "name": amendment.name,
                        "url": amendment.document_url,
                        "bip_url": amendment.bip_url,
                    }
                    for amendment in act.amendments
                ],
            }
            for act in discovery.acts
        ],
    }


def _mpzp_zone_row(index: int, zone: MpzpZoneResult, registry: _EvidenceRegistry) -> dict[str, Any]:
    share_known = zone.intersection_pct is not None and zone.intersection_area_sqm is not None
    return {
        "index": index,
        "symbol": zone.zone_symbol,
        "zone_id": zone.zone_id,
        "is_dominant": zone.is_dominant,
        "touches_boundary": zone.touches_boundary,
        "manual_review": zone.manual_review_required or zone.source.manual_review_required,
        "is_manual": zone.assignment_method == "manual_user_input",
        "assignment_label": _MPZP_ASSIGNMENT_LABELS.get(zone.assignment_method, zone.assignment_method),
        "area": _area(zone.intersection_area_sqm) if share_known else None,
        "share": _share(zone.intersection_pct) if share_known else None,
        "share_kind": "computed" if zone.assignment_method == "vector_intersection" else None,
        "act": zone.act_identifier,
        "act_version": zone.act_version,
        "act_version_id": zone.act_version_id,
        "release": zone.data_release_id,
        "document_url": zone.document_url,
        "source": _source_brief(zone.source),
        "confidence": _confidence_context(zone.source),
    }


def _mpzp_parameter_rows(zone: MpzpZoneResult, registry: _EvidenceRegistry) -> list[dict[str, Any]]:
    kind = "manual" if zone.assignment_method == "manual_user_input" else "source_fact"
    by_field: dict[str, list[Any]] = {}
    unmapped: list[Any] = []
    for parameter in zone.parameters:
        field = PARSER_TO_API_PARAMETER_MAP.get(parameter.name, parameter.name)
        if field in {name for name, _, _ in MPZP_PARAMETER_ROWS} | {name for name, _ in _MPZP_TEXT_ROWS}:
            by_field.setdefault(field, []).append(parameter)
        else:
            unmapped.append(parameter)
    rows: list[dict[str, Any]] = []
    for field, label in _MPZP_TEXT_ROWS:
        value = getattr(zone, field)
        candidates = by_field.get(field, [])
        refs = [registry.add(zone, item, label) for item in candidates]
        rows.append(_parameter_row(zone, label, value if value else None, None, refs, kind, "ok" if value else "null"))

    def candidate_text(item: Any, ref: str, unit: str | None, with_condition: bool = False) -> str:
        number = (
            _format_trimmed(float(item.normalized_value), 3)
            if isinstance(item.normalized_value, (int, float))
            else item.normalized_value
        )
        suffix = f" — {_conditions_text(item)}" if with_condition and item.conditions else ""
        return f"{number}{_unit_suffix(unit)}{suffix} [{ref}]"

    def with_model_reading(row: dict[str, Any], model_pairs: list[tuple[Any, str]], unit: str | None) -> dict[str, Any]:
        """Odczyt modelu (PV3-18) jest osobną linią wiersza z oznaczeniem; nie miesza się z wartością deterministyczną."""
        text = "; ".join(
            candidate_text(item, ref, unit, True) for item, ref in model_pairs if item.normalized_value is not None
        )
        if text:
            row["model_candidates"] = text
            if row["status"] == "null":
                # Brak wartości deterministycznej, jest tylko kandydat modelu: wiersz ma własny rodzaj.
                row.update(status="ai_candidate", kind="model_reading", kind_label=FINDING_KIND_LABELS["model_reading"])
        return row

    for field, label, unit in MPZP_PARAMETER_ROWS:
        value = getattr(zone, field)
        candidates = by_field.get(field, [])
        refs = [registry.add(zone, item, label) for item in candidates]
        all_pairs = list(zip(candidates, refs, strict=True))
        # Wartości z modelu językowego (``ai_candidate``) nigdy nie wchodzą do wartości deterministycznej, do
        # warunkowych ani do sprzecznych — mają własną, oznaczoną linię wiersza.
        model_pairs = [(item, ref) for item, ref in all_pairs if is_model_reading(item)]
        pairs = [(item, ref) for item, ref in all_pairs if not is_model_reading(item)]
        conflicting = [(item, ref) for item, ref in pairs if item.value_kind == "conflict"]
        conditional = [(item, ref) for item, ref in pairs if item.value_kind == "conditional"]

        base_text = (
            (str(int(value)) if field == "max_floors" else _format_trimmed(float(value), 3))
            if value is not None
            else None
        )
        conditional_text = "; ".join(
            candidate_text(item, ref, unit, True) for item, ref in conditional if item.normalized_value is not None
        )
        if conflicting:
            options = "; ".join(
                candidate_text(item, ref, unit, True) for item, ref in conflicting if item.normalized_value is not None
            )
            text = f"wymaga weryfikacji — kandydaci: {options}"
            if conditional_text:
                text += f"; warunkowo: {conditional_text}"
            row = _parameter_row(zone, label, text, None, refs, kind, "conflict")
        elif conditional_text:
            head = f"{base_text}{_unit_suffix(unit)}" if base_text is not None else "brak jednej wartości dla całej strefy"
            row = _parameter_row(zone, label, f"{head}; warunkowo: {conditional_text}", None, refs, kind, "conditional")
        else:
            row = _parameter_row(zone, label, base_text, unit, refs, kind, "ok" if value is not None else "null")
        rows.append(with_model_reading(row, model_pairs, unit))
    for parameter in unmapped:
        label = f"Inny zapis uchwały: {parameter.name}"
        ref = registry.add(zone, parameter, label)
        value = parameter.normalized_value
        if is_model_reading(parameter):
            unit = _UNIT_LABELS.get(parameter.unit or "", parameter.unit)
            if value is None:
                rows.append(_parameter_row(zone, label, None, None, [ref], "model_reading", "null"))
                continue
            row = _parameter_row(zone, label, None, None, [ref], "model_reading", "ai_candidate")
            row["model_candidates"] = candidate_text(parameter, ref, unit, True)
            rows.append(row)
            continue
        shown = _format_trimmed(float(value), 3) if isinstance(value, (int, float)) else value
        if shown is not None and parameter.conditions:
            shown = f"{shown}{_unit_suffix(_UNIT_LABELS.get(parameter.unit or '', parameter.unit))} — {_conditions_text(parameter)}"
            rows.append(_parameter_row(zone, label, shown, None, [ref], kind, "conditional"))
            continue
        rows.append(
            _parameter_row(
                zone,
                label,
                shown,
                _UNIT_LABELS.get(parameter.unit or "", parameter.unit),
                [ref],
                kind,
                "ok" if value is not None else "null",
            )
        )
    return rows


def _parameter_row(
    zone: MpzpZoneResult,
    label: str,
    value: str | None,
    unit: str | None,
    refs: list[str],
    kind: str,
    status: str,
) -> dict[str, Any]:
    return {
        "zone": zone.zone_symbol,
        "zone_id": zone.zone_id,
        "parameter": label,
        "value": value,
        "unit": unit,
        "display": (
            f"{value}{_unit_suffix(unit)}" if value is not None and status == "ok" else value
        ),
        "refs": refs,
        "kind": kind,
        "kind_label": FINDING_KIND_LABELS[kind],
        "status": status,
        # Linia odczytu modelu (PV3-18) z oznaczeniem; ``None`` dla wartości deterministycznych.
        "model_candidates": None,
    }


def _manual_selection_context(selection: Any) -> dict[str, Any]:
    return {
        "entered_symbol": selection.entered_symbol,
        "plan_id": selection.plan_id,
        "candidates": ", ".join(selection.candidate_zone_symbols) or "brak kandydatów",
        "symbol_in_candidates": selection.symbol_in_candidates,
        "document_url": selection.document_url,
        "document_pinned": selection.document_pinned,
        "document_sha256": selection.document_sha256,
        "document_version_id": selection.document_version_id,
        "document_fetched_at": (
            _format_datetime(selection.document_fetched_at) if selection.document_fetched_at else None
        ),
        "selected_at": _format_datetime(selection.selected_at),
    }


# --- 4. POG ----------------------------------------------------------------------


_POG_PARAMETER_COLUMNS: tuple[tuple[str, str, str | None], ...] = (
    ("max_overground_floor_area_ratio", "Maks. nadziemna intensywność zabudowy", None),
    ("max_building_height_m", "Maks. wysokość zabudowy", "m"),
    ("max_building_coverage_pct", "Maks. udział powierzchni zabudowy", "%"),
    ("min_biologically_active_pct", "Min. udział powierzchni biologicznie czynnej", "%"),
)


def _pog_context(pog: PogResult | None) -> dict[str, Any] | None:
    if pog is None:
        return None
    evidence = pog.legal_status_evidence
    coverage_evidence = pog.coverage_evidence
    binding = pog.legal_status == "binding"
    return {
        "schema_version": pog.schema_version,
        "binding": binding,
        "legal_status": pog.legal_status,
        "legal_status_label": LEGAL_STATUS_LABELS_PL[pog.legal_status],
        "coverage_status": pog.coverage_status,
        "coverage_status_label": COVERAGE_STATUS_LABELS_PL[pog.coverage_status],
        "data_availability_label": DATA_AVAILABILITY_LABELS_PL[pog.data_availability],
        "status_confirmed_at": (
            _format_datetime(pog.status_confirmed_at) if pog.status_confirmed_at else None
        ),
        "status_evidence": (
            {
                "source_name": evidence.source_name,
                "official": evidence.official,
                "source_id": evidence.source_id,
                "raw_value": evidence.raw_value,
                "reference": evidence.reference,
                "confirmed_at": _format_datetime(evidence.confirmed_at) if evidence.confirmed_at else None,
            }
            if evidence is not None
            else None
        ),
        "coverage_evidence": (
            {
                "source_name": coverage_evidence.source_name,
                "official": coverage_evidence.official,
                "source_id": coverage_evidence.source_id,
                "raw_value": coverage_evidence.raw_value,
                "reference": coverage_evidence.reference,
                "confirmed_at": (
                    _format_datetime(coverage_evidence.confirmed_at)
                    if coverage_evidence.confirmed_at
                    else None
                ),
            }
            if coverage_evidence is not None
            else None
        ),
        "status_notes": pog_status_notes_pl(
            pog.legal_status, pog.coverage_status, pog.data_availability, pog.status_confirmed_at
        ),
        "legacy_zone": (
            {
                "zone": pog.zone_type or pog.planning_zone,
                "area_ratio": _share(pog.area_ratio * 100.0) if pog.area_ratio is not None else None,
            }
            if not pog.zones and (pog.zone_type or pog.planning_zone)
            else None
        ),
        "uchwala_nr": pog.uchwala_nr,
        "uchwala_date": _format_date(pog.uchwala_date),
        "manual_review_required": pog.manual_review_required,
        "confidence": _confidence_context(pog.source),
        "zones": [_pog_zone_row(zone, pog) for zone in pog.zones],
        "zones_sum": _shares_sum([zone.area_pct for zone in pog.zones]),
        "zones_empty_reason": (
            None
            if pog.zones
            else f"Brak stref przecinających działkę w danych POG (zakres danych: "
            f"{COVERAGE_STATUS_LABELS_PL[pog.coverage_status]}). Nie oznacza to braku planu."
        ),
        "overlays": [
            _overlay_group("ouz", "Tabela 4.4. Obszar uzupełnienia zabudowy (OUZ)", pog.ouz, pog,
                           decision=_format_bool(pog.in_ouz),
                           decision_label="Działka w OUZ (decyzja wg jawnych progów)",
                           total=(pog.ouz_intersection_area_sqm, pog.ouz_intersection_pct),
                           touches=pog.touches_ouz_boundary),
            _overlay_group("downtown", "Tabela 4.5. Obszar zabudowy śródmiejskiej (OZS)",
                           pog.downtown_areas, pog,
                           decision=_format_bool(pog.in_downtown_area),
                           decision_label="Działka w OZS"),
            _overlay_group("social", "Tabela 4.6. Obszar standardów dostępności infrastruktury społecznej (OSDIS)",
                           pog.social_infrastructure_standard_areas, pog),
        ],
        "act": _pog_act_context(pog.act, binding),
    }


def _pog_zone_row(zone: Any, pog: PogResult) -> dict[str, Any]:
    parameters = []
    for field, label, unit in _POG_PARAMETER_COLUMNS:
        value = getattr(zone, field)
        parameters.append(
            {
                "label": label,
                "value": (
                    f"{_format_trimmed(float(value), 3)}{_unit_suffix(unit)}" if value is not None else None
                ),
            }
        )
    return {
        "id": zone.id,
        "symbol": zone.symbol,
        "type": zone.type,
        "label": zone.label,
        "area": _area(zone.area_sqm),
        "share": _share(zone.area_pct),
        "is_dominant": pog.dominant_zone_id is not None and pog.dominant_zone_id == zone.id,
        "parameters": parameters,
        "primary_profile": [_profile(profile) for profile in zone.primary_profile],
        "additional_profiles": [_profile(profile) for profile in zone.additional_profiles],
        "feature_version": zone.feature_version,
        "gml_url": zone.gml_url if zone.gml_url_verified else None,
        "source": _source_brief(zone.source) if zone.source else None,
        "swatch": _zone_swatch(zone.type, pog),
        "status_label": LEGAL_STATUS_LABELS_PL[pog.legal_status],
        "binding": pog.legal_status == "binding",
    }


def _zone_swatch(code: str | None, pog: PogResult) -> str | None:
    """Kolor strefy ze stylu zapisanego w analizie (BK-403) — ten sam co na mapie."""
    style = report_pog_style(pog)
    if style is None:
        return None
    rgb = pog_zone_layer_style(code, style).fill_rgb
    return "#%02x%02x%02x" % rgb if rgb else None


def _profile(profile: Any) -> dict[str, str]:
    return {
        "code": profile.code,
        "label": profile.label or profile.code,
        "dictionary": profile.dictionary_source,
    }


def _overlay_group(
    group_id: str,
    title: str,
    items: list[Any],
    pog: PogResult,
    *,
    decision: str | None = None,
    decision_label: str | None = None,
    total: tuple[float | None, float | None] | None = None,
    touches: bool | None = None,
) -> dict[str, Any]:
    empty = None
    if not items and total and total[0] is not None:
        empty = (
            "Snapshot nie zawiera listy obiektów (zapis sprzed kontraktu wszystkich obszarów) — "
            "powyżej wartości łączne."
        )
    elif not items:
        empty = (
            "Sprawdzono — brak obszaru przecinającego działkę."
            if pog.coverage_status in {"available", "no_act_confirmed"}
            else "Nie ustalono — zakres danych POG nie pozwala stwierdzić braku obszaru "
            f"({COVERAGE_STATUS_LABELS_PL[pog.coverage_status]})."
        )
    return {
        "id": group_id,
        "title": title,
        "rows": [
            {
                "id": item.id,
                "symbol": item.symbol,
                "label": item.label,
                "area": _area(item.area_sqm),
                "share": _share(item.area_pct),
                "touches_boundary": _format_bool(item.touches_boundary),
                "feature_version": item.feature_version,
                "gml_url": item.gml_url if item.gml_url_verified else None,
                "source": _source_brief(item.source) if item.source else None,
            }
            for item in items
        ],
        "empty_reason": empty,
        "decision": decision,
        "decision_label": decision_label,
        "total_area": _area(total[0]) if total and total[0] is not None else None,
        "total_share": _share(total[1]) if total and total[1] is not None else None,
        "touches": _format_bool(touches) if touches is not None else None,
    }


def _pog_act_context(act: Any, binding: bool) -> dict[str, Any] | None:
    """Łańcuch provenance aktu; linki wyłącznie dla zweryfikowanych HTTPS."""
    if act is None:
        return None
    metadata = act.metadata
    return {
        "identifier": act.act_identifier or act.id,
        "internal_id": act.id,
        "version": act.act_version or act.version,
        "title": act.title,
        "resolution_number": act.resolution_number,
        "resolution_date": _format_date(act.resolution_date),
        "publication_id": act.publication_id,
        "version_started_at": (
            _format_datetime(act.version_started_at) if act.version_started_at else None
        ),
        # BK-406: dla projektu nie używamy języka „obowiązuje”.
        "valid_from_label": (
            "Początek obowiązywania wersji (wg APP)"
            if binding
            else "Data początkowa w atrybucie APP (akt niewiążący)"
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
                "resource_identifier": metadata.resource_identifier,
                "title": metadata.title,
                "record_sha256": metadata.record_sha256,
                "response_sha256": metadata.response_sha256,
                "publication_date": _format_date(metadata.publication_date),
                "revision_date": _format_date(metadata.revision_date),
                "creation_date": _format_date(metadata.creation_date),
                "date_stamp": _format_date(metadata.date_stamp),
                "fetched_at": _format_datetime(metadata.fetched_at) if metadata.fetched_at else None,
            }
            if metadata is not None
            else None
        ),
        "documents": [
            {
                "title": document.title or document.short_name or document.document_identifier,
                "short_name": document.short_name,
                "identifier": document.document_identifier,
                "identification_number": document.identification_number,
                "publication_id": document.publication_id,
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


def _compatibility_context(assessment: Any) -> dict[str, Any] | None:
    """Ocena relacji MPZP–POG jako osobna, informacyjna tabela (BK-205)."""
    if assessment is None:
        return None
    legacy = assessment.legacy_evidence
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
                "ids": f"MPZP {pair.mpzp_zone_id or '—'} / POG {pair.pog_zone_id}",
                "mpzp_method": _MPZP_ASSIGNMENT_LABELS.get(
                    pair.mpzp_assignment_method, pair.mpzp_assignment_method
                ),
                "mpzp_function": pair.mpzp_function,
                "status_label": COMPATIBILITY_STATUS_LABELS_PL[pair.status],
                "rule_result": pair.rule_result,
                "spatial": pair.spatially_identified,
                "overlap": (
                    f"{_area(pair.overlap_area_sqm)}"
                    + (f" ({_share(pair.overlap_pct)})" if pair.overlap_pct is not None else "")
                    if pair.overlap_area_sqm is not None
                    else "nieustalone"
                ),
                "rule": f"{pair.rule_id} v{pair.rule_version}" if pair.rule_id else "brak reguły",
                "source": pair.source,
                "as_of": _format_date(pair.as_of),
                "rationale": pair.rationale,
                "manual_review": pair.manual_review_required,
            }
            for pair in assessment.zone_pairs
        ],
        "legacy": (
            {
                "origin": legacy.origin,
                "conflict": legacy.conflict_with_mpzp,
                "result": legacy.result,
                "reasoning": legacy.reasoning,
                "confidence": (
                    _format_percent(legacy.confidence * 100.0) if legacy.confidence is not None else None
                ),
            }
            if legacy is not None
            else None
        ),
    }


# --- 5. Środowisko -----------------------------------------------------------------


RISK_SECTION_TITLES: dict[str, str] = {
    "flood": "Zagrożenie powodziowe (ISOK)",
    "nature": "Formy ochrony przyrody (GDOŚ)",
}
RISK_STATUS_LABELS: dict[str, str] = {
    "available": "sprawdzono",
    "unavailable": "źródło niedostępne",
    "error": "błąd sprawdzenia",
    "unknown": "brak informacji w zapisanym wyniku",
}
RISK_RELATION_LABELS: dict[str, str] = {
    "no_match": "brak obiektów przecinających działkę",
    "boundary_only": "wyłącznie styk z granicą działki (bez wspólnej powierzchni)",
    "intersection": "obiekty przecinają działkę",
    "unknown": "nieustalona",
}
RISK_STATUS_NOTES: dict[str, str] = {
    "unavailable": (
        "Nie udało się sprawdzić źródła. Brak obiektów w tej sekcji NIE oznacza "
        "braku ryzyka ani ograniczeń."
    ),
    "error": (
        "Sprawdzenie zakończyło się nieoczekiwanym błędem. Brak obiektów NIE "
        "oznacza braku ryzyka ani ograniczeń."
    ),
    "unknown": (
        "Zapis sprzed strukturalnych sekcji ryzyka nie zawiera statusu "
        "sprawdzenia — brak obiektów nie potwierdza braku ryzyka."
    ),
}
_SEVERITY_LABELS_PL: dict[str, str] = {"low": "niski", "medium": "średni", "high": "wysoki"}
_RISK_SECTION_NAMES: tuple[Literal["flood", "nature"], ...] = ("flood", "nature")


def _risk_section_name(item: RiskResult) -> str:
    if item.section is not None:
        return item.section
    return "flood" if item.risk_type in {"flood", "flood_zone"} else "nature"


def _risk_sections_context(response: AnalyzeResponse) -> list[dict[str, Any]]:
    """Sekcje ryzyka ze statusem niezależnym od listy obiektów (BK-303)."""
    return [
        _risk_section_context(
            section,
            [item for item in response.risks if _risk_section_name(item) == section.section],
        )
        for section in _risk_sections(response)
    ]


def _risk_section_context(section: RiskSectionResult, items: list[RiskResult]) -> dict[str, Any]:
    return {
        "name": section.section,
        "number": 1 if section.section == "flood" else 2,
        "title": RISK_SECTION_TITLES[section.section],
        "status": section.status,
        "status_label": RISK_STATUS_LABELS[section.status],
        "note": RISK_STATUS_NOTES.get(section.status),
        "reason_code": section.reason_code,
        "relation_label": RISK_RELATION_LABELS[section.relation],
        "feature_count": section.feature_count,
        "intersecting_count": section.intersecting_feature_count,
        "boundary_count": section.boundary_feature_count,
        "union_area": _area(section.union_intersection_area_sqm)
        if section.union_intersection_area_sqm is not None
        else None,
        "union_pct": _share(section.union_intersection_pct)
        if section.union_intersection_pct is not None
        else None,
        "source": _source_brief(section.source) if section.source else None,
        "warnings": list(section.warnings),
        "features": [_risk_context(item) for item in items],
    }


def _risk_context(item: RiskResult) -> dict[str, Any]:
    return {
        "risk_type": item.risk_type,
        "feature_id": item.feature_id,
        "probability_class": item.probability_class,
        "return_period": (
            f"{item.return_period_years} lat" if item.return_period_years is not None else None
        ),
        "protection_type": item.protection_type,
        "name": item.name,
        "severity": _SEVERITY_LABELS_PL.get(item.severity or "", item.severity),
        "area": _area(item.intersection_area_sqm) if item.intersection_area_sqm is not None else None,
        "pct": _share(item.intersection_pct) if item.intersection_pct is not None else None,
        "touches_boundary": item.touches_boundary,
        "description": item.description,
        "warnings": list(item.warnings),
        "confidence": _confidence_context(item.source),
    }


# --- 6. Teren ----------------------------------------------------------------------


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
    return {
        "status": terrain.status,
        "status_label": _TERRAIN_STATUS_LABELS[terrain.status],
        "note": note,
        "reason_code": terrain.reason_code,
        "min_height": _meters(terrain.min_height_m),
        "max_height": _meters(terrain.max_height_m),
        "height_difference": _meters(terrain.height_difference_m),
        "grid_size": _meters(terrain.grid_size_m) if terrain.grid_size_m is not None else None,
        "sampled_points": terrain.sampled_points,
        "source": _terrain_source_context(terrain.source),
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
        "resolution": _meters(relief.resolution_m) if relief.resolution_m is not None else None,
        "valid_share": _share(relief.valid_area_share_pct),
        "valid_pixel_count": relief.valid_pixel_count,
        "parcel_pixel_count": relief.parcel_pixel_count,
        "nodata_pixel_count": relief.nodata_pixel_count,
        "min_height": _meters(relief.min_height_m),
        "max_height": _meters(relief.max_height_m),
        "mean_height": _meters(relief.mean_height_m),
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
                "id": item.class_id,
                "label": item.label,
                "range": (
                    f"{_format_trimmed(item.min_pct, 2)}–{_format_trimmed(item.max_pct, 2)}%"
                    if item.max_pct is not None
                    else f"≥ {_format_trimmed(item.min_pct, 2)}%"
                ),
                "pixels": item.pixel_count,
                "area": _area(item.area_sqm),
                "share": _share(item.share_pct),
            }
            for item in relief.slope_classes
        ],
        "aspect": (
            {
                "status": aspect.status,
                "direction": _ASPECT_LABELS.get(aspect.dominant_direction or "", None),
                "azimuth": _format_trimmed(aspect.mean_azimuth_deg, 1),
                "resultant": _format_trimmed(aspect.resultant_length, 2),
                "non_flat_share": _share(aspect.non_flat_share_pct),
                "flat_threshold": _format_trimmed(aspect.flat_threshold_pct, 1),
                "sectors": [
                    f"{_ASPECT_LABELS.get(key, key)} {_share(value)}"
                    for key, value in sorted(aspect.sector_shares_pct.items())
                ],
            }
            if aspect is not None
            else None
        ),
        "profile": _profile_context(relief),
        "raster": (
            {
                "coverage_id": raster.coverage_id,
                "crs": raster.crs,
                "resolution": _meters(raster.resolution_m),
                "size": f"{raster.width_px} × {raster.height_px} px",
                "bytes": f"{raster.size_bytes:,}".replace(",", " ") + " B",
                "bbox": ", ".join(_format_trimmed(value, 3) or "" for value in raster.bbox),
                "buffer": _meters(raster.buffer_m),
                "vertical_datum": raster.vertical_datum,
                "gdal_version": raster.gdal_version,
                "nodata_value": raster.nodata_value,
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
        "method": profile.method,
        "interpolation": profile.interpolation,
        "crs": profile.crs,
        "start": f"{_format_trimmed(profile.start[0], 3)}, {_format_trimmed(profile.start[1], 3)}",
        "end": f"{_format_trimmed(profile.end[0], 3)}, {_format_trimmed(profile.end[1], 3)}",
        "length": _meters(profile.length_m),
        "step": _meters(profile.step_m),
        "sample_count": len(profile.samples),
        "inside_count": sum(1 for sample in profile.samples if sample.inside_parcel),
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
    context.update(segments=segments, min_label=_meters(low), max_label=_meters(high))
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


# --- 7. Infrastruktura ------------------------------------------------------------


_UTILITIES_LABELS: dict[str, str] = {
    "covered": "powiat publikuje dane GESUT w KIUT",
    "not_covered": "KIUT nie potwierdził publikacji danych GESUT przez powiat",
    "unknown": "nie udało się sprawdzić pokrycia powiatu",
}


def _utilities_preview_context(response: AnalyzeResponse) -> dict[str, Any] | None:
    preview = response.utilities_preview
    if preview is None:
        return None
    return {
        "coverage_status": preview.coverage_status,
        "coverage_label": _UTILITIES_LABELS[preview.coverage_status],
        "county_name": preview.county_name,
        "layer_available": _format_bool(preview.layer_available),
        "note": preview.note,
        "source": _source_brief(preview.source),
    }


def _infrastructure_context(item: Any) -> dict[str, Any]:
    return {
        "network_type": item.network_type,
        "buffer": _meters(item.buffer_m, 2),
        "zone_area": _area(item.zone_area_sqm),
        "affects_buildable_area": _format_bool(item.affects_buildable_area),
        "rule_source": item.rule_source,
        "rule_confidence": (
            _format_percent(item.rule_confidence * 100.0) if item.rule_confidence is not None else None
        ),
        "rule_note": item.rule_note,
        "source": _source_brief(item.source),
    }


# --- 9. Źródła i provenance --------------------------------------------------------


def _source_context(source: SourceMetadata) -> dict[str, Any]:
    return {
        "source_name": source.source_name,
        "source_id": source.source_id,
        "source_version": source.source_version,
        "source_url": source.source_url,
        "data_release_id": source.data_release_id,
        "act_version": source.act_version,
        "artifact_sha256": source.artifact_sha256,
        "fetched_at": _format_datetime(source.fetched_at) if source.fetched_at else None,
        "response_status": (
            str(source.response_status) if source.response_status is not None else "brak danych"
        ),
        "confidence_pct": _format_percent(source.confidence * 100.0),
        "confidence_label": describe_confidence(source.confidence, source.manual_review_required),
        "manual_review_required": source.manual_review_required,
    }


def _source_brief(source: SourceMetadata | None) -> dict[str, Any] | None:
    if source is None:
        return None
    return {
        "name": source.source_name,
        "fetched_at": _format_datetime(source.fetched_at) if source.fetched_at else None,
        "response_status": source.response_status,
        "release": source.data_release_id,
        "version": source.source_version,
        "sha256": source.artifact_sha256,
        "confidence": _confidence_context(source),
    }


def _provenance_context(
    response: AnalyzeResponse,
    maps: ReportMaps | None,
    record: AnalysisRecordMeta,
    quality: SectionQualityMatrix | None = None,
) -> dict[str, Any]:
    releases = sorted(
        {
            *record.data_release_ids,
            *(source.data_release_id for source in response.sources if source.data_release_id),
        }
    )
    contracts = [
        ("Kontrakt wyniku analizy", record.result_contract_version),
        ("Układ raportu", REPORT_LAYOUT_VERSION),
        ("Wynik POG", response.pog.schema_version if response.pog else None),
        (
            "Ocena relacji MPZP–POG",
            response.pog.compatibility_assessment.schema_version
            if response.pog and response.pog.compatibility_assessment
            else None,
        ),
        (
            "Sekcje ryzyka",
            ", ".join(sorted({section.schema_version for section in response.risk_sections})) or None,
        ),
        ("Macierz jakości sekcji", quality.schema_version if quality else None),
        ("Polityka jakości (wersja)", quality.policy_version if quality else None),
        ("Teren (NMT)", response.terrain.schema_version if response.terrain else None),
        (
            "Discovery MPZP (KIMPZP)",
            response.mpzp_discovery.schema_version if response.mpzp_discovery else None,
        ),
        (
            "Teren — raster",
            response.terrain.relief.schema_version
            if response.terrain and response.terrain.relief
            else None,
        ),
    ]
    map_context = None
    if maps is not None:
        map_context = {
            "from_snapshot": maps.from_snapshot,
            "config_version": maps.config_version,
            "pog_theme": maps.pog_theme,
            "pog_style_version": maps.pog_style_version,
            "semantic_sha256": maps.semantic_sha256,
            "stored_semantic_sha256": maps.stored_semantic_sha256,
            "integrity_ok": maps.integrity_ok,
            "environment": maps.environment,
            "basemap": maps.basemap,
            "images": [
                {"title": item.title, "sha256": item.png_sha256, "status": item.status}
                for item in maps.maps
            ],
        }
    return {
        "releases": releases,
        "contracts": [(label, value or NOT_SPECIFIED) for label, value in contracts],
        "cache_signature": record.cache_signature,
        "maps": map_context,
    }


def _warning_context(warning: WarningMessage) -> dict[str, Any]:
    labels = {"info": "informacja", "warning": "ostrzeżenie", "error": "błąd"}
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


def _field_mapping_context(response: AnalyzeResponse) -> list[dict[str, Any]]:
    rows = []
    for item in field_presence(response):
        mapping = item["mapping"]
        present, missing = item["present"], item["missing"]
        rows.append(
            {
                "pattern": mapping.pattern,
                "section": SECTION_BY_ID[mapping.section].number,
                "element": mapping.element if not mapping.omitted else "pominięte",
                "reason": mapping.omitted_reason,
                "kind": MAPPING_KIND_LABELS[mapping.kind],
                "presence": (
                    "brak obiektu w snapshocie"
                    if present == 0 and missing == 0
                    else f"{present} wart.; null/puste: {missing}"
                ),
            }
        )
    return rows


# --- 10. Ograniczenia --------------------------------------------------------------


def _build_limitations(response: AnalyzeResponse, maps: ReportMaps | None = None) -> list[str]:
    """Ograniczenia wynikające wprost z danych snapshotu i map (bez nowych statusów)."""
    limitations: list[str] = [
        "Raport nie zawiera syntetycznej oceny (scoringu) atrakcyjności inwestycyjnej ani "
        "uśrednionych parametrów różnych stref — każda strefa jest opisana osobno.",
    ]
    if maps is None:
        limitations.append(
            "Nie udało się wygenerować map raportu. Pozostała część raportu jest kompletna."
        )
    else:
        limitations.extend(maps.warnings)
        if not maps.from_snapshot:
            limitations.append(
                "Analiza sprzed zamrażania map (BK-503): mapy odtworzono z danych snapshotu "
                "analizy przy bieżącej konfiguracji renderowania "
                f"{maps.config_version} — styl mapy może różnić się od widoku z chwili analizy."
            )
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
                f"Status prawny POG: {LEGAL_STATUS_LABELS_PL[response.pog.legal_status]}."
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
            "Nie udało się sprawdzić pokrycia KIUT. Pusty podgląd nie oznacza braku sieci."
        )
    elif response.utilities_preview.coverage_status == "not_covered":
        limitations.append(
            "KIUT nie potwierdził publikacji GESUT przez powiat. Nie jest to "
            "potwierdzenie braku sieci na działce."
        )
    limitations.append(
        "Transport i dostęp do drogi publicznej nie są analizowane (brak potwierdzonego "
        "kontraktu źródła, BK-305); bliskość geometryczna nie potwierdza prawnego dostępu."
    )

    if any(zone.assignment_method == "document_candidate" for zone in response.mpzp_zones):
        limitations.append(
            "Strefę MPZP przypisano bez wektora wydzieleń (punktowe discovery i "
            "dokument); przypisanie ma obniżoną pewność."
        )
    if any(
        parameter.value_kind == "conflict"
        for zone in response.mpzp_zones
        for parameter in zone.parameters
    ):
        limitations.append(
            "Uchwała zawiera sprzeczne wartości parametrów MPZP; żadna nie została "
            "wybrana automatycznie."
        )
    if any(
        parameter.value_kind == "conditional"
        for zone in response.mpzp_zones
        for parameter in zone.parameters
    ):
        limitations.append(
            "Część parametrów MPZP ma wartości zależne od warunków (np. rodzaj dachu, rodzaj zabudowy, "
            "podstrefa); płaskie pole strefy jest puste, gdy uchwała nie podaje jednej wartości dla całej "
            "strefy. Warunki i ich cytaty są w tabeli evidence."
        )
    if _has_model_reading(response):
        limitations.append(
            f"Wartości oznaczone jako {MODEL_READING_MARK} są kandydatami, nie ustaleniami. "
            f"{MODEL_READING_DISCLAIMER} Tabela evidence podaje model, wersję instrukcji i skrót odpowiedzi modelu."
        )
    if any(
        zone.assignment_method == "manual_user_input"
        or zone.source.source_name.casefold() == "manual_user_input"
        for zone in response.mpzp_zones
    ):
        limitations.append(
            "Symbol strefy podano ręcznie na podstawie podglądu rastrowego (WMS), "
            "bez wektorowej granicy strefy. Każdy parametr zależny od tego symbolu "
            "wymaga weryfikacji w materiale źródłowym, a wynik nie może być pełny."
        )
    if any(zone.intersection_pct is None and not zone.touches_boundary for zone in response.mpzp_zones):
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
    if response.infrastructure:
        limitations.append(
            "Bufory sieci uzbrojenia są przybliżeniem technicznym (reguła konfiguracyjna); "
            "wymagają uzgodnienia z gestorem sieci."
        )
    if any(item.source.manual_review_required for item in response.infrastructure):
        limitations.append("Dane o uzbrojeniu terenu wymagają ręcznej weryfikacji u gestora sieci.")
    if any(item.source.manual_review_required for item in response.risks):
        limitations.append("Dane o ryzykach wymagają ręcznej weryfikacji.")
    if response.buildable_area_sqm is not None:
        limitations.append(
            "Szacowany obszar zabudowy jest przybliżeniem technicznym (odsunięcie od granic), "
            "a nie ustaleniem prawnym."
        )

    limitations.extend(_risk_section_limitations(response))
    limitations.extend(_terrain_limitations(response.terrain))
    return limitations


def _risk_section_limitations(response: AnalyzeResponse) -> list[str]:
    limitations: list[str] = []
    for section in _risk_sections(response):
        title = RISK_SECTION_TITLES[section.section]
        if section.status in {"unavailable", "error"}:
            limitations.append(
                f"{title}: sprawdzenie nie powiodło się ({section.reason_code or section.status}) "
                "— brak obiektów nie oznacza braku ryzyka."
            )
        elif section.status == "unknown":
            limitations.append(f"{title}: zapisany wynik nie zawiera statusu sprawdzenia.")
    return limitations


def _terrain_limitations(terrain: TerrainResult | None) -> list[str]:
    terrain = terrain or terrain_from_snapshot(None)
    limitations: list[str] = []
    if terrain.status == "unknown":
        limitations.append("Zapisany wynik nie zawiera pomiaru NMT — rzeźba terenu jest nieznana.")
    elif terrain.status == "no_coverage":
        limitations.append(
            "Brak pokrycia danymi NMT — deniwelacja nieznana; nie oznacza to płaskiego terenu."
        )
    elif terrain.status == "unavailable":
        limitations.append(
            "Pomiar NMT był niedostępny — deniwelacja nieznana; nie oznacza to płaskiego terenu."
        )
    relief = terrain.relief
    if relief is not None and relief.status != "available":
        limitations.append(
            "Spadku, ekspozycji i profilu nie policzono "
            f"({relief.reason_code or relief.status}); brak statystyk nie oznacza płaskiego terenu."
        )
    elif relief is not None:
        limitations.append(
            "Spadek i ekspozycja pochodzą z rastra NMT metodą Horna "
            f"({relief.algorithm_version}); klasy nachylenia "
            f"({relief.slope_classes_version}) są konwencją systemu, nie normą "
            "prawną, a wynik nie zastępuje mapy do celów projektowych."
        )
    return limitations


# --- Renderowanie HTML i PDF ---------------------------------------------------------


_JINJA_ENV = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(enabled_extensions=("html",), default=True, default_for_string=True),
    trim_blocks=True,
    lstrip_blocks=True,
    undefined=StrictUndefined,
)


def _render_report_html(context: dict[str, Any]) -> str:
    """Renderuje szablon ``report.html`` z automatycznym escapowaniem danych."""
    return _JINJA_ENV.get_template(REPORT_TEMPLATE_NAME).render(**context)


def _offline_url_fetcher() -> Any:
    """Fetcher WeasyPrint dopuszczający wyłącznie osadzone ``data:``.

    Raport nie może pobierać zasobów z sieci ani z dysku: mapy i wszystkie
    obrazy są osadzone w HTML. WeasyPrint ≥ 69 przyjmuje obiekt ``URLFetcher``
    z listą protokołów; starsze wersje — funkcję (obsługiwane dla zgodności).
    """
    from weasyprint import urls  # import leniwy: patrz _html_to_pdf

    fetcher_class = getattr(urls, "URLFetcher", None)
    if fetcher_class is not None:
        return fetcher_class(allowed_protocols=("data",), allow_redirects=False)

    def fetch(url: str, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover - WeasyPrint < 69
        if not url.startswith("data:"):
            raise ValueError(f"Raport PDF nie pobiera zasobów zewnętrznych: {url[:40]}")
        return urls.default_url_fetcher(url, *args, **kwargs)

    return fetch


def _html_to_pdf(html: str) -> bytes:
    """Zamienia HTML na PDF przez WeasyPrint (import leniwy — Pango/Cairo w obrazie)."""
    try:
        from weasyprint import HTML  # import leniwy
    except (ImportError, OSError) as exc:  # pragma: no cover - zależne od środowiska
        raise AnalysisReportRenderError(
            "Biblioteka WeasyPrint lub jej zależności systemowe nie są dostępne."
        ) from exc

    try:
        return HTML(string=html, url_fetcher=_offline_url_fetcher()).write_pdf()
    except Exception as exc:  # noqa: BLE001 - granica: nie ujawniamy detali WeasyPrint
        logger.exception("WeasyPrint nie wygenerował PDF")
        raise AnalysisReportRenderError("Nie udało się wygenerować dokumentu PDF.") from exc


# --- Formatowanie (pl-PL; null → „nie określono”, 0 renderowane liczbowo) -----------


def _format_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.strftime("%d.%m.%Y %H:%M")


def _format_iso(value: str) -> str:
    try:
        return _format_datetime(datetime.fromisoformat(value)) or value
    except ValueError:
        return value


def _format_date(value: date | None) -> str | None:
    if value is None:
        return None
    return value.strftime("%d.%m.%Y")


def _format_number(value: float | None, decimals: int = 2) -> str | None:
    """Liczba ze spacją jako separatorem tysięcy i przecinkiem dziesiętnym."""
    if value is None:
        return None
    formatted = f"{value:,.{decimals}f}"
    return formatted.replace(",", " ").replace(".", ",")


def _format_trimmed(value: float | None, max_decimals: int = 3) -> str | None:
    """Liczba bez zbędnych zer (112.3 → 112,3; 0.0 → 0), pl-PL."""
    if value is None:
        return None
    formatted = _format_number(value, decimals=max_decimals) or ""
    if "," in formatted:
        formatted = formatted.rstrip("0").rstrip(",")
    return "0" if formatted in {"-0", ""} else formatted


def _format_percent(value: float | None) -> str | None:
    if value is None:
        return None
    return f"{_format_number(value, decimals=1)}%"


def _area(value: float | None) -> str:
    """Pole z dokładnością prezentacji 0,01 m² (dane źródłowe bez zmian)."""
    return f"{_format_number(value, 2)} m²" if value is not None else NOT_SPECIFIED


def _share(value: float | None) -> str:
    """Udział z dokładnością 0,01% — bez korekty zaokrągleń do sumy 100%."""
    return f"{_format_number(value, 2)}%" if value is not None else NOT_SPECIFIED


def _meters(value: float | None, decimals: int | None = None) -> str:
    if value is None:
        return NOT_SPECIFIED
    number = _format_number(value, decimals) if decimals is not None else _format_trimmed(value)
    return f"{number} m"


def _unit_suffix(unit: str | None) -> str:
    if not unit:
        return ""
    return unit if unit == "%" else f" {unit}"


def _shares_sum(values: list[float | None]) -> dict[str, str] | None:
    """Suma udziałów wierszy i odchylenie od 100% w punktach procentowych."""
    known = [value for value in values if value is not None]
    if len(known) < 2:
        return None
    total = sum(known)
    deviation = round(total - 100.0, 2)
    sign = "" if deviation == 0 else "+" if deviation > 0 else chr(0x2212)
    return {
        "total": _share(total),
        "deviation": f"{sign}{_format_number(abs(deviation), 2)}" + chr(0xA0) + "pp",
        "complete": len(known) == len(values),
    }


def _sentence(text: str) -> str:
    """Pierwsza litera wielka, jedna kropka na końcu (bez „..”)."""
    text = text.strip().rstrip(".")
    return (text[:1].upper() + text[1:] + ".") if text else text


def _format_bool(value: bool) -> str:
    return "tak" if value else "nie"


def _plural(count: int, one: str, few: str, many: str) -> str:
    if count == 1:
        return one
    if count % 10 in {2, 3, 4} and count % 100 not in {12, 13, 14}:
        return few
    return many
