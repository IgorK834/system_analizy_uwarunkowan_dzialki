"""Migracja 029 (PV3-13): cache i provenance wywołań modelu, bez przepisywania historii.

Issue Task 20.13 nazywa ją „027 po head 026”; numery 027 i 028 zajęły PV3-04 i PV3-08, więc migracja
dołącza się po faktycznym head ``028``. Test integracyjny cofa bazę do schematu ``026`` z danymi,
podnosi do head (027 → 028 → 029) i cofa samą 029.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.db.session import SessionLocal, engine
from app.models.mpzp_zone import MpzpZone
from app.schemas.source import SourceMetadata
from app.services.mpzp_zones import unassigned_share_zone
from app.services.persistence import _mpzp_zone_response

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
LLM_COLUMNS = {"review_status", "model_id", "prompt_version", "response_sha256"}
SOURCE = SourceMetadata(source_name="MPZP_BIP", source_url=None, confidence=0.9, manual_review_required=False)


def test_migration_follows_head_028_is_additive_and_does_not_rewrite_history() -> None:
    migration = (VERSIONS / "029_mpzp_llm_extractions.py").read_text(encoding="utf-8")
    assert 'revision: str = "029_mpzp_llm_extractions"' in migration
    assert 'down_revision: Union[str, None] = "028_mpzp_parameter_condition"' in migration
    upgrade = migration.split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]
    assert "UPDATE" not in upgrade.upper() and 'op.create_table(\n        "mpzp_llm_extractions"' in upgrade
    downgrade = migration.split("def downgrade()", 1)[1]
    assert 'op.drop_table("mpzp_llm_extractions")' in downgrade
    script = ScriptDirectory.from_config(Config(str(VERSIONS.parents[1] / "alembic.ini")))
    chain = [revision.revision for revision in script.walk_revisions("base", "heads")]
    start = chain.index("029_mpzp_llm_extractions")
    assert chain[start : start + 4] == ["029_mpzp_llm_extractions", "028_mpzp_parameter_condition",
                                        "027_zone_symbol_length", "026_section_quality_matrix"]
    # Historyczne migracje nie wiedzą o tabeli cache (nic nie zostało przepisane wstecz).
    assert all("mpzp_llm_extractions" not in path.read_text(encoding="utf-8")
               for path in VERSIONS.glob("0*.py") if path.name < "029")  # migracje sprzed 029


@pytest.mark.integration
def test_upgrade_and_downgrade_on_026_data_keep_rows_and_snapshots() -> None:
    config = Config("alembic.ini")
    suffix = uuid4().hex[:8]
    ids: dict[str, int] = {}
    try:
        command.upgrade(config, "head")
        command.downgrade(config, "026_section_quality_matrix")
        tables = set(inspect(engine).get_table_names())
        assert "mpzp_llm_extractions" not in tables

        snapshot = unassigned_share_zone("1MN", SOURCE).model_dump(mode="json")
        snapshot["parameters"] = [{
            "name": "max_building_height_m", "normalized_value": 9.0, "raw_value": "9 m", "unit": "m",
            "evidence_text": "wysokość 9 m", "page_number": 3, "confidence": 0.7, "manual_review_required": False,
            "extraction_method": "pdf_text",
        }]
        with engine.begin() as conn:
            ids["parcel"] = conn.execute(
                text("INSERT INTO parcels (parcel_identifier, geometry) VALUES (:p, ST_Multi(ST_GeomFromText("
                     "'POLYGON((0 0,1 0,1 1,0 1,0 0))',2180))) RETURNING id"), {"p": f"MIG029_{suffix}"},
            ).scalar_one()
            ids["analysis"] = conn.execute(
                text("INSERT INTO analyses (parcel_id, status) VALUES (:p,'partial') RETURNING id"), {"p": ids["parcel"]}
            ).scalar_one()
            ids["zone"] = conn.execute(
                text("INSERT INTO mpzp_zones (analysis_id, zone_symbol, is_dominant, result_snapshot) "
                     "VALUES (:a,'1MN',false, CAST(:s AS jsonb)) RETURNING id"),
                {"a": ids["analysis"], "s": json.dumps(snapshot)},
            ).scalar_one()
            conn.execute(
                text("INSERT INTO mpzp_parameters (mpzp_zone_id, parameter_name, normalized_value, "
                     "manual_review_required, extraction_method) VALUES (:z,'max_building_height_m','9.0',false,'pdf_text')"),
                {"z": ids["zone"]},
            )

        command.upgrade(config, "head")
        inspector = inspect(engine)
        assert "mpzp_llm_extractions" in inspector.get_table_names()
        assert LLM_COLUMNS <= {c["name"] for c in inspector.get_columns("mpzp_parameters")}
        uniques = inspector.get_unique_constraints("mpzp_llm_extractions")
        assert any(item["column_names"] == ["cache_key"] for item in uniques)
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT review_status, model_id, prompt_version, response_sha256, extraction_method "
                     "FROM mpzp_parameters WHERE mpzp_zone_id = :z"), {"z": ids["zone"]}).one()
        assert tuple(row) == (None, None, None, None, "pdf_text")  # stare wiersze nie są przeliczane
        with SessionLocal() as db:
            zone = _mpzp_zone_response(db.get(MpzpZone, ids["zone"]), [])
        assert zone.parameters[0].review_status is None and zone.parameters[0].normalized_value == 9.0

        key = uuid4().hex + uuid4().hex
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO mpzp_llm_extractions (cache_key, document_sha256, block_sha256, prompt_version, "
                     "schema_version, model_id, params_hash, status) VALUES (:k, :d, :b, 'p', 's', 'm', :h, 'ok')"),
                {"k": key, "d": "a" * 64, "b": "b" * 64, "h": "c" * 64},
            )
            with pytest.raises(IntegrityError):
                with conn.begin_nested():
                    conn.execute(
                        text("INSERT INTO mpzp_llm_extractions (cache_key, document_sha256, block_sha256, prompt_version, "
                             "schema_version, model_id, params_hash, status) VALUES (:k, :d, :b, 'p', 's', 'm', :h, 'ok')"),
                        {"k": key, "d": "a" * 64, "b": "b" * 64, "h": "c" * 64},
                    )
            with pytest.raises(IntegrityError):
                with conn.begin_nested():
                    conn.execute(
                        text("INSERT INTO mpzp_llm_extractions (cache_key, document_sha256, block_sha256, prompt_version, "
                             "schema_version, model_id, params_hash, status) VALUES (:k, :d, :b, 'p', 's', 'm', :h, 'cached')"),
                        {"k": key[::-1], "d": "a" * 64, "b": "b" * 64, "h": "c" * 64},
                    )

        command.downgrade(config, "028_mpzp_parameter_condition")  # sama 029 w dół
        inspector = inspect(engine)
        assert "mpzp_llm_extractions" not in inspector.get_table_names()
        assert not LLM_COLUMNS & {c["name"] for c in inspector.get_columns("mpzp_parameters")}
        with engine.connect() as conn:
            assert conn.execute(
                text("SELECT count(*) FROM mpzp_parameters WHERE mpzp_zone_id = :z"), {"z": ids["zone"]}
            ).scalar_one() == 1
    finally:
        command.upgrade(config, "head")
        if ids:
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM mpzp_parameters WHERE mpzp_zone_id = :z"), {"z": ids["zone"]})
                conn.execute(text("DELETE FROM mpzp_zones WHERE id = :z"), {"z": ids["zone"]})
                conn.execute(text("DELETE FROM analyses WHERE id = :a"), {"a": ids["analysis"]})
                conn.execute(text("DELETE FROM parcels WHERE id = :p"), {"p": ids["parcel"]})
