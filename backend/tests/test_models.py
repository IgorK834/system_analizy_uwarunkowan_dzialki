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
