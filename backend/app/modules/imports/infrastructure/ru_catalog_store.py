"""Zapis wykazu RU (``pog_ru_catalog``), odczyt tokenów rejestru i stan gmin (ADR-026).

Każda operacja ma własną, krótką transakcję (``session_factory``), więc działa bezpiecznie z wielu wątków importu.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, cast

from sqlalchemy import ColumnElement, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.models.pog_registry import PogRuCatalogEntry
from app.modules.imports.domain.ru_catalog import RegistryToken, RuListing, RuPublishedAct


@dataclass(frozen=True)
class CatalogSyncResult:
    rows: int
    new: int
    changed_status: int
    no_longer_current: int
    marked_non_current: bool


class SqlAlchemyRuCatalogStore:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def sync_catalog(
        self, listing: RuListing, *, run_id: int | None = None, now: datetime | None = None
    ) -> CatalogSyncResult:
        """Upsert bieżących wersji; wiersze nieobecne na KOMPLETNEJ liście dostają ``is_current = false``.

        Lista niekompletna tylko dopisuje i odświeża — nie oznacza nieobecnych jako nieaktualnych (brak aktu na
        niepełnej liście nie jest dowodem wycofania).
        """
        stamp = now or datetime.now(timezone.utc)
        with self._session_factory() as session:
            existing = {
                row.published_act_id: row
                for row in session.execute(select(PogRuCatalogEntry)).scalars()
            }
            new = changed = 0
            for act in listing.acts:
                previous = existing.get(act.published_act_id)
                if previous is None:
                    new += 1
                elif previous.legal_status != act.legal_status or previous.status_text != act.status_text:
                    changed += 1
                values = _values(act, stamp, run_id)
                statement = pg_insert(PogRuCatalogEntry).values(**values, first_seen_at=stamp)
                statement = statement.on_conflict_do_update(
                    index_elements=[PogRuCatalogEntry.published_act_id], set_=values
                )
                session.execute(statement)
            gone = 0
            if listing.complete:
                current_ids = [act.published_act_id for act in listing.acts]
                conditions: list[ColumnElement[bool]] = [PogRuCatalogEntry.is_current.is_(True)]
                if current_ids:
                    conditions.append(PogRuCatalogEntry.published_act_id.not_in(current_ids))
                result = cast(
                    CursorResult[Any],
                    session.execute(update(PogRuCatalogEntry).where(*conditions).values(is_current=False)),
                )
                gone = int(result.rowcount or 0)
            session.commit()
        return CatalogSyncResult(len(listing.acts), new, changed, gone, listing.complete)

    def registry_tokens(self) -> list[RegistryToken]:
        with self._session_factory() as session:
            rows = session.execute(
                text(
                    """
                    SELECT e.id AS entry_id, e.planning_act_id, pa.act_identifier, pa.teryt, e.ru_published_act_id
                    FROM pog_registry_entries e JOIN planning_acts pa ON pa.id = e.planning_act_id
                    WHERE e.state_to_id IS NULL AND pa.kind = 'pog' ORDER BY pa.act_identifier
                    """
                )
            ).mappings()
            return [
                RegistryToken(
                    act_id=int(row["planning_act_id"]),
                    act_identifier=str(row["act_identifier"]),
                    teryt=row["teryt"],
                    ru_published_act_id=(
                        int(row["ru_published_act_id"]) if row["ru_published_act_id"] is not None else None
                    ),
                    entry_id=int(row["entry_id"]),
                )
                for row in rows
            ]

    def current_catalog_for_terc(self, terc: str) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            return [
                dict(row)
                for row in session.execute(
                    text(
                        "SELECT published_act_id, terc, commune, status_text, legal_status, publish_date, last_seen_at "
                        "FROM pog_ru_catalog WHERE is_current AND terc LIKE :prefix ORDER BY published_act_id"
                    ),
                    {"prefix": f"{terc[:6]}%"},
                ).mappings()
            ]

    def last_catalog_sync(self) -> datetime | None:
        with self._session_factory() as session:
            value = session.execute(text("SELECT max(last_seen_at) FROM pog_ru_catalog")).scalar_one()
            return value


def _values(act: RuPublishedAct, stamp: datetime, run_id: int | None) -> dict[str, Any]:
    return {
        "published_act_id": act.published_act_id,
        "terc": act.terc,
        "plan_type": act.plan_type,
        "title": act.title,
        "voivodeship": act.voivodeship,
        "district": act.district,
        "commune": act.commune,
        "status_text": act.status_text,
        "legal_status": act.legal_status,
        "publish_date": act.publish_date,
        "valid_from": act.valid_from,
        "valid_to": act.valid_to,
        "is_current": True,
        "last_seen_at": stamp,
        "last_sync_run_id": run_id,
    }


def terc_filter_values(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted({value.strip()[:6] for value in values if value.strip()}))
