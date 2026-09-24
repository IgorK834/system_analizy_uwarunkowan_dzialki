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


def test_pog_data_has_audit_columns_after_migration() -> None:
    config = Config("alembic.ini")
    command.upgrade(config, "head")

    columns = {
        column["name"]: column for column in inspect(engine).get_columns("pog_data")
    }

    assert columns["zone_type"]["nullable"] is True
    assert columns["in_ouz"]["nullable"] is False
    assert columns["area_ratio"]["nullable"] is True
    assert columns["in_downtown_area"]["nullable"] is False
    assert columns["manual_review_required"]["nullable"] is False
    assert columns["conflict_with_mpzp"]["nullable"] is True
    assert columns["raw_attributes"]["nullable"] is True


def test_infrastructure_has_buildable_area_audit_columns_after_migration() -> None:
    config = Config("alembic.ini")
    command.upgrade(config, "head")

    columns = {
        column["name"]: column
        for column in inspect(engine).get_columns("infrastructure_records")
    }

    assert columns["zone_area_sqm"]["nullable"] is True
    assert columns["rule_source"]["nullable"] is True
    assert columns["rule_confidence"]["nullable"] is True
    assert columns["rule_note"]["nullable"] is True
    assert columns["affects_buildable_area"]["nullable"] is False


def test_map_layer_geojson_columns_exist_after_migration() -> None:
    config = Config("alembic.ini")
    command.upgrade(config, "head")

    infrastructure_columns = {
        column["name"]
        for column in inspect(engine).get_columns("infrastructure_records")
    }
    risk_columns = {
        column["name"] for column in inspect(engine).get_columns("risk_records")
    }

    assert {
        "network_geometry_geojson",
        "protection_zone_geojson",
    }.issubset(infrastructure_columns)
    assert "geometry_geojson" in risk_columns


def test_analysis_has_utilities_preview_snapshot_column() -> None:
    config = Config("alembic.ini")
    command.upgrade(config, "head")

    columns = {
        column["name"]: column for column in inspect(engine).get_columns("analyses")
    }

    assert columns["utilities_preview"]["nullable"] is True


def test_pog_v2_migration_marks_legacy_snapshot_partial_without_inventing_zones() -> None:
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    command.downgrade(config, "014_utilities_preview")
    parcel_identifier = "ALEMBIC_POG_V2_LEGACY"
    parcel_id: int | None = None
    analysis_id: int | None = None
    try:
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM parcels WHERE parcel_identifier = :identifier"),
                {"identifier": parcel_identifier},
            )
            parcel_id = connection.execute(
                text(
                    "INSERT INTO parcels (parcel_identifier, geometry) "
                    "VALUES (:identifier, ST_Multi(ST_GeomFromText("
                    "'POLYGON((0 0,10 0,10 10,0 10,0 0))', 2180))) RETURNING id"
                ),
                {"identifier": parcel_identifier},
            ).scalar_one()
            analysis_id = connection.execute(
                text(
                    "INSERT INTO analyses (parcel_id, status) "
                    "VALUES (:parcel_id, 'partial') RETURNING id"
                ),
                {"parcel_id": parcel_id},
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO pog_data "
                    "(analysis_id, status, planning_zone, touches_ouz_boundary) "
                    "VALUES (:analysis_id, 'adopted', 'SJ', false)"
                ),
                {"analysis_id": analysis_id},
            )

        command.upgrade(config, "head")
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT schema_version, legacy_partial, result_v2 "
                    "FROM pog_data WHERE analysis_id = :analysis_id"
                ),
                {"analysis_id": analysis_id},
            ).one()
        assert tuple(row) == ("1.0", True, None)
        assert "pog_formal_documents" in inspect(engine).get_table_names()
        analysis_columns = {
            column["name"] for column in inspect(engine).get_columns("analyses")
        }
        assert {"data_release_ids", "result_contract_version", "cache_signature"} <= (
            analysis_columns
        )
    finally:
        command.upgrade(config, "head")
        if analysis_id is not None and parcel_id is not None:
            with engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM pog_data WHERE analysis_id = :analysis_id"),
                    {"analysis_id": analysis_id},
                )
                connection.execute(
                    text("DELETE FROM analyses WHERE id = :analysis_id"),
                    {"analysis_id": analysis_id},
                )
                connection.execute(
                    text("DELETE FROM parcels WHERE id = :parcel_id"),
                    {"parcel_id": parcel_id},
                )


