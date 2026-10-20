"""AU-201: coverage request -> deduplicated queue -> real frozen import; retry and schema failure."""
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import text

from app.db.session import SessionLocal
from app.main import app
from app.modules.imports.api.sync_loop import process_one_job
from app.modules.imports.composition import run_bdot10k_command
from app.modules.imports.domain.bdot10k import Bdot10kValidationError
from tests.bdot_support import purge_bdot

pytestmark = pytest.mark.integration
client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_jobs():
    with SessionLocal() as db:
        db.execute(text("DELETE FROM import_jobs WHERE kind='bdot_county'"))
        db.commit()
    yield
    with SessionLocal() as db:
        db.execute(text("DELETE FROM import_jobs WHERE kind='bdot_county'"))
        db.commit()
        purge_bdot(db)


def test_api_deduplicates_and_worker_imports_real_county(tmp_path):
    from app.core.settings import settings
    package = Path(__file__).parent / "fixtures/source_contracts/bdot10k_base/1261/1261_GML.zip"
    first = client.post("/api/v1/coverage/bdot10k/county/1261/import")
    second = client.post("/api/v1/coverage/bdot10k/county/1261/import")
    assert first.status_code == second.status_code == 200
    assert first.json()["job_id"] == second.json()["job_id"]
    assert first.json()["status"] == "queued"
    def execute(db, **kwargs):
        return run_bdot10k_command(db, **kwargs, package=str(package))
    with patch("app.modules.imports.api.bdot_jobs.run_bdot10k_command", side_effect=execute), patch.object(settings, "import_artifact_storage_dir", str(tmp_path)):
        result = process_one_job(with_csw=False)
    assert result["powiat"] == "1261" and result["job_status"] == "succeeded"
    body = client.get("/api/v1/coverage/bdot10k").json()
    county = next(row for row in body["counties"] if row["teryt_powiat"] == "1261")
    assert county["powerline_count"] == 607


@pytest.mark.parametrize("exc,expected", [(httpx.ReadTimeout("timeout"), "queued"), (Bdot10kValidationError("schema drift"), "failed")])
def test_worker_maps_failure_to_queue_status(exc, expected):
    assert client.post("/api/v1/coverage/bdot10k/county/1261/import").status_code == 200
    with patch("app.modules.imports.api.bdot_jobs.run_bdot10k_command", side_effect=exc):
        result = process_one_job(with_csw=False)
    assert result["status"] == "failed" and result["job_status"] == expected


def test_invalid_county_and_unavailable_queue_do_not_return_500():
    from sqlalchemy.exc import OperationalError
    assert client.post("/api/v1/coverage/bdot10k/county/nope/import").status_code == 422
    with patch("app.routers.coverage.enqueue_county_import", side_effect=OperationalError("query", {}, Exception("offline"))):
        assert client.post("/api/v1/coverage/bdot10k/county/1261/import").status_code == 503
