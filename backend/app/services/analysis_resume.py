"""Atomowe wznowienie analizy oczekującej na symbol strefy MPZP."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.analysis import Analysis
from app.models.mpzp_parameter import MpzpParameter
from app.models.mpzp_zone import MpzpZone
from app.models.pog_data import PogData
from app.models.source_record import SourceRecord
from app.schemas.analyze import AnalyzeResponse, MpzpZoneResult, WarningMessage
from app.schemas.mpzp import MpzpParserWarning
from app.schemas.source import SourceMetadata
from app.services.mpzp_fetch import fetch_mpzp_document
from app.services.mpzp_parser import parse_mpzp_document
from app.services.mpzp_zones import (
    MANUAL_ZONE_SYMBOL_CONFIDENCE,
    DocumentEvidenceContext,
    cap_fallback_zone,
    legal_unit_evidence_from_snapshot,
    map_parser_zone_to_analyze_response,
    validate_zone_symbol_format,
)
from app.services.ouz import OUZ_LEGAL_DISCLAIMER, OuzStatusResult
from app.services.persistence import (
    add_mpzp_zone_snapshot,
    build_analyze_response_from_analysis,
)
from app.services.pog_scenarios import PogScenarioResult, build_pog_scenario_result
from app.modules.documents.composition import (
    build_ocr_provider,
    persist_parser_audit,
)


class AnalysisResumeNotFoundError(Exception):
    """Nie istnieje analiza o przekazanym identyfikatorze."""


class AnalysisResumeStateError(Exception):
    """Analiza nie oczekuje na ręczny symbol strefy."""


class AnalysisResumeSourceMissingError(Exception):
    """Snapshot nie ma URL dokumentu potrzebnego do wznowienia."""


async def resume_analysis_with_zone(
    analysis_id: int,
    raw_zone_symbol: str,
    db: Session,
) -> AnalyzeResponse:
    """Wznawia snapshot i zwraca jego pełny, ponownie odtworzony kontrakt.

    Pobranie i parsowanie dokumentu odbywa się przed jakąkolwiek modyfikacją
    snapshotu. Strefa, parametry, ostrzeżenia, źródło oraz status są następnie
    zapisywane jednym commitem. Ręczny fallback bez granicy wektorowej zawsze
    pozostaje ``partial``, nawet gdy parser dokumentu zwrócił komplet parametrów.
    """
    analysis = db.get(Analysis, analysis_id)
    if analysis is None:
        raise AnalysisResumeNotFoundError
    if analysis.status != "waiting_for_zone_symbol":
        raise AnalysisResumeStateError

    zone_symbol = validate_zone_symbol_format(raw_zone_symbol)
    if not analysis.pending_uchwala_url:
        raise AnalysisResumeSourceMissingError

    document_url = analysis.pending_uchwala_url
    document = await fetch_mpzp_document(document_url)
    parse_result = await parse_mpzp_document(
        document,
        [zone_symbol],
        build_ocr_provider(),
    )
    persistence_warning: WarningMessage | None = None
    snapshot = None
    try:
        snapshot = persist_parser_audit(
            db,
            planning_act_identifier=(
                analysis.pending_plan_id
                or f"mpzp-document:{analysis.pending_uchwala_url}"
            ),
            document=document,
            parse_result=parse_result,
        )
    except Exception:
        db.rollback()
        analysis = db.get(Analysis, analysis_id)
        assert analysis is not None
        persistence_warning = WarningMessage(
            code="MPZP_DOCUMENT_PERSISTENCE_FAILED",
            message=(
                "Nie udało się zapisać cytowalnej struktury dokumentu MPZP. "
                "Wznowiony wynik pozostaje częściowy."
            ),
            severity="error",
            source_name="mpzp",
        )
    source = SourceMetadata(
        source_name="manual_user_input",
        source_url=document_url,
        fetched_at=document.source_metadata.fetched_at,
        response_status=document.source_metadata.response_status,
        confidence=MANUAL_ZONE_SYMBOL_CONFIDENCE,
        manual_review_required=True,
    )

    matching_zone = next(
        (zone for zone in parse_result.zones if zone.zone_symbol == zone_symbol),
        None,
    )
    parcel_area_sqm = analysis.parcel.area_sqm or 0.0
    if matching_zone is not None:
        mapped_zone, skipped_parameters = map_parser_zone_to_analyze_response(
            matching_zone,
            parcel_area_sqm,
            source,
            evidence=DocumentEvidenceContext(
                document_version_id=getattr(snapshot, "document_version_id", None),
                legal_units=legal_unit_evidence_from_snapshot(snapshot),
            ),
        )
    else:
        mapped_zone = _manual_zone_without_parameters(
            zone_symbol,
            parcel_area_sqm,
            source,
        )
        skipped_parameters = []

    # Ręczny odczyt symbolu z rastra: brak wektora, obniżona pewność (BK-202).
    mapped_zone = cap_fallback_zone(mapped_zone, assignment_method="manual_user_input")
    new_warnings = _resume_warnings(
        parse_result.warnings,
        skipped_parameters,
        matching_zone is None,
    )
    if persistence_warning is not None:
        new_warnings.append(persistence_warning)
    pog_record = db.scalar(select(PogData).where(PogData.analysis_id == analysis.id))
    pog_scenario = _pog_scenario_for_resume(
        pog_record,
        mapped_zone,
        parcel_area_sqm,
    )
    if pog_scenario is not None:
        new_warnings.extend(pog_scenario.warnings)
    try:
        zone_ids = select(MpzpZone.id).where(MpzpZone.analysis_id == analysis.id)
        db.execute(delete(MpzpParameter).where(MpzpParameter.mpzp_zone_id.in_(zone_ids)))
        db.execute(delete(MpzpZone).where(MpzpZone.analysis_id == analysis.id))
        add_mpzp_zone_snapshot(db, analysis.id, mapped_zone)
        if pog_record is not None and pog_scenario is not None:
            compatibility = pog_scenario.compatibility
            pog_record.conflict_with_mpzp = (
                pog_scenario.conflict
                if compatibility is not None
                and compatibility.result in {"compatible", "incompatible"}
                else None
            )
            pog_record.manual_review_required = (
                pog_record.manual_review_required
                or pog_scenario.manual_review_required
            )
            raw_attributes = dict(pog_record.raw_attributes or {})
            raw_attributes["scenario"] = {
                "message": pog_scenario.message,
                "legal_disclaimer": pog_scenario.legal_disclaimer,
                "conflict_uncertain": pog_scenario.conflict_uncertain,
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
            pog_record.raw_attributes = raw_attributes

            if pog_record.result_v2 is not None:
                result_v2 = dict(pog_record.result_v2)
                result_v2["conflict_with_mpzp"] = pog_record.conflict_with_mpzp
                result_v2["manual_review_required"] = pog_record.manual_review_required
                result_v2_raw_attributes = dict(result_v2.get("raw_attributes") or {})
                result_v2_raw_attributes["scenario"] = raw_attributes["scenario"]
                result_v2["raw_attributes"] = result_v2_raw_attributes
                pog_record.result_v2 = result_v2

        retained_warnings = [
            warning
            for warning in (analysis.warnings or [])
            if warning.get("code") != "MPZP_MANUAL_ZONE_REQUIRED"
        ]
        analysis.warnings = [
            *retained_warnings,
            *(warning.model_dump(mode="json") for warning in new_warnings),
        ]
        analysis.status = "partial"
        analysis.analyzed_at = datetime.now(timezone.utc)
        analysis.resolved_zone_symbol = zone_symbol
        analysis.pending_uchwala_url = None
        analysis.pending_plan_id = None
        analysis.pending_zone_symbol_candidates = None
        db.add(
            SourceRecord(
                analysis_id=analysis.id,
                source_name=source.source_name,
                source_url=source.source_url,
                fetched_at=source.fetched_at,
                response_status=(
                    str(source.response_status)
                    if source.response_status is not None
                    else "available"
                ),
                confidence=source.confidence,
                manual_review_required=True,
                warnings=[warning.message for warning in new_warnings],
                checksum=None,
            )
        )
        db.commit()
        db.refresh(analysis)
    except Exception:
        db.rollback()
        raise

    return build_analyze_response_from_analysis(analysis, db)


def _pog_scenario_for_resume(
    pog_record: PogData | None,
    mpzp_zone: MpzpZoneResult,
    parcel_area_sqm: float,
) -> PogScenarioResult | None:
    """Odtwarza jawny stan OUZ i przelicza scenariusz po podaniu MPZP."""
    if pog_record is None:
        return None
    raw_ouz = (pog_record.raw_attributes or {}).get("ouz")
    ouz_status_name: Literal["available", "unknown"] = (
        "available"
        if isinstance(raw_ouz, dict) and raw_ouz.get("status") == "available"
        else "unknown"
    )
    ouz_area = pog_record.ouz_intersection_area_sqm or 0.0
    ouz_status = OuzStatusResult(
        status=ouz_status_name,
        in_ouz=pog_record.in_ouz,
        intersection_area_sqm=ouz_area,
        area_ratio=(
            ouz_area / parcel_area_sqm * 100.0 if parcel_area_sqm > 0 else 0.0
        ),
        touches_ouz_boundary=pog_record.touches_ouz_boundary,
        manual_review_required=(
            pog_record.manual_review_required or ouz_status_name == "unknown"
        ),
        legal_disclaimer=OUZ_LEGAL_DISCLAIMER,
    )
    return build_pog_scenario_result(mpzp_zone, pog_record, ouz_status)


def _manual_zone_without_parameters(
    zone_symbol: str,
    parcel_area_sqm: float,
    source: SourceMetadata,
) -> MpzpZoneResult:
    return MpzpZoneResult(
        zone_symbol=zone_symbol,
        intersection_area_sqm=parcel_area_sqm,
        intersection_pct=100.0,
        is_dominant=True,
        source=source,
    )


def _resume_warnings(
    parser_warnings: list[MpzpParserWarning],
    skipped_parameters: list[str],
    symbol_missing_in_document: bool,
) -> list[WarningMessage]:
    warnings = [
        WarningMessage(
            code=warning.code,
            message=warning.message,
            severity=warning.severity,
            source_name="mpzp",
        )
        for warning in parser_warnings
    ]
    warnings.append(
        WarningMessage(
            code="MPZP_MANUAL_ZONE_FALLBACK",
            message=(
                "Symbol strefy podano ręcznie na podstawie rastrowej nakładki "
                "WMS, bez wektorowej granicy. Całą działkę przypisano technicznie "
                "do tej strefy, dlatego wynik pozostaje częściowy i wymaga "
                "weryfikacji."
            ),
            severity="warning",
            source_name="mpzp",
        )
    )
    if symbol_missing_in_document:
        warnings.append(
            WarningMessage(
                code="MPZP_SYMBOL_NOT_FOUND_IN_DOCUMENT",
                message=(
                    "Parser nie odnalazł podanego symbolu w dokumencie; "
                    "zachowano ręczny symbol bez automatycznie odczytanych parametrów."
                ),
                severity="warning",
                source_name="mpzp",
            )
        )
    if skipped_parameters:
        warnings.append(
            WarningMessage(
                code="MPZP_PARAMETERS_NOT_IN_FLAT_CONTRACT",
                message=(
                    "Parser znalazł dodatkowe parametry bez odpowiednika w "
                    "płaskim kontrakcie API: "
                    + ", ".join(sorted(set(skipped_parameters)))
                    + "."
                ),
                severity="warning",
                source_name="mpzp",
            )
        )
    return warnings
