"""Kontrolowane pobieranie małych odpowiedzi WFS do lokalnego artefaktu."""

from __future__ import annotations

import io
import threading
import zipfile
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.modules.imports.infrastructure.ogc_client import (
    OgcCancelled,
    OgcClient,
    OgcError,
    OgcResult,
    WfsPageEvent,
)
from app.modules.imports.infrastructure.page_store import FilePageStore
from app.shared.provenance import Provenance


class WfsFetchError(RuntimeError):
    """Usługa WFS nie zwróciła poprawnego artefaktu."""

    def __init__(
        self,
        message: str,
        *,
        source: Provenance | None = None,
        partial: OgcResult | None = None,
    ) -> None:
        super().__init__(message)
        self.source = source
        self.partial = partial


class WfsFetchInterrupted(WfsFetchError):
    """Pobieranie przerwane na żądanie operatora; pobrane strony zostały w magazynie i pozwalają wznowić import."""


class FetchObserver(Protocol):
    """Odbiorca postępu pobierania (``application.progress.ImportProgress``)."""

    def type_started(self, name: str) -> None: ...

    def type_finished(self, name: str) -> None: ...

    def on_page(self, event: WfsPageEvent) -> None: ...


@dataclass(frozen=True)
class WfsResource:
    role: str
    url: str
    type_name: str
    source_crs: str
    field_mapping: dict[str, str]
    source_id: str = "wfs"
    extra_params: dict[str, str] | None = None


class WfsFetcher:
    """Pobiera zasoby WFS i pakuje je w deterministyczny ZIP.

    ``concurrency`` > 1 włącza równoległe pobieranie stron jednego typu (limit grzeczności wobec usługi),
    ``resume_directory`` — magazyn stron do wznawiania po przerwaniu (podkatalog na każdy zasób),
    ``observer`` — postęp, ``cancel`` — przerwanie na żądanie operatora (SIGINT/SIGTERM).
    """

    def __init__(
        self,
        timeout_seconds: float = 60.0,
        *,
        ogc_client: OgcClient | None = None,
        concurrency: int = 1,
        resume_directory: Path | None = None,
        observer: FetchObserver | None = None,
        cancel: threading.Event | None = None,
    ) -> None:
        self._timeout = timeout_seconds
        self._ogc_client = ogc_client
        self._concurrency = concurrency
        self._resume_directory = resume_directory
        self._observer = observer
        self._cancel = cancel

    def fetch(self, resources: tuple[WfsResource, ...]) -> bytes:
        """Pobiera zasoby w kolejności katalogowej i pakuje je deterministycznie."""
        if not resources:
            raise WfsFetchError("Nie wskazano żadnego zasobu WFS.")
        payloads: list[tuple[str, bytes]] = []
        managed = self._ogc_client is None
        client = self._ogc_client or OgcClient.for_urls(
            source_id=resources[0].source_id,
            urls=(resource.url for resource in resources),
            config_overrides={
                "connect_timeout_seconds": min(self._timeout, 5.0),
                "read_timeout_seconds": self._timeout,
                "total_timeout_seconds": max(self._timeout, 1.0) * 10,
                "retries": 4,
            },
        )
        context = client if managed else nullcontext(client)
        with context:
            for index, resource in enumerate(resources):
                options: dict[str, Any] = {}
                if self._concurrency > 1:
                    options["concurrency"] = self._concurrency
                if self._resume_directory is not None:
                    options["page_store"] = FilePageStore(
                        self._resume_directory / f"{index:02d}-{resource.role}"
                    )
                if self._observer is not None:
                    options["on_page"] = self._observer.on_page
                    self._observer.type_started(resource.type_name)
                if self._cancel is not None:
                    options["cancel"] = self._cancel
                try:
                    result = client.fetch_wfs(
                        resource.url,
                        type_name=resource.type_name,
                        srs_name=resource.source_crs,
                        extra_params=resource.extra_params,
                        **options,
                    )
                except OgcCancelled as exc:
                    raise WfsFetchInterrupted(
                        f"Pobieranie zasobu {resource.type_name!r} przerwane na żądanie operatora.",
                        source=exc.source,
                    ) from exc
                except OgcError as exc:
                    raise WfsFetchError(
                        f"WFS nie zwrócił zasobu {resource.type_name!r}: {exc}",
                        source=exc.source,
                        partial=exc.partial,
                    ) from exc
                if self._observer is not None:
                    self._observer.type_finished(resource.type_name)
                if not result.complete:
                    raise WfsFetchError(
                        f"WFS zwrócił niekompletny zasób {resource.type_name!r}.",
                        source=result.source,
                        partial=result,
                    )
                payloads.append((f"{index:02d}-{resource.role}.gml", result.artifact))

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in payloads:
                info = zipfile.ZipInfo(name)
                # Stały timestamp zapewnia stabilny SHA identycznej odpowiedzi.
                info.date_time = (1980, 1, 1, 0, 0, 0)
                archive.writestr(info, content)
        return buffer.getvalue()
