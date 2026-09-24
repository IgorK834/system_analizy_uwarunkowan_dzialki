from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.analysis import Analysis


class SourceRecord(Base):
    __tablename__ = "source_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("analyses.id"),
        nullable=False,
        index=True,
    )
    source_name: Mapped[str] = mapped_column(String(120), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # API przechowuje liczbowy kod HTTP, ale warstwa audytowa musi również
    # odróżnić brak wywołania od niedostępności i błędu sekcji. Dlatego baza
    # zapisuje zarówno "200", jak i semantyczne "unavailable"/"error".
    response_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    manual_review_required: Mapped[bool] = mapped_column(nullable=False, default=False)
    warnings: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    artifact_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    data_release_id: Mapped[int | None] = mapped_column(nullable=True)
    act_version: Mapped[str | None] = mapped_column(String(120), nullable=True)

    analysis: Mapped[Analysis] = relationship(back_populates="source_records")