def test_pog_audit_upgrade_downgrade_preserves_existing_columns() -> None:
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    parcel_identifier = "ALEMBIC_POG_AUDIT_ROUNDTRIP"

    with engine.begin() as connection:
        connection.execute(
            text(
                "DELETE FROM pog_data WHERE analysis_id IN ("
                "SELECT analyses.id FROM analyses JOIN parcels "
                "ON parcels.id = analyses.parcel_id "
                "WHERE parcels.parcel_identifier = :identifier)"
            ),
            {"identifier": parcel_identifier},
        )
        connection.execute(
            text(
                "DELETE FROM analyses WHERE parcel_id IN ("
                "SELECT id FROM parcels WHERE parcel_identifier = :identifier)"
            ),
            {"identifier": parcel_identifier},
        )
        connection.execute(
            text("DELETE FROM parcels WHERE parcel_identifier = :identifier"),
            {"identifier": parcel_identifier},
        )
        parcel_id = connection.execute(
            text(
                "INSERT INTO parcels (parcel_identifier, geometry) "
                "VALUES (:identifier, ST_Multi(ST_GeomFromText("
                "'POLYGON((0 0,10 0,10 10,0 10,0 0))', 2180))) "
                "RETURNING id"
            ),
            {"identifier": parcel_identifier},
        ).scalar_one()
        analysis_id = connection.execute(
            text(
                "INSERT INTO analyses (parcel_id, status) "
                "VALUES (:parcel_id, 'complete') RETURNING id"
            ),
            {"parcel_id": parcel_id},
        ).scalar_one()
        connection.execute(
            text(
                "INSERT INTO pog_data "
                "(analysis_id, status, planning_zone, touches_ouz_boundary, raw_attributes) "
                "VALUES (:analysis_id, 'adopted', 'SJ', false, CAST(:raw AS jsonb))"
            ),
            {"analysis_id": analysis_id, "raw": '{"zone_type": "SJ"}'},
        )

    try:
        command.downgrade(config, "003_source_audit")
        with engine.connect() as connection:
            old_values = connection.execute(
                text(
                    "SELECT status, planning_zone, touches_ouz_boundary "
                    "FROM pog_data WHERE analysis_id = :analysis_id"
                ),
                {"analysis_id": analysis_id},
            ).one()
        assert tuple(old_values) == ("adopted", "SJ", False)

        command.upgrade(config, "head")
        with engine.connect() as connection:
            restored = connection.execute(
                text(
                    "SELECT status, planning_zone, in_ouz, in_downtown_area, "
                    "manual_review_required FROM pog_data "
                    "WHERE analysis_id = :analysis_id"
                ),
                {"analysis_id": analysis_id},
            ).one()
        # Migracja 016 (BK-106): ``adopted`` bez potwierdzenia źródłowego
        # staje się ``unknown``, a oryginał trafia do ``legacy_status``.
        assert tuple(restored) == ("unknown", "SJ", False, False, False)
        with engine.connect() as connection:
            legacy = connection.execute(
                text("SELECT legacy_status FROM pog_data WHERE analysis_id = :analysis_id"),
                {"analysis_id": analysis_id},
            ).scalar_one()
        assert legacy == "adopted"
    finally:
        command.upgrade(config, "head")
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM pog_data WHERE analysis_id = :analysis_id"),
                {"analysis_id": analysis_id},
            )
            connection.execute(
                text("DELETE FROM analyses WHERE id = :analysis_id"),
                {"analysis_id": analysis_id},
            )
            connection.execute(
                text("DELETE FROM parcels WHERE id = :parcel_id"),
                {"parcel_id": parcel_id},
            )
