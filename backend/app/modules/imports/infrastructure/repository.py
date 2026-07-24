"""PostGIS/SQLAlchemy: naprawa geometrii i atomowa publikacja wydań."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any

from geoalchemy2.elements import WKTElement
from pyproj import Transformer
from shapely import from_wkt
from shapely.ops import transform
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.core.data_sources import DataSourceEntry
from app.models.parcel import Parcel
from app.models.versioned import (
    DataRelease,
    DataSource,
    ImportRun,
    LandUseArea,
    ParcelVersion,
    PlanBoundary,
    PlanningAct,
    PlanningActVersion,
    PlanningSymbol,
    SourceArtifact,
)
from app.modules.imports.application.common import ImportRelease, MutableImportStats
from app.modules.imports.application.mpzp_import import (
    MpzpPublicationResult,
    MpzpSourceBatch,
)
from app.modules.imports.application.parcels_import import (
    ParcelSourceBatch,
    PublicationResult,
    PublishParcel,
    RepairedGeometry,
)
from app.modules.imports.domain.mpzp import PlanningActRecord
from app.modules.imports.infrastructure.artifacts import LocalArtifactStore
from app.shared.crs import CANONICAL_CRS, is_allowed_crs
from app.shared.geometry import GeometryPayload


class SqlAlchemyImportRepository:
    """Wspólna jednostka pracy obu importerów."""

    def __init__(
        self,
        session: Session,
        source: DataSourceEntry,
        artifact_store: LocalArtifactStore,
        *,
        area_tolerance_ratio: float = 0.02,
        overlap_tolerance_sqm: float = 0.01,
        topology_distance_tolerance_m: float = 0.05,
        topology_area_tolerance_sqm: float = 0.01,
    ) -> None:
        self.session = session
        self.source = source
        self.artifact_store = artifact_store
        self.area_tolerance_ratio = area_tolerance_ratio
        self.overlap_tolerance_sqm = overlap_tolerance_sqm
        self.topology_distance_tolerance_m = topology_distance_tolerance_m
        self.topology_area_tolerance_sqm = topology_area_tolerance_sqm

    def repair_geometry(self, geometry: GeometryPayload) -> RepairedGeometry | None:
        """Reprojektuje do 2180, a naprawę i ekstrakcję poligonów wykonuje PostGIS."""
        if not is_allowed_crs(geometry.crs):
            raise ValueError(f"Nieznany CRS geometrii: {geometry.crs!r}.")
        shape = from_wkt(geometry.wkt)
        if geometry.crs != CANONICAL_CRS:
            transformer = Transformer.from_crs(
                geometry.crs, CANONICAL_CRS, always_xy=True
            )
            shape = transform(transformer.transform, shape)

        row = self.session.execute(
            text(
                """
                WITH source AS (
                    SELECT ST_GeomFromText(:wkt, 2180) AS geom
                ), repaired AS (
                    SELECT geom AS source_geom,
                           ST_Multi(ST_CollectionExtract(ST_MakeValid(geom), 3)) AS geom
                    FROM source
                )
                SELECT
                    CASE WHEN geom IS NULL OR ST_IsEmpty(geom)
                         THEN NULL ELSE ST_AsText(geom) END AS wkt,
                    CASE WHEN geom IS NULL OR ST_IsEmpty(geom)
                         THEN NULL ELSE ST_Area(geom) END AS area_sqm,
                    CASE WHEN geom IS NULL OR ST_IsEmpty(geom)
                         THEN NULL ELSE ST_AsBinary(ST_Normalize(geom)) END AS canonical_wkb,
                    CASE WHEN geom IS NULL OR ST_IsEmpty(geom) THEN false
                         ELSE NOT ST_IsValid(source_geom)
                              OR NOT ST_Equals(source_geom, geom) END AS repaired
                FROM repaired
                """
            ),
            {"wkt": shape.wkt},
        ).mappings().one()
        if row["wkt"] is None:
            return None
        canonical = bytes(row["canonical_wkb"])
        return RepairedGeometry(
            geometry=GeometryPayload(str(row["wkt"]), crs=CANONICAL_CRS),
            area_sqm=float(row["area_sqm"]),
            content_hash=hashlib.sha256(canonical).hexdigest(),
            repaired=bool(row["repaired"]),
        )

    def overlapping_identifiers(
        self, parcels: tuple[PublishParcel, ...]
    ) -> set[str]:
        if len(parcels) < 2:
            return set()
        selects: list[str] = []
        params: dict[str, Any] = {"threshold": self.overlap_tolerance_sqm}
        for index, parcel in enumerate(parcels):
            params[f"id_{index}"] = parcel.record.parcel_identifier
            params[f"wkt_{index}"] = parcel.record.geometry.wkt
            selects.append(
                f"SELECT :id_{index}::text AS identifier, "
                f"ST_GeomFromText(:wkt_{index}, 2180) AS geom"
            )
        statement = text(
            f"""
            WITH staged AS ({' UNION ALL '.join(selects)})
            SELECT DISTINCT a.identifier
            FROM staged a
            JOIN staged b ON a.identifier < b.identifier
            WHERE a.geom && b.geom
              AND ST_Area(ST_Intersection(a.geom, b.geom)) > :threshold
            UNION
            SELECT DISTINCT b.identifier
            FROM staged a
            JOIN staged b ON a.identifier < b.identifier
            WHERE a.geom && b.geom
              AND ST_Area(ST_Intersection(a.geom, b.geom)) > :threshold
            """
        )
        return set(self.session.execute(statement, params).scalars())

    def publish_parcels(
        self,
        *,
        release: ImportRelease,
        batch: ParcelSourceBatch,
        artifact_hash: str,
        parcels: tuple[PublishParcel, ...],
        stats: MutableImportStats,
        warnings: tuple[str, ...],
    ) -> PublicationResult:
        uri = self.artifact_store.save(
            source_id=release.source_id,
            content_hash=artifact_hash,
            filename=batch.filename,
            content=batch.content,
        )
        transaction = (
            self.session.begin_nested()
            if self.session.in_transaction()
            else self.session.begin()
        )
        with transaction:
            source = self._source_row()
            artifact = self._artifact_row(
                source.id, uri, batch.media_type, artifact_hash, len(batch.content)
            )
            data_release = self._release_row(source.id, release, artifact_hash)
            run = self._run_row(source.id, data_release.id, artifact_hash)
            now = datetime.now(timezone.utc)
            new = changed = unchanged = 0
            for item in parcels:
                parcel = self.session.execute(
                    select(Parcel).where(
                        Parcel.parcel_identifier == item.record.parcel_identifier
                    )
                ).scalar_one_or_none()
                if parcel is None:
                    parcel = Parcel(
                        parcel_identifier=item.record.parcel_identifier,
                        geometry=WKTElement(item.record.geometry.wkt, srid=2180),
                        area_sqm=item.area_sqm,
                        teryt=item.record.teryt,
                    )
                    self.session.add(parcel)
                    self.session.flush()
                latest = self.session.execute(
                    select(ParcelVersion)
                    .where(
                        ParcelVersion.parcel_id == parcel.id,
                        ParcelVersion.valid_to.is_(None),
                    )
                    .order_by(ParcelVersion.valid_from.desc())
                    .limit(1)
                ).scalar_one_or_none()
                if latest is not None and latest.content_hash == item.content_hash:
                    unchanged += 1
                    continue
                if latest is not None:
                    latest.valid_to = now
                    changed += 1
                else:
                    new += 1
                parcel.geometry = WKTElement(item.record.geometry.wkt, srid=2180)
                parcel.area_sqm = item.area_sqm
                parcel.teryt = item.record.teryt
                self.session.add(
                    ParcelVersion(
                        parcel_id=parcel.id,
                        geometry=WKTElement(item.record.geometry.wkt, srid=2180),
                        source_artifact_id=artifact.id,
                        data_release_id=data_release.id,
                        valid_from=now,
                        valid_to=None,
                        published_at=release.published_at,
                        content_hash=item.content_hash,
                        review_status="verified",
                    )
                )
            if new or changed or data_release.is_active:
                self._activate_release(source.id, data_release)
            final_stats = stats.as_dict() | {
                "new": new,
                "changed": changed,
                "unchanged": unchanged,
            }
            run.status = "succeeded"
            run.stats = final_stats
            run.checkpoint = {
                "artifact_hash": artifact_hash,
                "processed": len(parcels),
                "warnings": list(warnings),
            }
            run.finished_at = now
            self.session.flush()
            return PublicationResult(
                new, changed, unchanged, run.id, data_release.id
            )

    def publish_mpzp(
        self,
        *,
        release: ImportRelease,
        batch: MpzpSourceBatch,
        artifact_hash: str,
        acts: tuple[tuple[PlanningActRecord, str], ...],
        stats: MutableImportStats,
        warnings: tuple[str, ...],
    ) -> MpzpPublicationResult:
        uri = self.artifact_store.save(
            source_id=release.source_id,
            content_hash=artifact_hash,
            filename=batch.filename,
            content=batch.content,
        )
        transaction = (
            self.session.begin_nested()
            if self.session.in_transaction()
            else self.session.begin()
        )
        with transaction:
            source = self._source_row()
            artifact = self._artifact_row(
                source.id, uri, batch.media_type, artifact_hash, len(batch.content)
            )
            data_release = self._release_row(source.id, release, artifact_hash)
            run = self._run_row(source.id, data_release.id, artifact_hash)
            now = datetime.now(timezone.utc)
            new = changed = unchanged = 0
            for act_record, snapshot_hash in acts:
                act = self.session.execute(
                    select(PlanningAct).where(
                        PlanningAct.act_identifier == act_record.act_identifier
                    )
                ).scalar_one_or_none()
                if act is None:
                    act = PlanningAct(
                        act_identifier=act_record.act_identifier,
                        teryt=act_record.teryt,
                        kind="mpzp",
                    )
                    self.session.add(act)
                    self.session.flush()
                latest = self.session.execute(
                    select(PlanningActVersion)
                    .where(
                        PlanningActVersion.planning_act_id == act.id,
                        PlanningActVersion.valid_to.is_(None),
                    )
                    .order_by(PlanningActVersion.valid_from.desc())
                    .limit(1)
                ).scalar_one_or_none()
                if latest is not None and latest.content_hash == snapshot_hash:
                    unchanged += 1
                    continue
                if latest is not None:
                    latest.valid_to = now
                    changed += 1
                else:
                    new += 1
                raster_only = act_record.legal_status == "raster_only"
                version = PlanningActVersion(
                    planning_act_id=act.id,
                    legal_status=act_record.legal_status,
                    version_label=release.version_label,
                    resolution_number=act_record.resolution_number,
                    resolution_date=act_record.resolution_date,
                    name=act_record.name,
                    manual_review_required=raster_only,
                    source_artifact_id=artifact.id,
                    data_release_id=data_release.id,
                    valid_from=now,
                    valid_to=None,
                    published_at=release.published_at,
                    content_hash=snapshot_hash,
                    review_status="unreviewed" if raster_only else "verified",
                )
                self.session.add(version)
                self.session.flush()
                if act_record.boundary is not None:
                    self.session.add(
                        PlanBoundary(
                            planning_act_version_id=version.id,
                            geometry=WKTElement(act_record.boundary.wkt, srid=2180),
                        )
                    )
                symbols: dict[str, PlanningSymbol] = {}
                for zone in act_record.zones:
                    symbol = symbols.get(zone.original_symbol)
                    if symbol is None:
                        symbol = PlanningSymbol(
                            planning_act_version_id=version.id,
                            local_symbol=zone.original_symbol,
                            normalized_category=zone.normalized_symbol,
                        )
                        self.session.add(symbol)
                        self.session.flush()
                        symbols[zone.original_symbol] = symbol
                    self.session.add(
                        LandUseArea(
                            planning_act_version_id=version.id,
                            planning_symbol_id=symbol.id,
                            symbol=zone.original_symbol,
                            raw_attributes=_jsonable(zone.raw_attributes),
                            geometry=WKTElement(zone.geometry.wkt, srid=2180),
                        )
                    )
            if new or changed or data_release.is_active:
                self._activate_release(source.id, data_release)
            final_stats = stats.as_dict() | {
                "new": new,
                "changed": changed,
                "unchanged": unchanged,
            }
            run.status = "succeeded"
            run.stats = final_stats
            run.checkpoint = {
                "artifact_hash": artifact_hash,
                "processed": len(acts),
                "warnings": list(warnings),
            }
            run.finished_at = now
            self.session.flush()
            return MpzpPublicationResult(
                new, changed, unchanged, run.id, data_release.id
            )

    def _source_row(self) -> DataSource:
        row = self.session.execute(
            select(DataSource).where(DataSource.source_id == self.source.source_id)
        ).scalar_one_or_none()
        if row is None:
            row = DataSource(
                source_id=self.source.source_id,
                owner=self.source.owner,
                status=self.source.status.value,
                access_type=self.source.access_type.value,
                license=self.source.license,
                attribution=self.source.attribution,
            )
            self.session.add(row)
            self.session.flush()
        return row

    def _artifact_row(
        self,
        source_id: int,
        uri: str,
        media_type: str,
        content_hash: str,
        size_bytes: int,
    ) -> SourceArtifact:
        artifact = self.session.execute(
            select(SourceArtifact).where(
                SourceArtifact.data_source_id == source_id,
                SourceArtifact.content_hash == content_hash,
            )
        ).scalar_one_or_none()
        if artifact is None:
            artifact = SourceArtifact(
                data_source_id=source_id,
                uri=uri,
                media_type=media_type,
                content_hash=content_hash,
                size_bytes=size_bytes,
                fetched_at=datetime.now(timezone.utc),
            )
            self.session.add(artifact)
            self.session.flush()
        return artifact

    def _release_row(
        self,
        source_id: int,
        release: ImportRelease,
        artifact_hash: str,
    ) -> DataRelease:
        version_label = f"{release.version_label}-{artifact_hash[:12]}"
        row = self.session.execute(
            select(DataRelease).where(
                DataRelease.data_source_id == source_id,
                DataRelease.version_label == version_label,
            )
        ).scalar_one_or_none()
        if row is None:
            row = DataRelease(
                data_source_id=source_id,
                version_label=version_label,
                published_at=release.published_at,
                importer_version="imports-v1",
                is_active=False,
            )
            self.session.add(row)
            self.session.flush()
        return row

    def _run_row(
        self, source_id: int, release_id: int, artifact_hash: str
    ) -> ImportRun:
        row = ImportRun(
            data_source_id=source_id,
            data_release_id=release_id,
            status="running",
            importer_version="imports-v1",
            checkpoint={"artifact_hash": artifact_hash},
            started_at=datetime.now(timezone.utc),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def _activate_release(
        self, source_id: int, data_release: DataRelease
    ) -> None:
        self.session.execute(
            update(DataRelease)
            .where(
                DataRelease.data_source_id == source_id,
                DataRelease.id != data_release.id,
                DataRelease.is_active.is_(True),
            )
            .values(is_active=False)
        )
        self.session.flush()
        data_release.is_active = True


def find_plan_intersections(
    session: Session, parcel_geometry: GeometryPayload
) -> list[dict[str, int | str | float]]:
    """Zwraca wszystkie akty przecinające działkę i rzeczywiste pola przecięć."""
    rows = session.execute(
        text(
            """
            SELECT pa.id, pa.act_identifier,
                   ST_Area(ST_Intersection(
                       pb.geometry, ST_GeomFromText(:parcel_wkt, 2180)
                   )) AS intersection_area_sqm
            FROM planning_acts pa
            JOIN planning_act_versions pav ON pav.planning_act_id=pa.id
              AND pav.valid_to IS NULL
              AND pav.legal_status <> 'raster_only'
            JOIN plan_boundaries pb ON pb.planning_act_version_id=pav.id
            WHERE pb.geometry && ST_GeomFromText(:parcel_wkt, 2180)
              AND ST_Intersects(pb.geometry, ST_GeomFromText(:parcel_wkt, 2180))
            ORDER BY pa.act_identifier
            """
        ),
        {"parcel_wkt": parcel_geometry.wkt},
    ).mappings()
    return [
        {
            "id": int(row["id"]),
            "act_identifier": str(row["act_identifier"]),
            "intersection_area_sqm": float(row["intersection_area_sqm"]),
        }
        for row in rows
    ]


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return item()
        except (TypeError, ValueError):
            pass
    try:
        json.dumps(value)
    except TypeError:
        return str(value)
    return value
