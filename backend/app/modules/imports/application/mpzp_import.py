"""Przypadek użycia importu pełnych snapshotów aktów MPZP."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any, Protocol

from app.modules.imports.application.common import (
    ImportOutcome,
    ImportRelease,
    ImportSourceNotRunnable,
    MutableImportStats,
    SourceBatch,
    artifact_sha256,
)
from app.modules.imports.application.parcels_import import RepairedGeometry
from app.modules.imports.domain.mpzp import (
    PlanningActRecord,
    PlanningActValidationError,
    validate_topology,
)
from app.shared.geometry import GeometryPayload


@dataclass(frozen=True)
class MpzpSourceBatch(SourceBatch):
    acts: tuple[PlanningActRecord, ...] = ()
    vector_available: bool = True
    raster_act: PlanningActRecord | None = None


@dataclass(frozen=True)
class MpzpPublicationResult:
    new: int
    changed: int
    unchanged: int
    import_run_id: int
    data_release_id: int


class MpzpSourceReader(Protocol):
    def read(self) -> MpzpSourceBatch: ...


class MpzpImportRepository(Protocol):
    topology_distance_tolerance_m: float
    topology_area_tolerance_sqm: float

    def repair_geometry(self, geometry: GeometryPayload) -> RepairedGeometry | None: ...

    def publish_mpzp(
        self,
        *,
        release: ImportRelease,
        batch: MpzpSourceBatch,
        artifact_hash: str,
        acts: tuple[tuple[PlanningActRecord, str], ...],
        stats: MutableImportStats,
        warnings: tuple[str, ...],
    ) -> MpzpPublicationResult: ...


def _snapshot_hash(act: PlanningActRecord, geometry_hashes: list[str]) -> str:
    if act.resolution_date is None:
        raise PlanningActValidationError("Brak daty uchwały.")
    boundary_hash = geometry_hashes[0] if geometry_hashes else None
    zone_hashes = geometry_hashes[1:]
    if len(zone_hashes) != len(act.zones):
        raise ValueError("Liczba hashy geometrii stref jest niespójna z aktem.")
    zones = [
        {
            "original_symbol": zone.original_symbol,
            "normalized_symbol": zone.normalized_symbol,
            "raw_attributes": _stable_json_value(zone.raw_attributes),
            "geometry_hash": geometry_hash,
        }
        for zone, geometry_hash in zip(act.zones, zone_hashes, strict=True)
    ]
    zones.sort(
        key=lambda item: json.dumps(
            item, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    )
    payload = {
        "identifier": act.act_identifier,
        "resolution_number": act.resolution_number,
        "resolution_date": act.resolution_date.isoformat(),
        "teryt": act.teryt,
        "name": act.name,
        "legal_status": act.legal_status,
        "boundary_hash": boundary_hash,
        "zones": zones,
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stable_json_value(value: Any) -> Any:
    """Sprowadza atrybuty OGR do deterministycznej postaci hashowalnej JSON."""
    if isinstance(value, dict):
        return {
            str(key): _stable_json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_stable_json_value(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _stable_json_value(item())
        except (TypeError, ValueError):
            pass
    try:
        json.dumps(value)
    except TypeError:
        return str(value)
    return value


def run_mpzp_import(
    reader: MpzpSourceReader,
    gmina_source_id: str,
    db: MpzpImportRepository,
    *,
    release: ImportRelease,
) -> ImportOutcome:
    """Naprawia i waliduje cały akt, a następnie publikuje go atomowo."""
    if gmina_source_id != release.source_id:
        raise ValueError("source_id czytnika i wydania muszą być zgodne.")
    if not release.publication_allowed and not release.dry_run:
        raise ImportSourceNotRunnable(
            "DRY-RUN ONLY: źródło MPZP nie jest production_ready."
        )

    batch = reader.read()
    stats = MutableImportStats(input=len(batch.acts))
    warnings: list[str] = []

    if not batch.vector_available:
        stats.extra["raster_only"] = 1
        if batch.raster_act is None:
            raise PlanningActValidationError(
                "Fallback raster_only wymaga jednoznacznych metadanych aktu."
            )
        batch.raster_act.validate()
        if release.dry_run or not release.publication_allowed:
            return ImportOutcome(
                status="dry_run_only",
                stats=stats.as_dict(),
                warnings=("raster_only", "manual_review_required"),
            )
        raster_act = replace(
            batch.raster_act,
            boundary=None,
            zones=(),
            legal_status="raster_only",
        )
        result = db.publish_mpzp(
            release=release,
            batch=batch,
            artifact_hash=artifact_sha256(batch.content),
            acts=((raster_act, _snapshot_hash(raster_act, [])),),
            stats=stats,
            warnings=("raster_only", "manual_review_required"),
        )
        stats.new, stats.changed, stats.unchanged = (
            result.new,
            result.changed,
            result.unchanged,
        )
        return ImportOutcome(
            status="succeeded",
            stats=stats.as_dict(),
            warnings=("raster_only", "manual_review_required"),
            import_run_id=result.import_run_id,
            data_release_id=result.data_release_id,
        )

    prepared: list[tuple[PlanningActRecord, str]] = []
    for act in batch.acts:
        try:
            act.validate()
        except PlanningActValidationError as exc:
            stats.rejected += 1
            warnings.append(str(exc))
            continue
        if act.boundary is None:
            stats.rejected += 1
            warnings.append(f"missing_boundary:{act.act_identifier}")
            continue
        boundary = db.repair_geometry(act.boundary)
        if boundary is None:
            stats.rejected += 1
            warnings.append(f"empty_boundary:{act.act_identifier}")
            continue
        if boundary.repaired:
            stats.repaired += 1

        repaired_zones = []
        geometry_hashes = [boundary.content_hash]
        for zone in act.zones:
            repaired = db.repair_geometry(zone.geometry)
            if repaired is None:
                stats.rejected += 1
                warnings.append(
                    f"empty_zone:{act.act_identifier}:{zone.original_symbol}"
                )
                continue
            if repaired.repaired:
                stats.repaired += 1
            repaired_zones.append(zone.with_geometry(repaired.geometry))
            geometry_hashes.append(repaired.content_hash)

        prepared_act = replace(
            act,
            boundary=boundary.geometry,
            zones=tuple(repaired_zones),
        )
        topology = validate_topology(
            prepared_act,
            distance_tolerance_m=db.topology_distance_tolerance_m,
            area_tolerance_sqm=db.topology_area_tolerance_sqm,
        )
        warnings.extend(
            f"{warning}:{act.act_identifier}" for warning in topology.warnings
        )
        for key, value in {
            "boundary_area_sqm": topology.boundary_area_sqm,
            "zones_area_sqm": topology.zones_area_sqm,
            "zone_overlap_area_sqm": topology.overlap_area_sqm,
            "zone_gap_area_sqm": topology.gap_area_sqm,
            "zones_outside_boundary": topology.outside_zone_count,
        }.items():
            stats.extra[key] = round(
                float(stats.extra.get(key, 0)) + float(value), 6
            )
        prepared.append(
            (prepared_act, _snapshot_hash(prepared_act, geometry_hashes))
        )

    if release.dry_run or not release.publication_allowed:
        stats.extra["publishable"] = len(prepared)
        return ImportOutcome(
            status="dry_run_only",
            stats=stats.as_dict(),
            warnings=tuple(dict.fromkeys(warnings)),
        )

    result = db.publish_mpzp(
        release=release,
        batch=batch,
        artifact_hash=artifact_sha256(batch.content),
        acts=tuple(prepared),
        stats=stats,
        warnings=tuple(dict.fromkeys(warnings)),
    )
    stats.new, stats.changed, stats.unchanged = (
        result.new,
        result.changed,
        result.unchanged,
    )
    return ImportOutcome(
        status="succeeded",
        stats=stats.as_dict(),
        warnings=tuple(dict.fromkeys(warnings)),
        import_run_id=result.import_run_id,
        data_release_id=result.data_release_id,
    )
