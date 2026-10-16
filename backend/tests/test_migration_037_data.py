"""Migracja 037 na danych: dawny model „jedno aktywne wydanie” → rejestr aktywnych wersji (ADR-026), z rollbackiem."""

from __future__ import annotations

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.db.session import engine

pytestmark = pytest.mark.integration

PRE = "036_bdot10k_context"


def _legacy_state(connection) -> dict[str, int]:  # noqa: ANN001
    """Model sprzed ADR-026: gmina A zaimportowana pierwsza (wydanie R1 nieaktywne), potem B i C (R2 aktywne)."""
    source = connection.execute(
        text("INSERT INTO data_sources (source_id, owner, status) VALUES ('pog_app', 'test', 'production') RETURNING id")
    ).scalar_one()
    ids: dict[str, int] = {"source": source}
    for label, active in (("r1", False), ("r2", True)):
        ids[label] = connection.execute(
            text("INSERT INTO data_releases (data_source_id, version_label, published_at, is_active) VALUES (:s, :l, now(), :a) RETURNING id"),
            {"s": source, "l": label, "a": active},
        ).scalar_one()
        ids[f"{label}_artifact"] = connection.execute(
            text("INSERT INTO source_artifacts (data_source_id, uri, content_hash, fetched_at) VALUES (:s, :u, :h, now()) RETURNING id"),
            {"s": source, "u": f"u-{label}", "h": f"hash-{label}"},
        ).scalar_one()
    for name, teryt in (("A", "246101"), ("B", "226401"), ("C", "026201")):
        ids[f"act_{name}"] = connection.execute(
            text("INSERT INTO planning_acts (act_identifier, teryt, kind) VALUES (:i, :t, 'pog') RETURNING id"),
            {"i": f"legacy-{name}", "t": teryt},
        ).scalar_one()

    def version(act: str, release: str, *, closed: bool, key: str) -> int:
        return connection.execute(
            text(
                "INSERT INTO planning_act_versions (planning_act_id, source_artifact_id, data_release_id, legal_status, valid_from, "
                "valid_to, published_at, content_hash) VALUES (:a, :s, :r, 'binding', now() - interval '2 days', "
                "CASE WHEN :c THEN now() - interval '1 day' END, now(), :h) RETURNING id"
            ),
            {"a": ids[f"act_{act}"], "s": ids[f"{release}_artifact"], "r": ids[release], "c": closed, "h": key},
        ).scalar_one()

    ids["vA"] = version("A", "r1", closed=False, key="a1")  # ukryta przez dawny model (wydanie R1 nieaktywne)
    ids["vB"] = version("B", "r2", closed=False, key="b1")
    ids["vC_old"] = version("C", "r1", closed=True, key="c1")
    ids["vC"] = version("C", "r2", closed=False, key="c2")
    ids["summary"] = connection.execute(
        text(
            "INSERT INTO pog_area_summaries (data_release_id, scope, teryt, edition, zones_area_sqm, overlap_area_sqm, "
            "outside_area_sqm, zone_count, act_count, is_complete, incomplete_reasons, act_identifiers, area_tolerance_sqm, "
            "share_tolerance_pct, method_version, computed_at) VALUES (:r, 'municipality', '226401', 'binding', 10, 0, 0, 1, 1, "
            "false, CAST('[\"no_boundary\"]' AS jsonb), CAST('[\"legacy-B\"]' AS jsonb), 1, 0.1, 'pog-aggregates/1', now()) RETURNING id"
        ),
        {"r": ids["r2"]},
    ).scalar_one()
    return ids


def test_legacy_active_release_becomes_registry_and_hidden_acts_return_then_rollback_restores_flags() -> None:
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    command.downgrade(config, PRE)
    with engine.begin() as connection:
        ids = _legacy_state(connection)

    command.upgrade(config, "037_pog_registry")

    with engine.connect() as connection:
        entries = {
            row.act_identifier: row
            for row in connection.execute(
                text(
                    "SELECT pa.act_identifier, e.planning_act_version_id AS version_id, e.state_to_id, e.ru_published_act_id "
                    "FROM pog_registry_entries e JOIN planning_acts pa ON pa.id = e.planning_act_id"
                )
            )
        }
        assert set(entries) == {"legacy-A", "legacy-B", "legacy-C"}
        # Akt A, ukryty przez dawny model (wydanie nieaktywne), wraca do stanu krajowego; C — wersja z wydania aktywnego.
        assert entries["legacy-A"].version_id == ids["vA"]
        assert entries["legacy-B"].version_id == ids["vB"]
        assert entries["legacy-C"].version_id == ids["vC"], "wersja zamknięta (valid_to) nie może zostać aktywna"
        assert all(row.state_to_id is None and row.ru_published_act_id is None for row in entries.values())
        state = connection.execute(
            text("SELECT id, kind, summary FROM pog_registry_states")
        ).one()
        assert state.kind == "migration" and state.summary["legacy_active_release_ids"] == [ids["r2"]]
        # Wydania źródła są od teraz rekordami audytowymi; agregat gminy z wydania aktywnego dostał numer stanu.
        assert connection.execute(text("SELECT count(*) FROM data_releases WHERE is_active")).scalar_one() == 0
        assert connection.execute(
            text("SELECT registry_state_id FROM pog_area_summaries WHERE id = :id"), {"id": ids["summary"]}
        ).scalar_one() == state.id
        # Dane źródłowe wersji pozostają nietknięte.
        assert connection.execute(
            text("SELECT valid_to IS NOT NULL FROM planning_act_versions WHERE id = :v"), {"v": ids["vC_old"]}
        ).scalar_one() is True

    command.downgrade(config, PRE)
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT is_active FROM data_releases WHERE id = :id"), {"id": ids["r2"]}
        ).scalar_one() is True
        assert "pog_registry_entries" not in {
            row[0] for row in connection.execute(text("SELECT table_name FROM information_schema.tables"))
        }
        assert connection.execute(text("SELECT count(*) FROM planning_act_versions")).scalar_one() == 4

    with engine.begin() as connection:  # sprzątanie danych testowych
        for table in ("pog_area_summaries", "planning_act_versions", "planning_acts", "source_artifacts", "data_releases", "data_sources"):
            connection.execute(text(f"DELETE FROM {table}"))
    command.upgrade(config, "head")


def test_empty_database_gets_no_migration_state() -> None:
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    command.downgrade(config, PRE)
    command.upgrade(config, "037_pog_registry")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM pog_registry_states")).scalar_one() == 0
    command.upgrade(config, "head")
