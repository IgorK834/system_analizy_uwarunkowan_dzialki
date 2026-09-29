"""Zapytania wersjonowane ``as_of`` (Faza 10.3).

Publiczne repozytorium zwracające wersję rekordu obowiązującą we wskazanej
chwili. Zakres obowiązywania jest prawostronnie otwarty ``[valid_from, valid_to)``:
wersja obowiązuje, gdy ``valid_from <= as_of`` oraz ``valid_to`` jest NULL albo
``valid_to > as_of``. Dzięki temu wersja wygasła (o ``valid_to <= as_of``) nie
jest zwracana.
"""

from __future__ import annotations

from datetime import datetime
from typing import TypeVar

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.orm import QueryableAttribute, Session

from app.models.versioned import (
    DocumentVersion,
    ParcelVersion,
    PlanningActVersion,
)

# Typy wersji obsługiwane przez zapytania as_of (mają valid_from/valid_to).
VersionT = TypeVar("VersionT", ParcelVersion, PlanningActVersion, DocumentVersion)


def _as_of_predicate(model: type[VersionT], as_of: datetime) -> ColumnElement[bool]:
    """Buduje predykat obowiązywania wersji w chwili ``as_of``."""
    return (model.valid_from <= as_of) & or_(
        model.valid_to.is_(None), model.valid_to > as_of
    )


def version_as_of(
    session: Session,
    model: type[VersionT],
    owner_column: ColumnElement[int] | QueryableAttribute[int],
    owner_id: int,
    as_of: datetime,
) -> VersionT | None:
    """Zwraca wersję danego rekordu obowiązującą w chwili ``as_of`` albo None.

    Przy nakładających się zakresach (nie powinny wystąpić dla aktywnych wersji
    dzięki częściowemu indeksowi unikalnemu) wybierana jest wersja o najnowszym
    ``valid_from``.
    """
    statement = (
        select(model)
        .where(owner_column == owner_id, _as_of_predicate(model, as_of))
        .order_by(model.valid_from.desc())
        .limit(1)
    )
    return session.execute(statement).scalar_one_or_none()


def parcel_version_as_of(
    session: Session, parcel_id: int, as_of: datetime
) -> ParcelVersion | None:
    """Wersja geometrii działki obowiązująca w chwili ``as_of``."""
    return version_as_of(
        session, ParcelVersion, ParcelVersion.parcel_id, parcel_id, as_of
    )


def planning_act_version_as_of(
    session: Session, planning_act_id: int, as_of: datetime
) -> PlanningActVersion | None:
    """Wersja aktu planistycznego obowiązująca w chwili ``as_of``."""
    return version_as_of(
        session,
        PlanningActVersion,
        PlanningActVersion.planning_act_id,
        planning_act_id,
        as_of,
    )


def document_version_as_of(
    session: Session, source_document_id: int, as_of: datetime
) -> DocumentVersion | None:
    """Wersja dokumentu źródłowego obowiązująca w chwili ``as_of``."""
    return version_as_of(
        session,
        DocumentVersion,
        DocumentVersion.source_document_id,
        source_document_id,
        as_of,
    )
