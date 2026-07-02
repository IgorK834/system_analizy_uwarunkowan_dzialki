from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String
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
    ouz_intersection_area_sqm: Mapped[float | None] = mapped_column(nullable=True)
    touches_ouz_boundary: Mapped[bool] = mapped_column(nullable=False, default=False)
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confidence: Mapped[float | None] = mapped_column(nullable=True)

    analysis: Mapped[Analysis] = relationship(back_populates="pog_data")
