"""Przypadek użycia raw → QA → atomowa publikacja wersji działek."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.modules.imports.application.common import (
    ImportOutcome,
    ImportRelease,
    ImportSourceNotRunnable,
    MutableImportStats,
    SourceBatch,
    artifact_sha256,
)
from app.modules.imports.domain.parcels import (
    NormalizedParcel,
    ParcelNormalizationError,
    ParcelRecord,
    area_is_within_tolerance,
    duplicate_identifiers,
    normalize_parcel,
    teryt_is_in_scope,
)
from app.shared.geometry import GeometryPayload


@dataclass(frozen=True)
class ParcelSourceBatch(SourceBatch):
    records: tuple[ParcelRecord, ...] = ()


@dataclass(frozen=True)
class RepairedGeometry:
    geometry: GeometryPayload
    area_sqm: float
    content_hash: str
    repaired: bool


@dataclass(frozen=True)
class PublishParcel:
    record: NormalizedParcel
    area_sqm: float
    content_hash: str


@dataclass(frozen=True)
class PublicationResult:
    new: int
    changed: int
    unchanged: int
    import_run_id: int
    data_release_id: int


class ParcelSourceReader(Protocol):
    def read(self) -> ParcelSourceBatch: ...


class ParcelImportRepository(Protocol):
    area_tolerance_ratio: float
    overlap_tolerance_sqm: float

    def repair_geometry(self, geometry: GeometryPayload) -> RepairedGeometry | None: ...

    def overlapping_identifiers(
        self, parcels: tuple[PublishParcel, ...]
    ) -> set[str]: ...

    def publish_parcels(
        self,
        *,
        release: ImportRelease,
        batch: ParcelSourceBatch,
        artifact_hash: str,
        parcels: tuple[PublishParcel, ...],
        stats: MutableImportStats,
        warnings: tuple[str, ...],
    ) -> PublicationResult: ...


def run_parcel_import(
    reader: ParcelSourceReader,
    release: ImportRelease,
    db: ParcelImportRepository,
) -> ImportOutcome:
    """Wykonuje deterministyczny import, nie znając formatu ani SQL."""
    if not release.publication_allowed and not release.dry_run:
        raise ImportSourceNotRunnable(
            "DRY-RUN ONLY: źródło nie jest production_ready."
        )

    batch = reader.read()
    stats = MutableImportStats(input=len(batch.records))
    warnings: list[str] = []
    normalized: list[NormalizedParcel] = []
    for raw in batch.records:
        try:
            normalized.append(normalize_parcel(raw))
        except ParcelNormalizationError as exc:
            stats.rejected += 1
            warnings.append(str(exc))

    duplicates = duplicate_identifiers(normalized)
    if duplicates:
        stats.rejected += sum(
            1 for item in normalized if item.parcel_identifier in duplicates
        )
        normalized = [
            item for item in normalized if item.parcel_identifier not in duplicates
        ]
        warnings.append("duplicate_parcel_identifier")

    publishable: list[PublishParcel] = []
    for record in normalized:
        repaired = db.repair_geometry(record.geometry)
        if repaired is None:
            stats.rejected += 1
            warnings.append(f"empty_geometry:{record.parcel_identifier}")
            continue
        if repaired.repaired:
            stats.repaired += 1
        if not area_is_within_tolerance(
            record.reported_area_sqm,
            repaired.area_sqm,
            db.area_tolerance_ratio,
        ):
            stats.rejected += 1
            warnings.append(f"area_out_of_tolerance:{record.parcel_identifier}")
            continue
        in_scope = teryt_is_in_scope(record.teryt, release.teryt_scope)
        if in_scope is False:
            warnings.append(f"teryt_out_of_scope:{record.parcel_identifier}")
        elif in_scope is None:
            warnings.append(f"teryt_scope_not_verifiable:{record.parcel_identifier}")
        publishable.append(
            PublishParcel(
                record=record.with_geometry(repaired.geometry),
                area_sqm=repaired.area_sqm,
                content_hash=repaired.content_hash,
            )
        )

    overlapping = db.overlapping_identifiers(tuple(publishable))
    if overlapping:
        rejected_overlaps = [
            item
            for item in publishable
            if item.record.parcel_identifier in overlapping
        ]
        stats.rejected += len(rejected_overlaps)
        publishable = [
            item
            for item in publishable
            if item.record.parcel_identifier not in overlapping
        ]
        warnings.append("parcel_overlaps")

    artifact_hash = artifact_sha256(batch.content)
    if release.dry_run or not release.publication_allowed:
        stats.extra["publishable"] = len(publishable)
        return ImportOutcome(
            status="dry_run_only",
            stats=stats.as_dict(),
            warnings=tuple(dict.fromkeys(warnings)),
        )

    result = db.publish_parcels(
        release=release,
        batch=batch,
        artifact_hash=artifact_hash,
        parcels=tuple(publishable),
        stats=stats,
        warnings=tuple(dict.fromkeys(warnings)),
    )
    stats.new = result.new
    stats.changed = result.changed
    stats.unchanged = result.unchanged
    return ImportOutcome(
        status="succeeded",
        stats=stats.as_dict(),
        warnings=tuple(dict.fromkeys(warnings)),
        import_run_id=result.import_run_id,
        data_release_id=result.data_release_id,
    )
