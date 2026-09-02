"""Czytniki jawnie skonfigurowanych warstw POG (APP/GML/GeoJSON/ZIP).

Każda warstwa POG jest jawnie przypisana do jednego z czterech typów
(``planning_zone``, ``ouz``, ``downtown_area``, ``social_infrastructure_standard``)
— importer nie zgaduje typu ustawowego. Schematy APP różnią się między
producentami GIS, dlatego mapowanie atrybutów jest konfigurowalne per warstwa, a
surowe atrybuty źródłowe zawsze są zachowywane jako dowód.

Pobieranie i rozpakowywanie ZIP przechodzi przez wspólny, bezpieczny mechanizm
``app.shared.safe_archive`` (Zip Slip, zip bomb, limity), a nie przez
niekontrolowane ``extractall``.
"""

from __future__ import annotations

import io
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from shapely import from_wkt
from shapely.ops import unary_union

from app.modules.imports.application.pog_import import PogSourceBatch
from app.modules.imports.domain.pog import PogActRecord, PogFeatureRecord
from app.modules.imports.infrastructure.vector import VectorFeature, read_vector_features
from app.modules.imports.infrastructure.wfs import WfsFetcher, WfsResource
from app.shared.geometry import GeometryPayload
from app.shared.safe_archive import extract_zip


@dataclass(frozen=True)
class PogLayerResource:
    """Pojedyncza warstwa POG przypisana do typu obiektu."""

    feature_type: str
    path: Path
    source_crs: str
    field_mapping: dict[str, str] = field(default_factory=dict)
    layer: str | int | None = None


@dataclass(frozen=True)
class PogBoundaryResource:
    """Warstwa granicy aktu (aktPlanowaniaPrzestrzennego)."""

    path: Path
    source_crs: str
    layer: str | int | None = None


@dataclass(frozen=True)
class PogActMetadata:
    """Metadane aktu, gdy nie pochodzą z atrybutów warstwy."""

    act_identifier: str
    teryt: str
    legal_status: str
    resolution_number: str | None = None
    resolution_date: date | None = None
    name: str | None = None


def _boundary_payload(
    boundary: PogBoundaryResource | None,
) -> GeometryPayload | None:
    if boundary is None:
        return None
    features = read_vector_features(
        boundary.path, layer=boundary.layer, declared_crs=boundary.source_crs
    )
    if not features:
        return None
    merged = unary_union([from_wkt(feature.geometry.wkt) for feature in features])
    if merged.is_empty:
        return None
    return GeometryPayload(merged.wkt, crs=features[0].geometry.crs)


def _features_from_layers(
    layers: list[tuple[PogLayerResource, tuple[VectorFeature, ...]]],
) -> tuple[PogFeatureRecord, ...]:
    records: list[PogFeatureRecord] = []
    for resource, features in layers:
        for feature in features:
            records.append(
                PogFeatureRecord(
                    feature_type=resource.feature_type,
                    geometry=feature.geometry,
                    raw_attributes=feature.attributes,
                )
            )
    return tuple(records)


def _build_act(
    metadata: PogActMetadata,
    layers: list[tuple[PogLayerResource, tuple[VectorFeature, ...]]],
    boundary: GeometryPayload | None,
) -> PogActRecord:
    return PogActRecord(
        act_identifier=metadata.act_identifier,
        resolution_number=metadata.resolution_number,
        resolution_date=metadata.resolution_date,
        teryt=metadata.teryt,
        name=metadata.name,
        legal_status=metadata.legal_status,
        boundary=boundary,
        features=_features_from_layers(layers),
    )


class PyogrioPogReader:
    """Czyta lokalne pliki warstw POG jawnie przypisane do typów obiektów."""

    def __init__(
        self,
        resources: tuple[PogLayerResource, ...],
        *,
        metadata: PogActMetadata,
        boundary: PogBoundaryResource | None = None,
    ) -> None:
        self._resources = resources
        self._metadata = metadata
        self._boundary = boundary

    def read(self) -> PogSourceBatch:
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
        boundary = _boundary_payload(self._boundary)
        act = _build_act(self._metadata, layers, boundary)
        return PogSourceBatch(
            content=_pack_local_resources(self._resources, self._boundary),
            filename="pog-vector.zip",
            media_type="application/zip",
            acts=(act,),
        )


