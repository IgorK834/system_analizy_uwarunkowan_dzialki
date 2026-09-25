"""Migracje 018/019 (BK-202/203): ID wydzieleń, snapshot stref i evidence parametrów."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from app.db.session import SessionLocal, engine
from tests.mpzp_fixtures import act, import_acts, origin, rect, zone

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def test_migrations_follow_current_head_in_order() -> None:
    m018 = (VERSIONS / "018_mpzp_vector_zone_intersections.py").read_text(encoding="utf-8")
    m019 = (VERSIONS / "019_mpzp_parameter_evidence.py").read_text(encoding="utf-8")
    assert 'down_revision: Union[str, None] = "017_pog_act_provenance"' in m018
    assert 'revision: str = "018_mpzp_vector_zones"' in m018
    assert 'down_revision: Union[str, None] = "018_mpzp_vector_zones"' in m019


@pytest.mark.integration
def test_backfill_formula_matches_import_identifier(tmp_path: Path) -> None:
    x, y = origin()
    identifier = f"plan-{uuid4().hex[:8]}"
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
        import_acts(
            db, tmp_path, f"mpzp_{uuid4().hex[:8]}",
            act(identifier, rect(x, y, x + 50, y + 50), (zone("1MN", rect(x, y, x + 50, y + 50)),)),
        )
        stored, recomputed = db.execute(
            text(
                """
                SELECT lua.zone_identifier,
                       pa.act_identifier || ':' || coalesce(lua.symbol, '') || ':'
                       || substr(encode(sha256(ST_AsBinary(ST_Normalize(lua.geometry))), 'hex'), 1, 16)
                FROM land_use_areas lua
                JOIN planning_act_versions pav ON pav.id = lua.planning_act_version_id
                JOIN planning_acts pa ON pa.id = pav.planning_act_id
                WHERE pa.act_identifier = :identifier
                """
            ),
            {"identifier": identifier},
        ).one()
        db.rollback()
    assert stored == recomputed


@pytest.mark.integration
def test_upgrade_backfills_ids_marks_legacy_and_downgrade_roundtrips() -> None:
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    command.downgrade(config, "017_pog_act_provenance")
    suffix = uuid4().hex[:8]
    ids: dict[str, int] = {}
    try:
        with engine.begin() as conn:
            ids["source"] = conn.execute(text("INSERT INTO data_sources (source_id, owner, status) VALUES (:s,'t','production') RETURNING id"), {"s": f"mig018_{suffix}"}).scalar_one()
            ids["artifact"] = conn.execute(text("INSERT INTO source_artifacts (data_source_id, uri, content_hash, fetched_at) VALUES (:d,'file:///x',:h,now()) RETURNING id"), {"d": ids["source"], "h": "f" * 64}).scalar_one()
            ids["release"] = conn.execute(text("INSERT INTO data_releases (data_source_id, version_label, published_at) VALUES (:d,'mig018',now()) RETURNING id"), {"d": ids["source"]}).scalar_one()
            ids["act"] = conn.execute(text("INSERT INTO planning_acts (act_identifier, teryt, kind) VALUES (:a,'1261011','mpzp') RETURNING id"), {"a": f"mig018-{suffix}"}).scalar_one()
            ids["version"] = conn.execute(text("INSERT INTO planning_act_versions (planning_act_id, legal_status, source_artifact_id, data_release_id, valid_from, published_at, content_hash) VALUES (:a,'adopted',:art,:rel,now(),now(),'h') RETURNING id"), {"a": ids["act"], "art": ids["artifact"], "rel": ids["release"]}).scalar_one()
            for _ in range(2):  # dwa identyczne wydzielenia w starej wersji
                conn.execute(text("INSERT INTO land_use_areas (planning_act_version_id, symbol, geometry) VALUES (:v,'1MN',ST_Multi(ST_GeomFromText('POLYGON((0 0,10 0,10 10,0 10,0 0))',2180)))"), {"v": ids["version"]})
            ids["parcel"] = conn.execute(text("INSERT INTO parcels (parcel_identifier, geometry) VALUES (:p, ST_Multi(ST_GeomFromText('POLYGON((0 0,1 0,1 1,0 1,0 0))',2180))) RETURNING id"), {"p": f"MIG018_{suffix}"}).scalar_one()
            ids["analysis"] = conn.execute(text("INSERT INTO analyses (parcel_id, status) VALUES (:p,'partial') RETURNING id"), {"p": ids["parcel"]}).scalar_one()
            ids["zone"] = conn.execute(text("INSERT INTO mpzp_zones (analysis_id, zone_symbol, intersection_area_sqm, intersection_pct, is_dominant) VALUES (:a,'MN',1,100,true) RETURNING id"), {"a": ids["analysis"]}).scalar_one()
            conn.execute(text("INSERT INTO mpzp_parameters (mpzp_zone_id, parameter_name, normalized_value, manual_review_required) VALUES (:z,'max_building_height_m','9',false)"), {"z": ids["zone"]})

        command.upgrade(config, "head")
        with engine.connect() as conn:
            identifiers = conn.execute(text("SELECT zone_identifier FROM land_use_areas WHERE planning_act_version_id = :v ORDER BY id"), {"v": ids["version"]}).scalars().all()
            method, snapshot = conn.execute(text("SELECT assignment_method, result_snapshot FROM mpzp_zones WHERE id = :z"), {"z": ids["zone"]}).one()
            evidence = conn.execute(text("SELECT raw_value, conflict_group_id FROM mpzp_parameters WHERE mpzp_zone_id = :z"), {"z": ids["zone"]}).one()
        base = f"mig018-{suffix}:1MN:"
        assert identifiers[0].startswith(base) and len(identifiers[0]) == len(base) + 16
        assert identifiers[1].startswith(identifiers[0] + ":")  # duplikat dostał sufiks, nie zniknął
        assert (method, snapshot) == ("legacy", None)
        assert tuple(evidence) == (None, None)
        with pytest.raises(Exception):
            with engine.begin() as conn:
                conn.execute(text("UPDATE mpzp_zones SET assignment_method = 'guess' WHERE id = :z"), {"z": ids["zone"]})

        with SessionLocal() as db:
            from app.models.analysis import Analysis
            from app.services.persistence import build_analyze_response_from_analysis

            response = build_analyze_response_from_analysis(db.get(Analysis, ids["analysis"]), db)
        assert response.mpzp_zones[0].assignment_method == "legacy"
        assert response.mpzp_zones[0].max_building_height_m == 9.0

        command.downgrade(config, "017_pog_act_provenance")
        assert "zone_identifier" not in {c["name"] for c in inspect(engine).get_columns("land_use_areas")}
        assert "conflict_group_id" not in {c["name"] for c in inspect(engine).get_columns("mpzp_parameters")}
        assert "result_snapshot" not in {c["name"] for c in inspect(engine).get_columns("mpzp_zones")}
    finally:
        command.upgrade(config, "head")
        if ids:
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM mpzp_parameters WHERE mpzp_zone_id = :z"), {"z": ids.get("zone")})
                conn.execute(text("DELETE FROM mpzp_zones WHERE id = :z"), {"z": ids.get("zone")})
                conn.execute(text("DELETE FROM analyses WHERE id = :a"), {"a": ids.get("analysis")})
                conn.execute(text("DELETE FROM parcels WHERE id = :p"), {"p": ids.get("parcel")})
                conn.execute(text("DELETE FROM land_use_areas WHERE planning_act_version_id = :v"), {"v": ids.get("version")})
                conn.execute(text("DELETE FROM planning_act_versions WHERE id = :v"), {"v": ids.get("version")})
                conn.execute(text("DELETE FROM planning_acts WHERE id = :a"), {"a": ids.get("act")})
                conn.execute(text("DELETE FROM data_releases WHERE id = :r"), {"r": ids.get("release")})
                conn.execute(text("DELETE FROM source_artifacts WHERE id = :a"), {"a": ids.get("artifact")})
                conn.execute(text("DELETE FROM data_sources WHERE id = :s"), {"s": ids.get("source")})
