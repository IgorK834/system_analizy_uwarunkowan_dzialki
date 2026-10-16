"""Migracja 035 (AU-104): snapshot kompletności analizy i komunikatu nagłówkowego."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from app.db.session import engine

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION = VERSIONS / "035_analysis_completeness.py"


def test_migration_is_additive_chained_after_the_current_head_and_rewrites_no_history() -> None:
    text = MIGRATION.read_text(encoding="utf-8")
    assert 'revision: str = "035_completeness"' in text
    assert 'down_revision: Union[str, None] = "034_mpzp_status"' in text
    assert len("035_completeness") <= 32  # limit alembic_version.version_num
    upgrade = text.split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]
    assert "UPDATE" not in upgrade.upper()  # bez uzupełniania wstecz: NULL = zapis sprzed AU-104
    assert "nullable=True" in upgrade and "completeness" in upgrade
    assert "result_contract_version" in upgrade and "length=128" in upgrade  # kontrakt przekroczył 64 znaki
    assert all(
        "completeness" not in other.read_text(encoding="utf-8")
        for other in VERSIONS.glob("0*.py")
        if other.name < MIGRATION.name
    )


def test_chain_has_a_single_head() -> None:
    script = ScriptDirectory.from_config(Config(str(VERSIONS.parents[1] / "alembic.ini")))
    heads = script.get_heads()
    assert len(heads) == 1  # jedna linia migracji; 035 pozostaje w łańcuchu przed obecną głową
    assert "035_completeness" in {revision.revision for revision in script.walk_revisions()}


@pytest.mark.integration
def test_upgrade_adds_and_downgrade_drops_the_nullable_jsonb_column() -> None:
    config = Config("alembic.ini")
    try:
        command.upgrade(config, "head")
        columns = {column["name"]: column for column in inspect(engine).get_columns("analyses")}
        assert columns["completeness"]["nullable"] is True
        assert columns["completeness"]["type"].__class__.__name__ == "JSONB"
        assert columns["result_contract_version"]["type"].length == 256  # poszerzone w 036 (w 035: 128)
        command.downgrade(config, "034_mpzp_status")
        after = {column["name"] for column in inspect(engine).get_columns("analyses")}
        assert "completeness" not in after
        narrowed = {c["name"]: c for c in inspect(engine).get_columns("analyses")}
        assert narrowed["result_contract_version"]["type"].length == 64
        assert {"mpzp_status", "mpzp_acts", "section_quality"} <= after  # wcześniejsze snapshoty nienaruszone
    finally:
        command.upgrade(config, "head")


def test_result_contract_version_fits_its_column_with_room_to_grow() -> None:
    """Regresja z live smoke 2026-10-12: kontrakt (71 znaków) nie mieścił się w kolumnie ``String(64)`` — każda
    analiza kończyła się 503 ``PERSISTENCE_FAILED``. Jednostkowe testy z ``respx`` tego nie wykrywały."""
    from app.models.analysis import Analysis
    from app.services.cache import RESULT_CONTRACT_VERSION

    limit = Analysis.__table__.c.result_contract_version.type.length
    assert limit == 256  # 036 poszerzyła kolumnę (97 znaków kontraktu przy limicie 128 zostawiało za mało zapasu)
    assert len(RESULT_CONTRACT_VERSION) <= limit - 32  # zapas na kolejne składniki i wersje dwucyfrowe
