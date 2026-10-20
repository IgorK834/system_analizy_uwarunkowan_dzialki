"""Root kompozycji importerów — guard katalogu i wiązanie adapterów."""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

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
from app.db.session import SessionLocal
from app.modules.imports.application.bdot10k_import import run_bdot10k_import
from app.modules.imports.infrastructure.bdot_jobs import enqueue_county_import
from app.modules.imports.application.common import ImportOutcome, ImportRelease
from app.modules.imports.application.mpzp_import import (
    MpzpSourceReader,
    run_mpzp_import,
)
from app.modules.imports.application.national_import import (
    NationalImportOptions,
    NationalImportResult,
    NationalProgress,
    run_national_import,
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
from app.modules.imports.domain.bdot10k import Bdot10kValidationError, validate_powiat_teryt
from app.modules.imports.infrastructure.artifacts import LocalArtifactStore
from app.modules.imports.infrastructure.bdot10k.download import (
    Bdot10kDownloadError,
    allowed_hosts_from,
    download_package,
    package_url,
)
from app.modules.imports.infrastructure.bdot10k.geometry import ShapelyGeometryTools
from app.modules.imports.infrastructure.bdot10k.reader import Bdot10kReadError, ZipBdot10kReader
from app.modules.imports.infrastructure.bdot10k.repository import SqlAlchemyBdot10kRepository
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
from app.modules.imports.infrastructure.job_queue import (
    ClaimedJob,
    claim_next_job,
    complete_job,
    fail_job,
    worker_identity,
)
from app.modules.imports.infrastructure.pog_map_tables import rebuild_missing_map_tables
from app.modules.imports.infrastructure.repository import SqlAlchemyImportRepository
from app.modules.imports.infrastructure.sync_status import pog_sync_status
from app.modules.imports.infrastructure.national_runtime import (
    HttpActsGateway,
    NationalRunRecorder,
    SqlActPublisher,
    SqlRegistryGateway,
)
from app.modules.imports.infrastructure.ru_catalog_store import SqlAlchemyRuCatalogStore
from app.modules.imports.infrastructure.ru_published import RuPublishedClient, Throttle
from app.modules.imports.infrastructure.run_checkpoint import ImportRunCheckpoints
from app.modules.imports.infrastructure.wfs import WfsFetcher, WfsFetchInterrupted, WfsResource
from app.modules.imports.application.progress import ImportProgress

logger = logging.getLogger(__name__)


@dataclass
class PogImportOptions:
    """Opcje długiego importu POG z WFS (AU-106): równoległość, wznawianie, postęp i przerwanie.

    Wszystkie pola są opcjonalne — bez nich import działa jak dotąd (pobieranie sekwencyjne, bez magazynu stron).
    ``save_checkpoint`` jest ustawiane przez ``run_pog_command`` po założeniu wiersza checkpointu i służy
    wołającemu (CLI) do okresowego zapisu postępu.
    """

    concurrency: int = 1
    resume: bool = False
    fresh: bool = False
    progress: ImportProgress | None = None
    cancel: threading.Event | None = None
    checkpoints: ImportRunCheckpoints | None = None
    resume_key: str | None = None
    resumed: bool = False
    save_checkpoint: Callable[..., None] | None = None


def pog_resume_key(*parts: object) -> str:
    """Klucz wznowienia: ten sam import (źródło, TERYT, akt, usługa, typy, filtr) = ten sam klucz."""
    encoded = json.dumps(parts, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


# Kompozycja jest fasadą dla innych modułów (analysis/terrain, testy): te nazwy
# są celowym re-eksportem, nie nieużywanymi importami.
__all__ = [
    "enqueue_county_import",
    "ClaimedJob",
    "claim_next_job",
    "complete_job",
    "fail_job",
    "pog_sync_status",
    "rebuild_missing_map_tables",
    "worker_identity",
    "Bdot10kDownloadError",
    "Bdot10kReadError",
    "Bdot10kValidationError",
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
    options: PogImportOptions | None = None,
):
    options = options or PogImportOptions()
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
    resume_dir: Path | None = None
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
        resume_key = pog_resume_key(
            source.source_id,
            effective_teryt,
            act_identifier,
            wfs_contract.url,
            [resource.type_name for _role, resource in remote_resources],
            filter_xml,
        )
        resume_dir = _prepare_resume_directory(options, resume_key)
        reader = WfsPogReader(
            remote_resources,
            metadata=metadata,
            csw_url=csw_resource.url if csw_resource else None,
            fetcher=WfsFetcher(
                concurrency=options.concurrency,
                resume_directory=resume_dir,
                observer=options.progress,
                cancel=options.cancel,
            ),
            progress=options.progress,
        )
        _begin_checkpoint(
            options,
            resume_key,
            resume_dir,
            {"source_id": source.source_id, "teryt": effective_teryt, "act_identifier": act_identifier},
        )
    release = _release(source, dry_run=dry_run)
    try:
        outcome = run_pog_import(
            reader,
            source_id,
            _repository(session, source),
            release=release,
            **({"progress": options.progress} if options.progress is not None else {}),
        )
        if outcome.status == "succeeded":
            session.commit()
        else:
            session.rollback()
        _finish_checkpoint(options, resume_dir, outcome.status)
        return outcome
    except BaseException as exc:
        session.rollback()
        if options.save_checkpoint is not None:
            interrupted = isinstance(exc, (WfsFetchInterrupted, KeyboardInterrupt))
            options.save_checkpoint(status="interrupted" if interrupted else "failed")
        raise


def _prepare_resume_directory(options: PogImportOptions, resume_key: str) -> Path | None:
    """Katalog magazynu stron tego importu (``None`` bez wznawiania); ``fresh`` czyści poprzednie strony."""
    if not (options.resume or options.fresh):
        return None
    directory = Path(settings.import_artifact_storage_dir) / "resume" / resume_key[:32]
    if options.fresh:
        shutil.rmtree(directory, ignore_errors=True)
    return directory


def _begin_checkpoint(
    options: PogImportOptions, resume_key: str, resume_dir: Path | None, identity: dict[str, Any]
) -> None:
    """Zakłada albo wznawia wiersz checkpointu i podpina ``save_checkpoint`` do opcji."""
    options.resume_key = resume_key
    if options.checkpoints is None:
        return
    checkpoints = options.checkpoints
    run = checkpoints.begin(resume_key, fresh=options.fresh)
    options.resumed = run.resumed

    def save_checkpoint(status: str | None = None) -> None:
        payload: dict[str, Any] = {
            "resume_key": resume_key,
            "resume_dir": str(resume_dir) if resume_dir is not None else None,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **identity,
        }
        if options.progress is not None:
            payload["progress"] = options.progress.snapshot()
        checkpoints.save(run.run_id, payload, status=status)

    options.save_checkpoint = save_checkpoint
    save_checkpoint()


def _finish_checkpoint(options: PogImportOptions, resume_dir: Path | None, outcome_status: str) -> None:
    """Po udanym imporcie magazyn stron jest zbędny; po próbie bez publikacji zostaje do wznowienia."""
    if outcome_status == "succeeded":
        if resume_dir is not None:
            shutil.rmtree(resume_dir, ignore_errors=True)
        status = "succeeded"
    else:
        status = "fetched"  # pobrano komplet stron, ale nic nie opublikowano (np. --dry-run)
    if options.save_checkpoint is not None:
        options.save_checkpoint(status=status)


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


def run_bdot10k_command(
    session: Session,
    *,
    source_id: str,
    dry_run: bool,
    powiat: str,
    package: str | None = None,
) -> ImportOutcome:
    """Import paczki powiatowej BDOT10k: plik lokalny, adres z allowlisty albo adres z katalogu źródeł."""
    teryt = validate_powiat_teryt(powiat)
    source = get_catalog().get(source_id)
    templates = [
        item.url for item in source.resources if item.role == "county_package"
    ] or ([source.file_url] if source.file_url else [])
    if not templates:
        raise Bdot10kValidationError("Katalog źródeł nie zawiera adresu paczek powiatowych BDOT10k.")
    with tempfile.TemporaryDirectory(prefix="dzialki-bdot10k-download-") as download_dir:
        location = package or package_url(templates[0], teryt)
        if location.lower().startswith(("http://", "https://")):
            downloaded = download_package(
                location,
                Path(download_dir),
                allowed_hosts=allowed_hosts_from(templates),
                max_bytes=settings.bdot10k_max_package_bytes,
                timeout_seconds=settings.bdot10k_download_timeout_seconds,
            )
            package_path = downloaded.path
        else:
            package_path = Path(location)
        reader = ZipBdot10kReader(package_path, teryt_powiat=teryt, mapping=source.field_mapping)
        release = _release(source, dry_run=dry_run)
        repository = (
            None
            if dry_run
            else SqlAlchemyBdot10kRepository(
                session, source, LocalArtifactStore(settings.import_artifact_storage_dir)
            )
        )
        try:
            outcome = run_bdot10k_import(
                reader,
                release,
                ShapelyGeometryTools(),
                repository,
                source.field_mapping,
                teryt_powiat=teryt,
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
    before_request: Callable[[], None] | None = None,
) -> OgcClient:
    """Klient OGC z allowlistą hostów wyprowadzoną z przekazanych adresów.

    ``before_request`` jest wołane przed każdym żądaniem HTTP (budżet żądań na analizę, AU-105).
    """
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
        before_request=before_request,
    )


def build_raster_decoder(command_timeout_s: float) -> GdalRasterProcessor:
    """Dekoder GeoTIFF oparty o ``gdal-bin`` z twardym limitem czasu procesu."""
    return GdalRasterProcessor(command_timeout_s=command_timeout_s)



# --- Import krajowy POG z GML Rejestru Urbanistycznego (ADR-026) ---------------------------------------------------


def build_ru_published_client(source: DataSourceEntry) -> tuple[RuPublishedClient, str]:
    """Klient wykazu i plików GML RU z adresem z katalogu (zasób ``ru_published``); zwraca też adres bazowy."""
    resource = next((item for item in source.resources if item.role == "ru_published"), None)
    if resource is None:
        raise ValueError("Katalog źródła pog_app nie deklaruje zasobu ru_published (import krajowy z GML).")
    http = OgcClient.for_urls(
        source_id=source.source_id,
        urls=(resource.url,),
        config_overrides={
            "connect_timeout_seconds": 10.0,
            "read_timeout_seconds": 120.0,
            "total_timeout_seconds": settings.ru_published_timeout_seconds,
            "max_response_bytes": settings.ru_published_max_gml_bytes,
            "max_total_bytes": settings.ru_published_max_gml_bytes * 2,
            "retries": 2,
            "backoff_seconds": 2.0,
        },
    )
    client = RuPublishedClient(
        http,
        base_url=resource.url,
        min_interval_seconds=settings.ru_published_min_interval_seconds,
        max_gml_bytes=settings.ru_published_max_gml_bytes,
        timeout_seconds=settings.ru_published_timeout_seconds,
    )
    return client, resource.url


@dataclass
class NationalRuntime:
    gateway: HttpActsGateway
    registry: SqlRegistryGateway
    publisher: SqlActPublisher
    recorder: NationalRunRecorder
    client: RuPublishedClient


def build_national_runtime(
    *, source_id: str = "pog_app", with_csw: bool = True, allow_regression: bool = False
) -> NationalRuntime:
    source = get_catalog().get(source_id)
    by_role = {resource.role: resource for resource in source.resources}
    client, _base = build_ru_published_client(source)
    artifact_store = LocalArtifactStore(settings.import_artifact_storage_dir)
    csw_resource = by_role.get("ru_csw") if with_csw else None
    csw_gate = Throttle(settings.ru_published_min_interval_seconds)
    csw_factory: Callable[[], Any] | None = None
    if csw_resource is not None:
        csw_url = csw_resource.url

        def csw_factory() -> OgcClient:
            return OgcClient.for_urls(
                source_id=source.source_id,
                urls=(csw_url,),
                config_overrides={"read_timeout_seconds": 30.0, "total_timeout_seconds": 60.0},
            )

    catalog = SqlAlchemyRuCatalogStore(SessionLocal)
    return NationalRuntime(
        gateway=HttpActsGateway(client),
        registry=SqlRegistryGateway(SessionLocal, source, artifact_store, catalog),
        publisher=SqlActPublisher(
            SessionLocal,
            source,
            artifact_store,
            wfs_url=by_role["ru_wfs"].url if "ru_wfs" in by_role else None,
            csw_url=csw_resource.url if csw_resource is not None else None,
            csw_client_factory=csw_factory,
            csw_gate=csw_gate,
            xml_max_nodes=settings.pog_national_xml_max_nodes,
            publication_allowed=_publication_allowed(source.source_id),
            allow_regression=allow_regression,
        ),
        recorder=NationalRunRecorder(SessionLocal, source, artifact_store),
        client=client,
    )


def run_pog_national_command(
    options: NationalImportOptions,
    *,
    source_id: str = "pog_app",
    with_csw: bool = True,
    allow_regression: bool = False,
    progress: NationalProgress | None = None,
    cancel: threading.Event | None = None,
) -> NationalImportResult:
    runtime = build_national_runtime(source_id=source_id, with_csw=with_csw, allow_regression=allow_regression)
    try:
        result = run_national_import(
            runtime.gateway,
            runtime.registry,
            runtime.publisher,
            runtime.recorder,
            options,
            progress=progress,
            cancel=cancel,
        )
    finally:
        runtime.client.close()
    result.catalog.setdefault("ru_requests", runtime.client.stats)
    return result
