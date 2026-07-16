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

from dataclasses import dataclass
from datetime import datetime

from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import MultiPolygon
from shapely.geometry.base import BaseGeometry
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.analysis import Analysis
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
    MpzpZoneResult,
    ParcelGeometryResponse,
    PogResult,
    RiskResult,
    WarningMessage,
)
from app.schemas.source import SourceMetadata
from app.services.context import ContextResult
from app.services.geojson import parcel_geometry_to_geojson
from app.services.geometry import calculate_geometry_metrics


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
    records.extend(_source_record_data(item.source, result) for item in result.risks)

    if context_result is not None:
        for section in (
            context_result.kiut,
            context_result.isok,
            context_result.gdos,
        ):
            source = section.source_metadata
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

    Jakikolwiek błąd sekcji wycofuje całą transakcję i jest propagowany do
    wywołującego, który odpowiada za mapowanie go na odpowiedź HTTP.
    """
    try:
        parcel = get_or_create_parcel(db, parcel_identifier, parcel_geometry)
        analysis = Analysis(
            parcel_id=parcel.id,
            analyzed_at=result.analyzed_at,
            status=database_status or result.status,
            buildable_area_sqm=result.buildable_area_sqm,
            warnings=(
                [warning.model_dump(mode="json") for warning in result.warnings]
                or None
            ),
            pending_uchwala_url=pending_uchwala_url,
            pending_plan_id=pending_plan_id,
            pending_zone_symbol_candidates=pending_zone_symbol_candidates,
        )
        db.add(analysis)
        db.flush()

        for zone in result.mpzp_zones:
            zone_record = MpzpZone(
                analysis_id=analysis.id,
                zone_symbol=zone.zone_symbol,
                primary_use=zone.primary_use,
                intersection_area_sqm=zone.intersection_area_sqm,
                intersection_pct=zone.intersection_pct,
                is_dominant=zone.is_dominant,
                source_url=zone.source.source_url,
                fetched_at=zone.source.fetched_at,
                confidence=zone.source.confidence,
            )
            db.add(zone_record)
            db.flush()

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
                        manual_review_required=(
                            zone.source.manual_review_required
                        ),
                    )
                )

        if result.pog is not None:
            source = result.pog.source
            db.add(
                PogData(
                    analysis_id=analysis.id,
                    status=result.pog.status,
                    planning_zone=result.pog.planning_zone,
                    ouz_intersection_area_sqm=(
                        result.pog.ouz_intersection_area_sqm
                    ),
                    touches_ouz_boundary=result.pog.touches_ouz_boundary,
                    source_url=source.source_url if source else None,
                    fetched_at=source.fetched_at if source else None,
                    confidence=source.confidence if source else None,
                )
            )

        for item in result.infrastructure:
            db.add(
                Infrastructure(
                    analysis_id=analysis.id,
                    network_type=item.network_type,
                    buffer_m=item.buffer_m,
                    source_url=item.source.source_url,
                    fetched_at=item.source.fetched_at,
                    confidence=item.source.confidence,
                    manual_review_required=(
                        item.source.manual_review_required
                    ),
                )
            )

        for item in result.risks:
            db.add(
                Risk(
                    analysis_id=analysis.id,
                    risk_type=item.risk_type,
                    description=item.description,
                    source_url=item.source.source_url,
                    fetched_at=item.source.fetched_at,
                    confidence=item.source.confidence,
                    manual_review_required=(
                        item.source.manual_review_required
                    ),
                )
            )

        for source in collect_source_records(result, context_result):
            db.add(
                SourceRecord(
                    analysis_id=analysis.id,
                    source_name=source.source_name,
                    source_url=source.source_url,
                    fetched_at=source.fetched_at,
                    response_status=source.response_status,
                    confidence=source.confidence,
                    manual_review_required=source.manual_review_required,
                    warnings=source.warnings or None,
                    checksum=source.checksum,
                )
            )

        db.commit()
        db.refresh(analysis)
        return analysis
    except Exception:
        db.rollback()
        raise


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
        )
    ).scalar_one()

    parcel_geometry = to_shape(loaded.parcel.geometry)
    metrics = calculate_geometry_metrics(parcel_geometry)
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
    )

    zones = [
        _mpzp_zone_response(zone, loaded.source_records)
        for zone in loaded.mpzp_zones
    ]
    pog = (
        _pog_response(
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
        RiskResult(
            risk_type=item.risk_type,
            description=item.description or "Brak opisu ryzyka.",
            source=_source_for_child(
                loaded.source_records,
                item.source_url,
                item.fetched_at,
                fallback_name=(
                    "ISOK" if item.risk_type == "flood_zone" else "GDOŚ"
                ),
                confidence=item.confidence,
                manual_review_required=item.manual_review_required,
            ),
        )
        for item in loaded.risk_records
    ]
    warnings = [
        WarningMessage.model_validate(warning)
        for warning in (loaded.warnings or [])
    ]

    return AnalyzeResponse(
        analysis_id=loaded.id,
        status=loaded.status,
        analyzed_at=loaded.analyzed_at,
        parcel=parcel_response,
        mpzp_zones=zones,
        pog=pog,
        infrastructure=infrastructure,
        risks=risks,
        buildable_area_sqm=loaded.buildable_area_sqm,
        manual_zone_required=loaded.status == "waiting_for_zone_symbol",
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
        checksum=None,
    )


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
        if record.response_status in {"unavailable", "error", "not_attempted"}:
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
    parameters = {
        parameter.parameter_name: parameter
        for parameter in zone.parameters
    }
    manual_review_required = any(
        parameter.manual_review_required for parameter in zone.parameters
    )
    return MpzpZoneResult(
        zone_symbol=zone.zone_symbol,
        primary_use=zone.primary_use,
        supplementary_use=None,
        max_building_height_m=_parameter_float(
            parameters.get("max_building_height_m")
        ),
        max_floors=_parameter_int(parameters.get("max_floors")),
        min_biologically_active_pct=_parameter_float(
            parameters.get("min_biologically_active_pct")
        ),
        max_floor_area_ratio=_parameter_float(
            parameters.get("max_floor_area_ratio")
        ),
        min_floor_area_ratio=_parameter_float(
            parameters.get("min_floor_area_ratio")
        ),
        max_building_coverage_pct=_parameter_float(
            parameters.get("max_building_coverage_pct")
        ),
        intersection_area_sqm=zone.intersection_area_sqm or 0.0,
        intersection_pct=zone.intersection_pct or 0.0,
        is_dominant=zone.is_dominant,
        source=_source_for_child(
            source_records,
            zone.source_url,
            zone.fetched_at,
            fallback_name="MPZP",
            confidence=zone.confidence,
            manual_review_required=manual_review_required,
        ),
    )


def _pog_response(
    pog: PogData,
    source_records: list[SourceRecord],
    parcel_area_sqm: float,
) -> PogResult:
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
    return PogResult(
        status=pog.status,
        planning_zone=pog.planning_zone,
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
            if (
                record.source_url == source_url
                and record.fetched_at == fetched_at
            ):
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
