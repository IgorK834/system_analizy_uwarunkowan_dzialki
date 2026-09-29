"""Migracje 020/021 (BK-204/205): przypięty dokument, nieustalony udział i ocena MPZP–POG."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from app.db.session import SessionLocal, engine

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def test_migrations_follow_current_head_in_order() -> None:
    m020 = (VERSIONS / "020_manual_zone_pending_document.py").read_text(encoding="utf-8")
    m021 = (VERSIONS / "021_mpzp_pog_compatibility_assessment.py").read_text(encoding="utf-8")
    assert 'down_revision: Union[str, None] = "019_mpzp_parameter_evidence"' in m020
    assert 'revision: str = "020_manual_zone_pending_doc"' in m020
    assert 'down_revision: Union[str, None] = "020_manual_zone_pending_doc"' in m021
    # alembic_version.version_num ma 32 znaki.
    assert len("020_manual_zone_pending_doc") <= 32
    assert len("021_compatibility_assessment") <= 32


def test_frozen_migration_texts_match_application_constants() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "m021", VERSIONS / "021_mpzp_pog_compatibility_assessment.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from app.schemas.analyze import CompatibilityAssessment, legacy_compatibility_assessment

    frozen = module.legacy_assessment(True, None)
    current = legacy_compatibility_assessment(True, None)
    assert CompatibilityAssessment.model_validate(frozen) == CompatibilityAssessment.model_validate(current)
    assert module.conflict_from_assessment({"status": "incompatible"}) is True
    assert module.conflict_from_assessment({"status": "compatible"}) is False
    assert module.conflict_from_assessment({"status": "uncertain"}) is None
    assert module.conflict_from_assessment(None) is None
    assert module.legacy_assessment(None, {"scenario": {"message": "x"}}) is None


@pytest.mark.integration
def test_upgrade_moves_legacy_boolean_nulls_fake_share_and_downgrade_roundtrips() -> None:
    config = Config("alembic.ini")
    suffix = uuid4().hex[:8]
    ids: dict[str, int] = {}
    legacy_result = {
        "schema_version": "2.2",
        "legal_status": "unknown",
        "touches_ouz_boundary": False,
        "conflict_with_mpzp": True,
        "raw_attributes": {
            "scenario": {
                "compatibility": {
                    "result": "incompatible",
                    "reasoning": "stara reguła",
                    "confidence": 0.85,
                }
            }
        },
    }
    try:
        # BK-306: upgrade/downgrade też wewnątrz try — nieudany downgrade nie
        # może zostawić bazy w pośredniej rewizji bez przywrócenia head.
        command.upgrade(config, "head")
        command.downgrade(config, "019_mpzp_parameter_evidence")
        with engine.begin() as conn:
            ids["parcel"] = conn.execute(
                text("INSERT INTO parcels (parcel_identifier, geometry, area_sqm) VALUES (:p, ST_Multi(ST_GeomFromText('POLYGON((0 0,10 0,10 10,0 10,0 0))',2180)), 100) RETURNING id"),
                {"p": f"MIG020_{suffix}"},
            ).scalar_one()
            ids["analysis"] = conn.execute(
                text("INSERT INTO analyses (parcel_id, status) VALUES (:p,'partial') RETURNING id"),
                {"p": ids["parcel"]},
            ).scalar_one()
            ids["manual_zone"] = conn.execute(
                text(
                    "INSERT INTO mpzp_zones (analysis_id, zone_symbol, intersection_area_sqm, intersection_pct, "
                    "is_dominant, assignment_method, result_snapshot) VALUES (:a,'230_U',100,100,true,'manual_user_input', "
                    "CAST(:snap AS jsonb)) RETURNING id"
                ),
                {"a": ids["analysis"], "snap": json.dumps({"zone_symbol": "230_U", "intersection_pct": 100.0, "intersection_area_sqm": 100.0, "is_dominant": True, "assignment_method": "manual_user_input", "source": {"source_name": "manual_user_input", "confidence": 0.5, "manual_review_required": True}})},
            ).scalar_one()
            ids["vector_zone"] = conn.execute(
                text(
                    "INSERT INTO mpzp_zones (analysis_id, zone_symbol, intersection_area_sqm, intersection_pct, "
                    "is_dominant, assignment_method) VALUES (:a,'1MN',60,60,true,'vector_intersection') RETURNING id"
                ),
                {"a": ids["analysis"]},
            ).scalar_one()
            ids["pog_v2"] = conn.execute(
                text(
                    "INSERT INTO pog_data (analysis_id, status, legal_status, touches_ouz_boundary, conflict_with_mpzp, "
                    "raw_attributes, schema_version, result_v2) VALUES (:a,'unknown','unknown',false,true, "
                    "CAST(:raw AS jsonb),'2.2',CAST(:res AS jsonb)) RETURNING id"
                ),
                {"a": ids["analysis"], "raw": json.dumps(legacy_result["raw_attributes"]), "res": json.dumps(legacy_result)},
            ).scalar_one()
            ids["pog_v1"] = conn.execute(
                text(
                    "INSERT INTO pog_data (analysis_id, status, legal_status, touches_ouz_boundary, conflict_with_mpzp) "
                    "VALUES (:a,'unknown','unknown',false,false) RETURNING id"
                ),
                {"a": ids["analysis"]},
            ).scalar_one()
            ids["pog_none"] = conn.execute(
                text(
                    "INSERT INTO pog_data (analysis_id, status, legal_status, touches_ouz_boundary) "
                    "VALUES (:a,'unknown','unknown',false) RETURNING id"
                ),
                {"a": ids["analysis"]},
            ).scalar_one()

        command.upgrade(config, "head")
        columns = {column["name"] for column in inspect(engine).get_columns("pog_data")}
        assert "conflict_with_mpzp" not in columns and "compatibility_assessment" in columns
        assert "analysis_pending_documents" in inspect(engine).get_table_names()
        with engine.connect() as conn:
            manual = conn.execute(text("SELECT intersection_area_sqm, intersection_pct, is_dominant, result_snapshot FROM mpzp_zones WHERE id = :z"), {"z": ids["manual_zone"]}).one()
            vector = conn.execute(text("SELECT intersection_pct, is_dominant FROM mpzp_zones WHERE id = :z"), {"z": ids["vector_zone"]}).one()
            v2 = conn.execute(text("SELECT compatibility_assessment, result_v2 FROM pog_data WHERE id = :p"), {"p": ids["pog_v2"]}).one()
            v1 = conn.execute(text("SELECT compatibility_assessment FROM pog_data WHERE id = :p"), {"p": ids["pog_v1"]}).scalar_one()
            none = conn.execute(text("SELECT compatibility_assessment FROM pog_data WHERE id = :p"), {"p": ids["pog_none"]}).scalar_one()
        # Fikcyjne 100% ręcznej strefy staje się nieustalone; wektor bez zmian.
        assert tuple(manual[:3]) == (None, None, False)
        assert manual.result_snapshot["intersection_pct"] is None
        assert manual.result_snapshot["is_dominant"] is False
        assert tuple(vector) == (60.0, True)
        # Boolean → legacy evidence ze statusem unknown, bez udawania pełnej oceny.
        assessment, result_v2 = v2
        assert assessment["status"] == "unknown"
        assert assessment["reason_code"] == "LEGACY_BOOLEAN_ONLY"
        assert assessment["legacy_evidence"]["conflict_with_mpzp"] is True
        assert assessment["legacy_evidence"]["result"] == "incompatible"
        assert "conflict_with_mpzp" not in result_v2
        assert result_v2["compatibility_assessment"] == assessment
        assert v1["legacy_evidence"] == {
            "origin": "pog_data.conflict_with_mpzp",
            "conflict_with_mpzp": False,
            "result": None,
            "reasoning": None,
            "confidence": None,
        }
        assert none is None

        with SessionLocal() as db:
            from app.models.analysis import Analysis
            from app.services.persistence import build_analyze_response_from_analysis

            response = build_analyze_response_from_analysis(db.get(Analysis, ids["analysis"]), db)
        manual_zone = next(zone for zone in response.mpzp_zones if zone.zone_symbol == "230_U")
        assert manual_zone.intersection_pct is None
        assert response.pog is not None
        assert response.pog.compatibility_assessment is not None

        command.downgrade(config, "019_mpzp_parameter_evidence")
        assert "analysis_pending_documents" not in inspect(engine).get_table_names()
        with engine.connect() as conn:
            restored = conn.execute(text("SELECT conflict_with_mpzp, result_v2 FROM pog_data WHERE id = :p"), {"p": ids["pog_v2"]}).one()
            restored_v1 = conn.execute(text("SELECT conflict_with_mpzp FROM pog_data WHERE id = :p"), {"p": ids["pog_v1"]}).scalar_one()
            manual_back = conn.execute(text("SELECT intersection_area_sqm, intersection_pct, is_dominant FROM mpzp_zones WHERE id = :z"), {"z": ids["manual_zone"]}).one()
        assert restored.conflict_with_mpzp is True
        assert restored.result_v2["conflict_with_mpzp"] is True
        assert "compatibility_assessment" not in restored.result_v2
        assert restored_v1 is False
        assert tuple(manual_back) == (100.0, 100.0, True)
    finally:
        command.upgrade(config, "head")
        if ids:
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM pog_data WHERE analysis_id = :a"), {"a": ids.get("analysis")})
                conn.execute(text("DELETE FROM mpzp_zones WHERE analysis_id = :a"), {"a": ids.get("analysis")})
                conn.execute(text("DELETE FROM analyses WHERE id = :a"), {"a": ids.get("analysis")})
                conn.execute(text("DELETE FROM parcels WHERE id = :p"), {"p": ids.get("parcel")})


@pytest.mark.integration
def test_pending_document_constraints_and_cascade() -> None:
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    suffix = uuid4().hex[:8]
    with engine.begin() as conn:
        parcel_id = conn.execute(
            text("INSERT INTO parcels (parcel_identifier, geometry) VALUES (:p, ST_Multi(ST_GeomFromText('POLYGON((0 0,1 0,1 1,0 1,0 0))',2180))) RETURNING id"),
            {"p": f"MIG020C_{suffix}"},
        ).scalar_one()
        analysis_id = conn.execute(
            text("INSERT INTO analyses (parcel_id, status) VALUES (:p,'waiting_for_zone_symbol') RETURNING id"),
            {"p": parcel_id},
        ).scalar_one()
    insert = text(
        "INSERT INTO analysis_pending_documents (analysis_id, requested_url, media_type, content, "
        "content_sha256, size_bytes) VALUES (:a, 'https://x', 'application/pdf', :c, :h, :n)"
    )
    try:
        with pytest.raises(Exception):
            with engine.begin() as conn:
                conn.execute(insert, {"a": analysis_id, "c": b"x", "h": "short", "n": 1})
        with engine.begin() as conn:
            conn.execute(insert, {"a": analysis_id, "c": b"x", "h": "b" * 64, "n": 1})
        with pytest.raises(Exception):  # jeden przypięty dokument na analizę
            with engine.begin() as conn:
                conn.execute(insert, {"a": analysis_id, "c": b"y", "h": "c" * 64, "n": 1})
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM analyses WHERE id = :a"), {"a": analysis_id})
            remaining = conn.execute(
                text("SELECT count(*) FROM analysis_pending_documents WHERE analysis_id = :a"),
                {"a": analysis_id},
            ).scalar_one()
        assert remaining == 0
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM analyses WHERE id = :a"), {"a": analysis_id})
            conn.execute(text("DELETE FROM parcels WHERE id = :p"), {"p": parcel_id})
