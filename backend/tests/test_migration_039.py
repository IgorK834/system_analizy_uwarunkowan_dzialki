"""Migracja 039 rozszerza schemat bez przepisywania istniejących wydań i analiz."""

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from app.db.session import engine

pytestmark = pytest.mark.integration


def test_upgrade_downgrade_and_single_head():
    cfg = Config("alembic.ini")
    script = ScriptDirectory.from_config(cfg)
    assert len(script.get_heads()) == 1
    assert script.get_revision("039_land_cover").down_revision == "038_source_dataset_date"
    command.upgrade(cfg, "head")
    db = inspect(engine)
    assert "bdot_landcover" in db.get_table_names()
    assert "land_cover" in {c["name"] for c in db.get_columns("analyses")}
    assert any(i["name"] == "ix_bdot_landcover_geometry_gist" for i in db.get_indexes("bdot_landcover"))
    command.downgrade(cfg, "038_source_dataset_date")
    db = inspect(engine)
    assert "bdot_landcover" not in db.get_table_names()
    assert "land_cover" not in {c["name"] for c in db.get_columns("analyses")}
    command.upgrade(cfg, "head")
