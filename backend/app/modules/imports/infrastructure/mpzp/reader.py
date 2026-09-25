"""Czytnik jawnie skonfigurowanych warstw granic i przeznaczeń MPZP."""

from __future__ import annotations

import io
import tempfile
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from shapely import from_wkt
from shapely.ops import unary_union

from app.modules.imports.application.mpzp_import import MpzpSourceBatch
from app.modules.imports.domain.mpzp import (
    PlanningActRecord,
    ZoneRecord,
    normalize_zone_symbol,
)
from app.modules.imports.infrastructure.vector import VectorFeature, read_vector_features
from app.modules.imports.infrastructure.wfs import WfsFetcher, WfsResource
from app.shared.geometry import GeometryPayload
from app.shared.safe_archive import extract_zip


@dataclass(frozen=True)
class VectorLayerResource:
    role: str
    path: Path
    source_crs: str
    field_mapping: dict[str, str]
    layer: str | int | None = None


class PyogrioMpzpReader:
    def __init__(self, resources: tuple[VectorLayerResource, ...], *, teryt: str) -> None:
        self._resources = resources
        self._teryt = teryt

    def read(self) -> MpzpSourceBatch:
        layers = [
            (
                resource,
                read_vector_features(
                    resource.path,
                    layer=resource.layer,
                    declared_crs=resource.source_crs,
                ),
            )
            for resource in self._resources
        ]
        acts = _build_acts(layers, self._teryt)
        return MpzpSourceBatch(
            content=_pack_local_resources(self._resources),
            filename="mpzp-vector.zip",
            media_type="application/zip",
            acts=acts,
            vector_available=True,
        )


class WfsMpzpReader:
    def __init__(
        self,
        resources: tuple[WfsResource, ...],
        *,
        teryt: str,
        fetcher: WfsFetcher | None = None,
    ) -> None:
        self._resources = resources
        self._teryt = teryt
        self._fetcher = fetcher or WfsFetcher()

    def read(self) -> MpzpSourceBatch:
        content = self._fetcher.fetch(self._resources)
        layers: list[tuple[VectorLayerResource, tuple[VectorFeature, ...]]] = []
        with tempfile.TemporaryDirectory(prefix="mpzp-wfs-") as temporary:
            # Odpowiedź WFS jest pakowana w ZIP; rozpakowujemy ją przez wspólny,
            # bezpieczny mechanizm (Zip Slip, zip bomb, limity), a nie
            # niekontrolowane extractall.
            extract_zip(content, temporary, allowed_suffixes=(".gml",))
            paths = sorted(Path(temporary).glob("*.gml"))
            for resource, path in zip(self._resources, paths, strict=True):
                local = VectorLayerResource(
                    role=resource.role,
                    path=path,
                    source_crs=resource.source_crs,
                    field_mapping=resource.field_mapping,
                )
                layers.append(
                    (
                        local,
                        read_vector_features(
                            path, declared_crs=resource.source_crs
                        ),
                    )
                )
            acts = _build_acts(layers, self._teryt)
        return MpzpSourceBatch(
            content=content,
            filename="mpzp-wfs.zip",
            media_type="application/zip",
            acts=acts,
            vector_available=True,
        )


class RasterOnlyMpzpReader:
    """Metadane aktu bez geometrii — nigdy nie tworzy poligonu zastępczego."""

    def __init__(self, act: PlanningActRecord, content: bytes = b"raster-only") -> None:
        self._act = act
        self._content = content

    def read(self) -> MpzpSourceBatch:
        return MpzpSourceBatch(
            content=self._content,
            filename="raster-only.json",
            media_type="application/json",
            acts=(),
            vector_available=False,
            raster_act=self._act,
        )


def _build_acts(
    layers: list[tuple[VectorLayerResource, tuple[VectorFeature, ...]]],
    teryt: str,
) -> tuple[PlanningActRecord, ...]:
    boundaries: dict[str, list[VectorFeature]] = defaultdict(list)
    zones: dict[str, list[tuple[VectorFeature, dict[str, str]]]] = defaultdict(list)
    mappings: dict[str, dict[str, str]] = {}
    for resource, features in layers:
        for feature in features:
            identifier = _text(
                feature.attributes, resource.field_mapping, "act_identifier"
            )
            if not identifier:
                continue
            mappings.setdefault(identifier, resource.field_mapping)
            if resource.role == "boundaries":
                boundaries[identifier].append(feature)
            elif resource.role == "zones":
                zones[identifier].append((feature, resource.field_mapping))

    acts: list[PlanningActRecord] = []
    for identifier in sorted(set(boundaries) | set(zones)):
        boundary_features = boundaries.get(identifier, [])
        metadata_feature = (
            boundary_features[0]
            if boundary_features
            else zones[identifier][0][0]
        )
        mapping = (
            mappings[identifier]
            if boundary_features
            else zones[identifier][0][1]
        )
        boundary = None
        if boundary_features:
            geometries = [
                from_wkt(feature.geometry.wkt) for feature in boundary_features
            ]
            merged = unary_union(geometries)
            boundary = GeometryPayload(
                merged.wkt, crs=boundary_features[0].geometry.crs
            )
        zone_records = tuple(
            ZoneRecord(
                original_symbol=_text(
                    feature.attributes, zone_mapping, "original_symbol"
                )
                or "",
                normalized_symbol=normalize_zone_symbol(
                    _text(feature.attributes, zone_mapping, "original_symbol")
                    or "",
                    _text(
                        feature.attributes, zone_mapping, "normalized_symbol"
                    ),
                ),
                geometry=feature.geometry,
                raw_attributes=feature.attributes,
                source_identifier=_text(
                    feature.attributes, zone_mapping, "zone_identifier"
                ),
            )
            for feature, zone_mapping in zones.get(identifier, [])
        )
        resolution_number = _text(
            metadata_feature.attributes, mapping, "resolution_number"
        )
        resolution_date = _date(
            metadata_feature.attributes.get(mapping.get("resolution_date", ""))
        )
        acts.append(
            PlanningActRecord(
                act_identifier=identifier,
                resolution_number=resolution_number or "",
                resolution_date=resolution_date,
                teryt=teryt,
                name=_text(metadata_feature.attributes, mapping, "name"),
                boundary=boundary,
                zones=zone_records,
                document_url=_text(metadata_feature.attributes, mapping, "document_url"),
            )
        )
    return tuple(acts)


def _text(
    attributes: dict[str, Any], mapping: dict[str, str], key: str
) -> str | None:
    field = mapping.get(key)
    value = attributes.get(field) if field else None
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value:
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return None


def _pack_local_resources(resources: tuple[VectorLayerResource, ...]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for index, resource in enumerate(resources):
            info = zipfile.ZipInfo(f"{index:02d}-{resource.role}{resource.path.suffix}")
            info.date_time = (1980, 1, 1, 0, 0, 0)
            archive.writestr(info, resource.path.read_bytes())
    return buffer.getvalue()
