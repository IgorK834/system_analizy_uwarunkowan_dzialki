from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.analysis import Analysis


class Risk(Base):
    __tablename__ = "risk_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("analyses.id"),
        nullable=False,
        index=True,
    )
    risk_type: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Pola strukturalne BK-303. NULL w zapisie sprzed migracji 023 oznacza
    # „nieznane” — wartości nie są odtwarzane z tekstu ``description``.
    section: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    feature_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    severity: Mapped[str | None] = mapped_column(String(20), nullable=True)
    probability_class: Mapped[str | None] = mapped_column(String(500), nullable=True)
    return_period_years: Mapped[int | None] = mapped_column(Integer, nullable=True)
    protection_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    intersection_area_sqm: Mapped[float | None] = mapped_column(Float, nullable=True)
    intersection_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    touches_boundary: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Pełny snapshot ``RiskResult`` — źródło prawdy odczytu historycznego.
    result_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    geometry_geojson: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    manual_review_required: Mapped[bool] = mapped_column(nullable=False, default=False)

    analysis: Mapped[Analysis] = relationship(back_populates="risk_records")
