"""Migracja 031 (AU-001): adresy źródeł i odnośniki zasilane z zewnątrz jako ``Text``."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Text, inspect, text

from app.db.session import engine
from tests.column_length_policy import COLUMN_POLICY, TEXT

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION = VERSIONS / "031_source_url_text.py"
LONG_URL = "https://services.gugik.gov.pl/nmt/?request=GetMinMaxByPolygon&polygon=" + "637961.867 486978.522," * 230


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_031", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_follows_head_030_and_rewrites_no_history() -> None:
    migration = MIGRATION.read_text(encoding="utf-8")
    assert 'revision: str = "031_source_url_text"' in migration
    assert 'down_revision: Union[str, None] = "030_mpzp_llm_usage"' in migration
    assert len("031_source_url_text") <= 32  # limit alembic_version.version_num
    upgrade = migration.split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]
    assert "UPDATE" not in upgrade.upper()  # upgrade zmienia typy, nie wartości
    script = ScriptDirectory.from_config(Config(str(VERSIONS.parents[1] / "alembic.ini")))
    chain = [revision.revision for revision in script.walk_revisions("base", "heads")]
    start = chain.index("031_source_url_text")
    assert chain[start : start + 2] == ["031_source_url_text", "030_mpzp_llm_usage"]
    assert all(
        "031_source_url_text" not in path.read_text(encoding="utf-8")
        for path in VERSIONS.glob("0*.py")
        if path.name < "031"
    )


def test_migration_covers_exactly_the_columns_the_policy_declares_as_text() -> None:
    migrated = {(table, column) for table, column, _, _ in _load_migration()._URL_COLUMNS}
    declared = {
        (table, column)
        for table, columns in COLUMN_POLICY.items()
        for column, (category, _) in columns.items()
        if category == TEXT
    }
    assert migrated == declared


@pytest.mark.integration
def test_upgrade_makes_url_columns_text_and_downgrade_clips_long_values_keeping_every_row() -> None:
    config = Config("alembic.ini")
    columns = _load_migration()._URL_COLUMNS
    try:
        command.upgrade(config, "head")
        inspector = inspect(engine)
        for table, column, _, nullable in columns:
            reflected = {item["name"]: item for item in inspector.get_columns(table)}[column]
            assert isinstance(reflected["type"], Text), f"{table}.{column}"
            assert reflected["nullable"] is nullable, f"{table}.{column}"

        with engine.begin() as conn:
            parcel_id = conn.execute(text(
                "INSERT INTO parcels (parcel_identifier, geometry) VALUES "
                "('MIG031_PARCEL', ST_GeomFromText('MULTIPOLYGON(((0 0,0 1,1 1,0 0)))', 2180)) RETURNING id"
            )).scalar_one()
            analysis_id = conn.execute(text(
                "INSERT INTO analyses (parcel_id, status, pending_uchwala_url) "
                "VALUES (:parcel, 'complete', :url) RETURNING id"
            ), {"parcel": parcel_id, "url": LONG_URL}).scalar_one()
            for name, url in (("NMT", LONG_URL), ("ULDK", "https://uldk.gugik.gov.pl/")):
                conn.execute(text(
                    "INSERT INTO source_records (analysis_id, source_name, source_url, manual_review_required) "
                    "VALUES (:analysis, :name, :url, false)"
                ), {"analysis": analysis_id, "name": name, "url": url})
            conn.execute(text(
                "INSERT INTO analysis_pending_documents "
                "(analysis_id, requested_url, final_url, media_type, content, content_sha256, size_bytes) "
                "VALUES (:analysis, :url, :url, 'application/pdf', '\\x25504446'::bytea, repeat('a', 64), 4)"
            ), {"analysis": analysis_id, "url": LONG_URL})
        assert len(LONG_URL) > 1000

        command.downgrade(config, "030_mpzp_llm_usage")

        inspector = inspect(engine)
        for table, column, limit, _ in columns:
            reflected = {item["name"]: item for item in inspector.get_columns(table)}[column]["type"]
            assert getattr(reflected, "length", None) == limit and not isinstance(reflected, Text)
        with engine.connect() as conn:
            urls = dict(conn.execute(text(
                "SELECT source_name, source_url FROM source_records WHERE analysis_id = :a"
            ), {"a": analysis_id}).all())
            pending = conn.execute(text(
                "SELECT requested_url, final_url FROM analysis_pending_documents WHERE analysis_id = :a"
            ), {"a": analysis_id}).one()
            analysis_url = conn.execute(text(
                "SELECT pending_uchwala_url FROM analyses WHERE id = :a"
            ), {"a": analysis_id}).scalar_one()
        # Wiersze zostają; za długi adres jest przycięty do 1000 znaków ze znacznikiem „…”, krótki nietknięty.
        assert set(urls) == {"NMT", "ULDK"}
        assert urls["ULDK"] == "https://uldk.gugik.gov.pl/"
        assert len(urls["NMT"]) == 1000 and urls["NMT"].endswith("…") and urls["NMT"][:-1] == LONG_URL[:999]
        assert pending == (urls["NMT"], urls["NMT"]) and analysis_url == urls["NMT"]
    finally:
        command.upgrade(config, "head")
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM analysis_pending_documents WHERE analysis_id IN "
                              "(SELECT id FROM analyses WHERE parcel_id IN "
                              "(SELECT id FROM parcels WHERE parcel_identifier = 'MIG031_PARCEL'))"))
            conn.execute(text("DELETE FROM source_records WHERE analysis_id IN "
                              "(SELECT id FROM analyses WHERE parcel_id IN "
                              "(SELECT id FROM parcels WHERE parcel_identifier = 'MIG031_PARCEL'))"))
            conn.execute(text("DELETE FROM analyses WHERE parcel_id IN "
                              "(SELECT id FROM parcels WHERE parcel_identifier = 'MIG031_PARCEL')"))
            conn.execute(text("DELETE FROM parcels WHERE parcel_identifier = 'MIG031_PARCEL'"))
