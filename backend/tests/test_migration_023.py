"""Migracja 023 (BK-303): strukturalne ryzyka bez odzyskiwania danych z opisu."""

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
NEW_COLUMNS = {
    "section",
    "feature_id",
    "severity",
    "probability_class",
    "return_period_years",
    "protection_type",
    "name",
    "intersection_area_sqm",
    "intersection_pct",
    "touches_boundary",
    "result_snapshot",
}


def test_migration_follows_head_022() -> None:
    migration = (VERSIONS / "023_structured_risk_results.py").read_text(encoding="utf-8")
    assert 'revision: str = "023_structured_risks"' in migration
    assert 'down_revision: Union[str, None] = "022_analysis_terrain"' in migration
    assert "description" not in migration.split('"""', 2)[2].replace("``description``", "")
    assert "+risk-v1.0" in RESULT_CONTRACT_VERSION
    assert len(RESULT_CONTRACT_VERSION) <= 64


@pytest.mark.integration
def test_old_risks_stay_unknown_after_upgrade_and_downgrade_roundtrips() -> None:
    config = Config("alembic.ini")
    suffix = uuid4().hex[:8]
    ids: dict[str, int] = {}
    try:
        # BK-306: upgrade/downgrade też wewnątrz try — nieudany downgrade nie
        # może zostawić bazy w pośredniej rewizji bez przywrócenia head.
        command.upgrade(config, "head")
        command.downgrade(config, "022_analysis_terrain")
        assert not NEW_COLUMNS & {c["name"] for c in inspect(engine).get_columns("risk_records")}
        with engine.begin() as conn:
            ids["parcel"] = conn.execute(
                text(
                    "INSERT INTO parcels (parcel_identifier, geometry, area_sqm) VALUES "
                    "(:p, ST_Multi(ST_GeomFromText('POLYGON((0 0,100 0,100 100,0 100,0 0))',2180)), 10000) "
                    "RETURNING id"
                ),
                {"p": f"MIG023_{suffix}"},
            ).scalar_one()
            ids["analysis"] = conn.execute(
                text("INSERT INTO analyses (parcel_id, status) VALUES (:p, 'partial') RETURNING id"),
                {"p": ids["parcel"]},
            ).scalar_one()
            for risk_type, description in (
                ("flood", "Ryzyko powodziowe: poziom high, udział przecięcia 12.50%."),
                ("natura_2000", "Forma ochrony przyrody Dolina; poziom low, udział 100.00%."),
            ):
                conn.execute(
                    text(
                        "INSERT INTO risk_records (analysis_id, risk_type, description, confidence, "
                        "manual_review_required) VALUES (:a, :t, :d, 0.85, false)"
                    ),
                    {"a": ids["analysis"], "t": risk_type, "d": description},
                )

        command.upgrade(config, "head")

        columns = {c["name"] for c in inspect(engine).get_columns("risk_records")}
        assert NEW_COLUMNS <= columns
        assert "risk_sections" in {c["name"] for c in inspect(engine).get_columns("analyses")}
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT risk_type, section, severity, intersection_pct, intersection_area_sqm, "
                    "touches_boundary, result_snapshot FROM risk_records WHERE analysis_id = :a ORDER BY id"
                ),
                {"a": ids["analysis"]},
            ).all()
        # Sekcja wynika ze strukturalnego risk_type; reszta pozostaje nieznana.
        assert [(row.risk_type, row.section) for row in rows] == [("flood", "flood"), ("natura_2000", "nature")]
        for row in rows:
            assert (row.severity, row.intersection_pct, row.intersection_area_sqm) == (None, None, None)
            assert row.touches_boundary is None and row.result_snapshot is None

        with SessionLocal() as db:
            from app.models.analysis import Analysis
            from app.services.persistence import build_analyze_response_from_analysis

            response = build_analyze_response_from_analysis(db.get(Analysis, ids["analysis"]), db)
        assert [risk.section for risk in response.risks] == ["flood", "nature"]
        assert all(risk.intersection_pct is None and risk.severity is None for risk in response.risks)
        assert response.risks[0].source.source_name == "ISOK"
        assert response.risks[1].source.source_name == "GDOS"
        assert [(s.section, s.status) for s in response.risk_sections] == [("flood", "unknown"), ("nature", "unknown")]

        command.downgrade(config, "022_analysis_terrain")
        assert not NEW_COLUMNS & {c["name"] for c in inspect(engine).get_columns("risk_records")}
        assert "risk_sections" not in {c["name"] for c in inspect(engine).get_columns("analyses")}
    finally:
        command.upgrade(config, "head")
        if ids:
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM risk_records WHERE analysis_id = :a"), {"a": ids["analysis"]})
                conn.execute(text("DELETE FROM analyses WHERE id = :a"), {"a": ids["analysis"]})
                conn.execute(text("DELETE FROM parcels WHERE id = :p"), {"p": ids["parcel"]})
