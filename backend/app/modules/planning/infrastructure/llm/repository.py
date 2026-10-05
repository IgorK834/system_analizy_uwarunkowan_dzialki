"""Adapter SQLAlchemy cache'u i provenance wywołań modelu (PV3-13, port ``LlmExtractionCache``).

Repozytorium otwiera WŁASNE krótkie sesje (``session_factory``), niezależne od transakcji analizy:
zapis cache nie może zatwierdzić niedokończonej analizy, a wycofanie analizy (np. po błędzie zapisu
audytu dokumentu) nie może skasować już opłaconej odpowiedzi modelu. Zapis jest idempotentny
(``INSERT … ON CONFLICT``): zapis ``ok`` w okresie retencji nie jest nadpisywany, a zapis ``error``,
``rejected_schema`` albo przeterminowany zastępuje nowsza próba.

Retencja: ``get`` nie zwraca zapisów starszych niż ``retention_days`` (nie są trafieniem), a
``purge`` je usuwa (polecenie ``python -m app.modules.planning purge-llm-cache``). Tabela nie ma
kolumny na treść żądania ani na identyfikator działki, analizy czy użytkownika.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.mpzp_llm_extraction import MpzpLlmExtraction
from app.modules.planning.application.ports import LlmExtractionCache, LlmExtractionRecord

_COLUMNS = (
    "cache_key",
    "document_sha256",
    "block_sha256",
    "prompt_version",
    "schema_version",
    "model_id",
    "params_hash",
    "status",
    "response",
    "response_sha256",
    "input_tokens",
    "output_tokens",
    "latency_ms",
    "cost_estimate_usd",
    "error_code",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _record(row: MpzpLlmExtraction) -> LlmExtractionRecord:
    return LlmExtractionRecord(
        **{column: getattr(row, column) for column in _COLUMNS},
        created_at=row.created_at,
    )


class SqlAlchemyLlmExtractionCache(LlmExtractionCache):
    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        retention_days: int,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if retention_days < 1:
            raise ValueError("Retencja cache modelu musi wynosić co najmniej 1 dzień.")
        self._session_factory = session_factory
        self._retention = timedelta(days=retention_days)
        self._clock = clock

    @property
    def retention_days(self) -> int:
        return self._retention.days

    def get(self, cache_key: str) -> LlmExtractionRecord | None:
        cutoff = self._clock() - self._retention
        with self._session_factory() as session:
            row = session.scalar(
                select(MpzpLlmExtraction).where(
                    MpzpLlmExtraction.cache_key == cache_key,
                    MpzpLlmExtraction.created_at >= cutoff,
                )
            )
            return _record(row) if row is not None else None

    def save(self, record: LlmExtractionRecord) -> LlmExtractionRecord:
        values = {column: getattr(record, column) for column in _COLUMNS}
        values["created_at"] = self._clock()
        statement = insert(MpzpLlmExtraction).values(**values)
        replaceable = {column: statement.excluded[column] for column in _COLUMNS if column != "cache_key"}
        replaceable["created_at"] = statement.excluded.created_at
        # Zapis ``ok`` w okresie retencji jest niezmienny; ``error``/``rejected_schema`` albo zapis
        # przeterminowany zastępuje nowsza próba (inaczej klucz poza retencją nigdy by się nie odświeżył).
        statement = statement.on_conflict_do_update(
            index_elements=[MpzpLlmExtraction.cache_key],
            set_=replaceable,
            where=or_(
                MpzpLlmExtraction.status != "ok",
                MpzpLlmExtraction.created_at < values["created_at"] - self._retention,
            ),
        )
        with self._session_factory() as session:
            session.execute(statement)
            session.commit()
            row = session.scalar(select(MpzpLlmExtraction).where(MpzpLlmExtraction.cache_key == record.cache_key))
            assert row is not None  # zapis właśnie wykonany albo istniejący ``ok``
            return _record(row)

    def purge(self, *, older_than: datetime) -> int:
        with self._session_factory() as session:
            result = session.execute(delete(MpzpLlmExtraction).where(MpzpLlmExtraction.created_at < older_than))
            session.commit()
            return int(result.rowcount or 0)

    def purge_expired(self) -> int:
        """Usuwa zapisy poza okresem retencji."""
        return self.purge(older_than=self._clock() - self._retention)
