"""Migracja 030 (PV3-15): rejestr zużycia modelu dla twardych limitów dobowych i miesięcznych."""

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
COLUMNS = {
    "id", "created_at", "settled_at", "model_id", "status", "reserved_tokens", "reserved_cost_usd", "input_tokens",
    "output_tokens", "cost_usd", "outcome",
}


def test_migration_follows_head_029_and_is_additive() -> None:
    migration = (VERSIONS / "030_mpzp_llm_usage.py").read_text(encoding="utf-8")
    assert 'down_revision: Union[str, None] = "029_mpzp_llm_extractions"' in migration
    assert "UPDATE" not in migration.split("def upgrade()", 1)[1].split("def downgrade()", 1)[0].upper()
    script = ScriptDirectory.from_config(Config(str(VERSIONS.parents[1] / "alembic.ini")))
    assert script.get_heads() == ["030_mpzp_llm_usage"]
    assert all("mpzp_llm_usage" not in path.read_text(encoding="utf-8")
               for path in VERSIONS.glob("0*.py") if path.name < "030")


@pytest.mark.integration
def test_upgrade_and_downgrade_create_and_drop_the_ledger_without_request_content() -> None:
    config = Config("alembic.ini")
    try:
        command.upgrade(config, "head")
        columns = {column["name"] for column in inspect(engine).get_columns("mpzp_llm_usage")}
        assert columns == COLUMNS  # brak treści żądania, odpowiedzi i identyfikatorów działki/użytkownika
        with engine.begin() as conn:
            with pytest.raises(IntegrityError):
                with conn.begin_nested():
                    conn.execute(text(
                        "INSERT INTO mpzp_llm_usage (created_at, model_id, status, reserved_tokens, reserved_cost_usd) "
                        "VALUES (now(), 'm', 'verified', 1, 0)"))
        command.downgrade(config, "029_mpzp_llm_extractions")
        assert "mpzp_llm_usage" not in inspect(engine).get_table_names()
        assert "mpzp_llm_extractions" in inspect(engine).get_table_names()  # 029 nienaruszona
    finally:
        command.upgrade(config, "head")
