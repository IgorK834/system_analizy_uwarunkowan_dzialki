"""Migracja 037 (ADR-026): krajowy rejestr aktywnych wersji aktów POG — struktura, ograniczenia i przeniesienie danych."""

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
MIGRATION = VERSIONS / "037_pog_registry.py"
NEW_TABLES = {
    "pog_registry_states", "pog_registry_entries", "pog_ru_catalog", "import_jobs",
    "pog_map_acts", "pog_map_zones", "pog_map_overlays",
}


def test_migration_is_chained_after_036_and_rewrites_no_history() -> None:
    body = MIGRATION.read_text(encoding="utf-8")
    assert 'revision: str = "037_pog_registry"' in body
    assert 'down_revision: Union[str, None] = "036_bdot10k_context"' in body
    assert len("037_pog_registry") <= 32
    script = ScriptDirectory.from_config(Config(str(VERSIONS.parents[1] / "alembic.ini")))
    assert len(script.get_heads()) == 1
    assert "037_pog_registry" in {revision.revision for revision in script.walk_revisions()}
    for other in VERSIONS.glob("0*.py"):
        if other.name < MIGRATION.name:
            assert "pog_registry_entries" not in other.read_text(encoding="utf-8")


@pytest.mark.integration
def test_upgrade_creates_tables_indexes_and_constraints() -> None:
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    inspector = inspect(engine)
    assert NEW_TABLES <= set(inspector.get_table_names())
    summaries = {column["name"] for column in inspector.get_columns("pog_area_summaries")}
    assert "registry_state_id" in summaries
    with engine.connect() as connection:
        definitions = {
            row.indexname: row.indexdef
            for row in connection.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes WHERE indexname IN ("
                    "'uq_pog_registry_entries_active', 'uq_import_jobs_active', 'uq_pog_area_summaries_municipality', "
                    "'ix_pog_map_zones_geom_full_gist', 'ix_pog_map_acts_geom_full_gist', 'ix_pog_map_overlays_geom_full_gist')"
                )
            )
        }
    assert "WHERE (state_to_id IS NULL)" in definitions["uq_pog_registry_entries_active"]
    assert "(status)::text = ANY" in definitions["uq_import_jobs_active"] or "status" in definitions["uq_import_jobs_active"]
    assert "registry_state_id" in definitions["uq_pog_area_summaries_municipality"]
    assert all("gist" in definitions[name].lower() for name in definitions if name.endswith("_gist"))


@pytest.mark.integration
def test_at_most_one_open_registry_entry_per_act_and_state_ranges_are_valid() -> None:
    command.upgrade(Config("alembic.ini"), "head")
    with engine.begin() as connection:
        source = connection.execute(
            text("INSERT INTO data_sources (source_id, owner, status) VALUES ('t037a', 'test', 'research') RETURNING id")
        ).scalar_one()
        release = connection.execute(
            text("INSERT INTO data_releases (data_source_id, version_label, published_at) VALUES (:s, 'r', now()) RETURNING id"),
            {"s": source},
        ).scalar_one()
        artifact = connection.execute(
            text("INSERT INTO source_artifacts (data_source_id, uri, content_hash, fetched_at) VALUES (:s, 'u', 'h', now()) RETURNING id"),
            {"s": source},
        ).scalar_one()
        act = connection.execute(
            text("INSERT INTO planning_acts (act_identifier, teryt, kind) VALUES ('t037-act', '000000', 'pog') RETURNING id")
        ).scalar_one()
        version = connection.execute(
            text(
                "INSERT INTO planning_act_versions (planning_act_id, source_artifact_id, data_release_id, valid_from, "
                "published_at, content_hash) VALUES (:a, :s, :r, now(), now(), 'c') RETURNING id"
            ),
            {"a": act, "s": artifact, "r": release},
        ).scalar_one()
        first = connection.execute(
            text("INSERT INTO pog_registry_states (kind) VALUES ('import') RETURNING id")
        ).scalar_one()
        second = connection.execute(
            text("INSERT INTO pog_registry_states (kind) VALUES ('import') RETURNING id")
        ).scalar_one()

        def entry(state_from: int, state_to: int | None, reason: str | None) -> None:
            connection.execute(
                text(
                    "INSERT INTO pog_registry_entries (planning_act_id, planning_act_version_id, state_from_id, state_to_id, end_reason) "
                    "VALUES (:a, :v, :f, :t, :r)"
                ),
                {"a": act, "v": version, "f": state_from, "t": state_to, "r": reason},
            )

        entry(first, None, None)
        for args in ((second, None, None), (second, first, "replaced"), (first, second, None), (first, None, "replaced")):
            nested = connection.begin_nested()
            try:
                entry(*args)  # type: ignore[arg-type]
            except IntegrityError:
                nested.rollback()
            else:
                nested.rollback()
                pytest.fail(f"rejestr przyjął niepoprawny wpis {args}")
        # Zamknięty wpis nie koliduje z otwartym.
        entry(first, second, "replaced")
        connection.execute(text("DELETE FROM pog_registry_entries WHERE planning_act_id = :a"), {"a": act})
        connection.execute(text("DELETE FROM pog_registry_states WHERE id IN (:a, :b)"), {"a": first, "b": second})
        connection.execute(text("DELETE FROM planning_act_versions WHERE id = :v"), {"v": version})
        connection.execute(text("DELETE FROM planning_acts WHERE id = :a"), {"a": act})
        connection.execute(text("DELETE FROM source_artifacts WHERE id = :a"), {"a": artifact})
        connection.execute(text("DELETE FROM data_releases WHERE id = :r"), {"r": release})
        connection.execute(text("DELETE FROM data_sources WHERE id = :s"), {"s": source})
