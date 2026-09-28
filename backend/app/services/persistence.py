"""Transakcyjna persystencja wyniku analizy i audyt użytych źródeł.

Moduł zapisuje istniejący kontrakt ``AnalyzeResponse`` i pomija sekcje, które
nie zostały jeszcze podłączone do orkiestratora. Nie próbuje odgadywać statusu
POG ani MPZP przy braku danych. Dokładne ``available/unavailable/error`` można
zapisać wyłącznie dla KIUT, ISOK i GDOŚ, gdy wywołujący przekaże
``ContextResult``.

To minimalny zapis snapshotowy na obecnym schemacie. Nie zastępuje docelowego,
wersjonowanego modelu provenance opisanego w
``ANALIZA_ARCHITEKTURY_I_PLAN.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from hashlib import sha256

from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import MultiPolygon
from shapely.geometry.base import BaseGeometry
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.analysis import Analysis
from app.models.analysis_pending_document import AnalysisPendingDocument
from app.models.infrastructure import Infrastructure
from app.models.mpzp_parameter import MpzpParameter
from app.models.mpzp_zone import MpzpZone
from app.models.parcel import Parcel
from app.models.pog_data import PogData
from app.models.risk import Risk
from app.models.source_record import SourceRecord
from app.schemas.analyze import (
    AnalyzeResponse,
    GeometryMetrics,
    InfrastructureResult,
    ManualZoneContext,
    ManualZoneSourceDocument,
    MpzpZoneResult,
    ParcelGeometryResponse,
    PogResult,
    RiskResult,
    UtilitiesPreviewResult,
    WarningMessage,
)
from app.schemas.source import SourceMetadata
from app.services.context import ContextResult
from app.services.geojson import (
    buildable_area_geometry_to_geojson,
    parcel_geometry_to_geojson,
)
from app.services.geometry import calculate_geometry_metrics, calculate_technical_setback
from app.services.cache import RESULT_CONTRACT_VERSION, current_cache_signature
from app.services.mpzp_fetch import DocumentBlob
from app.services.risks import risk_sections_from_snapshot
from app.services.terrain import terrain_from_snapshot
from app.services.mpzp_zones import ZONE_SYMBOL_ALLOWED_PATTERN, ZONE_SYMBOL_MAX_LENGTH
from app.modules.documents.composition import register_document_artifact

MANUAL_ZONE_NOTICE = (
    "Gmina nie udostępnia wektorowych granic stref MPZP. Porównaj podgląd "
    "rastrowy planu, identyfikator planu i kandydatów symboli, a następnie podaj "
    "symbol strefy. Symbol podany ręcznie nie ustala udziału strefy w "
    "powierzchni działki (pozostaje nieustalony), wynik pozostanie częściowy, a "
    "każdy parametr zależny od tego symbolu będzie wymagał weryfikacji."
)
MANUAL_ZONE_NOTICE_NO_DOCUMENT = (
    " Dokumentu uchwały nie udało się przypiąć przy wstrzymaniu analizy — "
    "parametry strefy nie zostaną odczytane automatycznie."
)


@dataclass(frozen=True)
class SourceRecordData:
    """Niezależny od ORM opis jednego źródła użytego w analizie."""

    source_name: str
    source_url: str | None
    fetched_at: datetime | None
    response_status: str | None
    confidence: float | None
    manual_review_required: bool
    warnings: list[str]
    checksum: str | None
    source_id: str | None = None
    source_version: str | None = None
    artifact_sha256: str | None = None
    data_release_id: int | None = None
    act_version: str | None = None


_NUMERIC_ZONE_PARAMETERS: tuple[str, ...] = (
    "max_building_height_m",
    "max_floors",
    "min_biologically_active_pct",
    "max_floor_area_ratio",
    "min_floor_area_ratio",
    "max_building_coverage_pct",
)


def get_or_create_parcel(
    db: Session,
    parcel_identifier: str,
    geometry: BaseGeometry,
) -> Parcel:
    """Zwraca istniejącą działkę albo zapisuje nową geometrię EPSG:2180.

    Kolumna PostGIS ma typ ``MULTIPOLYGON``, dlatego pojedynczy ``Polygon``
    jest opakowywany bez zmiany współrzędnych. Funkcja wykonuje tylko
    ``flush``; granicę transakcji kontroluje wywołujący.
    """
    existing = db.execute(
        select(Parcel).where(Parcel.parcel_identifier == parcel_identifier)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    multi_geometry = (
        geometry if geometry.geom_type == "MultiPolygon" else MultiPolygon([geometry])
    )
    parcel = Parcel(
        parcel_identifier=parcel_identifier,
        geometry=from_shape(multi_geometry, srid=2180),
        area_sqm=geometry.area,
    )
    db.add(parcel)
    db.flush()
    return parcel


def collect_source_records(
    result: AnalyzeResponse,
    context_result: ContextResult | None = None,
) -> list[SourceRecordData]:
    """Zbiera audytowalne źródła obecne w wyniku analizy.

    Bez ``context_result`` nie tworzy sztucznych wpisów KIUT/ISOK/GDOŚ — brak
    sekcji w ``AnalyzeResponse`` nie mówi, czy usługi nie wywołano, czy była
    niedostępna. Analogicznie brak POG albo MPZP nie jest automatycznie
    oznaczany jako ``unavailable``.

    Checksum pozostaje ``None``, ponieważ ``AnalyzeResponse`` nie przenosi
    bajtów ani hasha dokumentu. Wartość wolno uzupełnić dopiero w przepływie,
    który faktycznie ma dostęp do oryginalnego artefaktu.
    """
    records: list[SourceRecordData] = []

    if result.parcel is not None:
        records.append(_source_record_data(result.parcel.source, result))

    records.extend(_source_record_data(source, result) for source in result.sources)
    records.extend(
        _source_record_data(zone.source, result) for zone in result.mpzp_zones
    )

    if result.pog is not None and result.pog.source is not None:
        records.append(_source_record_data(result.pog.source, result))

    records.extend(
        _source_record_data(item.source, result) for item in result.infrastructure
    )
    if result.utilities_preview is not None:
        records.append(_source_record_data(result.utilities_preview.source, result))
    records.extend(_source_record_data(item.source, result) for item in result.risks)
    records.extend(_terrain_source_records(result))
    records.extend(_risk_section_source_records(result))

    if context_result is not None:
        for section in context_result.sections():
            source = section.source_metadata
            response_status: str | None
            if section.status in {"unavailable", "error"}:
                response_status = section.status
            elif source is not None and source.response_status is not None:
                response_status = str(source.response_status)
            else:
                response_status = "available"

            records.append(
                SourceRecordData(
                    source_name=source.source_name if source else section.section,
                    source_url=source.source_url if source else None,
                    fetched_at=source.fetched_at if source else None,
                    response_status=response_status,
                    confidence=source.confidence if source else None,
                    manual_review_required=(
                        (source.manual_review_required if source else False)
                        or section.status != "available"
                    ),
                    warnings=list(section.warnings),
                    checksum=None,
                )
            )

    return _deduplicate_source_records(records)


def save_analysis(
    result: AnalyzeResponse,
    parcel_identifier: str,
    parcel_geometry: BaseGeometry,
    db: Session,
    context_result: ContextResult | None = None,
    *,
    database_status: str | None = None,
    pending_uchwala_url: str | None = None,
    pending_plan_id: str | None = None,
    pending_zone_symbol_candidates: list[str] | None = None,
    pending_document: DocumentBlob | None = None,
) -> Analysis:
    """Atomowo zapisuje wszystkie wypełnione sekcje ``AnalyzeResponse``.

    Geometria musi być oryginalną geometrią działki w EPSG:2180. GeoJSON z
    odpowiedzi API jest przeznaczony dla frontendu i nie jest transformowany
    z powrotem. Każde wywołanie tworzy nowy ``Analysis`` jako element historii;
    ponownie używany jest wyłącznie rekord ``Parcel``.

    ``database_status`` rozdziela status publicznego API od stanu workflow.
    Jest używany tylko dla trybu ręcznego MPZP: API zwraca
    ``waiting_for_user_input``, podczas gdy istniejący endpoint resume wymaga
    w bazie ``waiting_for_zone_symbol``. Pola ``pending_*`` są zapisywane w tej
    samej transakcji, aby gałąź rastrowa nie wykonywała drugiego commita.
    ``pending_document`` (BK-204) przypina bajty i wersję uchwały w tej samej
    transakcji — resume parsuje wyłącznie ten artefakt.

    Jakikolwiek błąd sekcji wycofuje całą transakcję i jest propagowany do
    wywołującego, który odpowiada za mapowanie go na odpowiedź HTTP.
    """
    try:
        parcel = get_or_create_parcel(db, parcel_identifier, parcel_geometry)
        cache_signature, data_release_ids = current_cache_signature(db)
        analysis = Analysis(
            parcel_id=parcel.id,
            analyzed_at=result.analyzed_at,
            status=database_status or result.status,
            buildable_area_sqm=result.buildable_area_sqm,
            warnings=(
                [warning.model_dump(mode="json") for warning in result.warnings] or None
            ),
            utilities_preview=(
                result.utilities_preview.model_dump(mode="json")
                if result.utilities_preview is not None
                else None
            ),
            # ``None`` tylko, gdy wynik nie zawiera sekcji NMT — odczyt
            # zinterpretuje to jako ``unknown``, nigdy jako płaski teren.
            terrain=(
                result.terrain.model_dump(mode="json")
                if result.terrain is not None
                else None
            ),
            risk_sections=(
                [section.model_dump(mode="json") for section in result.risk_sections]
                or None
            ),
            pending_uchwala_url=pending_uchwala_url,
            pending_plan_id=pending_plan_id,
            pending_zone_symbol_candidates=pending_zone_symbol_candidates,
            data_release_ids=data_release_ids,
            result_contract_version=RESULT_CONTRACT_VERSION,
            cache_signature=cache_signature,
        )
        db.add(analysis)
        db.flush()

        if pending_document is not None and pending_uchwala_url:
            add_pending_document(
                db,
                analysis.id,
                pending_document,
                requested_url=pending_uchwala_url,
                planning_act_identifier=(
                    pending_plan_id or f"mpzp-document:{pending_uchwala_url}"
                ),
            )

        for zone in result.mpzp_zones:
            add_mpzp_zone_snapshot(db, analysis.id, zone)

        if result.pog is not None:
            pog_source = result.pog.source
            db.add(
                PogData(
                    analysis_id=analysis.id,
                    status=result.pog.legal_status,
                    legal_status=result.pog.legal_status,
                    coverage_status=result.pog.coverage_status,
                    data_availability=result.pog.data_availability,
                    status_confirmed_at=result.pog.status_confirmed_at,
                    planning_zone=result.pog.planning_zone,
                    zone_type=(result.pog.zone_type or result.pog.planning_zone),
                    # ``in_ouz`` jest decyzją domenową z jawnych progów OUZ,
                    # nie prostym testem dodatniego pola przecięcia.
                    in_ouz=result.pog.in_ouz,
                    area_ratio=result.pog.area_ratio,
                    in_downtown_area=result.pog.in_downtown_area,
                    uchwala_nr=result.pog.uchwala_nr,
                    uchwala_date=result.pog.uchwala_date,
                    manual_review_required=(
                        result.pog.manual_review_required
                        or (
                            pog_source.manual_review_required
                            if pog_source
                            else False
                        )
                    ),
                    compatibility_assessment=(
                        result.pog.compatibility_assessment.model_dump(mode="json")
                        if result.pog.compatibility_assessment is not None
                        else None
                    ),
                    raw_attributes=result.pog.raw_attributes,
                    ouz_intersection_area_sqm=(result.pog.ouz_intersection_area_sqm),
                    touches_ouz_boundary=result.pog.touches_ouz_boundary,
                    source_url=pog_source.source_url if pog_source else None,
                    fetched_at=pog_source.fetched_at if pog_source else None,
                    confidence=pog_source.confidence if pog_source else None,
                    schema_version=result.pog.schema_version,
                    result_v2=result.pog.model_dump(mode="json"),
                    legacy_partial=False,
                )
            )

        for infrastructure_item in result.infrastructure:
            db.add(
                Infrastructure(
                    analysis_id=analysis.id,
                    network_type=infrastructure_item.network_type,
                    buffer_m=infrastructure_item.buffer_m,
                    zone_area_sqm=infrastructure_item.zone_area_sqm,
                    rule_source=infrastructure_item.rule_source,
                    rule_confidence=infrastructure_item.rule_confidence,
                    rule_note=infrastructure_item.rule_note,
                    affects_buildable_area=(
                        infrastructure_item.affects_buildable_area
                    ),
                    network_geometry_geojson=(
                        infrastructure_item.network_geometry_geojson
                    ),
                    protection_zone_geojson=(
                        infrastructure_item.protection_zone_geojson
                    ),
                    source_url=infrastructure_item.source.source_url,
                    fetched_at=infrastructure_item.source.fetched_at,
                    confidence=infrastructure_item.source.confidence,
                    manual_review_required=(
                        infrastructure_item.source.manual_review_required
                    ),
                )
            )

        for risk_item in result.risks:
            db.add(
                Risk(
                    analysis_id=analysis.id,
                    risk_type=risk_item.risk_type,
                    description=risk_item.description,
                    section=risk_item.section,
                    feature_id=risk_item.feature_id,
                    severity=risk_item.severity,
                    probability_class=risk_item.probability_class,
                    return_period_years=risk_item.return_period_years,
                    protection_type=risk_item.protection_type,
                    name=risk_item.name,
                    intersection_area_sqm=risk_item.intersection_area_sqm,
                    intersection_pct=risk_item.intersection_pct,
                    touches_boundary=risk_item.touches_boundary,
                    result_snapshot=risk_item.model_dump(mode="json"),
                    geometry_geojson=risk_item.geometry_geojson,
                    source_url=risk_item.source.source_url,
                    fetched_at=risk_item.source.fetched_at,
                    confidence=risk_item.source.confidence,
                    manual_review_required=(risk_item.source.manual_review_required),
                )
            )

        for source_data in collect_source_records(result, context_result):
            db.add(
                SourceRecord(
                    analysis_id=analysis.id,
                    source_name=source_data.source_name,
                    source_url=source_data.source_url,
                    fetched_at=source_data.fetched_at,
                    response_status=source_data.response_status,
                    confidence=source_data.confidence,
                    manual_review_required=source_data.manual_review_required,
                    warnings=source_data.warnings or None,
                    checksum=source_data.checksum,
                    source_id=source_data.source_id,
                    source_version=source_data.source_version,
                    artifact_sha256=source_data.artifact_sha256,
                    data_release_id=source_data.data_release_id,
                    act_version=source_data.act_version,
                )
            )

        db.commit()
        db.refresh(analysis)
        return analysis
    except Exception:
        db.rollback()
        raise


def add_pending_document(
    db: Session,
    analysis_id: int,
    document: DocumentBlob,
    *,
    requested_url: str,
    planning_act_identifier: str,
) -> AnalysisPendingDocument:
    """Przypina dokument uchwały do wstrzymanej analizy (tylko ``flush``).

    Rejestruje też artefakt i wersję dokumentu w module dokumentów, aby
    evidence parametrów po wznowieniu wskazywało dokładnie tę wersję.
    """
    content_sha256 = sha256(document.content).hexdigest()
    document_version_id = register_document_artifact(
        db,
        planning_act_identifier=planning_act_identifier,
        document=document,
    )
    record = AnalysisPendingDocument(
        analysis_id=analysis_id,
        requested_url=requested_url,
        final_url=document.source_metadata.source_url,
        media_type=document.media_type,
        filename=document.filename,
        content=document.content,
        content_sha256=content_sha256,
        size_bytes=len(document.content),
        fetched_at=document.source_metadata.fetched_at,
        response_status=document.source_metadata.response_status,
        document_version_id=document_version_id,
    )
    db.add(record)
    db.flush()
    return record


def pending_document_blob(record: AnalysisPendingDocument) -> DocumentBlob:
    """Odtwarza ``DocumentBlob`` z przypiętego artefaktu (bez sieci)."""
    return DocumentBlob(
        content=record.content,
        media_type=record.media_type,
        filename=record.filename,
        source_metadata=SourceMetadata(
            source_name="MPZP_BIP",
            source_url=record.final_url or record.requested_url,
            fetched_at=record.fetched_at,
            response_status=record.response_status,
            artifact_sha256=record.content_sha256,
            confidence=0.9 if record.media_type == "application/pdf" else 0.5,
            manual_review_required=record.media_type != "application/pdf",
        ),
    )


def build_manual_zone_context(analysis: Analysis) -> ManualZoneContext:
    """Materiał pokazywany przed formularzem symbolu (BK-204)."""
    pinned = analysis.pending_document
    if pinned is not None:
        document_status = "pinned"
        document = ManualZoneSourceDocument(
            requested_url=pinned.requested_url,
            media_type=pinned.media_type,
            filename=pinned.filename,
            sha256=pinned.content_sha256,
            size_bytes=pinned.size_bytes,
            fetched_at=pinned.fetched_at,
            document_version_id=pinned.document_version_id,
            preview_path=f"/analyze/{analysis.id}/pending-document",
        )
    else:
        document = None
        document_status = (
            "unavailable" if analysis.pending_uchwala_url else "not_provided"
        )
    return ManualZoneContext(
        plan_id=analysis.pending_plan_id,
        candidate_zone_symbols=list(analysis.pending_zone_symbol_candidates or []),
        document_status=document_status,
        document=document,
        symbol_max_length=ZONE_SYMBOL_MAX_LENGTH,
        symbol_allowed_pattern=ZONE_SYMBOL_ALLOWED_PATTERN,
        notice=MANUAL_ZONE_NOTICE
        + ("" if pinned is not None else MANUAL_ZONE_NOTICE_NO_DOCUMENT),
    )


def add_mpzp_zone_snapshot(
    db: Session,
    analysis_id: int,
    zone: MpzpZoneResult,
) -> MpzpZone:
    """Dodaje strefę MPZP z pełnym snapshotem i evidence bez zamykania transakcji.

    ``result_snapshot`` jest źródłem prawdy odczytu historycznego (lista
    kandydatur z evidence, geometria przecięcia, provenance wersji aktu), więc
    przełączenie wydania ani ponowne pobranie dokumentu nie zmieniają odczytu.
    """
    zone_record = MpzpZone(
        analysis_id=analysis_id,
        zone_symbol=zone.zone_symbol,
        primary_use=zone.primary_use,
        intersection_area_sqm=zone.intersection_area_sqm,
        intersection_pct=zone.intersection_pct,
        is_dominant=zone.is_dominant,
        source_url=zone.source.source_url,
        fetched_at=zone.source.fetched_at,
        confidence=zone.source.confidence,
        zone_identifier=zone.zone_id,
        act_identifier=zone.act_identifier,
        act_version=zone.act_version,
        act_version_id=zone.act_version_id,
        data_release_id=zone.data_release_id,
        touches_boundary=zone.touches_boundary,
        assignment_method=zone.assignment_method,
        result_snapshot=zone.model_dump(mode="json"),
    )
    db.add(zone_record)
    db.flush()

    if zone.parameters:
        for parameter in zone.parameters:
            db.add(
                MpzpParameter(
                    mpzp_zone_id=zone_record.id,
                    parameter_name=parameter.name,
                    normalized_value=(
                        str(parameter.normalized_value)[:255]
                        if parameter.normalized_value is not None
                        else None
                    ),
                    unit=parameter.unit,
                    source_fragment=parameter.evidence_text,
                    page_number=parameter.page_number,
                    confidence=parameter.confidence,
                    manual_review_required=parameter.manual_review_required,
                    raw_value=parameter.raw_value,
                    segment_id=parameter.segment_id,
                    legal_unit_id=parameter.legal_unit_id,
                    document_sha256=parameter.document_sha256,
                    document_version_id=parameter.document_version_id,
                    parser_version=parameter.parser_version,
                    extraction_method=parameter.extraction_method,
                    conflict_group_id=parameter.conflict_group_id,
                )
            )
        return zone_record

    for parameter_name in _NUMERIC_ZONE_PARAMETERS:
        value = getattr(zone, parameter_name)
        if value is None:
            continue
        db.add(
            MpzpParameter(
                mpzp_zone_id=zone_record.id,
                parameter_name=parameter_name,
                normalized_value=str(value),
                unit=_parameter_unit(parameter_name),
                confidence=zone.source.confidence,
                manual_review_required=zone.source.manual_review_required,
            )
        )
    return zone_record


def build_analyze_response_from_analysis(
    analysis: Analysis,
    db: Session,
) -> AnalyzeResponse:
    """Odtwarza kontrakt API z zapisanego snapshotu analizy.

    Wszystkie relacje są ładowane jawnie w bieżącej sesji, dlatego wynik cache
    nie zależy od wcześniejszego stanu lazy loading. Geometria działki pozostaje
    w PostGIS jako EPSG:2180; dopiero przy budowaniu odpowiedzi jest zamieniana
    przez ``to_shape`` i istniejący helper GeoJSON na WGS84.

    Obecny schemat snapshotowy nie przechowuje ``supplementary_use`` ani udziału
    OUZ. Pierwsza wartość wraca więc jako ``None``, a udział OUZ jest odtwarzany
    z zapisanego pola przecięcia i powierzchni działki. Docelowo te ograniczenia
    usuwa wersjonowany model provenance z ADR-004/005.
    """
    loaded = db.execute(
        select(Analysis)
        .where(Analysis.id == analysis.id)
        .options(
            selectinload(Analysis.parcel),
            selectinload(Analysis.mpzp_zones).selectinload(MpzpZone.parameters),
            selectinload(Analysis.pog_data),
            selectinload(Analysis.infrastructure_records),
            selectinload(Analysis.risk_records),
            selectinload(Analysis.source_records),
            selectinload(Analysis.pending_document),
        )
    ).scalar_one()

    parcel_geometry = to_shape(loaded.parcel.geometry)
    metrics = calculate_geometry_metrics(parcel_geometry)
    setback = calculate_technical_setback(parcel_geometry)
    sources = [_source_metadata_from_record(record) for record in loaded.source_records]
    parcel_source_record = next(
        (
            record
            for record in loaded.source_records
            if record.source_name.casefold() == "uldk"
        ),
        None,
    )
    parcel_source = (
        _source_metadata_from_record(parcel_source_record)
        if parcel_source_record is not None
        else SourceMetadata(
            source_name="cache",
            source_url=None,
            fetched_at=loaded.analyzed_at,
            confidence=0.0,
            manual_review_required=True,
        )
    )
    parcel_response = ParcelGeometryResponse(
        parcel_identifier=loaded.parcel.parcel_identifier,
        geometry_geojson=parcel_geometry_to_geojson(
            parcel_geometry,
            loaded.parcel.parcel_identifier,
        ),
        metrics=GeometryMetrics(
            area_sqm=metrics.area_sqm,
            area_ha=metrics.area_ha,
            perimeter_m=metrics.perimeter_m,
            is_valid=metrics.is_valid,
            geometry_repaired=metrics.geometry_repaired,
        ),
        source=parcel_source,
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

    zones = [
        _mpzp_zone_response(zone, loaded.source_records) for zone in loaded.mpzp_zones
    ]
    pog = (
        pog_result_from_record(
            loaded.pog_data[0],
            loaded.source_records,
            metrics.area_sqm,
        )
        if loaded.pog_data
        else None
    )
    infrastructure = [
        InfrastructureResult(
            network_type=item.network_type,
            buffer_m=item.buffer_m or 0.0,
            zone_area_sqm=item.zone_area_sqm or 0.0,
            rule_source=item.rule_source,
            rule_confidence=item.rule_confidence,
            rule_note=item.rule_note,
            affects_buildable_area=item.affects_buildable_area,
            network_geometry_geojson=item.network_geometry_geojson,
            protection_zone_geojson=item.protection_zone_geojson,
            source=_source_for_child(
                loaded.source_records,
                item.source_url,
                item.fetched_at,
                fallback_name="KIUT",
                confidence=item.confidence,
                manual_review_required=item.manual_review_required,
            ),
        )
        for item in loaded.infrastructure_records
    ]
    risks = [
        _risk_response(item, loaded.source_records)
        for item in sorted(loaded.risk_records, key=lambda record: record.id)
    ]
    warnings = [
        WarningMessage.model_validate(warning) for warning in (loaded.warnings or [])
    ]
    waiting = loaded.status == "waiting_for_zone_symbol"

    return AnalyzeResponse(
        analysis_id=loaded.id,
        status=loaded.status,
        analyzed_at=loaded.analyzed_at,
        parcel=parcel_response,
        mpzp_zones=zones,
        pog=pog,
        infrastructure=infrastructure,
        utilities_preview=(
            UtilitiesPreviewResult.model_validate(loaded.utilities_preview)
            if loaded.utilities_preview is not None
            else None
        ),
        risks=risks,
        risk_sections=risk_sections_from_snapshot(loaded.risk_sections),
        terrain=terrain_from_snapshot(loaded.terrain),
        buildable_area_sqm=loaded.buildable_area_sqm,
        manual_zone_required=waiting,
        manual_zone_context=build_manual_zone_context(loaded) if waiting else None,
        warnings=warnings,
        sources=sources,
    )


def _source_record_data(
    source: SourceMetadata,
    result: AnalyzeResponse,
) -> SourceRecordData:
    response_status = (
        str(source.response_status)
        if source.response_status is not None
        else "available"
    )
    source_warnings = [
        warning.message
        for warning in result.warnings
        if warning.source_name is not None
        and warning.source_name.casefold() == source.source_name.casefold()
    ]
    return SourceRecordData(
        source_name=source.source_name,
        source_url=source.source_url,
        fetched_at=source.fetched_at,
        response_status=response_status,
        confidence=source.confidence,
        manual_review_required=source.manual_review_required,
        warnings=source_warnings,
        checksum=source.artifact_sha256,
        source_id=source.source_id,
        source_version=source.source_version,
        artifact_sha256=source.artifact_sha256,
        data_release_id=source.data_release_id,
        act_version=source.act_version,
    )


def _risk_response(item: Risk, source_records: list[SourceRecord]) -> RiskResult:
    """Obiekt ryzyka z bazy: snapshot BK-303 albo pola kolumn starego zapisu.

    Stary zapis nie jest „naprawiany” parsowaniem ``description`` — nieznane
    pola pozostają ``None``.
    """
    if item.result_snapshot is not None:
        return RiskResult.model_validate(item.result_snapshot)
    is_flood = item.section == "flood" or item.risk_type in {"flood", "flood_zone"}
    return RiskResult(
        risk_type=item.risk_type,
        section=item.section,  # type: ignore[arg-type]
        feature_id=item.feature_id,
        severity=item.severity,  # type: ignore[arg-type]
        probability_class=item.probability_class,
        return_period_years=item.return_period_years,
        protection_type=item.protection_type,
        name=item.name,
        intersection_area_sqm=item.intersection_area_sqm,
        intersection_pct=item.intersection_pct,
        touches_boundary=item.touches_boundary,
        description=item.description or "Brak opisu ryzyka.",
        geometry_geojson=item.geometry_geojson,
        source=_source_for_child(
            source_records,
            item.source_url,
            item.fetched_at,
            fallback_name="ISOK" if is_flood else "GDOS",
            confidence=item.confidence,
            manual_review_required=item.manual_review_required,
        ),
    )


def _risk_section_source_records(result: AnalyzeResponse) -> list[SourceRecordData]:
    """Provenance sekcji ryzyka ze statusem — także przy ``features=[]``."""
    records: list[SourceRecordData] = []
    for section in result.risk_sections:
        if section.source is None:
            continue
        record = _source_record_data(section.source, result)
        if section.status != "available":
            record = replace(record, response_status=section.status)
        records.append(record)
    return records


def _terrain_source_records(result: AnalyzeResponse) -> list[SourceRecordData]:
    """Źródła sekcji NMT z jawnym statusem — także brak pokrycia i porażka.

    Bez tego nieudana próba (timeout bez kodu HTTP) zostałaby zapisana jako
    ``available``, a brak pokrycia byłby nieodróżnialny od pomiaru.
    """
    terrain = result.terrain
    if terrain is None:
        return []
    records: list[SourceRecordData] = []
    sections = [(terrain.status, terrain.source)]
    if terrain.relief is not None:
        sections.append((terrain.relief.status, terrain.relief.source))
    for status, source in sections:
        if source is None:
            continue
        record = _source_record_data(source, result)
        if status != "available":
            record = replace(record, response_status=status)
        records.append(record)
    return records


def _deduplicate_source_records(
    records: list[SourceRecordData],
) -> list[SourceRecordData]:
    unique: list[SourceRecordData] = []
    indexes: dict[tuple[str, str | None, datetime | None], int] = {}
    for record in records:
        key = (record.source_name, record.source_url, record.fetched_at)
        existing_index = indexes.get(key)
        if existing_index is None:
            indexes[key] = len(unique)
            unique.append(record)
            continue

        existing = unique[existing_index]
        response_status = existing.response_status
        if record.response_status in {"unavailable", "error", "not_attempted", "no_coverage"}:
            response_status = record.response_status
        elif response_status in {None, "available"} and record.response_status:
            response_status = record.response_status

        unique[existing_index] = SourceRecordData(
            source_name=existing.source_name,
            source_url=existing.source_url,
            fetched_at=existing.fetched_at,
            response_status=response_status,
            confidence=(
                existing.confidence
                if existing.confidence is not None
                else record.confidence
            ),
            manual_review_required=(
                existing.manual_review_required or record.manual_review_required
            ),
            warnings=list(dict.fromkeys([*existing.warnings, *record.warnings])),
            checksum=existing.checksum or record.checksum,
            source_id=existing.source_id or record.source_id,
            source_version=existing.source_version or record.source_version,
            artifact_sha256=existing.artifact_sha256 or record.artifact_sha256,
            data_release_id=existing.data_release_id or record.data_release_id,
            act_version=existing.act_version or record.act_version,
        )
    return unique


def _parameter_unit(parameter_name: str) -> str | None:
    if parameter_name == "max_building_height_m":
        return "m"
    if parameter_name.endswith("_pct"):
        return "percent"
    return None


def _mpzp_zone_response(
    zone: MpzpZone,
    source_records: list[SourceRecord],
) -> MpzpZoneResult:
    if zone.result_snapshot is not None:
        return MpzpZoneResult.model_validate(zone.result_snapshot)
    parameters = {parameter.parameter_name: parameter for parameter in zone.parameters}
    manual_review_required = any(
        parameter.manual_review_required for parameter in zone.parameters
    )
    return MpzpZoneResult(
        zone_symbol=zone.zone_symbol,
        primary_use=zone.primary_use,
        supplementary_use=None,
        max_building_height_m=_parameter_float(parameters.get("max_building_height_m")),
        max_floors=_parameter_int(parameters.get("max_floors")),
        min_biologically_active_pct=_parameter_float(
            parameters.get("min_biologically_active_pct")
        ),
        max_floor_area_ratio=_parameter_float(parameters.get("max_floor_area_ratio")),
        min_floor_area_ratio=_parameter_float(parameters.get("min_floor_area_ratio")),
        max_building_coverage_pct=_parameter_float(
            parameters.get("max_building_coverage_pct")
        ),
        intersection_area_sqm=zone.intersection_area_sqm,
        intersection_pct=zone.intersection_pct,
        is_dominant=zone.is_dominant,
        assignment_method="legacy",
        source=_source_for_child(
            source_records,
            zone.source_url,
            zone.fetched_at,
            fallback_name="MPZP",
            confidence=zone.confidence,
            manual_review_required=manual_review_required,
        ),
    )


def pog_result_from_record(
    pog: PogData,
    source_records: list[SourceRecord],
    parcel_area_sqm: float,
) -> PogResult:
    """Odtwarza ``PogResult`` ze snapshotu (v2) albo z płaskich kolumn (v1)."""
    if pog.result_v2 is not None:
        return PogResult.model_validate(pog.result_v2)
    area = pog.ouz_intersection_area_sqm
    area_pct = (
        area / parcel_area_sqm * 100.0
        if area is not None and parcel_area_sqm > 0
        else None
    )
    source_record = _find_source_record(
        source_records,
        pog.source_url,
        pog.fetched_at,
        preferred_name="POG",
    )
    source = (
        _source_metadata_from_record(source_record)
        if source_record is not None
        else None
    )
    # Snapshot v1: status prawny jest odtwarzany wspólnym mapperem aliasów —
    # ``adopted`` bez zachowanego potwierdzenia źródłowego daje ``unknown``.
    return PogResult(
        schema_version="1.0",
        coverage_status=pog.coverage_status,
        status=pog.legacy_status or pog.status,
        planning_zone=pog.planning_zone,
        zone_type=pog.zone_type,
        in_ouz=pog.in_ouz,
        area_ratio=pog.area_ratio,
        in_downtown_area=pog.in_downtown_area,
        uchwala_nr=pog.uchwala_nr,
        uchwala_date=pog.uchwala_date,
        manual_review_required=pog.manual_review_required,
        compatibility_assessment=pog.compatibility_assessment,
        raw_attributes=pog.raw_attributes,
        ouz_intersection_area_sqm=area,
        ouz_intersection_pct=area_pct,
        touches_ouz_boundary=pog.touches_ouz_boundary,
        source=source,
    )


def _source_for_child(
    source_records: list[SourceRecord],
    source_url: str | None,
    fetched_at: datetime | None,
    *,
    fallback_name: str,
    confidence: float | None,
    manual_review_required: bool,
) -> SourceMetadata:
    matching = _find_source_record(
        source_records,
        source_url,
        fetched_at,
        preferred_name=fallback_name,
    )
    if matching is not None:
        return _source_metadata_from_record(matching)
    return SourceMetadata(
        source_name=fallback_name,
        source_url=source_url,
        fetched_at=fetched_at,
        confidence=confidence or 0.0,
        manual_review_required=manual_review_required,
    )


def _find_source_record(
    source_records: list[SourceRecord],
    source_url: str | None,
    fetched_at: datetime | None,
    *,
    preferred_name: str,
) -> SourceRecord | None:
    preferred = preferred_name.casefold()
    if source_url is not None:
        for record in source_records:
            if record.source_url == source_url and record.fetched_at == fetched_at:
                return record
    return next(
        (
            record
            for record in source_records
            if record.source_name.casefold() == preferred
        ),
        None,
    )


def _source_metadata_from_record(record: SourceRecord) -> SourceMetadata:
    response_status = (
        int(record.response_status)
        if record.response_status and record.response_status.isdecimal()
        else None
    )
    return SourceMetadata(
        source_id=record.source_id,
        source_version=record.source_version,
        artifact_sha256=record.artifact_sha256 or record.checksum,
        data_release_id=record.data_release_id,
        act_version=record.act_version,
        source_name=record.source_name,
        source_url=record.source_url,
        fetched_at=record.fetched_at,
        response_status=response_status,
        confidence=record.confidence or 0.0,
        manual_review_required=(
            record.manual_review_required
            or record.response_status in {"unavailable", "error", "not_attempted"}
        ),
    )


def _parameter_float(parameter: MpzpParameter | None) -> float | None:
    if parameter is None or parameter.normalized_value is None:
        return None
    try:
        return float(parameter.normalized_value)
    except ValueError:
        return None


def _parameter_int(parameter: MpzpParameter | None) -> int | None:
    value = _parameter_float(parameter)
    return int(value) if value is not None else None
