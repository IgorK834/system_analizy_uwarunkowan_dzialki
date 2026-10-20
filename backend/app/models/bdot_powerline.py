"""Wersjonowane osie linii energetycznych BDOT10k w EPSG:2180 (AU-201)."""
from datetime import datetime
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.types import ClippedString


class BdotPowerline(Base):
    __tablename__ = "bdot_powerlines"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    data_release_id: Mapped[int] = mapped_column(ForeignKey("data_releases.id"), nullable=False)
    local_id: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(ClippedString(160), nullable=False)
    existence_category: Mapped[str | None] = mapped_column(ClippedString(40))
    feature_version_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    geometry: Mapped[Any] = mapped_column(Geometry("MULTILINESTRING", srid=2180, spatial_index=False), nullable=False)
    __table_args__ = (
        UniqueConstraint("data_release_id", "local_id", name="uq_bdot_powerlines_release_local_id"),
        CheckConstraint("NOT ST_IsEmpty(geometry) AND ST_IsValid(geometry)", name="ck_bdot_powerlines_geometry"),
        Index("ix_bdot_powerlines_geometry_gist", "geometry", postgresql_using="gist"),
        Index("ix_bdot_powerlines_release", "data_release_id"),
    )
