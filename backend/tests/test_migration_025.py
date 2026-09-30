"""Migracja 025 (BK-503): zamrożony snapshot map raportu bez uzupełniania wstecz."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pymupdf
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from app.db.session import SessionLocal, engine
from app.services.report import generate_analysis_report_pdf

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def test_migration_follows_head_024_and_is_additive() -> None:
    migration = (VERSIONS / "025_report_map_snapshot.py").read_text(encoding="utf-8")
    assert 'revision: str = "025_report_map_snapshot"' in migration
    assert 'down_revision: Union[str, None] = "024_pog_area_summaries"' in migration
    upgrade = migration.split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]
    # Brak UPDATE: stare analizy nie są zamrażane bieżącą konfiguracją.
    assert "op.add_column" in upgrade and "UPDATE" not in upgrade.upper()
    assert 'op.drop_column("analyses", "report_map_snapshot")' in migration


@pytest.mark.integration
def test_old_analysis_keeps_null_snapshot_and_report_rebuilds_maps() -> None:
    config = Config("alembic.ini")
    suffix = uuid4().hex[:8]
    ids: dict[str, int] = {}
    try:
        command.upgrade(config, "head")
        command.downgrade(config, "024_pog_area_summaries")
        assert "report_map_snapshot" not in {c["name"] for c in inspect(engine).get_columns("analyses")}
        with engine.begin() as conn:
            ids["parcel"] = conn.execute(
                text(
                    "INSERT INTO parcels (parcel_identifier, geometry, area_sqm) VALUES "
                    "(:p, ST_Multi(ST_GeomFromText('POLYGON((566000 244000,566060 244000,"
                    "566060 244040,566000 244040,566000 244000))',2180)), 2400) RETURNING id"
                ),
                {"p": f"MIG025_{suffix}"},
            ).scalar_one()
            ids["analysis"] = conn.execute(
                text("INSERT INTO analyses (parcel_id, status) VALUES (:p, 'partial') RETURNING id"),
                {"p": ids["parcel"]},
            ).scalar_one()

        command.upgrade(config, "head")
        assert "report_map_snapshot" in {c["name"] for c in inspect(engine).get_columns("analyses")}
        with engine.connect() as conn:
            stored = conn.execute(
                text("SELECT report_map_snapshot FROM analyses WHERE id = :a"), {"a": ids["analysis"]}
            ).scalar_one()
        assert stored is None

        with SessionLocal() as db:
            pdf = generate_analysis_report_pdf(ids["analysis"], db)
        with pymupdf.open(stream=pdf, filetype="pdf") as doc:
            text_content = " ".join(page.get_text() for page in doc)
        assert "Mapa odtworzona z danych snapshotu" in text_content

        command.downgrade(config, "024_pog_area_summaries")
        assert "report_map_snapshot" not in {c["name"] for c in inspect(engine).get_columns("analyses")}
    finally:
        command.upgrade(config, "head")
        if ids:
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM analyses WHERE id = :a"), {"a": ids["analysis"]})
                conn.execute(text("DELETE FROM parcels WHERE id = :p"), {"p": ids["parcel"]})
