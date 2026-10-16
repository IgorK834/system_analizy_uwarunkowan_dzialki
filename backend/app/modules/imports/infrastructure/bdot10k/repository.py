"""PostGIS: atomowa publikacja wydania BDOT10k per powiat (zakres ``powiat:<TERYT>``).

Wydanie to jedna transakcja: wiersze ``bdot_roads``/``bdot_buildings``, opis ``bdot_releases`` z zasięgiem powiatu i
aktywacja wydania. Aktywacja wyłącza wyłącznie poprzednie wydanie tego samego powiatu; pozostałe powiaty zostają
aktywne. Ponowny import tej samej paczki (SHA-256) nie dopisuje obiektów — wydanie jest rozpoznawane po etykiecie
``bdot10k-<TERYT>-<sha12>`` i jedynie wraca do stanu aktywnego.
"""

from __future__ import annotations

from datetime import datetime, timezone
from itertools import islice
from typing import Any, Iterable, Iterator, TypeVar, cast

from sqlalchemy import LargeBinary, Table, bindparam, func, insert, select, text, update
from sqlalchemy.orm import Session

from app.core.data_sources import DataSourceEntry
from app.models.bdot10k import BdotBuilding, BdotRelease, BdotRoad
from app.models.versioned import DataRelease, DataSource, ImportRun
from app.modules.imports.application.bdot10k_import import (
    Bdot10kPublication,
    Bdot10kPublicationResult,
)
from app.modules.imports.domain.bdot10k import BDOT10K_SOURCE_ID, scope_key
from app.modules.imports.infrastructure.artifacts import LocalArtifactStore
from app.modules.imports.infrastructure.repository import SqlAlchemyImportRepository

BDOT10K_IMPORTER_VERSION = "bdot10k-import/1"
BATCH_SIZE = 5_000
_T = TypeVar("_T")


def release_label(teryt_powiat: str, package_sha256: str) -> str:
    """Etykieta wydania: ten sam powiat i ta sama paczka zawsze dają tę samą etykietę."""
    return f"{BDOT10K_SOURCE_ID}-{teryt_powiat}-{package_sha256[:12]}"


def _batches(items: Iterable[_T], size: int) -> Iterator[list[_T]]:
    iterator = iter(items)
    while batch := list(islice(iterator, size)):
        yield batch


