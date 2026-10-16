"""Zapis krajowego rejestru wersji aktów POG (ADR-026): stany, wpisy, wycofanie aktu i cofnięcie wersji.

Wszystkie operacje działają w transakcji wołającego. Kolejność blokad jest stała: najpierw blokady aktów
(``lock_acts``, posortowane), potem globalna blokada rejestru (``lock_registry``) tuż przed nadaniem numeru stanu —
dzięki temu numery stanów rosną zgodnie z kolejnością zatwierdzania, a dwa równoległe przebiegi nie zakleszczają się.
Operacje bez wersji do utworzenia (wycofanie, cofnięcie) biorą wyłącznie blokadę globalną.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Final

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models.pog_registry import PogRegistryEntry, PogRegistryState
from app.models.versioned import PlanningAct, PlanningActVersion
from app.shared.pog_registry import REGISTRY_LOCK

WITHDRAWAL_REASONS: Final[tuple[str, ...]] = ("withdrawn", "not_current")


@dataclass(frozen=True)
class ActiveEntry:
    entry_id: int
    act_id: int
    version_id: int
    content_hash: str
    ru_published_act_id: int | None
    state_from_id: int


def lock_registry(session: Session) -> None:
    """Globalna blokada doradcza rejestru, zwalniana przy zatwierdzeniu albo wycofaniu transakcji."""
    session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:scope))"), {"scope": REGISTRY_LOCK})


def lock_acts(session: Session, act_identifiers: Iterable[str]) -> None:
    """Blokady doradcze poszczególnych aktów (w kolejności alfabetycznej) — serializują import tego samego aktu."""
    for identifier in sorted(set(act_identifiers)):
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:scope))"), {"scope": f"pog-act:{identifier}"}
        )


def active_entries(session: Session, act_ids: Sequence[int]) -> dict[int, ActiveEntry]:
    """Otwarte wpisy rejestru dla podanych aktów (``planning_acts.id`` → wpis)."""
    if not act_ids:
        return {}
    rows = session.execute(
        select(
            PogRegistryEntry.id,
            PogRegistryEntry.planning_act_id,
            PogRegistryEntry.planning_act_version_id,
            PlanningActVersion.content_hash,
            PogRegistryEntry.ru_published_act_id,
            PogRegistryEntry.state_from_id,
        )
        .join(PlanningActVersion, PlanningActVersion.id == PogRegistryEntry.planning_act_version_id)
        .where(PogRegistryEntry.planning_act_id.in_(list(act_ids)), PogRegistryEntry.state_to_id.is_(None))
    ).all()
    return {
        int(row.planning_act_id): ActiveEntry(
            int(row.id),
            int(row.planning_act_id),
            int(row.planning_act_version_id),
            str(row.content_hash),
            int(row.ru_published_act_id) if row.ru_published_act_id is not None else None,
            int(row.state_from_id),
        )
        for row in rows
    }


def open_state(
    session: Session, *, kind: str, data_release_id: int | None, summary: Mapping[str, Any] | None = None
) -> int:
    """Nadaje kolejny numer stanu krajowego (wołać pod ``lock_registry``)."""
    state = PogRegistryState(kind=kind, data_release_id=data_release_id, summary=dict(summary or {}))
    session.add(state)
    session.flush()
    return int(state.id)


def close_entry(session: Session, entry: ActiveEntry, *, state_id: int, reason: str) -> None:
    session.execute(
        text(
            "UPDATE pog_registry_entries SET state_to_id = :state_id, end_reason = :reason WHERE id = :entry_id"
        ),
        {"state_id": state_id, "reason": reason, "entry_id": entry.entry_id},
    )
    session.execute(
        text("UPDATE planning_act_versions SET valid_to = :now WHERE id = :version_id AND valid_to IS NULL"),
        {"now": datetime.now(timezone.utc), "version_id": entry.version_id},
    )


def add_entry(
    session: Session, *, act_id: int, version_id: int, state_id: int, ru_published_act_id: int | None
) -> None:
    session.add(
        PogRegistryEntry(
            planning_act_id=act_id,
            planning_act_version_id=version_id,
            state_from_id=state_id,
            ru_published_act_id=ru_published_act_id,
        )
    )
    session.flush()


def previous_feature_counts(session: Session, act_identifiers: Sequence[str]) -> dict[str, int]:
    """Liczba obiektów aktywnej wersji każdego z aktów — podstawa kontroli regresji liczona per akt."""
    if not act_identifiers:
        return {}
    rows = session.execute(
        text(
            """
            SELECT pa.act_identifier, count(pf.id) AS features
            FROM planning_acts pa
            JOIN pog_registry_entries e ON e.planning_act_id = pa.id AND e.state_to_id IS NULL
            LEFT JOIN planning_features pf ON pf.planning_act_version_id = e.planning_act_version_id
            WHERE pa.kind = 'pog' AND pa.act_identifier = ANY(CAST(:ids AS text[]))
            GROUP BY pa.act_identifier
            """
        ),
        {"ids": list(act_identifiers)},
    )
    return {str(row.act_identifier): int(row.features) for row in rows}


def act_teryts(session: Session, act_ids: Iterable[int]) -> set[str]:
    ids = list(act_ids)
    if not ids:
        return set()
    return {
        str(value)
        for value in session.execute(
            select(PlanningAct.teryt).where(PlanningAct.id.in_(ids), PlanningAct.teryt.is_not(None))
        ).scalars()
    }


def withdraw_acts(
    session: Session,
    entries: Sequence[ActiveEntry],
    *,
    reason: str,
    data_release_id: int,
    summary: Mapping[str, Any] | None = None,
) -> int | None:
    """Dezaktywuje akty (wpisy zostają w historii, wersje i geometrie nie są usuwane); zwraca numer nowego stanu."""
    if reason not in WITHDRAWAL_REASONS:
        raise ValueError(f"Nieznany powód wycofania: {reason!r}.")
    if not entries:
        return None
    lock_registry(session)
    current = active_entries(session, [entry.act_id for entry in entries])
    targets = [current[entry.act_id] for entry in entries if entry.act_id in current]
    if not targets:
        return None
    state_id = open_state(
        session,
        kind="withdrawal",
        data_release_id=data_release_id,
        summary={**(summary or {}), "withdrawn_acts": len(targets), "reason": reason},
    )
    for entry in targets:
        close_entry(session, entry, state_id=state_id, reason=reason)
    return state_id


def activate_previous_version(
    session: Session,
    *,
    act_identifier: str,
    to_version_id: int | None,
    data_release_id: int,
) -> tuple[int, int]:
    """Cofnięcie aktu do wcześniejszej wersji (nowy stan ``rollback``); zwraca ``(stan, wersja)``.

    Bez ``to_version_id`` wybierana jest najnowsza wersja starsza od aktywnej. Wersje nie są kopiowane ani usuwane:
    nowy wpis rejestru wskazuje istniejący wiersz wersji, więc geometrie i agregaty są te same, co przed zastąpieniem.
    """
    act = session.execute(
        select(PlanningAct).where(PlanningAct.act_identifier == act_identifier, PlanningAct.kind == "pog")
    ).scalar_one_or_none()
    if act is None:
        raise LookupError(f"Nieznany akt POG: {act_identifier}.")
    lock_acts(session, [act_identifier])
    lock_registry(session)
    current = active_entries(session, [act.id]).get(act.id)
    if to_version_id is None:
        stmt = (
            select(PlanningActVersion.id)
            .where(PlanningActVersion.planning_act_id == act.id)
            .order_by(PlanningActVersion.id.desc())
        )
        candidates = [int(value) for value in session.execute(stmt).scalars()]
        older = [value for value in candidates if current is None or value < current.version_id]
        if not older:
            raise LookupError(f"Akt {act_identifier} nie ma starszej wersji, do której można wrócić.")
        to_version_id = older[0]
    else:
        owner = session.execute(
            select(PlanningActVersion.planning_act_id).where(PlanningActVersion.id == to_version_id)
        ).scalar_one_or_none()
        if owner != act.id:
            raise LookupError(f"Wersja {to_version_id} nie należy do aktu {act_identifier}.")
    if current is not None and current.version_id == to_version_id:
        raise ValueError(f"Wersja {to_version_id} jest już aktywna.")
    state_id = open_state(
        session,
        kind="rollback",
        data_release_id=data_release_id,
        summary={"act_identifier": act_identifier, "to_version_id": to_version_id},
    )
    if current is not None:
        close_entry(session, current, state_id=state_id, reason="rolled_back")
    add_entry(
        session,
        act_id=act.id,
        version_id=to_version_id,
        state_id=state_id,
        ru_published_act_id=None,
    )
    session.execute(
        text("UPDATE planning_act_versions SET valid_to = NULL WHERE id = :version_id"),
        {"version_id": to_version_id},
    )
    return state_id, to_version_id


def registry_counts(session: Session) -> dict[str, int]:
    """Liczby do statusu operacyjnego: aktywne akty, wszystkie stany."""
    active = session.execute(
        select(func.count()).select_from(PogRegistryEntry).where(PogRegistryEntry.state_to_id.is_(None))
    ).scalar_one()
    states = session.execute(select(func.count()).select_from(PogRegistryState)).scalar_one()
    return {"active_acts": int(active), "states": int(states)}
