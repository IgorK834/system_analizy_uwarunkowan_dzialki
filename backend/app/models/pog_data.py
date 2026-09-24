from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, String, false
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.analysis import Analysis


class PogData(Base):
    __tablename__ = "pog_data"

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("analyses.id"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    planning_zone: Mapped[str | None] = mapped_column(String(120), nullable=True)
    zone_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    ouz_intersection_area_sqm: Mapped[float | None] = mapped_column(nullable=True)
    touches_ouz_boundary: Mapped[bool] = mapped_column(nullable=False, default=False)
    in_ouz: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )
    # Udział dominującej strefy planistycznej POG jest przechowywany w skali
    # 0-1, zgodnie z PogZoneIntersection z Task 5.3. Nie jest to procent OUZ.
    area_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    in_downtown_area: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )
    uchwala_nr: Mapped[str | None] = mapped_column(String(120), nullable=True)
    uchwala_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    manual_review_required: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )
    conflict_with_mpzp: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    raw_attributes: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    schema_version: Mapped[str] = mapped_column(
        String(20), nullable=False, default="2.0", server_default="1.0"
    )
    result_v2: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    legacy_partial: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    analysis: Mapped[Analysis] = relationship(back_populates="pog_data")
