"""Zapis wywołania modelu językowego dla bloku strefy MPZP (PV3-13, ADR-012).

Wiersz jest cache'em i dowodem provenance: pozwala odtworzyć ekstrakcję bez ponownego
wywołania modelu i wiąże każdy przyjęty parametr z modelem, wersją promptu i skrótem
odpowiedzi. **Nie ma tu treści żądania** (tekstu dokumentu, instrukcji ani wiadomości z
danymi) — wyłącznie skróty (``document_sha256``, ``block_sha256``, ``params_hash``) — ani
żadnego identyfikatora działki, analizy czy użytkownika: ten sam blok tego samego aktu daje
ten sam wiersz niezależnie od tego, kto i dla jakiej działki go analizował. ``response``
zawiera wyłącznie wyjście modelu (obiekt JSON zgodny ze schematem, per część bloku).

Retencja: wiersze starsze niż ``MPZP_LLM_CACHE_RETENTION_DAYS`` nie są serwowane jako
trafienie i są usuwane poleceniem ``python -m app.modules.planning.api.cli purge-llm-cache``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Float, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

LLM_EXTRACTION_STATUSES = ("ok", "rejected_schema", "error")


class MpzpLlmExtraction(Base):
    __tablename__ = "mpzp_llm_extractions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ok', 'rejected_schema', 'error')",
            name="ck_mpzp_llm_extractions_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    document_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    block_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(60), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(60), nullable=False)
    model_id: Mapped[str] = mapped_column(String(64), nullable=False)
    params_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_estimate_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
