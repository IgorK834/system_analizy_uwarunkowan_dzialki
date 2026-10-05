"""Migracja 028 (PV3-08): warunki i rodzaj wartości w evidence parametrów MPZP."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, select, text

from app.db.session import SessionLocal, engine
from app.models.analysis import Analysis
from app.models.mpzp_parameter import MpzpParameter
from app.models.mpzp_zone import MpzpZone
from app.models.parcel import Parcel
from app.schemas import analyze as analyze_schemas
from app.schemas.source import SourceMetadata
from app.services.persistence import _mpzp_zone_response, add_mpzp_zone_snapshot
from app.services.mpzp_zones import unassigned_share_zone

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
SOURCE = SourceMetadata(source_name="MPZP_BIP", source_url=None, confidence=0.9, manual_review_required=False)


def test_migration_follows_head_027_and_is_additive_and_reversible() -> None:
    migration = (VERSIONS / "028_mpzp_parameter_condition.py").read_text(encoding="utf-8")
    assert 'revision: str = "028_mpzp_parameter_condition"' in migration
    assert 'down_revision: Union[str, None] = "027_zone_symbol_length"' in migration
    upgrade = migration.split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]
    assert upgrade.count("op.add_column") == 2 and "UPDATE" not in upgrade.upper()  # bez przeliczania wierszy
    downgrade = migration.split("def downgrade()", 1)[1]
    assert 'op.drop_column("mpzp_parameters", "conditions")' in downgrade and "drop_constraint" in downgrade
    assert len(list(VERSIONS.glob("02[0-9]_*.py"))) >= 8  # historia nie została przepisana


def _evidence(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "max_building_height_m", "normalized_value": 9.0, "raw_value": "9 m", "unit": "m",
        "evidence_text": "wysokość 9 m", "page_number": 3, "segment_id": None, "legal_unit_id": None,
        "document_sha256": "d" * 64, "document_version_id": 1, "parser_version": "mpzp-parser/2.0",
        "extraction_method": "pdf_text", "confidence": 0.7, "conflict_group_id": None, "manual_review_required": False,
    }
    payload.update(overrides)
    return payload


@pytest.mark.integration
def test_upgrade_and_downgrade_on_026_data_keep_rows_and_old_snapshots_read_without_conditions() -> None:
    config = Config("alembic.ini")
    suffix = uuid4().hex[:8]
    ids: dict[str, int] = {}
    try:
        command.upgrade(config, "head")
        command.downgrade(config, "026_section_quality_matrix")
        columns = {c["name"] for c in inspect(engine).get_columns("mpzp_parameters")}
        assert not {"conditions", "value_kind"} & columns

        snapshot = unassigned_share_zone("1MN", SOURCE).model_dump(mode="json")
        snapshot["parameters"] = [_evidence(), _evidence(normalized_value=12.0, conflict_group_id="v:1MN:h")]
        with engine.begin() as conn:
            ids["parcel"] = conn.execute(
                text("INSERT INTO parcels (parcel_identifier, geometry) VALUES (:p, ST_Multi(ST_GeomFromText('POLYGON((0 0,1 0,1 1,0 1,0 0))',2180))) RETURNING id"),
                {"p": f"MIG028_{suffix}"},
            ).scalar_one()
            ids["analysis"] = conn.execute(
                text("INSERT INTO analyses (parcel_id, status) VALUES (:p,'partial') RETURNING id"), {"p": ids["parcel"]}
            ).scalar_one()
            ids["zone"] = conn.execute(
                text("INSERT INTO mpzp_zones (analysis_id, zone_symbol, intersection_area_sqm, intersection_pct, is_dominant, result_snapshot) "
                     "VALUES (:a,'1MN',1,100,true, CAST(:s AS jsonb)) RETURNING id"),
                {"a": ids["analysis"], "s": json.dumps(snapshot)},
            ).scalar_one()
            for value, group in ((9.0, None), (12.0, "v:1MN:h")):
                conn.execute(
                    text("INSERT INTO mpzp_parameters (mpzp_zone_id, parameter_name, normalized_value, manual_review_required, conflict_group_id) "
                         "VALUES (:z,'max_building_height_m',:v,false,:g)"),
                    {"z": ids["zone"], "v": str(value), "g": group},
                )

        command.upgrade(config, "head")
        columns = {c["name"] for c in inspect(engine).get_columns("mpzp_parameters")}
        assert {"conditions", "value_kind"} <= columns
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT conditions, value_kind FROM mpzp_parameters WHERE mpzp_zone_id = :z"), {"z": ids["zone"]}
            ).all()
        assert rows == [(None, None), (None, None)]  # stare wiersze nie są przeliczane

        with SessionLocal() as db:
            zone = _mpzp_zone_response(db.get(MpzpZone, ids["zone"]), [])
        assert [p.value_kind for p in zone.parameters] == ["unconditional", "conflict"]
        assert all(p.conditions == [] for p in zone.parameters)

        command.downgrade(config, "026_section_quality_matrix")  # wiersze przeżywają, kolumny znikają
        with engine.connect() as conn:
            assert conn.execute(
                text("SELECT count(*) FROM mpzp_parameters WHERE mpzp_zone_id = :z"), {"z": ids["zone"]}
            ).scalar_one() == 2
        assert not {"conditions", "value_kind"} & {c["name"] for c in inspect(engine).get_columns("mpzp_parameters")}
    finally:
        command.upgrade(config, "head")
        if ids:
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM mpzp_parameters WHERE mpzp_zone_id = :z"), {"z": ids["zone"]})
                conn.execute(text("DELETE FROM mpzp_zones WHERE id = :z"), {"z": ids["zone"]})
                conn.execute(text("DELETE FROM analyses WHERE id = :a"), {"a": ids["analysis"]})
                conn.execute(text("DELETE FROM parcels WHERE id = :p"), {"p": ids["parcel"]})


@pytest.mark.integration
def test_new_snapshot_writes_conditions_and_kind_to_the_evidence_rows_and_reads_back() -> None:
    condition = analyze_schemas.MpzpValueCondition(kind="roof_type", label="dach płaski", quote="dachem płaskim")
    parameters = [
        analyze_schemas.MpzpParameterEvidence(
            name="max_building_height_m", normalized_value=9.5, raw_value="9,5 m", unit="m",
            evidence_text="dla budynków przekrytych dachem płaskim: 9,5 m", page_number=4, confidence=0.8,
            conditions=[condition], value_kind="conditional",
        ),
        analyze_schemas.MpzpParameterEvidence(
            name="max_building_height_m", normalized_value=11.0, raw_value="11 m", unit="m",
            evidence_text="maksymalną wysokość zabudowy: 11 m", page_number=4, confidence=0.8,
        ),
    ]
    zone_result = unassigned_share_zone("1MN", SOURCE).model_copy(
        update={"parameters": parameters, "assignment_method": "vector_intersection", "zone_id": "plan:1MN:x"}
    )
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
        parcel = Parcel(parcel_identifier=f"MIG028N_{uuid4().hex[:6]}", geometry="SRID=2180;MULTIPOLYGON(((0 0,1 0,1 1,0 1,0 0)))")
        db.add(parcel)
        db.flush()
        analysis = Analysis(parcel_id=parcel.id, status="partial")
        db.add(analysis)
        db.flush()
        record = add_mpzp_zone_snapshot(db, analysis.id, zone_result)
        db.flush()
        rows = db.scalars(
            select(MpzpParameter).where(MpzpParameter.mpzp_zone_id == record.id).order_by(MpzpParameter.id)
        ).all()
        assert rows[0].conditions == [{"kind": "roof_type", "label": "dach płaski", "quote": "dachem płaskim"}]
        assert (rows[0].value_kind, rows[1].conditions, rows[1].value_kind) == ("conditional", None, "unconditional")
        restored = _mpzp_zone_response(record, [])
        assert [p.conditions for p in restored.parameters] == [[condition], []]
        assert [p.value_kind for p in restored.parameters] == ["conditional", "unconditional"]
        db.rollback()
