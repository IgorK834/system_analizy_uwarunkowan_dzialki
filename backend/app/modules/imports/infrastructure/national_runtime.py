"""Adaptery PostgreSQL/HTTP importu krajowego POG (porty z ``application/national_import.py``) — ADR-026."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.data_sources import DataSourceEntry
from app.models.versioned import ImportRun
from app.modules.imports.application.common import ImportRelease
from app.modules.imports.application.pog_import import run_pog_import
from app.modules.imports.domain.ru_catalog import RegistryToken, RuListing, RuPublishedAct
from app.modules.imports.infrastructure.artifacts import LocalArtifactStore
from app.modules.imports.infrastructure.pog.ru_gml_reader import RuGmlPogReader, RuGmlSource
from app.modules.imports.infrastructure.repository import SqlAlchemyImportRepository
from app.modules.imports.infrastructure.ru_catalog_store import SqlAlchemyRuCatalogStore
from app.modules.imports.infrastructure.ru_published import RuDownload, RuPublishedClient

logger = logging.getLogger(__name__)

NATIONAL_IMPORTER_VERSION = "pog-national-v1"
NATIONAL_RESUMABLE = ("running", "interrupted", "failed")


def current_state_id(session: Session) -> int | None:
    """Najnowszy zatwierdzony numer stanu krajowego (``None`` — rejestr jest pusty)."""
    value = session.execute(text("SELECT max(id) FROM pog_registry_states")).scalar_one()
    return int(value) if value is not None else None


class HttpActsGateway:
    """Wykaz i pobieranie przez ``RuPublishedClient``."""

    def __init__(self, client: RuPublishedClient) -> None:
        self._client = client

    def list_current(self) -> RuListing:
        return self._client.list_acts("CURRENT")

    def download(self, act: RuPublishedAct) -> RuDownload:
        return self._client.download_gml(act.published_act_id)


class SqlRegistryGateway:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        source: DataSourceEntry,
        artifact_store: LocalArtifactStore,
        catalog: SqlAlchemyRuCatalogStore,
    ) -> None:
        self._session_factory = session_factory
        self._source = source
        self._artifact_store = artifact_store
        self._catalog = catalog

    def sync_catalog(self, listing: RuListing, run_id: int | None) -> dict[str, Any]:
        result = self._catalog.sync_catalog(listing, run_id=run_id)
        return {
            "stored_rows": result.rows,
            "new": result.new,
            "status_changed": result.changed_status,
            "no_longer_current": result.no_longer_current,
            "marked_non_current": result.marked_non_current,
        }

    def registry_tokens(self) -> list[RegistryToken]:
        return self._catalog.registry_tokens()

    def withdraw(self, entries: Sequence[RegistryToken], reason: str) -> dict[str, Any]:
        with self._session_factory() as session:
            repository = SqlAlchemyImportRepository(session, self._source, self._artifact_store)
            outcome = repository.withdraw_pog_acts([entry.act_id for entry in entries], reason=reason)
            session.commit()
        return {
            "withdrawn": outcome["withdrawn"],
            "registry_state_id": outcome["state_id"],
            "teryts": outcome["teryts"],
        }

    def current_state_id(self) -> int | None:
        with self._session_factory() as session:
            return current_state_id(session)


class SqlActPublisher:
    """Publikacja jednego aktu z pobranego GML: czytnik → ``run_pog_import`` → zatwierdzenie we własnej sesji."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        source: DataSourceEntry,
        artifact_store: LocalArtifactStore,
        *,
        wfs_url: str | None,
        csw_url: str | None,
        csw_client_factory: Callable[[], Any] | None,
        csw_gate: Any,
        xml_max_nodes: int,
        publication_allowed: bool,
        allow_regression: bool = False,
    ) -> None:
        self._session_factory = session_factory
        self._source = source
        self._artifact_store = artifact_store
        self._wfs_url = wfs_url
        self._csw_url = csw_url
        self._csw_client_factory = csw_client_factory
        self._csw_gate = csw_gate
        self._xml_max_nodes = xml_max_nodes
        self._publication_allowed = publication_allowed
        self._allow_regression = allow_regression

    def __call__(self, act: RuPublishedAct, download: Any, *, dry_run: bool) -> dict[str, Any]:
        csw_client = self._csw_client_factory() if (self._csw_url and self._csw_client_factory) else None
        reader = RuGmlPogReader(
            RuGmlSource(
                content=download.content,
                filename=download.filename or f"{act.published_act_id}.gml",
                published_act_id=act.published_act_id,
                terc=act.terc,
                source_reference=self._wfs_url,
                gml_url=getattr(download, "url", None),
            ),
            csw_url=self._csw_url if csw_client is not None else None,
            csw_client=csw_client,
            csw_gate=self._csw_gate,
            max_nodes=self._xml_max_nodes,
        )
        now = datetime.now(timezone.utc)
        release = ImportRelease(
            source_id=self._source.source_id,
            version_label=f"{now.date().isoformat()}-{self._source.source_id}-{act.published_act_id}",
            published_at=now,
            publication_allowed=self._publication_allowed,
            dry_run=dry_run,
            teryt_scope=(act.teryt6,),
            allow_regression=self._allow_regression,
        )
        try:
            with self._session_factory() as session:
                repository = SqlAlchemyImportRepository(session, self._source, self._artifact_store)
                outcome = run_pog_import(reader, self._source.source_id, repository, release=release)
                if outcome.status == "succeeded":
                    session.commit()
                else:
                    session.rollback()
        finally:
            closer = getattr(csw_client, "close", None)
            if callable(closer):
                closer()
        stats: dict[str, Any] = dict(outcome.stats)
        stats["status"] = outcome.status
        stats["warnings"] = list(outcome.warnings)[:20]
        stats["data_release_id"] = outcome.data_release_id
        return stats


