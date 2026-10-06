"""Adapter pakietu audytowego: zapisany snapshot analizy → dane do spakowania (BK-505).

Odczytuje analizę wyłącznie z bazy (``build_analyze_response_from_analysis``) —
bez ponownej analizy i bez odpytywania źródeł. Zamienia ją na JSON-owe dane
wejściowe przypadku użycia ``reporting.application.audit_export``: warstwy
GeoJSON (EPSG:4326) z listą źródeł każdej warstwy, bloki surowych danych do
ewentualnego pominięcia, rejestr źródeł z katalogu i referencjami artefaktów
(``SourceArtifact``) oraz macierz jakości sekcji.

Zgoda na redystrybucję jest czytana z katalogu źródeł; źródło spoza katalogu
jest ``unconfirmed`` (traktowane jak zakaz).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any, Final

from geoalchemy2.shape import to_shape
from shapely.geometry import mapping
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.data_sources import CatalogError, DataSourceCatalog, get_catalog
from app.core.settings import settings
from app.models.analysis import Analysis
from app.models.versioned import DataRelease, DataSource, SourceArtifact
from app.modules.reporting.application.audit_export import (
    AuditInput,
    AuditLayer,
    AuditRawBlock,
    build_audit_package,
)
from app.modules.reporting.domain.audit_package import AuditLimits
from app.modules.reporting.infrastructure.archive import ArchiveResult, write_deterministic_zip
from app.schemas.analyze import AnalyzeResponse
from app.schemas.source import SourceMetadata
from app.services.persistence import build_analyze_response_from_analysis
from app.services.section_quality import quality_to_snapshot, resolve_source_id

logger = logging.getLogger(__name__)

# Pola API z geometrią GeoJSON — w ``analysis.json`` zastępuje je znacznik, a
# geometria trafia do plików GeoJSON (i podlega regule redystrybucji).
GEOMETRY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "geometry_geojson",
        "intersection_geojson",
        "buildable_area_geojson",
        "network_geometry_geojson",
        "protection_zone_geojson",
        "line_geojson",
    }
)
GEOMETRY_MOVED: Final[dict[str, bool]] = {"moved_to_geojson_files": True}


class AuditPackageNotFoundError(Exception):
    """Analiza o podanym identyfikatorze nie istnieje."""


def audit_limits() -> AuditLimits:
    return AuditLimits(
        max_files=settings.audit_export_max_files,
        max_file_bytes=settings.audit_export_max_file_bytes,
        max_total_bytes=settings.audit_export_max_total_bytes,
    )


def generate_audit_package(analysis_id: int, db: Session) -> ArchiveResult:
    """Buduje pakiet audytowy zapisanej analizy i zwraca archiwum gotowe do strumieniowania.

    Zgłasza ``AuditPackageNotFoundError`` (brak analizy) oraz
    ``AuditExportLimitError`` (przekroczony limit rozmiaru).
    """
    analysis = db.get(Analysis, analysis_id)
    if analysis is None:
        raise AuditPackageNotFoundError(f"Analiza o identyfikatorze {analysis_id} nie istnieje.")
    response = build_analyze_response_from_analysis(analysis, db)
    hashes = {source.artifact_sha256 for source in response.sources if source.artifact_sha256}
    release_ids = {source.data_release_id for source in response.sources if source.data_release_id}
    audit_input = build_audit_input(
        analysis,
        response,
        _catalog(),
        artifacts=_artifacts_by_hash(db, hashes),
        releases=_releases_by_id(db, release_ids),
    )
    limits = audit_limits()
    package = build_audit_package(audit_input, limits)
    logger.info(
        "Zbudowano pakiet audytowy analizy %s: %d plików, %d pominięć",
        analysis_id, len(package.files), len(package.omitted),
    )
    return write_deterministic_zip(package.files, limits)


def _catalog() -> DataSourceCatalog | None:
    try:
        return get_catalog()
    except CatalogError:
        logger.warning("audit_package_catalog_unavailable")
        return None


# --- Dane wejściowe --------------------------------------------------------------------


def build_audit_input(
    analysis: Analysis,
    response: AnalyzeResponse,
    catalog: DataSourceCatalog | None,
    *,
    artifacts: Mapping[str, Mapping[str, Any]],
    releases: Mapping[int, Mapping[str, Any]],
) -> AuditInput:
    """Dane wejściowe pakietu ze snapshotu; referencje artefaktów i wydań podaje wywołujący."""
    payload = response.model_dump(mode="json")
    payload.pop("access_token", None)  # sekret dostępu nie jest częścią pakietu
    payload.pop("sources", None)  # rejestr źródeł jest w sources.json
    _strip_ui_fields(payload)
    quality_payload = payload.pop("section_quality", None)
    quality = (
        quality_to_snapshot(response.section_quality) if response.section_quality else quality_payload
    )
    _replace_geometries(payload)

    parcel_ids = _ids([response.parcel.source] if response.parcel else [], catalog)
    parcel_geometry_2180 = (
        {"type": "Feature", "geometry": mapping(to_shape(analysis.parcel.geometry)),
         "properties": {"parcel_identifier": analysis.parcel.parcel_identifier, "crs": "EPSG:2180"}}
        if analysis.parcel is not None
        else None
    )
    redistribution = _redistribution_map(response, catalog)
    return AuditInput(
        analysis_id=response.analysis_id or analysis.id,
        analyzed_at=response.analyzed_at,
        parcel_identifier=response.parcel.parcel_identifier if response.parcel else None,
        status=response.status,
        analysis=payload,
        raw_blocks=tuple(_raw_blocks(response, catalog)),
        sources=tuple(_source_entries(response, catalog, artifacts, releases)),
        layers=tuple(_layers(response, catalog)),
        redistribution=redistribution,
        quality=quality,
        computation_parcel_2180=parcel_geometry_2180,
        parcel_source_ids=parcel_ids,
        # Źródło każdej strefy (po kolei jak ``result.mpzp_zones``): reguła redystrybucji cytatów z modelu.
        zone_source_ids=tuple(resolve_source_id(zone.source, catalog)[0] for zone in response.mpzp_zones),
        extra_context={
            "result_contract_version": analysis.result_contract_version,
            "data_release_ids": list(analysis.data_release_ids or []),
            "cache_signature": analysis.cache_signature,
        },
    )


def _strip_ui_fields(payload: dict[str, Any]) -> None:
    """Usuwa parametry formularza i ścieżki podglądu UI — nie są częścią wyniku."""
    context = payload.get("manual_zone_context")
    if isinstance(context, dict):
        for key in ("raster_preview_source_key", "symbol_max_length", "symbol_allowed_pattern"):
            context.pop(key, None)
        document = context.get("document")
        if isinstance(document, dict):
            document.pop("preview_path", None)


def _replace_geometries(node: Any) -> None:
    """Zastępuje pola geometrii znacznikiem (geometrie są w plikach GeoJSON)."""
    if isinstance(node, dict):
        for key, value in list(node.items()):
            if key in GEOMETRY_KEYS and value is not None:
                node[key] = dict(GEOMETRY_MOVED)
            else:
                _replace_geometries(value)
    elif isinstance(node, list):
        for item in node:
            _replace_geometries(item)


def _ids(sources: Iterable[SourceMetadata | None], catalog: DataSourceCatalog | None) -> tuple[str | None, ...]:
    return tuple(resolve_source_id(source, catalog)[0] for source in sources if source is not None)


def _redistribution_map(response: AnalyzeResponse, catalog: DataSourceCatalog | None) -> dict[str, str]:
    if catalog is None:
        return {}
    ids = {
        resolve_source_id(source, catalog)[0]
        for source in _all_sources(response)
    }
    return {sid: catalog.redistribution_of(sid) for sid in ids if sid}


def _all_sources(response: AnalyzeResponse) -> list[SourceMetadata]:
    found: list[SourceMetadata] = list(response.sources)
    if response.parcel:
        found.append(response.parcel.source)
    found.extend(zone.source for zone in response.mpzp_zones)
    if response.pog:
        if response.pog.source:
            found.append(response.pog.source)
        for zone in response.pog.zones:
            if zone.source:
                found.append(zone.source)
        for group in (response.pog.ouz, response.pog.downtown_areas,
                      response.pog.social_infrastructure_standard_areas):
            found.extend(item.source for item in group if item.source)
    found.extend(item.source for item in response.risks)
    found.extend(item.source for item in response.infrastructure)
    if response.terrain:
        if response.terrain.source:
            found.append(response.terrain.source)
        if response.terrain.relief and response.terrain.relief.source:
            found.append(response.terrain.relief.source)
    return found


def _raw_blocks(response: AnalyzeResponse, catalog: DataSourceCatalog | None) -> list[AuditRawBlock]:
    blocks: list[AuditRawBlock] = []
    if response.pog is not None and response.pog.raw_attributes:
        source_id = resolve_source_id(response.pog.source, catalog)[0] if response.pog.source else None
        blocks.append(AuditRawBlock(("pog", "raw_attributes"), source_id,
                                    "surowe atrybuty rekordu POG ze źródła"))
    return blocks


# --- Rejestr źródeł -------------------------------------------------------------------------


def _source_entries(
    response: AnalyzeResponse,
    catalog: DataSourceCatalog | None,
    artifacts: Mapping[str, Mapping[str, Any]],
    releases: Mapping[int, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for source in response.sources:
        source_id, codes = resolve_source_id(source, catalog)
        entry = catalog.find(source_id) if catalog else None
        entries.append(
            {
                "source_id": source_id,
                "source_name": source.source_name,
                "source_url": source.source_url,
                "source_version": source.source_version,
                "act_version": source.act_version,
                "fetched_at": _iso(source.fetched_at),
                "response_status": source.response_status,
                "confidence": source.confidence,
                "manual_review_required": source.manual_review_required,
                "data_release_id": source.data_release_id,
                "artifact_sha256": source.artifact_sha256,
                "artifact": artifacts.get(source.artifact_sha256 or ""),
                "release": releases.get(source.data_release_id or 0),
                "catalog": (
                    {
                        "name": entry.name,
                        "owner": entry.owner,
                        "status": entry.status.value,
                        "access_type": entry.access_type.value,
                        "license": " ".join(entry.license.split()),
                        "attribution": entry.attribution,
                        "expected_update_interval": entry.expected_update_interval,
                        "freshness_policy": (
                            entry.freshness_policy.model_dump(mode="json")
                            if entry.freshness_policy
                            else None
                        ),
                    }
                    if entry is not None
                    else None
                ),
                "resolution_notes": codes,
            }
        )
    return entries


def _iso(value: Any) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value is not None else None


def _artifacts_by_hash(db: Session, hashes: set[str]) -> dict[str, dict[str, Any]]:
    """Referencje artefaktów (URI, hash, rozmiar) — metadane, nigdy zawartość."""
    if not hashes:
        return {}
    rows = db.scalars(select(SourceArtifact).where(SourceArtifact.content_hash.in_(hashes))).all()
    found: dict[str, dict[str, Any]] = {}
    for row in sorted(rows, key=lambda item: item.id):
        found.setdefault(
            row.content_hash,
            {
                "uri": row.uri,
                "content_hash": row.content_hash,
                "size_bytes": row.size_bytes,
                "media_type": row.media_type,
                "fetched_at": _iso(row.fetched_at),
            },
        )
    return found


def _releases_by_id(db: Session, ids: set[int]) -> dict[int, dict[str, Any]]:
    """Niezmienne dane wydania (bez ``is_active``, które zmienia się w czasie)."""
    if not ids:
        return {}
    rows = db.execute(
        select(DataRelease, DataSource.source_id)
        .join(DataSource, DataSource.id == DataRelease.data_source_id)
        .where(DataRelease.id.in_(ids))
    ).all()
    return {
        release.id: {
            "release_id": release.id,
            "source_id": source_id,
            "version_label": release.version_label,
            "published_at": _iso(release.published_at),
        }
        for release, source_id in rows
    }


# --- Warstwy GeoJSON ------------------------------------------------------------------------


def _features(value: Mapping[str, Any] | None, properties: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Feature'y z pola geometrii (Feature, FeatureCollection albo sama geometria)."""
    if not value:
        return []
    kind = value.get("type")
    if kind == "FeatureCollection":
        return [
            item
            for feature in value.get("features", [])
            for item in _features(feature, properties)
        ]
    if kind == "Feature":
        merged = {**dict(properties), **(dict(value.get("properties") or {}))}
        return [{"type": "Feature", "geometry": value.get("geometry"), "properties": merged}]
    if "coordinates" in value or kind == "GeometryCollection":
        return [{"type": "Feature", "geometry": dict(value), "properties": dict(properties)}]
    return []


