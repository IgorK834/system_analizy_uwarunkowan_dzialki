from sqlalchemy.dialects.postgresql import JSONB

from app.models.analysis import Analysis
from app.models.infrastructure import Infrastructure
from app.models.mpzp_parameter import MpzpParameter
from app.models.mpzp_zone import MpzpZone
from app.models.parcel import Parcel
from app.models.pog_data import PogData
from app.models.risk import Risk
from app.models.source_record import SourceRecord


def test_parcel_table_name() -> None:
    assert Parcel.__tablename__ == "parcels"


def test_analysis_table_name() -> None:
    assert Analysis.__tablename__ == "analyses"


def test_parcel_geometry_has_srid_2180() -> None:
    assert Parcel.__table__.c.geometry.type.srid == 2180


def test_analysis_parcel_id_foreign_key_points_to_parcels_id() -> None:
    foreign_keys = Analysis.__table__.c.parcel_id.foreign_keys

    assert {str(foreign_key.column) for foreign_key in foreign_keys} == {"parcels.id"}


def test_analysis_warnings_uses_jsonb() -> None:
    assert isinstance(Analysis.__table__.c.warnings.type, JSONB)


def test_domain_model_classes_have_expected_table_names() -> None:
    assert MpzpZone.__tablename__ == "mpzp_zones"
    assert MpzpParameter.__tablename__ == "mpzp_parameters"
    assert PogData.__tablename__ == "pog_data"
    assert Infrastructure.__tablename__ == "infrastructure_records"
    assert Risk.__tablename__ == "risk_records"
    assert SourceRecord.__tablename__ == "source_records"


def test_pog_data_has_audit_and_scenario_columns() -> None:
    expected_columns = {
        "zone_type",
        "in_ouz",
        "area_ratio",
        "in_downtown_area",
        "uchwala_nr",
        "uchwala_date",
        "manual_review_required",
        "compatibility_assessment",
        "raw_attributes",
    }

    assert expected_columns.issubset(PogData.__table__.c.keys())
    assert "conflict_with_mpzp" not in PogData.__table__.c.keys()
    assert isinstance(PogData.__table__.c.compatibility_assessment.type, JSONB)
    assert PogData.__table__.c.in_ouz.nullable is False
    assert PogData.__table__.c.in_downtown_area.nullable is False
    assert PogData.__table__.c.manual_review_required.nullable is False


def test_pog_raw_attributes_uses_jsonb() -> None:
    assert isinstance(PogData.__table__.c.raw_attributes.type, JSONB)


def test_infrastructure_has_buildable_area_audit_columns() -> None:
    expected_columns = {
        "zone_area_sqm",
        "rule_source",
        "rule_confidence",
        "rule_note",
        "affects_buildable_area",
        "network_geometry_geojson",
        "protection_zone_geojson",
    }

    assert expected_columns.issubset(Infrastructure.__table__.c.keys())
    assert Infrastructure.__table__.c.affects_buildable_area.nullable is False
    assert isinstance(Infrastructure.__table__.c.network_geometry_geojson.type, JSONB)
    assert isinstance(Infrastructure.__table__.c.protection_zone_geojson.type, JSONB)


def test_risk_geometry_uses_jsonb() -> None:
    assert isinstance(Risk.__table__.c.geometry_geojson.type, JSONB)


def test_source_record_remains_generic_audit_model() -> None:
    assert "zone_type" not in SourceRecord.__table__.c
    assert "in_ouz" not in SourceRecord.__table__.c
    assert {
        "source_name",
        "confidence",
        "manual_review_required",
        "warnings",
        "checksum",
    }.issubset(SourceRecord.__table__.c.keys())
