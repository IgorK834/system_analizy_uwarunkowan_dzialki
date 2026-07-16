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

from geoalchemy2.shape import from_shape
from shapely.geometry import MultiPolygon
from shapely.geometry.base import BaseGeometry
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.analysis import Analysis
from app.models.infrastructure import Infrastructure
from app.models.mpzp_parameter import MpzpParameter
from app.models.mpzp_zone import MpzpZone
from app.models.parcel import Parcel
from app.models.pog_data import PogData
from app.models.risk import Risk
from app.models.source_record import SourceRecord
from app.schemas.analyze import AnalyzeResponse
from app.schemas.source import SourceMetadata
from app.services.context import ContextResult


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
) -> Analysis:
    """Atomowo zapisuje wszystkie wypełnione sekcje ``AnalyzeResponse``.

    Geometria musi być oryginalną geometrią działki w EPSG:2180. GeoJSON z
    odpowiedzi API jest przeznaczony dla frontendu i nie jest transformowany
    z powrotem. Każde wywołanie tworzy nowy ``Analysis`` jako element historii;
    ponownie używany jest wyłącznie rekord ``Parcel``.

    Jakikolwiek błąd sekcji wycofuje całą transakcję i jest propagowany do
    wywołującego, który odpowiada za mapowanie go na odpowiedź HTTP.
    """
    try:
        parcel = get_or_create_parcel(db, parcel_identifier, parcel_geometry)
        analysis = Analysis(
            parcel_id=parcel.id,
            analyzed_at=result.analyzed_at,
            status=result.status,
            buildable_area_sqm=result.buildable_area_sqm,
            warnings=(
                [warning.model_dump(mode="json") for warning in result.warnings]
                or None
            ),
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
