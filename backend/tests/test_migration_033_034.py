"""Migracje 033 i 034 (AU-101, AU-103): snapshot aktów MPZP i rozłącznego stanu ustalenia MPZP."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from app.db.session import engine

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
ACTS = VERSIONS / "033_analysis_mpzp_acts.py"
STATUS = VERSIONS / "034_analysis_mpzp_status.py"


@pytest.mark.parametrize(
    ("path", "revision", "down", "column"),
    [
        (ACTS, "033_mpzp_acts", "032_mpzp_discovery", "mpzp_acts"),
        (STATUS, "034_mpzp_status", "033_mpzp_acts", "mpzp_status"),
    ],
)
def test_migrations_are_additive_chained_and_rewrite_no_history(path: Path, revision: str, down: str, column: str) -> None:
    migration = path.read_text(encoding="utf-8")
    assert f'revision: str = "{revision}"' in migration
    assert f'down_revision: Union[str, None] = "{down}"' in migration
    assert len(revision) <= 32  # limit alembic_version.version_num
    upgrade = migration.split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]
    assert "UPDATE" not in upgrade.upper()  # bez uzupełniania wstecz: NULL = zapis sprzed zmiany
    assert "nullable=True" in upgrade and column in upgrade
    assert all(
        column not in other.read_text(encoding="utf-8")
        for other in VERSIONS.glob("0*.py")
        if other.name < path.name
    )


def test_chain_has_a_single_head_and_034_follows_033() -> None:
    script = ScriptDirectory.from_config(Config(str(VERSIONS.parents[1] / "alembic.ini")))
    assert len(script.get_heads()) == 1  # kolejne migracje (AU-104, Task 23.1, ADR-026) dopisane po 034
    chain = [revision.revision for revision in script.walk_revisions("base", "heads")]
    start = chain.index("036_bdot10k_context")
    assert chain[start : start + 5] == [
        "036_bdot10k_context", "035_completeness", "034_mpzp_status", "033_mpzp_acts", "032_mpzp_discovery"
    ]


@pytest.mark.integration
def test_upgrade_adds_and_downgrade_drops_both_nullable_jsonb_columns() -> None:
    config = Config("alembic.ini")
    try:
        command.upgrade(config, "head")
        columns = {column["name"]: column for column in inspect(engine).get_columns("analyses")}
        for name in ("mpzp_acts", "mpzp_status"):
            assert columns[name]["nullable"] is True
            assert columns[name]["type"].__class__.__name__ == "JSONB"
        command.downgrade(config, "032_mpzp_discovery")
        columns = {column["name"] for column in inspect(engine).get_columns("analyses")}
        assert not {"mpzp_acts", "mpzp_status"} & columns
        assert "mpzp_discovery" in columns  # sekcja z AU-004 nienaruszona
    finally:
        command.upgrade(config, "head")
