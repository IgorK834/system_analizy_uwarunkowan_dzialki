"""Czytniki GML/GPKG/SHP/GeoJSON oraz WFS dla działek."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from app.modules.imports.application.parcels_import import ParcelSourceBatch
from app.modules.imports.domain.parcels import ParcelRecord
from app.modules.imports.infrastructure.vector import read_vector_features
from app.modules.imports.infrastructure.wfs import WfsFetcher, WfsResource
from app.shared.safe_archive import extract_zip


class PyogrioParcelReader:
    """Mapuje jawnie skonfigurowane pola OGR na publiczny model domenowy."""

    def __init__(
        self,
        path: str | Path,
        *,
        field_mapping: dict[str, str],
        teryt: str | None,
        declared_crs: str,
        layer: str | int | None = None,
    ) -> None:
        self._path = Path(path)
        self._mapping = field_mapping
        self._teryt = teryt
        self._declared_crs = declared_crs
        self._layer = layer

    def read(self) -> ParcelSourceBatch:
        content = self._path.read_bytes()
        records = self._records(self._path)
        return ParcelSourceBatch(
            content=content,
            filename=self._path.name,
            media_type=_media_type(self._path),
            records=records,
        )

    def _records(self, path: Path) -> tuple[ParcelRecord, ...]:
        features = read_vector_features(
            path, layer=self._layer, declared_crs=self._declared_crs
        )
        records: list[ParcelRecord] = []
        for feature in features:
            attrs = feature.attributes
            records.append(
                ParcelRecord(
                    parcel_identifier=_text(attrs, self._mapping, "parcel_identifier")
                    or "",
                    number=_text(attrs, self._mapping, "number"),
                    sheet=_text(attrs, self._mapping, "sheet"),
                    precinct=_text(attrs, self._mapping, "precinct"),
                    cadastral_unit=_text(
                        attrs, self._mapping, "cadastral_unit"
                    ),
                    teryt=_text(attrs, self._mapping, "teryt") or self._teryt,
                    geometry=feature.geometry,
                    reported_area_sqm=_float(
                        attrs.get(self._mapping.get("reported_area_sqm", ""))
                    ),
                    raw_attributes=attrs,
                )
            )
        return tuple(records)


class WfsParcelReader:
    """Pobiera pojedynczą warstwę WFS i zachowuje oryginalną odpowiedź ZIP."""

    def __init__(
        self,
        resource: WfsResource,
        *,
        teryt: str | None,
        fetcher: WfsFetcher | None = None,
    ) -> None:
        self._resource = resource
        self._teryt = teryt
        self._fetcher = fetcher or WfsFetcher()

    def read(self) -> ParcelSourceBatch:
        content = self._fetcher.fetch((self._resource,))
        with tempfile.TemporaryDirectory(prefix="dzialki-wfs-") as temporary:
            # Wspólny, bezpieczny mechanizm rozpakowania (Zip Slip, zip bomb,
            # limity) zamiast niekontrolowanego extractall.
            extract_zip(content, temporary, allowed_suffixes=(".gml",))
            gml = next(Path(temporary).glob("*.gml"))
            reader = PyogrioParcelReader(
                gml,
                field_mapping=self._resource.field_mapping,
                teryt=self._teryt,
                declared_crs=self._resource.source_crs,
            )
            records = reader._records(gml)
        return ParcelSourceBatch(
            content=content,
            filename="parcels-wfs.zip",
            media_type="application/zip",
            records=records,
        )


def _text(
    attributes: dict[str, Any], mapping: dict[str, str], key: str
) -> str | None:
    field = mapping.get(key)
    value = attributes.get(field) if field else None
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "."))
    except ValueError:
        return None


def _media_type(path: Path) -> str:
    return {
        ".gml": "application/gml+xml",
        ".gpkg": "application/geopackage+sqlite3",
        ".json": "application/geo+json",
        ".geojson": "application/geo+json",
        ".shp": "application/x-esri-shapefile",
    }.get(path.suffix.casefold(), "application/octet-stream")
