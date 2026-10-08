from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.types import ClippedString

if TYPE_CHECKING:
    from app.models.mpzp_zone import MpzpZone


class MpzpParameter(Base):
    __tablename__ = "mpzp_parameters"
    __table_args__ = (
        CheckConstraint(
            "value_kind IS NULL OR value_kind IN ('unconditional', 'conditional', 'conflict')",
            name="ck_mpzp_parameters_value_kind",
        ),
        # Wartość z modelu nigdy nie jest „verified”: metoda ``llm_verified`` wymaga ``ai_candidate``.
        CheckConstraint(
            "extraction_method IS DISTINCT FROM 'llm_verified' OR review_status = 'ai_candidate'",
            name="ck_mpzp_parameters_llm_candidate",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    mpzp_zone_id: Mapped[int] = mapped_column(
        ForeignKey("mpzp_zones.id"),
        nullable=False,
        index=True,
    )
    parameter_name: Mapped[str] = mapped_column(ClippedString(120), nullable=False)
    normalized_value: Mapped[str | None] = mapped_column(ClippedString(255), nullable=True)
    unit: Mapped[str | None] = mapped_column(ClippedString(30), nullable=True)
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
    # PV3-08: warunki wartości (lista ``{kind, label, quote}``) i rodzaj wartości. NULL w wierszu
    # sprzed PV3-08 = zapis bez warunków (wartość bezwarunkowa).
    conditions: Mapped[list[dict[str, str]] | None] = mapped_column(JSONB, nullable=True)
    value_kind: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # PV3-13: provenance wartości z modelu językowego (``extraction_method = llm_verified``): status
    # kandydata (``ai_candidate``), model, wersja promptu i skrót odpowiedzi z ``mpzp_llm_extractions``.
    # NULL dla wartości z silnika deterministycznego i dla wierszy sprzed PV3-13.
    review_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    model_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(60), nullable=True)
    response_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)

    mpzp_zone: Mapped[MpzpZone] = relationship(back_populates="parameters")
