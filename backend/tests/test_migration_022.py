"""Migracja 022 (BK-301/302): snapshot ``analyses.terrain`` bez fikcyjnego zera."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from app.db.session import SessionLocal, engine
from app.services.cache import RESULT_CONTRACT_VERSION

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def test_migration_follows_head_021_and_fits_version_column() -> None:
    migration = (VERSIONS / "022_analysis_terrain_snapshot.py").read_text(encoding="utf-8")
    assert 'revision: str = "022_analysis_terrain"' in migration
    assert 'down_revision: Union[str, None] = "021_compatibility_assessment"' in migration
    assert len("022_analysis_terrain") <= 32
    # Upgrade nie wypełnia starych wierszy wartościami wysokości.
    assert "UPDATE analyses SET terrain" not in migration
    assert len(RESULT_CONTRACT_VERSION) <= 64
    assert "+terrain-v1.0" in RESULT_CONTRACT_VERSION


@pytest.mark.integration
def test_old_database_upgrades_without_fake_zero_and_downgrade_roundtrips() -> None:
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    command.downgrade(config, "021_compatibility_assessment")
    suffix = uuid4().hex[:8]
    ids: dict[str, int] = {}
    try:
        columns = {column["name"] for column in inspect(engine).get_columns("analyses")}
        assert "terrain" not in columns
        with engine.begin() as conn:
            ids["parcel"] = conn.execute(
                text(
                    "INSERT INTO parcels (parcel_identifier, geometry, area_sqm) VALUES "
                    "(:p, ST_Multi(ST_GeomFromText('POLYGON((637000 486000,637100 486000,"
                    "637100 486100,637000 486100,637000 486000))',2180)), 10000) RETURNING id"
                ),
                {"p": f"MIG022_{suffix}"},
            ).scalar_one()
            ids["analysis"] = conn.execute(
                text(
                    "INSERT INTO analyses (parcel_id, status, result_contract_version) "
                    "VALUES (:p, 'partial', 'pog-v2.3+mpzp-v2.1') RETURNING id"
                ),
                {"p": ids["parcel"]},
            ).scalar_one()
            conn.execute(
                text(
                    "INSERT INTO source_records (analysis_id, source_name, response_status, "
                    "manual_review_required) VALUES (:a, 'NMT', 'available', false)"
                ),
                {"a": ids["analysis"]},
            )

        command.upgrade(config, "head")

        columns = {column["name"]: column for column in inspect(engine).get_columns("analyses")}
        assert columns["terrain"]["nullable"] is True
        assert columns["result_contract_version"]["type"].length == 64
        with engine.connect() as conn:
            raw = conn.execute(
                text("SELECT terrain FROM analyses WHERE id = :a"), {"a": ids["analysis"]}
            ).scalar_one()
        assert raw is None

        with SessionLocal() as db:
            from app.models.analysis import Analysis
            from app.services.persistence import build_analyze_response_from_analysis

            response = build_analyze_response_from_analysis(db.get(Analysis, ids["analysis"]), db)
        assert response.terrain is not None
        assert response.terrain.status == "unknown"
        assert response.terrain.reason_code == "LEGACY_SNAPSHOT"
        assert response.terrain.height_difference_m is None
        assert response.terrain.min_height_m is None

        with engine.begin() as conn:
            conn.execute(
                text("UPDATE analyses SET result_contract_version = :v WHERE id = :a"),
                {"v": RESULT_CONTRACT_VERSION, "a": ids["analysis"]},
            )
        command.downgrade(config, "021_compatibility_assessment")
        columns = {column["name"]: column for column in inspect(engine).get_columns("analyses")}
        assert "terrain" not in columns
        assert columns["result_contract_version"]["type"].length == 20
        with engine.connect() as conn:
            version = conn.execute(
                text("SELECT result_contract_version FROM analyses WHERE id = :a"),
                {"a": ids["analysis"]},
            ).scalar_one()
        assert version == RESULT_CONTRACT_VERSION[:20]
    finally:
        command.upgrade(config, "head")
        if ids:
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM source_records WHERE analysis_id = :a"), {"a": ids.get("analysis")})
                conn.execute(text("DELETE FROM analyses WHERE id = :a"), {"a": ids.get("analysis")})
                conn.execute(text("DELETE FROM parcels WHERE id = :p"), {"p": ids.get("parcel")})
