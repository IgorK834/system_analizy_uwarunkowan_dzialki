"""Migracja 032 (AU-004): snapshot discovery MPZP z KIMPZP w ``analyses.mpzp_discovery``."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from app.db.session import engine

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION = VERSIONS / "032_analysis_mpzp_discovery.py"


def test_migration_follows_031_is_additive_and_rewrites_no_history() -> None:
    migration = MIGRATION.read_text(encoding="utf-8")
    assert 'revision: str = "032_mpzp_discovery"' in migration
    assert 'down_revision: Union[str, None] = "031_source_url_text"' in migration
    assert len("032_mpzp_discovery") <= 32  # limit alembic_version.version_num
    upgrade = migration.split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]
    assert "UPDATE" not in upgrade.upper()  # bez uzupełniania wstecz: NULL = zapis sprzed AU-004
    assert "nullable=True" in upgrade
    script = ScriptDirectory.from_config(Config(str(VERSIONS.parents[1] / "alembic.ini")))
    chain = [revision.revision for revision in script.walk_revisions("base", "heads")]
    assert chain[chain.index("032_mpzp_discovery") :][:2] == ["032_mpzp_discovery", "031_source_url_text"]
    assert all(
        "mpzp_discovery" not in path.read_text(encoding="utf-8")
        for path in VERSIONS.glob("0*.py")
        if path.name < "032"
    )


@pytest.mark.integration
def test_upgrade_adds_and_downgrade_drops_the_nullable_jsonb_column() -> None:
    config = Config("alembic.ini")
    try:
        command.upgrade(config, "head")
        columns = {column["name"]: column for column in inspect(engine).get_columns("analyses")}
        assert columns["mpzp_discovery"]["nullable"] is True
        assert columns["mpzp_discovery"]["type"].__class__.__name__ == "JSONB"
        command.downgrade(config, "031_source_url_text")
        columns = {column["name"] for column in inspect(engine).get_columns("analyses")}
        assert "mpzp_discovery" not in columns
        assert "risk_sections" in columns  # pozostałe sekcje nienaruszone
    finally:
        command.upgrade(config, "head")
