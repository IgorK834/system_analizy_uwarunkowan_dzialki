"""PostGIS/SQLAlchemy: naprawa geometrii i atomowa publikacja wydań."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any

from geoalchemy2.elements import WKTElement
from pyproj import Transformer
from shapely import from_wkt
from shapely.ops import transform
from sqlalchemy import func, select, text, update
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
    PlanningFeature,
    PogActMetadataRecord,
    PogFormalDocument,
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
from app.modules.imports.application.pog_import import (
    PogPublicationResult,
    PogSourceBatch,
)
from app.modules.imports.domain.mpzp import PlanningActRecord
from app.modules.imports.domain.pog import PogActRecord
from app.modules.imports.infrastructure.artifacts import LocalArtifactStore
from app.modules.imports.infrastructure.pog_aggregates import (
    aggregate_warnings,
    compute_pog_area_summaries,
)
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
                    document_url=act_record.document_url,
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
                            zone_identifier=(
                                zone.zone_identifier
                                or f"{act_record.act_identifier}:{zone.original_symbol}"
                            ),
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

    def publish_pog(
        self,
        *,
        release: ImportRelease,
        batch: PogSourceBatch,
        artifact_hash: str,
        acts: tuple[tuple[PogActRecord, str], ...],
        stats: MutableImportStats,
        warnings: tuple[str, ...],
    ) -> PogPublicationResult:
        """Publikuje akty POG (kind='pog') z czterema warstwami i statusem prawnym."""
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
            # Serializuje publikacje tego samego źródła w obrębie transakcji.
            self.session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:scope))"),
                {"scope": f"pog-release:{self.source.source_id}"},
            )
            source = self._source_row()
            artifact = self._artifact_row(
                source.id, uri, batch.media_type, artifact_hash, len(batch.content)
            )
            data_release = self._release_row(
                source.id,
                release,
                artifact_hash,
                parser_config_id=batch.parser_config_id,
            )
            run = self._run_row(source.id, data_release.id, artifact_hash)
            now = datetime.now(timezone.utc)
            new = changed = unchanged = carried_forward = 0
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
                        kind="pog",
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
                existing_in_release = self.session.execute(
                    select(PlanningActVersion).where(
                        PlanningActVersion.planning_act_id == act.id,
                        PlanningActVersion.data_release_id == data_release.id,
                        PlanningActVersion.content_hash == snapshot_hash,
                    )
                ).scalar_one_or_none()
                if existing_in_release is not None:
                    # Ponowny import identycznego artefaktu (to samo wydanie):
                    # wersja już należy do tego wydania — nic nie dopisujemy.
                    unchanged += 1
                    continue
                if latest is not None and latest.content_hash == snapshot_hash:
                    # Treść aktu bez zmian, ale wydanie jest inne: wydanie musi
                    # być kompletnym, odtwarzalnym snapshotem wszystkich aktów
                    # paczki (ADR-008), więc akt dostaje w nim własną wersję o
                    # tym samym content_hash. Poprzednia wersja zostaje w swoim
                    # wydaniu (audyt, rollback), ale przestaje być aktywna.
                    unchanged += 1
                    carried_forward += 1
                elif latest is not None:
                    changed += 1
                else:
                    new += 1
                if latest is not None:
                    latest.valid_to = now
                # Akt niewiążący (projekt/w trakcie) wymaga ręcznej weryfikacji i
                # nigdy nie jest źródłem obowiązujących ustaleń — status prawny
                # jest przenoszony bez zmian.
                binding = act_record.is_binding
                version = PlanningActVersion(
                    planning_act_id=act.id,
                    legal_status=act_record.legal_status,
                    raw_legal_status=act_record.raw_legal_status,
                    object_version_id=(
                        act_record.object_id.version_id if act_record.object_id else None
                    ),
                    version_label=release.version_label,
                    resolution_number=act_record.resolution_number,
                    resolution_date=act_record.resolution_date,
                    name=act_record.name,
                    publication_id=act_record.publication_id,
                    version_started_at=act_record.version_started_at,
                    legal_valid_from=act_record.valid_from,
                    legal_valid_to=act_record.valid_to,
                    source_reference=act_record.source_reference,
                    manual_review_required=not binding,
                    source_artifact_id=artifact.id,
                    data_release_id=data_release.id,
                    valid_from=now,
                    valid_to=None,
                    published_at=release.published_at,
                    content_hash=snapshot_hash,
                    review_status="verified" if binding else "unreviewed",
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
                for feature in act_record.features:
                    parameters = (
                        feature.parameters.values() if feature.parameters else None
                    )
                    self.session.add(
                        PlanningFeature(
                            planning_act_version_id=version.id,
                            feature_type=feature.feature_type,
                            feature_identifier=feature.stable_id,
                            feature_version=(
                                feature.object_id.version_id if feature.object_id else None
                            ),
                            act_reference=(
                                feature.act_reference.href if feature.act_reference else None
                            ),
                            source_reference=feature.source_reference,
                            raw_legal_status=feature.raw_legal_status,
                            symbol=feature.symbol,
                            label=feature.label,
                            parameters=_jsonable(parameters),
                            primary_profiles=_jsonable(feature.primary_profiles),
                            additional_profiles=_jsonable(feature.additional_profiles),
                            raw_attributes=_jsonable(feature.raw_attributes),
                            geometry=WKTElement(feature.geometry.wkt, srid=2180),
                        )
                    )
                for document in act_record.documents:
                    self.session.add(PogFormalDocument(
                        planning_act_version_id=version.id,
                        document_identifier=document.object_id.stable_id,
                        document_version=document.object_id.version_id,
                        act_reference=(document.act_reference.href if document.act_reference else None),
                        title=document.title,
                        link=document.link,
                        source_reference=document.source_reference,
                        raw_attributes=_jsonable(document.raw_attributes),
                        publication_id=document.publication_id,
                        short_name=document.short_name,
                        identification_number=document.identification_number,
                        relation=document.relation,
                        document_date=document.document_date,
                        effective_date=document.effective_date,
                        repeal_date=document.repeal_date,
                        record_sha256=document.record_sha256,
                        link_verified=document.link_verified,
                        resolution_status=document.resolution_status,
                        resolution_note=document.resolution_note,
                    ))
                for record in act_record.metadata:
                    self.session.add(PogActMetadataRecord(
                        planning_act_version_id=version.id,
                        record_id=record.record_id,
                        resource_identifier=record.resource_identifier,
                        title=record.title,
                        publication_date=record.publication_date,
                        revision_date=record.revision_date,
                        creation_date=record.creation_date,
                        date_stamp=record.date_stamp,
                        metadata_url=record.metadata_url,
                        reference_urls=list(record.references),
                        record_sha256=record.record_sha256,
                        response_sha256=record.response_sha256,
                        fetched_at=record.fetched_at,
                    ))
            self.session.flush()
            # BK-405: agregaty powierzchniowe stref liczone w 2180 na pełnym
            # snapshocie wydania, PRZED aktywacją i w tej samej transakcji —
            # błąd obliczeń wycofuje całą publikację, a aktywne wydanie i jego
            # agregaty przełączają się atomowo.
            aggregates = compute_pog_area_summaries(
                self.session, data_release.id, computed_at=now
            )
            incomplete = aggregate_warnings(aggregates)
            # Także idempotentny powrót do istniejącego wydania przełącza je
            # atomowo bez dublowania wersji obiektów.
            self._activate_release(source.id, data_release)
            final_stats = stats.as_dict() | {
                "new": new,
                "changed": changed,
                "unchanged": unchanged,
                "carried_forward": carried_forward,
                "area_summaries": len(aggregates),
                "area_summaries_incomplete": len(incomplete),
            }
            run.status = "succeeded"
            run.stats = final_stats
            run.checkpoint = {
                "artifact_hash": artifact_hash,
                "processed": len(acts),
                "warnings": [*warnings, *incomplete],
            }
            run.finished_at = now
            self.session.flush()
            return PogPublicationResult(
                new,
                changed,
                unchanged,
                run.id,
                data_release.id,
                carried_forward,
                area_summaries=len(aggregates),
                aggregate_warnings=incomplete,
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
        parser_config_id: str | None = None,
    ) -> DataRelease:
        config = parser_config_id or "imports-v1"
        fingerprint = hashlib.sha256(
            f"{artifact_hash}:{config}".encode("utf-8")
        ).hexdigest()[:12]
        version_label = (
            f"pog-{fingerprint}"
            if parser_config_id is not None
            else f"{release.version_label}-{artifact_hash[:12]}"
        )
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
                importer_version=config,
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
        self.session.flush()

    def previous_pog_feature_count(self) -> int | None:
        """Liczność cech aktywnego wydania POG dla kontroli regresji importu."""
        release_id = self.session.execute(
            select(DataRelease.id)
            .join(DataSource, DataRelease.data_source_id == DataSource.id)
            .where(
                DataSource.source_id == self.source.source_id,
                DataRelease.is_active.is_(True),
            )
        ).scalar_one_or_none()
        if release_id is None:
            return None
        count = self.session.execute(
            select(func.count(PlanningFeature.id))
            .join(
                PlanningActVersion,
                PlanningFeature.planning_act_version_id == PlanningActVersion.id,
            )
            .where(PlanningActVersion.data_release_id == release_id)
        ).scalar_one()
        return int(count)


_MPZP_VERSION_AS_OF_SQL = """
  AND pav.valid_from <= CAST(:as_of AS timestamptz)
  AND (pav.valid_to IS NULL OR pav.valid_to > CAST(:as_of AS timestamptz))
  AND (CAST(:release_id AS integer) IS NULL OR pav.data_release_id = CAST(:release_id AS integer))
