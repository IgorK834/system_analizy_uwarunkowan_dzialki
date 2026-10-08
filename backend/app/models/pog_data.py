from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    false,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.types import ClippedString
from app.shared.planning_status import (
    COVERAGE_STATUS_VALUES,
    DATA_AVAILABILITY_VALUES,
    LEGAL_STATUS_VALUES,
)

if TYPE_CHECKING:
    from app.models.analysis import Analysis


def _in_values(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


class PogData(Base):
    __tablename__ = "pog_data"
    __table_args__ = (
        CheckConstraint(
            _in_values("legal_status", LEGAL_STATUS_VALUES),
            name="ck_pog_data_legal_status",
        ),
        CheckConstraint(
            _in_values("coverage_status", COVERAGE_STATUS_VALUES),
            name="ck_pog_data_coverage_status",
        ),
        CheckConstraint(
            _in_values("data_availability", DATA_AVAILABILITY_VALUES),
            name="ck_pog_data_data_availability",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("analyses.id"),
        nullable=False,
        index=True,
    )
    # ``status`` jest lustrem ``legal_status`` dla odczytów POG v1.
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    legal_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="unknown", server_default="unknown"
    )
    coverage_status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="unknown", server_default="unknown"
    )
    data_availability: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unavailable", server_default="unavailable"
    )
    status_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Wartość ``status`` sprzed migracji 016 zachowana dla audytu i downgrade.
    legacy_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    planning_zone: Mapped[str | None] = mapped_column(ClippedString(120), nullable=True)
    zone_type: Mapped[str | None] = mapped_column(ClippedString(30), nullable=True)
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
    uchwala_nr: Mapped[str | None] = mapped_column(ClippedString(120), nullable=True)
    uchwala_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    manual_review_required: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )
    # Informacyjna ocena relacji MPZP–POG (BK-205); zastąpiła boolean
    # ``conflict_with_mpzp`` — historyczna wartość jest w ``legacy_evidence``.
    compatibility_assessment: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    raw_attributes: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
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
