"""Kontrolowane pobieranie małych odpowiedzi WFS do lokalnego artefaktu."""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass

import httpx


class WfsFetchError(RuntimeError):
    """Usługa WFS nie zwróciła poprawnego artefaktu."""


@dataclass(frozen=True)
class WfsResource:
    role: str
    url: str
    type_name: str
    source_crs: str
    field_mapping: dict[str, str]


class WfsFetcher:
    def __init__(self, timeout_seconds: float = 60.0) -> None:
        self._timeout = timeout_seconds

    def fetch(self, resources: tuple[WfsResource, ...]) -> bytes:
        """Pobiera zasoby w kolejności katalogowej i pakuje je deterministycznie."""
        payloads: list[tuple[str, bytes]] = []
        with httpx.Client(timeout=self._timeout, follow_redirects=True) as client:
            for index, resource in enumerate(resources):
                response = client.get(
                    resource.url,
                    params={
                        "service": "WFS",
                        "version": "2.0.0",
                        "request": "GetFeature",
                        "typeNames": resource.type_name,
                        "outputFormat": "GML32",
                        "srsName": resource.source_crs,
                    },
                )
                try:
                    response.raise_for_status()
                except httpx.HTTPError as exc:
                    raise WfsFetchError(
                        f"WFS nie zwrócił zasobu {resource.type_name!r}."
                    ) from exc
                if not response.content.lstrip().startswith(b"<?xml"):
                    raise WfsFetchError(
                        f"Zasób {resource.type_name!r} nie jest odpowiedzią XML/GML."
                    )
                payloads.append((f"{index:02d}-{resource.role}.gml", response.content))

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in payloads:
                info = zipfile.ZipInfo(name)
                # Stały timestamp zapewnia stabilny SHA identycznej odpowiedzi.
                info.date_time = (1980, 1, 1, 0, 0, 0)
                archive.writestr(info, content)
        return buffer.getvalue()
