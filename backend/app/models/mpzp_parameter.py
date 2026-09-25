from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.mpzp_zone import MpzpZone


class MpzpParameter(Base):
    __tablename__ = "mpzp_parameters"

    id: Mapped[int] = mapped_column(primary_key=True)
    mpzp_zone_id: Mapped[int] = mapped_column(
        ForeignKey("mpzp_zones.id"),
        nullable=False,
        index=True,
    )
    parameter_name: Mapped[str] = mapped_column(String(120), nullable=False)
    normalized_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(30), nullable=True)
    source_fragment: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_number: Mapped[int | None] = mapped_column(nullable=True)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    manual_review_required: Mapped[bool] = mapped_column(nullable=False, default=False)
    # Evidence BK-203: skąd dokładnie pochodzi wartość w uchwale.
    raw_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    segment_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    legal_unit_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    document_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    document_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String(60), nullable=True)
    extraction_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    conflict_group_id: Mapped[str | None] = mapped_column(
        String(300), nullable=True, index=True
    )

    mpzp_zone: Mapped[MpzpZone] = relationship(back_populates="parameters")
