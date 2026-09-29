"""Root kompozycji importerów — guard katalogu i wiązanie adapterów."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.data_sources import (
    AccessType,
    DataSourceEntry,
    MpzpDataClassification,
    MpzpSourceNotUsableError,
    SourceNotRunnableError,
    ensure_mpzp_vector_zones_source,
    ensure_source_runnable,
    get_catalog,
)
from app.core.settings import settings
from app.modules.imports.application.common import ImportRelease
from app.modules.imports.application.mpzp_import import (
    MpzpSourceReader,
    run_mpzp_import,
)
from app.modules.imports.application.parcels_import import (
    ParcelSourceReader,
    run_parcel_import,
)
from app.modules.imports.application.pog_import import (
    PogSourceReader,
    run_pog_import,
)
from app.modules.imports.domain.mpzp import PlanningActRecord
from app.modules.imports.domain.pog import normalize_legal_status
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
from app.modules.imports.application.raster_import import (
    RasterSource,
    run_raster_import,
)
from app.modules.imports.infrastructure.pog.reader import (
    PogActMetadata,
    PogBoundaryResource,
    PogLayerResource,
    PyogrioPogReader,
    WfsPogReader,
)
from app.modules.imports.infrastructure.ogc_client import (
    OgcClient,
    OgcContractError,
    OgcError,
    OgcExceptionReportError,
    OgcLimitError,
    OgcResult,
    OgcTransportError,
)
from app.modules.imports.infrastructure.raster.gdal import (
    DecodedFloatBand,
    GdalRasterProcessor,
    RasterProcessingError,
)
from app.modules.imports.infrastructure.raster.repository import (
    SqlAlchemyRasterRepository,
)
from app.modules.imports.infrastructure.repository import SqlAlchemyImportRepository
from app.modules.imports.infrastructure.wfs import WfsResource

# Kompozycja jest fasadą dla innych modułów (analysis/terrain, testy): te nazwy
# są celowym re-eksportem, nie nieużywanymi importami.
__all__ = [
    "DecodedFloatBand",
    "OgcClient",
    "OgcContractError",
    "OgcError",
    "OgcExceptionReportError",
    "OgcLimitError",
    "OgcResult",
    "OgcTransportError",
    "RasterProcessingError",
]


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


def _repository(
    session: Session, source: DataSourceEntry
) -> SqlAlchemyImportRepository:
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
    resource = next((item for item in source.resources if item.role == "parcels"), None)
    reader: ParcelSourceReader
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
        raise ValueError("Źródło nie ma czytelnego zasobu WFS/pliku; podaj --input.")
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
    reader: MpzpSourceReader
    if local_resources:
        ensure_mpzp_vector_zones_source(source)
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
        if not any(role == "zones" for role, _path in local_resources):
            raise MpzpSourceNotUsableError(
                "Lokalny import MPZP vector_zones wymaga zasobu roli 'zones'; "
                "sama granica aktu nie jest wydzieleniem planu."
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
            ensure_mpzp_vector_zones_source(source)
            reader = WfsMpzpReader(wfs_resources, teryt=teryt)
        else:
            if source.mpzp_classification is not MpzpDataClassification.RASTER:
                value = (
                    source.mpzp_classification.value
                    if source.mpzp_classification is not None
                    else "unset"
                )
                raise MpzpSourceNotUsableError(
                    f"Źródło {source.source_id!r} ma klasyfikację MPZP "
                    f"{value!r}; nie można utworzyć z niego importu rastrowego."
                )
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


def run_pog_command(
    session: Session,
    *,
    source_id: str,
    dry_run: bool,
    local_resources: tuple[tuple[str, str], ...] = (),
    act_identifier: str | None = None,
    resolution_number: str | None = None,
    resolution_date: date | None = None,
    teryt: str | None = None,
    legal_status: str | None = None,
    name_label: str | None = None,
    boundary_path: str | None = None,
):
    source = get_catalog().get(source_id)
    effective_teryt = teryt or next(
        (item for item in source.teryt_scope if item != "*"), ""
    )
    if not act_identifier:
        raise ValueError("Import POG wymaga jawnego --act-id.")
    if not effective_teryt:
        raise ValueError("Import POG wymaga TERYT (--teryt lub jednoznaczny katalog).")
    if not effective_teryt.isdigit() or len(effective_teryt) not in {6, 7}:
        raise ValueError("TERYT importu POG musi zawierać 6 albo 7 cyfr.")
    by_role = {resource.role: resource for resource in source.resources}
    metadata = PogActMetadata(
        act_identifier=act_identifier,
        teryt=effective_teryt,
        legal_status=normalize_legal_status(legal_status),
        raw_legal_status=legal_status,
        resolution_number=resolution_number,
        resolution_date=resolution_date,
        name=name_label or source.name,
    )
    reader: PogSourceReader
    if local_resources:
        # Kontrakt CRS/pól per warstwa pochodzi z katalogu; lokalna ścieżka jest
        # jawnym wejściem operatora, a nie alternatywnym źródłem URL.
        layers: list[PogLayerResource] = []
        for feature_type, path in local_resources:
            contract = by_role.get(feature_type)
            layers.append(
                PogLayerResource(
                    feature_type=feature_type,
                    path=Path(path),
                    source_crs=(contract.source_crs if contract else source.source_crs),
                    field_mapping=contract.field_mapping if contract else {},
                )
            )
        boundary = None
        if boundary_path:
            boundary_contract = by_role.get("boundaries") or by_role.get("act")
            boundary = PogBoundaryResource(
                path=Path(boundary_path),
                source_crs=(
                    boundary_contract.source_crs
                    if boundary_contract
                    else source.source_crs
                ),
            )
        reader = PyogrioPogReader(tuple(layers), metadata=metadata, boundary=boundary)
    else:
        wfs_contract = next(
            (
                item
                for item in source.resources
                if item.access_type is AccessType.WFS and item.declared_type_names
            ),
            None,
        )
        if wfs_contract is None:
            raise ValueError("Import POG bez --resource wymaga zasobu WFS w katalogu.")
        if not wfs_contract.namespace_uri:
            raise ValueError("Zasób WFS POG nie deklaruje namespace_uri APP.")
        namespace_uri = wfs_contract.namespace_uri
        filter_xml = (
            '<fes:Filter xmlns:fes="http://www.opengis.net/fes/2.0" '
            f'xmlns:app-pog="{namespace_uri}">'
            '<fes:PropertyIsLike wildCard="%" singleChar="_" escapeChar="\\">'
            "<fes:ValueReference>"
            "app-pog:idIIP/app-pog:Identyfikator/app-pog:przestrzenNazw"
            "</fes:ValueReference>"
            f"<fes:Literal>%{effective_teryt}%</fes:Literal>"
            "</fes:PropertyIsLike>"
            "</fes:Filter>"
        )
        request_params = {
            "namespaces": f"xmlns(app-pog,{namespace_uri})",
            "FILTER": filter_xml,
        }
        feature_roles = {
            "AktPlanowaniaPrzestrzennego": "planning_act",
            "DokumentFormalny": "formal_document",
            "StrefaPlanistyczna": "planning_zone",
            "ObszarUzupelnieniaZabudowy": "ouz",
            "ObszarZabudowySrodmiejskiej": "downtown_area",
            "ObszarStandardowDostepnosciInfrastrukturySpolecznej": (
                "social_infrastructure_standard"
            ),
        }
        remote_resources = tuple(
            (
                feature_roles[qualified_name.rsplit(":", 1)[-1]],
                WfsResource(
                    role=feature_roles[qualified_name.rsplit(":", 1)[-1]],
                    url=wfs_contract.url,
                    type_name=qualified_name,
                    source_crs=wfs_contract.source_crs,
                    field_mapping=wfs_contract.field_mapping,
                    source_id=source.source_id,
                    extra_params=request_params,
                ),
            )
            for qualified_name in wfs_contract.declared_type_names
            if qualified_name.rsplit(":", 1)[-1] in feature_roles
        )
        if not remote_resources:
            raise ValueError("Zasób WFS POG nie deklaruje wymaganych typów APP.")
        csw_resource = next(
            (item for item in source.resources if item.role == "ru_csw"),
            None,
        )
        reader = WfsPogReader(
            remote_resources,
            metadata=metadata,
            csw_url=csw_resource.url if csw_resource else None,
        )
    release = _release(source, dry_run=dry_run)
    try:
        outcome = run_pog_import(
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


def _raster_media_type(path: Path) -> str:
    return {
        ".pdf": "application/pdf",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
    }.get(path.suffix.casefold(), "application/octet-stream")


def run_raster_command(
    session: Session,
    *,
    source_id: str,
    dry_run: bool,
    input_path: str,
    control_points_path: str,
    page: int = 0,
    act_version_id: int | None = None,
    transform_method: str = "gcp_affine",
    nodata: float | None = None,
):
    source = get_catalog().get(source_id)
    raster_path = Path(input_path)
    control_points = json.loads(Path(control_points_path).read_text(encoding="utf-8"))
    if not isinstance(control_points, list):
        raise ValueError("Plik punktów kontrolnych musi być listą obiektów JSON.")
    raster_source = RasterSource(
        content=raster_path.read_bytes(),
        filename=raster_path.name,
        media_type=_raster_media_type(raster_path),
        page_number=page,
        source_crs=source.source_crs,
    )
    repository = SqlAlchemyRasterRepository(
        session,
        source,
        LocalArtifactStore(settings.import_artifact_storage_dir),
    )
    release = _release(source, dry_run=dry_run)
    try:
        outcome = run_raster_import(
            raster_source,
            control_points,
            GdalRasterProcessor(),
            repository,
            release=release,
            planning_act_version_id=act_version_id,
            transform_method=transform_method or "gcp_affine",
            nodata=nodata,
        )
        if outcome.status == "succeeded":
            session.commit()
        else:
            session.rollback()
        return outcome
    except Exception:
        session.rollback()
        raise


# --- Publiczne fabryki adapterów współdzielonych z innymi modułami (BK-302) ---
#
# Moduł ``analysis`` potrzebuje bezpiecznego klienta OGC (BK-102) i dekodera
# GeoTIFF opartego o GDAL CLI. ADR-001 zabrania importu cudzej warstwy
# ``infrastructure``, dlatego root kompozycji modułu ``imports`` wystawia wąskie
# fabryki i typy błędów jako jego publiczne API.

def build_ogc_client(
    *,
    source_id: str,
    urls: list[str],
    total_timeout_seconds: float,
    max_response_bytes: int,
    retries: int = 1,
) -> OgcClient:
    """Klient OGC z allowlistą hostów wyprowadzoną z przekazanych adresów."""
    return OgcClient.for_urls(
        source_id=source_id,
        urls=urls,
        config_overrides={
            "total_timeout_seconds": total_timeout_seconds,
            "read_timeout_seconds": min(30.0, total_timeout_seconds),
            "max_response_bytes": max_response_bytes,
            "max_total_bytes": max_response_bytes * 2,
            "retries": retries,
        },
    )


def build_raster_decoder(command_timeout_s: float) -> GdalRasterProcessor:
    """Dekoder GeoTIFF oparty o ``gdal-bin`` z twardym limitem czasu procesu."""
    return GdalRasterProcessor(command_timeout_s=command_timeout_s)