"""


def find_plan_intersections(
    session: Session,
    parcel_geometry: GeometryPayload,
    *,
    as_of: datetime | None = None,
    data_release_id: int | None = None,
) -> list[dict[str, Any]]:
    """Zwraca akty MPZP, których granica przecina działkę, z polem przecięcia.

    Wersje są wybierane jawnie na chwilę ``as_of`` (domyślnie: teraz) i
    opcjonalnie zawężane do ``data_release_id``. Filtr ``kind='mpzp'``
    gwarantuje, że akty POG nie przenikają do analizy MPZP.
    """
    rows = session.execute(
        text(
            """
            WITH parcel AS (SELECT ST_GeomFromText(:parcel_wkt, 2180) AS g)
            SELECT pa.id, pa.act_identifier, pav.id AS act_version_id,
                   pav.content_hash AS act_version, pav.data_release_id,
                   pav.legal_status, pav.document_url,
                   ST_Area(ST_Intersection(pb.geometry, parcel.g)) AS intersection_area_sqm
            FROM planning_acts pa
            JOIN planning_act_versions pav ON pav.planning_act_id = pa.id
              AND pav.legal_status <> 'raster_only'
            JOIN plan_boundaries pb ON pb.planning_act_version_id = pav.id
            CROSS JOIN parcel
            WHERE pa.kind = 'mpzp'
              AND pb.geometry && parcel.g
              AND ST_Intersects(pb.geometry, parcel.g)
            """
            + _MPZP_VERSION_AS_OF_SQL
            + """
            ORDER BY pa.act_identifier
            """
        ),
        {
            "parcel_wkt": parcel_geometry.wkt,
            "as_of": as_of or datetime.now(timezone.utc),
            "release_id": data_release_id,
        },
    ).mappings()
    return [
        {
            **dict(row),
            "id": int(row["id"]),
            "act_identifier": str(row["act_identifier"]),
            "intersection_area_sqm": float(row["intersection_area_sqm"]),
        }
        for row in rows
    ]


def find_mpzp_zone_intersections(
    session: Session,
    parcel_geometry: GeometryPayload,
    *,
    as_of: datetime,
    data_release_id: int | None = None,
) -> list[dict[str, Any]]:
    """Wszystkie wydzielenia MPZP przecinające pełny obrys działki (BK-202).

    Kandydaci są wybierani przez indeks GiST (``&&``), potwierdzani
    ``ST_Intersects``, a pole liczone jest z ``ST_Intersection`` i ``ST_Area``
    w EPSG:2180. Zwracane są także wydzielenia jedynie stykające się z działką
    (pole ~0) — rozdzielenie styczności od realnego przecięcia należy do
    serwisu. Wersje aktów wybiera jawny ``as_of`` i opcjonalny
    ``data_release_id``, więc późniejsze przełączenie wydania nie zmienia
    wyniku dla tego samego ``as_of``.
    """
    rows = session.execute(
        text(
            """
            WITH parcel AS (SELECT ST_GeomFromText(:parcel_wkt, 2180) AS g)
            SELECT lua.zone_identifier, lua.symbol, ps.normalized_category,
                   pa.act_identifier, pav.id AS act_version_id,
                   pav.content_hash AS act_version, pav.version_label,
                   pav.data_release_id, pav.resolution_number, pav.resolution_date,
                   pav.name AS act_name, pav.document_url, pav.legal_status,
                   sa.content_hash AS artifact_sha256, sa.fetched_at, sa.uri AS artifact_uri,
                   ds.source_id,
                   ST_Area(ST_Intersection(lua.geometry, parcel.g)) AS intersection_area_sqm,
                   ST_AsText(ST_CollectionExtract(
                       ST_Intersection(lua.geometry, parcel.g), 3
                   )) AS intersection_wkt
            FROM land_use_areas lua
            JOIN planning_act_versions pav ON pav.id = lua.planning_act_version_id
            JOIN planning_acts pa ON pa.id = pav.planning_act_id AND pa.kind = 'mpzp'
            LEFT JOIN planning_symbols ps ON ps.id = lua.planning_symbol_id
            JOIN source_artifacts sa ON sa.id = pav.source_artifact_id
            JOIN data_sources ds ON ds.id = sa.data_source_id
            CROSS JOIN parcel
            WHERE lua.geometry && parcel.g
              AND ST_Intersects(lua.geometry, parcel.g)
            """
            + _MPZP_VERSION_AS_OF_SQL
            + """
            ORDER BY intersection_area_sqm DESC, lua.zone_identifier
            """
        ),
        {
            "parcel_wkt": parcel_geometry.wkt,
            "as_of": as_of,
            "release_id": data_release_id,
        },
    ).mappings()
    return [
        {**dict(row), "intersection_area_sqm": float(row["intersection_area_sqm"])}
        for row in rows
    ]


def active_pog_release(session: Session, source_id: str = "pog_app") -> dict[str, Any] | None:
    """Przypina aktywne wydanie i jego artefakt na początku analizy."""
    row = session.execute(
        select(
            DataRelease.id,
            DataRelease.version_label,
            DataRelease.importer_version,
            SourceArtifact.content_hash,
            SourceArtifact.fetched_at,
        )
        .join(DataSource, DataRelease.data_source_id == DataSource.id)
        .join(
            PlanningActVersion,
            PlanningActVersion.data_release_id == DataRelease.id,
        )
        .join(
            SourceArtifact,
            PlanningActVersion.source_artifact_id == SourceArtifact.id,
        )
        .where(
            DataSource.source_id == source_id,
            DataRelease.is_active.is_(True),
        )
        .limit(1)
    ).mappings().one_or_none()
    return dict(row) if row is not None else None


def load_pog_release_features(
    session: Session,
    parcel_geometry: GeometryPayload,
    *,
    data_release_id: int,
) -> list[dict[str, Any]]:
    """Czyta wszystkie cztery warstwy z jednego, jawnie przypiętego wydania.

    Zwraca obiekty aktów w każdym kanonicznym statusie prawnym (BK-106).
    Rozstrzygnięcie, czy akt jest wiążący, należy do wspólnego mappera statusu,
    a nie do filtra SQL — dzięki temu projekt nie znika z wyniku, ale też nie
    jest prezentowany jako obowiązujący.
    """
    rows = session.execute(
        text(
            """
            SELECT pa.act_identifier, pa.teryt, pav.id AS act_version_id,
                   pav.legal_status, pav.raw_legal_status, pav.object_version_id,
                   pav.name AS act_name, pav.resolution_number, pav.resolution_date,
                   pf.feature_type, pf.feature_identifier, pf.feature_version,
                   pf.act_reference, pf.source_reference, pf.raw_legal_status AS feature_raw_legal_status,
                   pf.symbol, pf.label, pf.parameters, pf.primary_profiles,
                   pf.additional_profiles, pf.raw_attributes,
                   ST_AsText(pf.geometry) AS geometry_wkt
            FROM planning_act_versions pav
            JOIN planning_acts pa ON pa.id = pav.planning_act_id
            JOIN planning_features pf ON pf.planning_act_version_id = pav.id
            WHERE pa.kind = 'pog'
              AND pav.data_release_id = :release_id
              AND pf.geometry && ST_GeomFromText(:parcel_wkt, 2180)
              AND ST_Intersects(pf.geometry, ST_GeomFromText(:parcel_wkt, 2180))
            ORDER BY pa.act_identifier, pf.feature_type, pf.feature_identifier NULLS LAST
            """
        ),
        {"release_id": data_release_id, "parcel_wkt": parcel_geometry.wkt},
    ).mappings()
    return [dict(row) for row in rows]


def find_pog_acts_for_parcel(
    session: Session,
    parcel_geometry: GeometryPayload,
    *,
    data_release_id: int,
    teryt: str | None = None,
) -> list[dict[str, Any]]:
    """Zwraca akty POG wydania dotyczące działki wraz z faktami o pokryciu.

    Akt dotyczy działki, gdy jego granica lub obiekt przecina działkę albo —
    dla aktu bez danych przestrzennych — gdy TERYT aktu jest prefiksem TERYT
    działki. Wynik zasila tabelę decyzyjną BK-106: ``has_spatial_data``
    odróżnia akt bez geometrii od aktu z danymi niepokrywającymi działki.
    """
    rows = session.execute(
        text(
            """
            WITH parcel AS (SELECT ST_GeomFromText(:parcel_wkt, 2180) AS geom)
            SELECT pav.id AS act_version_id, pa.act_identifier, pa.teryt,
                   pav.legal_status, pav.raw_legal_status, pav.object_version_id,
                   sa.fetched_at AS status_confirmed_at,
                   EXISTS (
                     SELECT 1 FROM plan_boundaries pb
                     WHERE pb.planning_act_version_id = pav.id
                   ) AS has_boundary,
                   EXISTS (
                     SELECT 1 FROM plan_boundaries pb, parcel
                     WHERE pb.planning_act_version_id = pav.id
                       AND ST_Intersects(pb.geometry, parcel.geom)
                   ) AS boundary_intersects,
                   (SELECT count(*) FROM planning_features pf
                     WHERE pf.planning_act_version_id = pav.id) AS feature_count,
                   (SELECT count(*) FROM planning_features pf, parcel
                     WHERE pf.planning_act_version_id = pav.id
                       AND pf.geometry && parcel.geom
                       AND ST_Intersects(pf.geometry, parcel.geom)) AS features_on_parcel
            FROM planning_act_versions pav
            JOIN planning_acts pa ON pa.id = pav.planning_act_id
            JOIN source_artifacts sa ON sa.id = pav.source_artifact_id
            WHERE pa.kind = 'pog'
              AND pav.data_release_id = :release_id
            ORDER BY pa.act_identifier
            """
        ),
        {"release_id": data_release_id, "parcel_wkt": parcel_geometry.wkt},
    ).mappings()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        spatial = bool(item["boundary_intersects"]) or int(item["features_on_parcel"]) > 0
        by_teryt = bool(
            teryt and item.get("teryt") and str(teryt).startswith(str(item["teryt"]))
        )
        has_spatial_data = bool(item["has_boundary"]) or int(item["feature_count"]) > 0
        if spatial or (by_teryt and not has_spatial_data):
            item["has_spatial_data"] = has_spatial_data
            result.append(item)
    return result


def last_confirmed_pog_status(
    session: Session,
    parcel_geometry: GeometryPayload,
    *,
    teryt: str | None = None,
    source_id: str = "pog_app",
) -> dict[str, Any] | None:
    """Ostatnia potwierdzona wartość statusu aktu dla działki (dowolne wydanie).

    Używana wyłącznie przy awarii źródła: wynik ma zachować datę potwierdzenia
    i dostępność ``stale``. Wartości ``unknown`` nie są „potwierdzeniem”.
    """
    row = session.execute(
        text(
            """
            SELECT pa.act_identifier, pav.legal_status, pav.raw_legal_status,
                   pav.object_version_id, pav.data_release_id,
                   sa.fetched_at AS status_confirmed_at, sa.content_hash
            FROM planning_act_versions pav
            JOIN planning_acts pa ON pa.id = pav.planning_act_id
            JOIN source_artifacts sa ON sa.id = pav.source_artifact_id
            JOIN data_releases dr ON dr.id = pav.data_release_id
            JOIN data_sources ds ON ds.id = dr.data_source_id
            WHERE pa.kind = 'pog'
              AND ds.source_id = :source_id
              AND pav.legal_status IS NOT NULL
              AND pav.legal_status <> 'unknown'
              AND pav.raw_legal_status IS NOT NULL
              AND (
                EXISTS (
                  SELECT 1 FROM plan_boundaries pb
                  WHERE pb.planning_act_version_id = pav.id
                    AND ST_Intersects(pb.geometry, ST_GeomFromText(:parcel_wkt, 2180))
                )
                OR EXISTS (
                  SELECT 1 FROM planning_features pf
                  WHERE pf.planning_act_version_id = pav.id
                    AND pf.geometry && ST_GeomFromText(:parcel_wkt, 2180)
                    AND ST_Intersects(pf.geometry, ST_GeomFromText(:parcel_wkt, 2180))
                )
                OR (CAST(:teryt AS text) IS NOT NULL AND pa.teryt IS NOT NULL
                    AND CAST(:teryt AS text) LIKE pa.teryt || '%')
              )
            ORDER BY sa.fetched_at DESC, pav.id DESC
            LIMIT 1
            """
        ),
        {"parcel_wkt": parcel_geometry.wkt, "teryt": teryt, "source_id": source_id},
    ).mappings().one_or_none()
    return dict(row) if row is not None else None


def load_pog_act_provenance(
    session: Session, act_version_id: int
) -> dict[str, Any] | None:
    """Czyta zamrożony łańcuch wersja aktu → metadane CSW → dokumenty.

    Dane pochodzą wyłącznie z wersji aktu zapisanej w wydaniu; odczyt nie
    odpytuje bieżącego katalogu ani usług RU.
    """
    act = session.execute(
        text(
            """
            SELECT pav.id AS act_version_id, pa.act_identifier, pa.teryt,
                   pav.object_version_id, pav.publication_id, pav.version_started_at,
                   pav.legal_valid_from, pav.legal_valid_to, pav.source_reference,
                   pav.name AS title, pav.resolution_number, pav.resolution_date,
                   pav.data_release_id, sa.content_hash AS artifact_sha256,
                   sa.fetched_at AS artifact_fetched_at, dr.version_label AS release_label
            FROM planning_act_versions pav
            JOIN planning_acts pa ON pa.id = pav.planning_act_id
            JOIN source_artifacts sa ON sa.id = pav.source_artifact_id
            JOIN data_releases dr ON dr.id = pav.data_release_id
            WHERE pav.id = :id
            """
        ),
        {"id": act_version_id},
    ).mappings().one_or_none()
    if act is None:
        return None
    documents = session.execute(
        select(PogFormalDocument)
        .where(PogFormalDocument.planning_act_version_id == act_version_id)
        .order_by(PogFormalDocument.document_identifier, PogFormalDocument.document_version)
    ).scalars().all()
    metadata = session.execute(
        select(PogActMetadataRecord)
        .where(PogActMetadataRecord.planning_act_version_id == act_version_id)
        .order_by(PogActMetadataRecord.record_id)
    ).scalars().all()
    return {
        **dict(act),
        "documents": [
            {
                "document_identifier": doc.document_identifier,
                "document_version": doc.document_version,
                "publication_id": doc.publication_id,
                "title": doc.title,
                "short_name": doc.short_name,
                "identification_number": doc.identification_number,
                "relation": doc.relation,
                "document_date": doc.document_date,
                "effective_date": doc.effective_date,
                "repeal_date": doc.repeal_date,
                "link": doc.link,
                "link_verified": doc.link_verified,
                "record_sha256": doc.record_sha256,
                "resolution_status": doc.resolution_status,
                "resolution_note": doc.resolution_note,
            }
            for doc in documents
        ],
        "metadata": [
            {
                "record_id": record.record_id,
                "resource_identifier": record.resource_identifier,
                "title": record.title,
                "publication_date": record.publication_date,
                "revision_date": record.revision_date,
                "creation_date": record.creation_date,
                "date_stamp": record.date_stamp,
                "metadata_url": record.metadata_url,
                "references": list(record.reference_urls or []),
                "record_sha256": record.record_sha256,
                "response_sha256": record.response_sha256,
                "fetched_at": record.fetched_at,
            }
            for record in metadata
        ],
    }


def find_pog_intersections(
    session: Session,
    parcel_geometry: GeometryPayload,
    *,
    data_release_id: int | None = None,
    legal_statuses: tuple[str, ...] = ("binding",),
) -> list[dict[str, int | str | float]]:
    """Zwraca akty POG w podanych statusach przecinające działkę wraz z polami.

    Domyślnie zwracane są WYŁĄCZNIE akty ``binding`` (obowiązujące, potwierdzone
    urzędowym kodem statusu). Projekty i akty w trakcie sporządzania nie są
    prezentowane jako wiążące ograniczenie planistyczne. Pole przecięcia liczone
    jest na obiektach warstw POG (``planning_features``) rozbite na typ warstwy.
    """
    if data_release_id is None:
        pinned = active_pog_release(session)
        if pinned is None:
            return []
        data_release_id = int(pinned["id"])
    rows = session.execute(
        text(
            """
            SELECT pa.id, pa.act_identifier, pf.feature_type,
                   ST_Area(ST_Intersection(
                       pf.geometry, ST_GeomFromText(:parcel_wkt, 2180)
                   )) AS intersection_area_sqm
            FROM planning_acts pa
            JOIN planning_act_versions pav ON pav.planning_act_id=pa.id
              AND pav.data_release_id = :release_id
              AND pav.legal_status = ANY(:legal_statuses)
            JOIN planning_features pf ON pf.planning_act_version_id=pav.id
            WHERE pa.kind = 'pog'
              AND pf.geometry && ST_GeomFromText(:parcel_wkt, 2180)
              AND ST_Intersects(pf.geometry, ST_GeomFromText(:parcel_wkt, 2180))
            ORDER BY pa.act_identifier, pf.feature_type
            """
        ),
        {
            "parcel_wkt": parcel_geometry.wkt,
            "release_id": data_release_id,
            "legal_statuses": list(legal_statuses),
        },
    ).mappings()
    return [
        {
            "id": int(row["id"]),
            "act_identifier": str(row["act_identifier"]),
            "feature_type": str(row["feature_type"]),
            "intersection_area_sqm": float(row["intersection_area_sqm"]),
        }
        for row in rows
    ]


def _jsonable(value: Any) -> Any:
    # Profile funkcjonalne i inne rekordy domenowe są dataclassami — zapisujemy
    # je jako obiekty JSON (kod, etykieta, źródło słownika), a nie jako ``repr``.
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _jsonable(dataclasses.asdict(value))
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
