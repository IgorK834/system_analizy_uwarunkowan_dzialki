from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, false
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.analysis import Analysis
    from app.models.mpzp_parameter import MpzpParameter


MPZP_ASSIGNMENT_METHODS: tuple[str, ...] = (
    "vector_intersection",
    "document_candidate",
    "manual_user_input",
    "legacy",
)


class MpzpZone(Base):
    __tablename__ = "mpzp_zones"
    __table_args__ = (
        CheckConstraint(
            "assignment_method IN ("
            + ", ".join(repr(value) for value in MPZP_ASSIGNMENT_METHODS)
            + ")",
            name="ck_mpzp_zones_assignment_method",
        ),
    )

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
    zone_identifier: Mapped[str | None] = mapped_column(String(500), nullable=True)
    act_identifier: Mapped[str | None] = mapped_column(String(200), nullable=True)
    act_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    act_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    data_release_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    touches_boundary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    assignment_method: Mapped[str] = mapped_column(
        String(40), nullable=False, default="legacy", server_default="legacy"
    )
    # Pełny wynik strefy (lista parametrów z evidence, geometria przecięcia,
    # provenance) — źródło prawdy odczytu historycznego, jak ``pog_data.result_v2``.
    result_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    analysis: Mapped[Analysis] = relationship(back_populates="mpzp_zones")
    parameters: Mapped[list[MpzpParameter]] = relationship(back_populates="mpzp_zone")
