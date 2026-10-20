"""AU-201: append-only migration, spatial index and reversible queue contract."""
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
import pytest
from app.db.session import engine

pytestmark = pytest.mark.integration


def test_migration_appends_after_land_cover_and_is_reversible():
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == ["040_bdot_powerlines"]
    assert script.get_revision("040_bdot_powerlines").down_revision == "039_land_cover"
    command.upgrade(config, "head")
    assert "bdot_powerlines" in inspect(engine).get_table_names()
    assert "ix_bdot_powerlines_geometry_gist" in {row["name"] for row in inspect(engine).get_indexes("bdot_powerlines")}
    command.downgrade(config, "039_land_cover")
    assert "bdot_powerlines" not in inspect(engine).get_table_names()
    command.upgrade(config, "head")