class NationalRunRecorder:
    """Przebieg krajowy w ``import_runs`` (``pog-national-v1``): klucz wznowienia, checkpoint, wynik."""

    def __init__(self, session_factory: Callable[[], Session], source: DataSourceEntry, artifact_store: LocalArtifactStore) -> None:
        self._session_factory = session_factory
        self._source = source
        self._artifact_store = artifact_store

    def begin(self, key: str, identity: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        with self._session_factory() as session:
            source_row_id = SqlAlchemyImportRepository(session, self._source, self._artifact_store).ensure_source_row().id
            previous = session.execute(
                select(ImportRun)
                .where(
                    ImportRun.data_source_id == source_row_id,
                    ImportRun.importer_version == NATIONAL_IMPORTER_VERSION,
                    ImportRun.status.in_(NATIONAL_RESUMABLE),
                    ImportRun.checkpoint["run_key"].as_string() == key,
                )
                .order_by(ImportRun.id.desc())
                .limit(1)
            ).scalar_one_or_none()
            now = datetime.now(timezone.utc)
            if previous is not None:
                payload = dict(previous.checkpoint or {})
                payload["resumes"] = int(payload.get("resumes", 0)) + 1
                previous.checkpoint = payload
                previous.status = "running"
                previous.finished_at = None
                session.commit()
                return int(previous.id), payload
            run = ImportRun(
                data_source_id=source_row_id,
                status="running",
                importer_version=NATIONAL_IMPORTER_VERSION,
                checkpoint={"run_key": key, "resumes": 0, "identity": identity},
                started_at=now,
            )
            session.add(run)
            session.flush()
            run_id = int(run.id)
            session.commit()
            return run_id, {}

    def save(self, run_id: int, payload: dict[str, Any], status: str | None = None) -> None:
        try:
            with self._session_factory() as session:
                run = session.get(ImportRun, run_id)
                if run is None:
                    return
                run.checkpoint = {**(run.checkpoint or {}), **payload}
                summary = payload.get("summary")
                if isinstance(summary, dict):
                    run.stats = {
                        key: summary.get(key)
                        for key in ("status", "counts", "failure_codes", "failures", "plan", "withdrawal", "timing", "catalog")
                    }
                elif isinstance(payload.get("progress"), dict):
                    run.stats = payload["progress"]
                if status is not None:
                    run.status = status
                    if status != "running":
                        run.finished_at = datetime.now(timezone.utc)
                session.commit()
        except Exception:  # noqa: BLE001 - zapis postępu nie może przerwać importu
            logger.warning("national_import_checkpoint_save_failed", extra={"run_id": run_id})
