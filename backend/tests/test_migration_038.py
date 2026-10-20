"""AU-111: metadane daty w rejestrze źródeł przetrwają zapis/odczyt; jedna nowa migracja."""
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
import pytest

from app.db.session import engine
from app.models.source_record import SourceRecord
from app.schemas.source import SourceMetadata
from app.services.persistence import _source_metadata_from_record, _source_record_data
from tests.test_section_quality import _response


def test_dataset_date_survives_record_mapping():
    source = SourceMetadata(source_name="ISOK", confidence=0.9, manual_review_required=False,
                            data_as_of="2026-01-15", data_as_of_basis="HTTP Last-Modified")
    data = _source_record_data(source, _response())
    record = SourceRecord(source_name=data.source_name, confidence=data.confidence, fetched_at=data.fetched_at,
                          manual_review_required=data.manual_review_required, dataset_metadata=data.dataset_metadata)
    reread = _source_metadata_from_record(record)
    assert reread.data_as_of == source.data_as_of and reread.data_as_of_basis == source.data_as_of_basis
    assert reread.data_as_of_status == "known" and reread.data_as_of_reason is None


@pytest.mark.integration
def test_migration_chains_after_actual_head_and_preserves_other_source_columns():
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    assert len(script.get_heads()) == 1
    assert script.get_revision("038_source_dataset_date").down_revision == "037_pog_registry"
    command.upgrade(config, "head")
    assert "dataset_metadata" in {item["name"] for item in inspect(engine).get_columns("source_records")}
    command.downgrade(config, "037_pog_registry")
    assert "dataset_metadata" not in {item["name"] for item in inspect(engine).get_columns("source_records")}
    command.upgrade(config, "head")
