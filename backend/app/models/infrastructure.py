from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.types import ClippedString

if TYPE_CHECKING:
    from app.models.analysis import Analysis


class Infrastructure(Base):
    __tablename__ = "infrastructure_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("analyses.id"),
        nullable=False,
        index=True,
    )
    network_type: Mapped[str] = mapped_column(String(80), nullable=False)
    buffer_m: Mapped[float | None] = mapped_column(nullable=True)
    zone_area_sqm: Mapped[float | None] = mapped_column(nullable=True)
    rule_source: Mapped[str | None] = mapped_column(ClippedString(1000), nullable=True)
    rule_confidence: Mapped[float | None] = mapped_column(nullable=True)
    rule_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    affects_buildable_area: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    network_geometry_geojson: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    protection_zone_geojson: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    manual_review_required: Mapped[bool] = mapped_column(nullable=False, default=False)

    analysis: Mapped[Analysis] = relationship(back_populates="infrastructure_records")
