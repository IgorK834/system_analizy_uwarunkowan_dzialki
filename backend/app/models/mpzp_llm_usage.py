"""Rejestr zużycia modelu językowego (PV3-15): podstawa twardych limitów dobowych i miesięcznych.

Jeden wiersz = jedno żądanie do dostawcy. Przed wysłaniem powstaje rezerwacja (``reserved``) na
najgorszy przypadek (szacowane wejście + limit wyjścia); po odpowiedzi jest rozliczana faktycznym
zużyciem (``settled``), a żądanie niewysłane — zwalniane (``released``). Suma dnia/miesiąca liczy
rezerwacje i rozliczenia, więc limit jest twardy także dla żądań w locie i wielu procesów (sprawdzenie
i rezerwacja pod blokadą doradczą PostgreSQL). Wiersz nie zawiera treści żądania ani odpowiedzi ani
żadnego identyfikatora działki, analizy czy użytkownika.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

LLM_USAGE_STATUSES = ("reserved", "settled", "released")


class MpzpLlmUsage(Base):
    __tablename__ = "mpzp_llm_usage"
    __table_args__ = (
        CheckConstraint("status IN ('reserved', 'settled', 'released')", name="ck_mpzp_llm_usage_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    model_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    reserved_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_cost_usd: Mapped[float] = mapped_column(Float, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(60), nullable=True)