class SqlAlchemyBdot10kRepository(SqlAlchemyImportRepository):
    """Jednostka pracy importu BDOT10k (współdzieli wiersze źródła, artefaktu i przebiegu z innymi importerami)."""

    def __init__(self, session: Session, source: DataSourceEntry, artifact_store: LocalArtifactStore) -> None:
        super().__init__(session, source, artifact_store)

    def previous_counts(self, teryt_powiat: str) -> tuple[int, int] | None:
        row = self.session.execute(
            select(BdotRelease.road_count, BdotRelease.building_count)
            .join(DataRelease, DataRelease.id == BdotRelease.data_release_id)
            .join(DataSource, DataSource.id == DataRelease.data_source_id)
            .where(
                DataSource.source_id == self.source.source_id,
                DataRelease.is_active.is_(True),
                DataRelease.scope_key == scope_key(teryt_powiat),
            )
        ).first()
        return (int(row[0]), int(row[1])) if row is not None else None

    def publish_bdot10k(self, publication: Bdot10kPublication) -> Bdot10kPublicationResult:
        batch = publication.batch
        teryt = batch.teryt_powiat
        scope = scope_key(teryt)
        uri = self.artifact_store.save_file(
            source_id=publication.release.source_id,
            content_hash=batch.package_sha256,
            filename=batch.package_name,
            path=batch.package_path,
        )
        transaction = self.session.begin_nested() if self.session.in_transaction() else self.session.begin()
        with transaction:
            # Serializuje publikacje tego samego powiatu; inne powiaty importują się równolegle.
            self.session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:scope))"),
                {"scope": f"bdot10k-release:{self.source.source_id}:{scope}"},
            )
            source = self._source_row()
            artifact = self._artifact_row(source.id, uri, "application/zip", batch.package_sha256, batch.size_bytes)
            label = release_label(teryt, batch.package_sha256)
            data_release = self.session.execute(
                select(DataRelease).where(
                    DataRelease.data_source_id == source.id, DataRelease.version_label == label
                )
            ).scalar_one_or_none()
            created = data_release is None
            run = ImportRun(
                data_source_id=source.id,
                data_release_id=None,
                status="running",
                importer_version=BDOT10K_IMPORTER_VERSION,
                checkpoint={"artifact_hash": batch.package_sha256, "artifact_id": artifact.id},
                started_at=datetime.now(timezone.utc),
            )
            self.session.add(run)
            self.session.flush()
            if data_release is None:
                data_release = DataRelease(
                    data_source_id=source.id,
                    version_label=label,
                    published_at=publication.release.published_at,
                    importer_version=BDOT10K_IMPORTER_VERSION,
                    scope_key=scope,
                    is_active=False,
                )
                self.session.add(data_release)
                self.session.flush()
                self._insert_objects(data_release.id, teryt, publication)
            run.data_release_id = data_release.id
            was_active = bool(data_release.is_active)
            self._activate_scoped(source.id, scope, data_release)
            run.status = "succeeded"
            run.stats = {
                "created": created,
                "roads": len(publication.roads),
                "buildings": len(publication.buildings),
                "classes": dict(publication.class_stats),
            }
            run.finished_at = datetime.now(timezone.utc)
            self.session.flush()
            return Bdot10kPublicationResult(
                data_release_id=data_release.id,
                import_run_id=run.id,
                created=created,
                activated=not was_active,
            )

    def _insert_objects(self, release_id: int, teryt: str, publication: Bdot10kPublication) -> None:
        batch = publication.batch
        self.session.add(
            BdotRelease(
                data_release_id=release_id,
                teryt_powiat=teryt,
                powiat_name=publication.extent.name,
                package_name=batch.package_name,
                package_sha256=batch.package_sha256,
                data_as_of=publication.data_as_of,
                road_count=len(publication.roads),
                building_count=len(publication.buildings),
                class_stats=dict(publication.class_stats),
                extent=func.ST_SetSRID(func.ST_GeomFromWKB(publication.extent.geometry_wkb), 2180),
            )
        )
        self.session.flush()
        road_stmt = insert(cast(Table, BdotRoad.__table__)).values(
            geometry=func.ST_SetSRID(func.ST_GeomFromWKB(bindparam("geometry_wkb", type_=LargeBinary)), 2180)
        )
        for road_chunk in _batches(publication.roads, BATCH_SIZE):
            self.session.execute(
                road_stmt,
                [
                    {
                        "data_release_id": release_id,
                        "local_id": row.local_id,
                        "existence_category": row.existence_category,
                        "management_category": row.management_category,
                        "road_class": row.road_class,
                        "surface_material": row.surface_material,
                        "road_position": row.road_position,
                        "road_number": row.road_number,
                        "street_name": row.street_name,
                        "surface_width_m": row.surface_width_m,
                        "feature_version_at": row.feature_version_at,
                        "geometry_wkb": row.geometry_wkb,
                    }
                    for row in road_chunk
                ],
            )
        building_stmt = insert(cast(Table, BdotBuilding.__table__)).values(
            geometry=func.ST_SetSRID(func.ST_GeomFromWKB(bindparam("geometry_wkb", type_=LargeBinary)), 2180)
        )
        for building_chunk in _batches(publication.buildings, BATCH_SIZE):
            self.session.execute(
                building_stmt,
                [
                    {
                        "data_release_id": release_id,
                        "local_id": row.local_id,
                        "existence_category": row.existence_category,
                        "general_function": row.general_function,
                        "detailed_function": row.detailed_function,
                        "predominant_function": row.predominant_function,
                        "floors": row.floors,
                        "geometry_source": row.geometry_source,
                        "feature_version_at": row.feature_version_at,
                        "area_sqm": row.area_sqm,
                        "geometry_wkb": row.geometry_wkb,
                    }
                    for row in building_chunk
                ],
            )

    def _activate_scoped(self, source_row_id: int, scope: str, data_release: DataRelease) -> None:
        """Aktywuje wydanie, wyłączając poprzednie wydanie tego samego zakresu (powiatu)."""
        self.session.execute(
            update(DataRelease)
            .where(
                DataRelease.data_source_id == source_row_id,
                DataRelease.scope_key == scope,
                DataRelease.id != data_release.id,
                DataRelease.is_active.is_(True),
            )
            .values(is_active=False)
        )
        self.session.flush()
        data_release.is_active = True
        self.session.flush()


def stats_for_release(session: Session, data_release_id: int) -> dict[str, Any] | None:
    """Liczności zapisane w wydaniu (do kontroli odtwarzalności: ten sam hash → te same liczby)."""
    row = session.execute(
        select(BdotRelease.road_count, BdotRelease.building_count, BdotRelease.package_sha256)
        .where(BdotRelease.data_release_id == data_release_id)
    ).first()
    if row is None:
        return None
    roads = session.execute(
        select(func.count()).select_from(BdotRoad).where(BdotRoad.data_release_id == data_release_id)
    ).scalar_one()
    buildings = session.execute(
        select(func.count()).select_from(BdotBuilding).where(BdotBuilding.data_release_id == data_release_id)
    ).scalar_one()
    return {
        "road_count": int(row[0]),
        "building_count": int(row[1]),
        "package_sha256": str(row[2]),
        "roads_in_table": int(roads),
        "buildings_in_table": int(buildings),
    }
