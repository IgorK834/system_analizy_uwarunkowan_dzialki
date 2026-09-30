"""Atomowe wznowienie analizy oczekującej na symbol strefy MPZP (BK-204).

Wznowienie nie sięga do sieci: parser dostaje wyłącznie dokument przypięty w
chwili wstrzymania analizy (bajty + SHA-256 + wersja dokumentu), więc inna
uchwała opublikowana później pod tym samym URL nie zmieni wyniku. Ręcznie
podany symbol nie ustala udziału strefy w powierzchni działki — udział pozostaje
nieustalony, każdy zależny parametr wymaga weryfikacji, a wynik nigdy nie
staje się ``complete``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Literal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.analysis import Analysis
from app.models.analysis_pending_document import AnalysisPendingDocument
from app.models.mpzp_parameter import MpzpParameter
from app.models.mpzp_zone import MpzpZone
from app.models.pog_data import PogData
from app.models.source_record import SourceRecord
from app.schemas.analyze import (
    AnalyzeResponse,
    ManualZoneSelection,
    MpzpZoneResult,
    WarningMessage,
)
from app.schemas.mpzp import MpzpParserWarning
from app.schemas.source import SourceMetadata
from app.services.mpzp_parser import parse_mpzp_document
from app.services.mpzp_zones import (
    MANUAL_ZONE_SYMBOL_CONFIDENCE,
    DocumentEvidenceContext,
    cap_fallback_zone,
    legal_unit_evidence_from_snapshot,
    map_parser_zone_to_analyze_response,
    unassigned_share_zone,
    validate_zone_symbol_format,
)
from app.services.ouz import OUZ_LEGAL_DISCLAIMER, OuzStatusResult
from app.services.persistence import (
    add_mpzp_zone_snapshot,
    build_analyze_response_from_analysis,
    pending_document_blob,
    pog_result_from_record,
    refresh_report_map_snapshot,
)
from app.services.pog_scenarios import PogScenarioResult, build_pog_scenario_result
from app.modules.documents.composition import (
    build_ocr_provider,
    persist_parser_audit,
)

# Ostrzeżenia wyliczane przy wstrzymaniu, które resume zastępuje nowymi
# (ocena MPZP–POG jest liczona ponownie po podaniu symbolu).
_RECOMPUTED_WARNING_CODES = frozenset(
    {
        "MPZP_MANUAL_ZONE_REQUIRED",
        "MPZP_POG_INPUT_INCOMPLETE",
        "MPZP_POG_COMPATIBILITY_UNKNOWN",
        "MPZP_POG_PAIRS_NOT_SPATIAL",
        "MPZP_POG_POTENTIAL_DIVERGENCE",
        "POG_TRANSITIONAL_STATUS",
        "POG_SCENARIO_UNKNOWN",
    }
)


class AnalysisResumeNotFoundError(Exception):
    """Nie istnieje analiza o przekazanym identyfikatorze."""


class AnalysisResumeStateError(Exception):
    """Analiza nie oczekuje na ręczny symbol strefy."""


class AnalysisResumeDocumentError(Exception):
    """Przypięty dokument jest uszkodzony albo nie daje się sparsować."""


async def resume_analysis_with_zone(
    analysis_id: int,
    raw_zone_symbol: str,
    db: Session,
) -> AnalyzeResponse:
    """Wznawia snapshot i zwraca jego pełny, ponownie odtworzony kontrakt.

    Kolejność gwarantuje brak częściowego zapisu: walidacja (404/409/422) →
    weryfikacja SHA i parsowanie przypiętego dokumentu (błąd → 503) → blokada
    wiersza analizy ``FOR UPDATE`` i ponowne sprawdzenie statusu (równoległe
    wznowienie → 409) → jeden commit strefy, parametrów, oceny POG, ostrzeżeń,
    źródła i statusu. POG, ryzyka, infrastruktura i źródła kontekstu (w tym
    NMT) nie są modyfikowane.
    """
    # Zapytania do bazy są synchroniczne, więc biegną w wątku roboczym — inaczej
    # blokowałyby pętlę zdarzeń dla wszystkich pozostałych żądań. Sesja jest
    # używana sekwencyjnie (nigdy równolegle), więc jest bezpieczna.
    inputs = await asyncio.to_thread(_prepare_resume, db, analysis_id, raw_zone_symbol)

    parse_result = None
    if inputs.document is not None:
        try:
            parse_result = await parse_mpzp_document(
                inputs.document, [inputs.zone_symbol], build_ocr_provider()
            )
        except Exception as exc:
            raise AnalysisResumeDocumentError(
                "Nie udało się odczytać przypiętego dokumentu MPZP."
            ) from exc

    return await asyncio.to_thread(_apply_resume, db, analysis_id, inputs, parse_result)


@dataclass(frozen=True)
class _ResumeInputs:
    """Dane odczytane przed parsowaniem przypiętego dokumentu."""

    zone_symbol: str
    pinned: AnalysisPendingDocument | None
    plan_id: str | None
    document_url: str | None
    candidates: list[str]
    paused_at: datetime
    document: Any


def _prepare_resume(db: Session, analysis_id: int, raw_zone_symbol: str) -> _ResumeInputs:
    """Walidacja 404/409/422 i weryfikacja SHA przypiętego dokumentu (sync)."""
    analysis = db.get(Analysis, analysis_id)
    if analysis is None:
        raise AnalysisResumeNotFoundError
    if analysis.status != "waiting_for_zone_symbol":
        raise AnalysisResumeStateError

    zone_symbol = validate_zone_symbol_format(raw_zone_symbol)
    pinned = db.scalar(
        select(AnalysisPendingDocument).where(
            AnalysisPendingDocument.analysis_id == analysis_id
        )
    )
    plan_id = analysis.pending_plan_id
    document_url = analysis.pending_uchwala_url
    candidates = list(analysis.pending_zone_symbol_candidates or [])
    paused_at = analysis.analyzed_at

    document = None
    if pinned is not None:
        if sha256(pinned.content).hexdigest() != pinned.content_sha256:
            raise AnalysisResumeDocumentError(
                "Przypięty dokument nie zgadza się z zapisanym SHA-256."
            )
        document = pending_document_blob(pinned)

    return _ResumeInputs(
        zone_symbol=zone_symbol,
        pinned=pinned,
        plan_id=plan_id,
        document_url=document_url,
        candidates=candidates,
        paused_at=paused_at,
        document=document,
    )


def _apply_resume(
    db: Session,
    analysis_id: int,
    inputs: _ResumeInputs,
    parse_result: Any,
) -> AnalyzeResponse:
    """Jeden transakcyjny zapis wznowienia i odtworzenie odpowiedzi (sync)."""
    zone_symbol = inputs.zone_symbol
    pinned = inputs.pinned
    plan_id = inputs.plan_id
    document_url = inputs.document_url
    candidates = inputs.candidates
    paused_at = inputs.paused_at
    document = inputs.document

    try:
        analysis = _lock_waiting_analysis(db, analysis_id)
        new_warnings: list[WarningMessage] = []
        snapshot = None
        if document is not None and parse_result is not None:
            snapshot, persistence_warning = _persist_audit_safely(
                db, plan_id or f"mpzp-document:{document_url}", document, parse_result
            )
            if persistence_warning is not None:
                # Rollback zapisu audytu zwolnił blokadę — pobieramy ją ponownie.
                analysis = _lock_waiting_analysis(db, analysis_id)
                new_warnings.append(persistence_warning)

        selection = ManualZoneSelection(
            entered_symbol=zone_symbol,
            plan_id=plan_id,
            candidate_zone_symbols=candidates,
            symbol_in_candidates=zone_symbol in candidates,
            document_url=document_url or (pinned.requested_url if pinned else None),
            document_sha256=pinned.content_sha256 if pinned else None,
            document_version_id=(
                getattr(snapshot, "document_version_id", None)
                or (pinned.document_version_id if pinned else None)
            ),
            document_fetched_at=pinned.fetched_at if pinned else None,
            document_pinned=pinned is not None,
            selected_at=datetime.now(timezone.utc),
        )
        source = SourceMetadata(
            source_name="manual_user_input",
            source_url=selection.document_url,
            fetched_at=selection.document_fetched_at,
            response_status=pinned.response_status if pinned else None,
            artifact_sha256=selection.document_sha256,
            confidence=MANUAL_ZONE_SYMBOL_CONFIDENCE,
            manual_review_required=True,
        )
        mapped_zone, skipped_parameters, symbol_missing = _manual_zone(
            zone_symbol, parse_result, source, snapshot, selection
        )
        new_warnings = [
            *_resume_warnings(
                parse_result.warnings if parse_result is not None else [],
                skipped_parameters,
                symbol_missing_in_document=symbol_missing,
                document_pinned=pinned is not None,
                symbol_in_candidates=selection.symbol_in_candidates,
            ),
            *new_warnings,
        ]

        pog_record = db.scalar(select(PogData).where(PogData.analysis_id == analysis.id))
        parcel_area_sqm = analysis.parcel.area_sqm or 0.0
        pog_scenario = _pog_scenario_for_resume(
            db, analysis, pog_record, mapped_zone, parcel_area_sqm, paused_at
        )
        if pog_scenario is not None:
            new_warnings.extend(pog_scenario.warnings)

        zone_ids = select(MpzpZone.id).where(MpzpZone.analysis_id == analysis.id)
        db.execute(delete(MpzpParameter).where(MpzpParameter.mpzp_zone_id.in_(zone_ids)))
        db.execute(delete(MpzpZone).where(MpzpZone.analysis_id == analysis.id))
        add_mpzp_zone_snapshot(db, analysis.id, mapped_zone)
        if pog_record is not None and pog_scenario is not None:
            _update_pog_record(pog_record, pog_scenario)

        retained_warnings = [
            warning
            for warning in (analysis.warnings or [])
            if warning.get("code") not in _RECOMPUTED_WARNING_CODES
        ]
        analysis.warnings = [
            *retained_warnings,
            *(warning.model_dump(mode="json") for warning in new_warnings),
        ]
        # Ręczna identyfikacja strefy nigdy nie daje ``complete`` (BK-204).
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
                checksum=source.artifact_sha256,
                artifact_sha256=source.artifact_sha256,
            )
        )
        # BK-503: treść analizy zmieniła się (strefa ręczna, scenariusz POG),
        # więc mapy raportu są zamrażane ponownie w tej samej transakcji.
        refresh_report_map_snapshot(analysis, db)
        db.commit()
        db.refresh(analysis)
    except Exception:
        db.rollback()
        raise

    return build_analyze_response_from_analysis(analysis, db)


def _lock_waiting_analysis(db: Session, analysis_id: int) -> Analysis:
    """Blokuje wiersz analizy i ponownie sprawdza status po uzyskaniu blokady."""
    locked = db.execute(
        select(Analysis)
        .where(Analysis.id == analysis_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if locked is None:
        raise AnalysisResumeNotFoundError
    if locked.status != "waiting_for_zone_symbol":
        raise AnalysisResumeStateError
    return locked


def _persist_audit_safely(db: Session, planning_act_identifier: str, document, parse_result):  # noqa: ANN001
    try:
        return (
            persist_parser_audit(
                db,
                planning_act_identifier=planning_act_identifier,
                document=document,
                parse_result=parse_result,
            ),
            None,
        )
    except Exception:
        db.rollback()
        return None, WarningMessage(
            code="MPZP_DOCUMENT_PERSISTENCE_FAILED",
            message=(
                "Nie udało się zapisać cytowalnej struktury dokumentu MPZP. "
                "Wznowiony wynik pozostaje częściowy."
            ),
            severity="error",
            source_name="mpzp",
        )


def _manual_zone(
    zone_symbol: str,
    parse_result,  # noqa: ANN001
    source: SourceMetadata,
    snapshot,  # noqa: ANN001
    selection: ManualZoneSelection,
) -> tuple[MpzpZoneResult, list[str], bool]:
    """Strefa ręczna: parametry z przypiętego dokumentu, udział nieustalony."""
    matching_zone = (
        next((zone for zone in parse_result.zones if zone.zone_symbol == zone_symbol), None)
        if parse_result is not None
        else None
    )
    skipped: list[str] = []
    if matching_zone is not None:
        mapped, skipped = map_parser_zone_to_analyze_response(
            matching_zone,
            0.0,
            source,
            evidence=DocumentEvidenceContext(
                document_version_id=selection.document_version_id,
                legal_units=legal_unit_evidence_from_snapshot(snapshot),
            ),
        )
    else:
        mapped = unassigned_share_zone(zone_symbol, source)
    mapped = mapped.model_copy(
        update={"act_identifier": selection.plan_id, "document_url": selection.document_url}
    )
    capped = cap_fallback_zone(
        mapped, assignment_method="manual_user_input", manual_selection=selection
    )
    return capped, skipped, parse_result is not None and matching_zone is None


def _pog_scenario_for_resume(
    db: Session,
    analysis: Analysis,
    pog_record: PogData | None,
    mpzp_zone: MpzpZoneResult,
    parcel_area_sqm: float,
    paused_at: datetime,
) -> PogScenarioResult | None:
    """Przelicza ocenę MPZP–POG na zachowanym snapshotcie POG (bez sieci)."""
    if pog_record is None:
        return None
    pog = pog_result_from_record(pog_record, list(analysis.source_records), parcel_area_sqm)
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
    return build_pog_scenario_result(
        [mpzp_zone],
        pog,
        ouz_status,
        as_of=paused_at,
        parcel_area_sqm=parcel_area_sqm,
    )


def _update_pog_record(pog_record: PogData, scenario: PogScenarioResult) -> None:
    """Aktualizuje ocenę w kolumnie i w snapshotcie v2 (źródło prawdy odczytu)."""
    assessment = scenario.assessment.model_dump(mode="json")
    pog_record.compatibility_assessment = assessment
    pog_record.manual_review_required = (
        pog_record.manual_review_required or scenario.manual_review_required
    )
    if pog_record.result_v2 is not None:
        # Pozostałe pola snapshotu POG zostają nietknięte (zachowanie POG przy
        # wznowieniu); usuwamy jedynie przestarzały boolean.
        result_v2 = dict(pog_record.result_v2)
        result_v2.pop("conflict_with_mpzp", None)
        result_v2["compatibility_assessment"] = assessment
        result_v2["manual_review_required"] = pog_record.manual_review_required
        pog_record.result_v2 = result_v2


def _resume_warnings(
    parser_warnings: list[MpzpParserWarning],
    skipped_parameters: list[str],
    *,
    symbol_missing_in_document: bool,
    document_pinned: bool,
    symbol_in_candidates: bool,
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
                "Symbol strefy podano ręcznie na podstawie podglądu rastrowego, bez "
                "wektorowej granicy. Udział strefy w powierzchni działki pozostaje "
                "nieustalony, każdy parametr zależny od tego symbolu wymaga "
                "weryfikacji, a wynik pozostaje częściowy."
            ),
            severity="warning",
            source_name="mpzp",
        )
    )
    if not symbol_in_candidates:
        warnings.append(
            WarningMessage(
                code="MPZP_MANUAL_SYMBOL_NOT_IN_CANDIDATES",
                message=(
                    "Podany symbol nie należy do kandydatów wskazanych przez "
                    "discovery dla tego planu — sprawdź go z rysunkiem planu."
                ),
                severity="warning",
                source_name="mpzp",
            )
        )
    if not document_pinned:
        warnings.append(
            WarningMessage(
                code="MPZP_PINNED_DOCUMENT_MISSING",
                message=(
                    "Dokument uchwały nie został przypięty przy wstrzymaniu analizy; "
                    "parametry strefy pozostają nieustalone (system nie pobiera "
                    "dokumentu ponownie, aby nie odczytać innej wersji uchwały)."
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
