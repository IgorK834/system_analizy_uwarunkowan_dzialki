import pytest
from sqlalchemy import text

from app.db.session import SessionLocal


pytestmark = pytest.mark.integration


def test_database_select_one() -> None:
    with SessionLocal() as db:
        result = db.execute(text("SELECT 1")).scalar_one()

    assert result == 1


def test_postgis_area_query() -> None:
    query = text(
        "SELECT ST_Area("
        "ST_GeomFromText('POLYGON((0 0,1 0,1 1,0 1,0 0))',2180)"
        ")"
    )

    with SessionLocal() as db:
        result = db.execute(query).scalar_one()

    assert result > 0


def test_session_closes_after_context_manager() -> None:
    db = SessionLocal()

    with db:
        assert db.is_active is True

    assert db.is_active is True
