"""PostGIS/SQLAlchemy: zapis rastrów COG i ich ręcznej weryfikacji."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Mapping, Sequence

from geoalchemy2.elements import WKTElement
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.data_sources import DataSourceEntry
from app.models.versioned import (
    DataSource,
    ManualReview,
    RasterAsset,
    SourceArtifact,
)
from app.modules.imports.domain.raster import ControlPoint, GeoreferenceResult
from app.modules.imports.infrastructure.artifacts import LocalArtifactStore
from app.shared.geometry import GeometryPayload


class SqlAlchemyRasterRepository:
    """Adapter zapisu rastrów zgodny z protokołami import/review."""

    def __init__(
        self,
        session: Session,
        source: DataSourceEntry | None = None,
        artifact_store: LocalArtifactStore | None = None,
    ) -> None:
        # ``source`` i ``artifact_store`` są potrzebne tylko dla operacji importu
        # (save_artifact/persist_raster_asset). Operacje przeglądu (review) używają
        # wyłącznie sesji, więc mogą być pominięte w kontekście administracyjnym.
        self.session = session
        self.source = source
        self.artifact_store = artifact_store

    # --- RasterImportRepository --------------------------------------------

    def save_artifact(
        self, *, source_id: str, filename: str, content: bytes, media_type: str
    ) -> int:
        if self.artifact_store is None:
            raise RuntimeError("Zapis artefaktu wymaga skonfigurowanego magazynu.")
        content_hash = hashlib.sha256(content).hexdigest()
        uri = self.artifact_store.save(
            source_id=source_id,
            content_hash=content_hash,
            filename=filename,
            content=content,
        )
        source = self._source_row()
        artifact = self.session.execute(
            select(SourceArtifact).where(
                SourceArtifact.data_source_id == source.id,
                SourceArtifact.content_hash == content_hash,
            )
        ).scalar_one_or_none()
        if artifact is None:
            artifact = SourceArtifact(
                data_source_id=source.id,
                uri=uri,
                media_type=media_type,
                content_hash=content_hash,
                size_bytes=len(content),
                fetched_at=datetime.now(timezone.utc),
            )
            self.session.add(artifact)
            self.session.flush()
        return artifact.id

    def persist_raster_asset(
        self,
        *,
        planning_act_version_id: int | None,
        source_artifact_id: int,
        cog_artifact_id: int,
        transform_method: str,
        control_points: Sequence[ControlPoint],
        georeference: GeoreferenceResult,
        bounds: GeometryPayload,
        width_px: int,
        height_px: int,
        nodata: float | None,
        qa_report: Mapping[str, object],
    ) -> int:
        asset = RasterAsset(
            planning_act_version_id=planning_act_version_id,
            source_artifact_id=source_artifact_id,
            cog_artifact_id=cog_artifact_id,
            transform_method=transform_method,
            control_points=[
                {
                    "pixel_col": point.pixel_col,
                    "pixel_row": point.pixel_row,
                    "map_x": point.map_x,
                    "map_y": point.map_y,
                }
                for point in control_points
            ],
            rmse_m=georeference.rmse_m,
            pixel_size=georeference.pixel_size_m,
            nodata=nodata,
            width_px=width_px,
            height_px=height_px,
            bounds=WKTElement(bounds.wkt, srid=2180),
            # Import NIGDY nie ustawia 'verified' — akceptacja jest ręczna.
            review_status="unreviewed",
            qa_report=dict(qa_report),
        )
        self.session.add(asset)
        self.session.flush()
        return asset.id

    # --- RasterReviewRepository --------------------------------------------

    def review_status_of(self, raster_asset_id: int) -> str | None:
        asset = self.session.get(RasterAsset, raster_asset_id)
        return None if asset is None else asset.review_status

    def apply_review_decision(
        self,
        *,
        raster_asset_id: int,
        review_status: str,
        operator_id: str,
        reason: str,
    ) -> int:
        asset = self.session.get(RasterAsset, raster_asset_id)
        if asset is None:
            raise ValueError(f"Raster {raster_asset_id} nie istnieje.")
        # Aktualizacja statusu i audyt ManualReview w jednej transakcji sesji.
        asset.review_status = review_status
        review = ManualReview(
            subject_type="raster_asset",
            subject_id=raster_asset_id,
            reviewer=operator_id,
            decision=review_status,
            review_status=review_status,
            notes=reason,
        )
        self.session.add(review)
        self.session.flush()
        return review.id

    # --- pomocnicze ---------------------------------------------------------

    def _source_row(self) -> DataSource:
        if self.source is None:
            raise RuntimeError("Operacja wymaga skonfigurowanego źródła danych.")
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


def servable_raster_assets(session: Session) -> list[dict[str, object]]:
    """Zwraca WYŁĄCZNIE rastry zaakceptowane (``verified``) do serwowania.

    Twardy filtr w warstwie serwującej: raster bez ręcznej akceptacji nigdy nie
    jest publikowany do warstwy mapy.
    """
    rows = session.execute(
        select(
            RasterAsset.id,
            RasterAsset.cog_artifact_id,
            RasterAsset.review_status,
        ).where(RasterAsset.review_status == "verified")
    ).all()
    return [
        {
            "id": row.id,
            "cog_artifact_id": row.cog_artifact_id,
            "review_status": row.review_status,
        }
        for row in rows
    ]
