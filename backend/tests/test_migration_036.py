"""Migracja 036 (Task 23.1–23.3): wydania BDOT10k per powiat, tabele obiektów, snapshoty sekcji i szerszy kontrakt wyniku."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.db.session import engine

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION = VERSIONS / "036_bdot10k_context.py"


def test_migration_is_chained_after_035_and_rewrites_no_history() -> None:
    text_ = MIGRATION.read_text(encoding="utf-8")
    assert 'revision: str = "036_bdot10k_context"' in text_
    assert 'down_revision: Union[str, None] = "035_completeness"' in text_
    assert len("036_bdot10k_context") <= 32  # limit alembic_version.version_num
    upgrade = text_.split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]
    assert "UPDATE" not in upgrade.upper()  # bez uzupełniania wstecz: NULL = zapis sprzed sekcji
    for other in VERSIONS.glob("0*.py"):
        if other.name < MIGRATION.name:
            assert "bdot_roads" not in other.read_text(encoding="utf-8")
    script = ScriptDirectory.from_config(Config(str(VERSIONS.parents[1] / "alembic.ini")))
    assert len(script.get_heads()) == 1  # jedna linia migracji; 036 pozostaje w łańcuchu przed obecną głową (037+)
    assert "036_bdot10k_context" in {revision.revision for revision in script.walk_revisions()}


@pytest.mark.integration
def test_upgrade_creates_tables_columns_and_the_scoped_active_release_index() -> None:
    config = Config("alembic.ini")
    try:
        command.upgrade(config, "head")
        inspector = inspect(engine)
        assert {"bdot_releases", "bdot_roads", "bdot_buildings"} <= set(inspector.get_table_names())
        analyses = {column["name"]: column for column in inspector.get_columns("analyses")}
        assert analyses["road_context"]["nullable"] and analyses["neighbour_buildings"]["nullable"]
        assert analyses["road_context"]["type"].__class__.__name__ == "JSONB"
        assert analyses["result_contract_version"]["type"].length == 256
        releases = {column["name"]: column for column in inspector.get_columns("data_releases")}
        assert releases["scope_key"]["nullable"] is True and releases["scope_key"]["type"].length == 40
        for table in ("bdot_roads", "bdot_buildings", "bdot_releases"):
            indexes = {index["name"] for index in inspector.get_indexes(table)}
            assert any(name and name.endswith("geometry_gist") or name == "ix_bdot_releases_extent_gist" for name in indexes), table
        with engine.connect() as connection:
            definition = connection.execute(
                text("SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_data_releases_active'")
            ).scalar_one()
        assert "COALESCE(scope_key" in definition and "WHERE is_active" in definition
    finally:
        command.upgrade(config, "head")


@pytest.mark.integration
def test_active_release_is_unique_per_source_and_scope_and_unscoped_sources_keep_the_old_rule() -> None:
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    with engine.begin() as connection:
        source = connection.execute(
            text(
                "INSERT INTO data_sources (source_id, owner, status) VALUES ('t036', 'test', 'research') RETURNING id"
            )
        ).scalar_one()

        def release(label: str, scope: str | None, active: bool) -> None:
            connection.execute(
                text(
                    "INSERT INTO data_releases (data_source_id, version_label, published_at, is_active, scope_key) "
                    "VALUES (:s, :l, now(), :a, :k)"
                ),
                {"s": source, "l": label, "a": active, "k": scope},
            )

        release("a", "powiat:0001", True)
        release("b", "powiat:0002", True)  # inny zakres: obie aktywne
        release("c", "powiat:0001", False)  # nieaktywne: bez ograniczeń
        for label, scope in (("d", "powiat:0001"), ("e", None)):
            nested = connection.begin_nested()
            try:
                release(label, scope, True)
            except IntegrityError:
                nested.rollback()
            else:
                nested.commit()
                assert scope is None, "drugie aktywne wydanie tego samego zakresu zostało przyjęte"
        release("f", None, False)
        nested = connection.begin_nested()
        with pytest.raises(IntegrityError):
            release("g", None, True)  # bez zakresu: nadal jedno aktywne wydanie na źródło (reguła sprzed 036)
            connection.execute(text("SELECT 1"))
        nested.rollback()
        connection.execute(text("DELETE FROM data_releases WHERE data_source_id = :s"), {"s": source})
        connection.execute(text("DELETE FROM data_sources WHERE id = :s"), {"s": source})


@pytest.mark.integration
def test_downgrade_removes_tables_and_columns_and_restores_the_per_source_index() -> None:
    config = Config("alembic.ini")
    try:
        command.upgrade(config, "head")
        command.downgrade(config, "035_completeness")
        inspector = inspect(engine)
        assert not {"bdot_releases", "bdot_roads", "bdot_buildings"} & set(inspector.get_table_names())
        analyses = {column["name"] for column in inspector.get_columns("analyses")}
        assert not {"road_context", "neighbour_buildings"} & analyses
        assert "scope_key" not in {column["name"] for column in inspector.get_columns("data_releases")}
        assert {column["name"]: column for column in inspector.get_columns("analyses")}["result_contract_version"]["type"].length == 128
        with engine.connect() as connection:
            definition = connection.execute(
                text("SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_data_releases_active'")
            ).scalar_one()
        assert "COALESCE" not in definition and "(data_source_id)" in definition
    finally:
        command.upgrade(config, "head")
