"""Checkpoint pobierania w ``import_runs.checkpoint`` (AU-106, ADR-024).

Faza pobierania importu z WFS ma własny wiersz ``import_runs`` (``importer_version = pog-download-v1``), zapisywany
w **osobnej transakcji** niż publikacja wydania: przerwanie procesu w połowie pobierania nie może wycofać zapisu
postępu. Wiersz niesie klucz wznowienia (``resume_key``), katalog magazynu stron i ostatni stan postępu, a jego
``status`` mówi, co zrobić dalej:

* ``running`` — pobieranie trwa albo proces zginął bez sprzątania (też wznawialne);
* ``interrupted`` — przerwane sygnałem; ``failed`` — zakończone błędem (np. awaria usługi);
* ``fetched`` — wszystkie strony pobrane, publikacja jeszcze nie zakończona;
* ``succeeded`` — wydanie opublikowane; ``abandoned`` — porzucone przez ``--fresh``.

Kolejne uruchomienie z tym samym kluczem wznawia najnowszy wiersz wznawialny zamiast tworzyć nowy.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.data_sources import DataSourceEntry
from app.models.versioned import ImportRun
from app.modules.imports.infrastructure.artifacts import LocalArtifactStore
from app.modules.imports.infrastructure.repository import SqlAlchemyImportRepository

logger = logging.getLogger(__name__)

DOWNLOAD_IMPORTER_VERSION: Final[str] = "pog-download-v1"
RESUMABLE_STATUSES: Final[tuple[str, ...]] = ("running", "interrupted", "failed", "fetched")


@dataclass(frozen=True)
class CheckpointRun:
    """Wiersz fazy pobierania; ``previous`` to checkpoint wznawianego przebiegu (``None`` przy nowym)."""

    run_id: int
    resumed: bool
    previous: dict[str, Any] | None = None


class ImportRunCheckpoints:
    """Zapis checkpointu w osobnych, krótkich transakcjach (każda operacja ma własną sesję)."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        source: DataSourceEntry,
        *,
        artifact_dir: str,
    ) -> None:
        self._session_factory = session_factory
        self._source = source
        self._artifact_dir = artifact_dir

    def begin(self, resume_key: str, *, fresh: bool = False) -> CheckpointRun:
        """Wznawia najnowszy wiersz z tym kluczem albo zakłada nowy; ``fresh`` porzuca poprzednie."""
        with self._session_factory() as session:
            source_id = SqlAlchemyImportRepository(
                session, self._source, LocalArtifactStore(self._artifact_dir)
            ).ensure_source_row().id
            previous = session.execute(
                select(ImportRun)
                .where(
                    ImportRun.data_source_id == source_id,
                    ImportRun.importer_version == DOWNLOAD_IMPORTER_VERSION,
                    ImportRun.status.in_(RESUMABLE_STATUSES),
                    ImportRun.checkpoint["resume_key"].as_string() == resume_key,
                )
                .order_by(ImportRun.id.desc())
                .limit(1)
            ).scalar_one_or_none()
            now = datetime.now(timezone.utc)
            if previous is not None and fresh:
                previous.status = "abandoned"
                previous.finished_at = now
                previous = None
            if previous is not None:
                payload = dict(previous.checkpoint or {})
                payload["resumes"] = int(payload.get("resumes", 0)) + 1
                previous.status = "running"
                previous.checkpoint = payload
                previous.finished_at = None
                run_id = previous.id
                session.commit()
                return CheckpointRun(run_id, True, payload)
            run = ImportRun(
                data_source_id=source_id,
                status="running",
                importer_version=DOWNLOAD_IMPORTER_VERSION,
                checkpoint={"resume_key": resume_key, "resumes": 0},
                started_at=now,
            )
            session.add(run)
            session.flush()
            run_id = run.id
            session.commit()
            return CheckpointRun(run_id, False, None)

    def save(self, run_id: int, payload: dict[str, Any], *, status: str | None = None) -> None:
        """Zapisuje stan postępu (scalony z kluczem wznowienia); ``status`` zamyka wiersz, gdy jest końcowy."""
        try:
            with self._session_factory() as session:
                run = session.get(ImportRun, run_id)
                if run is None:
                    return
                merged = {**(run.checkpoint or {}), **payload}
                run.checkpoint = merged
                run.stats = payload.get("progress") or run.stats
                if status is not None:
                    run.status = status
                    if status != "running":
                        run.finished_at = datetime.now(timezone.utc)
                session.commit()
        except Exception:  # noqa: BLE001 - zapis postępu nie może przerwać importu
            logger.warning("import_checkpoint_save_failed", extra={"run_id": run_id})
