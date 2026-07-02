from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.analysis import Analysis
    from app.models.mpzp_parameter import MpzpParameter


class MpzpZone(Base):
    __tablename__ = "mpzp_zones"

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("analyses.id"),
        nullable=False,
        index=True,
    )
    zone_symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    primary_use: Mapped[str | None] = mapped_column(String(255), nullable=True)
    intersection_area_sqm: Mapped[float | None] = mapped_column(nullable=True)
    intersection_pct: Mapped[float | None] = mapped_column(nullable=True)
    is_dominant: Mapped[bool] = mapped_column(nullable=False, default=False)
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confidence: Mapped[float | None] = mapped_column(nullable=True)

    analysis: Mapped[Analysis] = relationship(back_populates="mpzp_zones")
    parameters: Mapped[list[MpzpParameter]] = relationship(back_populates="mpzp_zone")
