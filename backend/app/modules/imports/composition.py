"""Root kompozycji importerów — guard katalogu i wiązanie adapterów."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.data_sources import (
    AccessType,
    DataSourceEntry,
    SourceNotRunnableError,
    ensure_source_runnable,
    get_catalog,
)
from app.core.settings import settings
from app.modules.imports.application.common import ImportRelease
from app.modules.imports.application.mpzp_import import run_mpzp_import
from app.modules.imports.application.parcels_import import run_parcel_import
from app.modules.imports.domain.mpzp import PlanningActRecord
from app.modules.imports.infrastructure.artifacts import LocalArtifactStore
from app.modules.imports.infrastructure.mpzp.reader import (
    PyogrioMpzpReader,
    RasterOnlyMpzpReader,
    VectorLayerResource,
    WfsMpzpReader,
)
from app.modules.imports.infrastructure.parcels.reader import (
    PyogrioParcelReader,
    WfsParcelReader,
)
from app.modules.imports.infrastructure.repository import SqlAlchemyImportRepository
from app.modules.imports.infrastructure.wfs import WfsResource


def _publication_allowed(source_id: str) -> bool:
    try:
        ensure_source_runnable(source_id)
    except SourceNotRunnableError:
        return False
    return True


def _release(source: DataSourceEntry, *, dry_run: bool) -> ImportRelease:
    now = datetime.now(timezone.utc)
    return ImportRelease(
        source_id=source.source_id,
        version_label=f"{now.date().isoformat()}-{source.source_id}",
        published_at=now,
        publication_allowed=_publication_allowed(source.source_id),
        dry_run=dry_run,
        teryt_scope=tuple(source.teryt_scope),
    )


def _repository(session: Session, source: DataSourceEntry) -> SqlAlchemyImportRepository:
    return SqlAlchemyImportRepository(
        session,
        source,
        LocalArtifactStore(settings.import_artifact_storage_dir),
        area_tolerance_ratio=settings.import_area_tolerance_ratio,
        overlap_tolerance_sqm=settings.import_overlap_tolerance_sqm,
        topology_distance_tolerance_m=settings.import_topology_tolerance_m,
        topology_area_tolerance_sqm=settings.import_topology_area_tolerance_sqm,
    )


def run_parcels_command(
    session: Session,
    *,
    source_id: str,
    dry_run: bool,
    input_path: str | None = None,
):
    source = get_catalog().get(source_id)
    mapping = source.field_mapping
    teryt = next((item for item in source.teryt_scope if item != "*"), None)
    resource = next(
        (item for item in source.resources if item.role == "parcels"), None
    )
    if input_path:
        reader = PyogrioParcelReader(
            input_path,
            field_mapping=mapping or (resource.field_mapping if resource else {}),
            teryt=teryt,
            declared_crs=source.source_crs,
        )
    elif resource and resource.access_type is AccessType.WFS and resource.type_name:
        reader = WfsParcelReader(
            WfsResource(
                role=resource.role,
                url=resource.url,
                type_name=resource.type_name,
                source_crs=resource.source_crs,
                field_mapping=resource.field_mapping,
            ),
            teryt=teryt,
        )
    else:
        raise ValueError(
            "Źródło nie ma czytelnego zasobu WFS/pliku; podaj --input."
        )
    try:
        outcome = run_parcel_import(
            reader,
            _release(source, dry_run=dry_run),
            _repository(session, source),
        )
        if outcome.status == "succeeded":
            session.commit()
        else:
            session.rollback()
        return outcome
    except Exception:
        session.rollback()
        raise


def run_mpzp_command(
    session: Session,
    *,
    source_id: str,
    dry_run: bool,
    local_resources: tuple[tuple[str, str], ...] = (),
    act_identifier: str | None = None,
    resolution_number: str | None = None,
    resolution_date: date | None = None,
    raster_teryt: str | None = None,
):
    source = get_catalog().get(source_id)
    teryt = next((item for item in source.teryt_scope if item != "*"), "")
    if local_resources:
        configured = []
        by_role = {resource.role: resource for resource in source.resources}
        for role, path in local_resources:
            contract = by_role.get(role)
            if contract is None:
                raise ValueError(f"Brak zasobu roli {role!r} w katalogu.")
            configured.append(
                VectorLayerResource(
                    role=role,
                    path=Path(path),
                    source_crs=contract.source_crs,
                    field_mapping=contract.field_mapping,
                )
            )
        reader = PyogrioMpzpReader(tuple(configured), teryt=teryt)
    else:
        wfs_resources = tuple(
            WfsResource(
                role=item.role,
                url=item.url,
                type_name=item.type_name or "",
                source_crs=item.source_crs,
                field_mapping=item.field_mapping,
            )
            for item in source.resources
            if item.access_type is AccessType.WFS and item.type_name
        )
        if wfs_resources:
            reader = WfsMpzpReader(wfs_resources, teryt=teryt)
        else:
            effective_teryt = raster_teryt or teryt
            if (
                act_identifier is None
                or resolution_number is None
                or resolution_date is None
                or not effective_teryt
            ):
                raise ValueError(
                    "Źródło rastrowe wymaga jawnych metadanych: --act-id, "
                    "--resolution-number, --resolution-date i --teryt "
                    "(chyba że katalog wskazuje pojedynczy TERYT)."
                )
            reader = RasterOnlyMpzpReader(
                PlanningActRecord(
                    act_identifier=act_identifier,
                    resolution_number=resolution_number,
                    resolution_date=resolution_date,
                    teryt=effective_teryt,
                    name=source.name,
                    boundary=None,
                    legal_status="raster_only",
                )
            )
    release = _release(source, dry_run=dry_run)
    try:
        outcome = run_mpzp_import(
            reader,
            source_id,
            _repository(session, source),
            release=release,
        )
        if outcome.status == "succeeded":
            session.commit()
        else:
            session.rollback()
        return outcome
    except Exception:
        session.rollback()
        raise