def _layers(response: AnalyzeResponse, catalog: DataSourceCatalog | None) -> list[AuditLayer]:
    layers: list[AuditLayer] = []

    def add(name: str, title: str, description: str, features: list[dict[str, Any]],
            sources: Iterable[SourceMetadata | None], kind: str = "derived") -> None:
        if features:
            layers.append(
                AuditLayer(
                    name=name, title=title, description=description,
                    features=tuple(features), source_ids=_ids(sources, catalog),
                    kind=kind,  # type: ignore[arg-type]
                )
            )

    if response.parcel is not None:
        parcel = response.parcel
        add("parcel", "Obrys działki",
            "Geometria działki z ULDK przeliczona z EPSG:2180 do EPSG:4326.",
            _features(parcel.geometry_geojson, {"layer": "parcel",
                                                "parcel_identifier": parcel.parcel_identifier}),
            [parcel.source], kind="parcel")
        add("buildable_area", "Obszar po technicznym odsunięciu od granic (przybliżenie)",
            "Przybliżenie techniczne (bufor ujemny) — nie jest linią zabudowy z MPZP.",
            _features(parcel.buildable_area_geojson, {"layer": "buildable_area",
                                                      "is_technical_approximation": True}),
            [parcel.source])

    mpzp: list[dict[str, Any]] = []
    for zone in response.mpzp_zones:
        mpzp += _features(
            zone.intersection_geojson,
            {"layer": "mpzp_zone", "zone_symbol": zone.zone_symbol, "zone_id": zone.zone_id,
             "share_pct": zone.intersection_pct, "area_sqm": zone.intersection_area_sqm,
             "assignment_method": zone.assignment_method},
        )
    add("mpzp_zones", "Strefy MPZP przecinające działkę",
        "Przecięcia stref MPZP z działką (obliczone w EPSG:2180, zapis w EPSG:4326).",
        mpzp, [zone.source for zone in response.mpzp_zones])

    if response.pog is not None:
        pog = response.pog
        add("pog_zones", "Strefy POG przecinające działkę",
            "Przecięcia stref planu ogólnego z działką (obliczone w EPSG:2180).",
            [f for zone in pog.zones
             for f in _features(zone.geometry_geojson,
                                {"layer": "pog_zone", "zone_id": zone.id, "symbol": zone.symbol,
                                 "type": zone.type, "share_pct": zone.area_pct})],
            [zone.source or pog.source for zone in pog.zones])
        overlays = (("OUZ", pog.ouz), ("OZS", pog.downtown_areas),
                    ("OSDIS", pog.social_infrastructure_standard_areas))
        add("pog_overlays", "OUZ, OZS i OSDIS przecinające działkę",
            "Obszary uzupełnienia zabudowy, śródmiejskie i standardu infrastruktury społecznej.",
            [f for kind, group in overlays for item in group
             for f in _features(item.geometry_geojson,
                                {"layer": "pog_overlay", "kind": kind, "area_id": item.id,
                                 "symbol": item.symbol, "share_pct": item.area_pct})],
            [item.source or pog.source for _, group in overlays for item in group])

    for section, name, title in (("flood", "flood_risk", "Strefy zagrożenia powodziowego (ISOK)"),
                                 ("nature", "nature_protection", "Formy ochrony przyrody (GDOŚ)")):
        items = [item for item in response.risks if _risk_section(item.section, item.risk_type) == section]
        add(name, title, "Obiekty źródła przecinające działkę (geometria przycięta do działki).",
            [f for item in items
             for f in _features(item.geometry_geojson,
                                {"layer": name, "risk_type": item.risk_type,
                                 "feature_id": item.feature_id, "name": item.name,
                                 "severity": item.severity, "share_pct": item.intersection_pct,
                                 "touches_boundary": item.touches_boundary})],
            [item.source for item in items])

    infra = response.infrastructure
    add("infrastructure_buffers", "Sieci uzbrojenia i bufory techniczne (przybliżenie)",
        "Bufory sieci są przybliżeniem technicznym wg reguły konfiguracyjnej.",
        [f for item in infra
         for key, value in (("network", item.network_geometry_geojson),
                            ("protection_zone", item.protection_zone_geojson))
         for f in _features(value, {"layer": "infrastructure_buffer", "part": key,
                                    "network_type": item.network_type, "buffer_m": item.buffer_m})],
        [item.source for item in infra])

    terrain = response.terrain
    if terrain is not None and terrain.relief is not None and terrain.relief.profile is not None:
        relief = terrain.relief
        add("terrain_profile", "Linia profilu wysokościowego (NMT)",
            "Linia, wzdłuż której próbkowano profil wysokościowy z rastra NMT.",
            _features(relief.profile.line_geojson, {"layer": "terrain_profile"}),
            [relief.source or terrain.source])
    return layers


def _risk_section(section: str | None, risk_type: str) -> str:
    if section in {"flood", "nature"}:
        return str(section)
    return "flood" if risk_type in {"flood", "flood_zone"} else "nature"
