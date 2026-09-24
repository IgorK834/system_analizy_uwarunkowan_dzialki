"""Kontrolowane pobieranie małych odpowiedzi WFS do lokalnego artefaktu."""

from __future__ import annotations

import io
import zipfile
from contextlib import nullcontext
from dataclasses import dataclass

from app.modules.imports.infrastructure.ogc_client import (
    OgcClient,
    OgcError,
    OgcResult,
)
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
    def __init__(
        self,
        timeout_seconds: float = 60.0,
        *,
        ogc_client: OgcClient | None = None,
    ) -> None:
        self._timeout = timeout_seconds
        self._ogc_client = ogc_client

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
                "total_timeout_seconds": max(self._timeout, 1.0) * 2,
            },
        )
        context = client if managed else nullcontext(client)
        with context:
            for index, resource in enumerate(resources):
                try:
                    result = client.fetch_wfs(
                        resource.url,
                        type_name=resource.type_name,
                        srs_name=resource.source_crs,
                        extra_params=resource.extra_params,
                    )
                except OgcError as exc:
                    raise WfsFetchError(
                        f"WFS nie zwrócił zasobu {resource.type_name!r}: {exc}",
                        source=exc.source,
                        partial=exc.partial,
                    ) from exc
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
