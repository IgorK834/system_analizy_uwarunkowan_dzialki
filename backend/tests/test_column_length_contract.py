"""Kontrakt długości kolumn tekstowych (AU-001): każda kolumna ``String(n)`` ma test z wartością ``n+1``.

Zapis tekstu dłuższego niż kolumna kończył się w PostgreSQL ``StringDataRightTruncation`` i HTTP 500
dla całej analizy. Kontrakt wymusza, żeby każda kolumna tekstowa w metadanych ORM miała jawną decyzję
(``tests/column_length_policy.py``) i żeby wartość o długości ``n+1`` kończyła się zawsze
przewidywalnie:

- ``text``    — kolumna jest ``Text``: wartość dłuższa niż jakikolwiek dawny limit zapisuje się w całości;
- ``clipped`` — ``ClippedString(n)``: zapis przycina do ``n`` znaków ze znacznikiem ``…``;
- ``strict``  — ``String(n)`` dla wartości nadawanych przez aplikację: nadmiar jest odrzucany jawnym
  ``DataError`` (który ``save_analysis`` zamienia na ``PersistenceError`` → 503), nigdy po cichu zmieniany.

Wartości są zapisywane przez rzeczywisty typ kolumny do tabeli tymczasowej PostGIS; osobny test porównuje
typy z modelu z rzeczywistym schematem bazy po migracjach (dryf modelu względem migracji 031).
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy import Column, Integer, MetaData, String, Table, Text, inspect, select
from sqlalchemy.exc import DataError
from sqlalchemy.types import TypeDecorator

import app.models  # noqa: F401  (rejestruje tabele w metadanych)
import app.modules.location.infrastructure.models  # noqa: F401
from app.db.base import Base
from app.db.session import engine
from app.models.types import ClippedString
from tests.column_length_policy import CLIPPED, COLUMN_POLICY, STRICT, TEXT

# Wartość dłuższa niż jakikolwiek dawny limit (największy to 2000) — dla kolumn ``Text``.
_OVER_ANY_FORMER_LIMIT = 5001


def _length(column_type: sa.types.TypeEngine) -> int | None:
    impl = column_type.impl if isinstance(column_type, TypeDecorator) else column_type
    return impl.length if isinstance(impl, String) else None


def _declared_category(column: sa.Column) -> str | None:
    if isinstance(column.type, ClippedString):
        return CLIPPED
    if isinstance(column.type, Text):
        return TEXT
    if isinstance(column.type, String) and column.type.length is not None:
        return STRICT
    return None


def _columns() -> dict[tuple[str, str], sa.Column]:
    return {
        (table.name, column.name): column
        for table in Base.metadata.tables.values()
        for column in table.columns
        if _declared_category(column) is not None
    }


def _policy_cases() -> list[tuple[str, str, str, str]]:
    return [
        (table, column, category, reason)
        for table, columns in COLUMN_POLICY.items()
        for column, (category, reason) in columns.items()
    ]


def test_every_text_column_has_a_documented_decision_and_the_registry_has_no_stale_entries() -> None:
    declared = {key for key, column in _columns().items() if not isinstance(column.type, Text)}
    policy_keys = {(table, column) for table, column, _, _ in _policy_cases()}
    text_in_policy = {
        (table, column) for table, column, category, _ in _policy_cases() if category == TEXT
    }

    missing = sorted(declared - policy_keys)
    assert not missing, f"Kolumny String(n)/ClippedString bez wpisu w column_length_policy: {missing}"
    stale = sorted(policy_keys - declared - text_in_policy)
    assert not stale, f"Wpisy polityki bez kolumny w modelu: {stale}"
    absent_text = sorted(text_in_policy - set(_columns()))
    assert not absent_text, f"Kolumny oznaczone jako text nie istnieją albo nie są Text: {absent_text}"


@pytest.mark.parametrize(
    ("table", "column", "category", "reason"),
    _policy_cases(),
    ids=lambda value: str(value) if not isinstance(value, str) or len(value) < 40 else "…",
)
def test_declared_type_matches_the_decision(table: str, column: str, category: str, reason: str) -> None:
    assert reason.strip(), "każda decyzja ma uzasadnienie źródła wartości"
    declared = _columns()[(table, column)]
    assert _declared_category(declared) == category


def test_external_urls_and_references_are_never_length_limited() -> None:
    """Adres/odnośnik zasilany z zewnątrz musi być ``Text`` — to był rdzeń awarii 500."""
    by_name = {(table, column): category for table, column, category, _ in _policy_cases()}
    for key in [
        ("source_records", "source_url"),
        ("risk_records", "source_url"),
        ("mpzp_zones", "source_url"),
        ("infrastructure_records", "source_url"),
        ("pog_data", "source_url"),
        ("analysis_pending_documents", "requested_url"),
        ("analysis_pending_documents", "final_url"),
        ("source_artifacts", "uri"),
        ("analyses", "pending_uchwala_url"),
    ]:
        assert by_name[key] == TEXT, key


def _probe(value_type: sa.types.TypeEngine, value: str):
    """Zapisuje ``value`` przez ``value_type`` do tabeli tymczasowej i zwraca zapisaną wartość."""
    table = Table(
        "au001_probe",
        MetaData(),
        Column("id", Integer, primary_key=True),
        Column("v", value_type),
        prefixes=["TEMPORARY"],
    )
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            table.create(connection)
            connection.execute(table.insert().values(v=value))
            return connection.execute(select(table.c.v)).scalar_one()
        finally:
            transaction.rollback()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("table", "column", "category", "reason"),
    _policy_cases(),
    ids=lambda value: str(value) if not isinstance(value, str) or len(value) < 40 else "…",
)
def test_value_one_character_over_the_limit_ends_predictably(
    table: str, column: str, category: str, reason: str
) -> None:
    column_type = _columns()[(table, column)].type

    if category == TEXT:
        value = "u" * _OVER_ANY_FORMER_LIMIT
        assert _probe(column_type, value) == value
        return

    limit = _length(column_type)
    assert limit is not None
    assert _probe(column_type, "k" * limit) == "k" * limit  # dokładnie n znaków zawsze się mieści

    over = "k" * (limit + 1)
    if category == CLIPPED:
        stored = _probe(column_type, over)
        assert len(stored) == limit and stored.endswith("…") and stored[:-1] == "k" * (limit - 1)
    else:
        with pytest.raises(DataError) as raised:
            _probe(column_type, over)
        assert "value too long" in str(raised.value.orig)


@pytest.mark.integration
def test_model_columns_match_the_real_database_schema_after_migrations() -> None:
    """Model i baza po ``alembic upgrade head`` mają te same typy i długości (migracja 031)."""
    inspector = inspect(engine)
    mismatches: list[str] = []
    for table, column, category, _ in _policy_cases():
        reflected = {item["name"]: item for item in inspector.get_columns(table)}[column]["type"]
        if category == TEXT:
            if not isinstance(reflected, sa.Text):
                mismatches.append(f"{table}.{column}: baza {reflected!r}, oczekiwano TEXT")
            continue
        expected = _length(_columns()[(table, column)].type)
        if not isinstance(reflected, sa.String) or isinstance(reflected, sa.Text) or reflected.length != expected:
            mismatches.append(f"{table}.{column}: baza {reflected!r}, oczekiwano VARCHAR({expected})")
    assert not mismatches, "\n".join(mismatches)
