import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from app.db.session import engine


pytestmark = pytest.mark.integration


def test_alembic_upgrade_head_creates_core_tables() -> None:
    config = Config("alembic.ini")

    command.upgrade(config, "head")

    inspector = inspect(engine)
    assert "parcels" in inspector.get_table_names()
    assert "analyses" in inspector.get_table_names()


def test_parcels_geometry_has_srid_2180_after_migration() -> None:
    with engine.connect() as connection:
        result = connection.execute(
            text(
                "SELECT srid FROM geometry_columns "
                "WHERE f_table_name = 'parcels' "
                "AND f_geometry_column = 'geometry'"
            )
        ).scalar_one()

    assert result == 2180


def test_parcels_geometry_has_gist_index_after_migration() -> None:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename = 'parcels' "
                "AND indexdef ILIKE '%gist%'"
            )
        ).scalars()

    assert "ix_parcels_geometry_gist" in set(rows)
