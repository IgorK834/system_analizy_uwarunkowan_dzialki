from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text
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

    mpzp_zone: Mapped[MpzpZone] = relationship(back_populates="parameters")