class WfsPogReader:
    """Pobiera warstwy POG przez WFS i zachowuje bezpieczny artefakt ZIP."""

    def __init__(
        self,
        resources: tuple[tuple[str, WfsResource], ...],
        *,
        metadata: PogActMetadata,
        fetcher: WfsFetcher | None = None,
    ) -> None:
        # Każdy element to (feature_type, zasób WFS). Kolejność == kolejność
        # plików GML w rozpakowanym artefakcie.
        self._resources = resources
        self._metadata = metadata
        self._fetcher = fetcher or WfsFetcher()

    def read(self) -> PogSourceBatch:
        wfs_resources = tuple(resource for _feature_type, resource in self._resources)
        content = self._fetcher.fetch(wfs_resources)
        layers: list[tuple[PogLayerResource, tuple[VectorFeature, ...]]] = []
        with tempfile.TemporaryDirectory(prefix="pog-wfs-") as temporary:
            # Bezpieczne rozpakowanie zamiast extractall (Zip Slip, zip bomb).
            extract_zip(content, temporary, allowed_suffixes=(".gml",))
            paths = sorted(Path(temporary).glob("*.gml"))
            for (feature_type, resource), path in zip(
                self._resources, paths, strict=True
            ):
                local = PogLayerResource(
                    feature_type=feature_type,
                    path=path,
                    source_crs=resource.source_crs,
                    field_mapping=resource.field_mapping,
                )
                layers.append(
                    (local, read_vector_features(path, declared_crs=resource.source_crs))
                )
            act = _build_act(self._metadata, layers, None)
        return PogSourceBatch(
            content=content,
            filename="pog-wfs.zip",
            media_type="application/zip",
            acts=(act,),
        )


def read_pog_archive(
    content: bytes,
    resources: tuple[PogLayerResource, ...],
    *,
    metadata: PogActMetadata,
) -> PogSourceBatch:
    """Rozpakowuje bezpiecznie ZIP APP/GML i mapuje pliki na warstwy po kolejności.

    Kolejność ``resources`` odpowiada posortowanej alfabetycznie liście plików w
    archiwum. Ekstrakcja używa wspólnego mechanizmu bezpieczeństwa, więc Zip
    Slip i zip bomb powodują kontrolowane odrzucenie całego importu.
    """
    with tempfile.TemporaryDirectory(prefix="pog-archive-") as temporary:
        extracted = extract_zip(
            content,
            temporary,
            allowed_suffixes=(".gml", ".xml", ".geojson", ".json"),
        )
        paths = sorted(extracted)
        layers = [
            (
                PogLayerResource(
                    feature_type=resource.feature_type,
                    path=path,
                    source_crs=resource.source_crs,
                    field_mapping=resource.field_mapping,
                    layer=resource.layer,
                ),
                read_vector_features(
                    path, layer=resource.layer, declared_crs=resource.source_crs
                ),
            )
            for resource, path in zip(resources, paths, strict=True)
        ]
        act = _build_act(metadata, layers, None)
    return PogSourceBatch(
        content=content,
        filename="pog-archive.zip",
        media_type="application/zip",
        acts=(act,),
    )


def _pack_local_resources(
    resources: tuple[PogLayerResource, ...],
    boundary: PogBoundaryResource | None,
) -> bytes:
    """Pakuje wejściowe pliki w deterministyczny ZIP dla artefaktu źródłowego."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for index, resource in enumerate(resources):
            info = zipfile.ZipInfo(
                f"{index:02d}-{resource.feature_type}{resource.path.suffix}"
            )
            info.date_time = (1980, 1, 1, 0, 0, 0)
            archive.writestr(info, resource.path.read_bytes())
        if boundary is not None:
            info = zipfile.ZipInfo(f"99-boundary{boundary.path.suffix}")
            info.date_time = (1980, 1, 1, 0, 0, 0)
            archive.writestr(info, boundary.path.read_bytes())
    return buffer.getvalue()
