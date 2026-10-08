"""Dokument MPZP przypięty w chwili wstrzymania analizy (BK-204)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.types import ClippedString

if TYPE_CHECKING:
    from app.models.analysis import Analysis


class AnalysisPendingDocument(Base):
    """Bajty i wersja uchwały zapisane przy statusie ``waiting_for_zone_symbol``.

    Wznowienie parsuje wyłącznie ten artefakt (weryfikując SHA-256), więc
    podmiana uchwały pod tym samym URL między wstrzymaniem a wznowieniem nie
    zmienia wyniku. Rekord zostaje po wznowieniu jako dowód, z którego
    dokumentu odczytano parametry ręcznie wskazanej strefy.
    """

    __tablename__ = "analysis_pending_documents"
    __table_args__ = (
        CheckConstraint(
            "char_length(content_sha256) = 64",
            name="ck_analysis_pending_documents_sha256",
        ),
        CheckConstraint(
            "size_bytes >= 0", name="ck_analysis_pending_documents_size"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("analyses.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    requested_url: Mapped[str] = mapped_column(Text, nullable=False)
    final_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_type: Mapped[str] = mapped_column(ClippedString(120), nullable=False)
    filename: Mapped[str | None] = mapped_column(ClippedString(500), nullable=True)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    document_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_versions.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    analysis: Mapped[Analysis] = relationship(back_populates="pending_document")
