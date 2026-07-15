from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.infrastructure import Infrastructure
    from app.models.mpzp_zone import MpzpZone
    from app.models.parcel import Parcel
    from app.models.pog_data import PogData
    from app.models.risk import Risk
    from app.models.source_record import SourceRecord


class Analysis(Base):
    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    parcel_id: Mapped[int] = mapped_column(
        ForeignKey("parcels.id"),
        nullable=False,
        index=True,
    )
    analyzed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    cache_valid_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    buildable_area_sqm: Mapped[float | None] = mapped_column(nullable=True)
    warnings: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Pola trybu ręcznego wznowienia (status='waiting_for_zone_symbol'), gdy
    # discover_mpzp zwraca brak_wektorow=True. Nie przechowujemy bytes
    # dokumentu — tylko URL do ponownego pobrania przy wznowieniu; magazyn
    # obiektowy oryginałów jest świadomie poza zakresem tego zadania (patrz
    # ANALIZA_ARCHITEKTURY_I_PLAN.md, sekcja E, ADR-004/ADR-005).
    pending_uchwala_url: Mapped[str | None] = mapped_column(
        String(1000), nullable=True
    )
    pending_plan_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    pending_zone_symbol_candidates: Mapped[list | None] = mapped_column(
        JSONB, nullable=True
    )
    resolved_zone_symbol: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )

    parcel: Mapped[Parcel] = relationship(back_populates="analyses")
    mpzp_zones: Mapped[list[MpzpZone]] = relationship(back_populates="analysis")
    pog_data: Mapped[list[PogData]] = relationship(back_populates="analysis")
    infrastructure_records: Mapped[list[Infrastructure]] = relationship(
        back_populates="analysis"
    )
    risk_records: Mapped[list[Risk]] = relationship(back_populates="analysis")
    source_records: Mapped[list[SourceRecord]] = relationship(back_populates="analysis")
