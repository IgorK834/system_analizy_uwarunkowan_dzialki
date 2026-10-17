"""Regresje AU-108/109/111 na rzeczywistych granicach i zamrożonych odpowiedziach źródeł."""
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.main import app
from app.modules.planning.application.pog_release_queries import coverage_features
from app.modules.planning.infrastructure.commune_boundaries import PrgCommuneBoundaries
from app.schemas.source import SourceMetadata
from app.services.dataset_dates import aggregate_dataset_dates, dataset_date, response_dataset_date
from app.services.terrain import build_terrain_result, terrain_from_snapshot, terrain_warnings
from app.services.context import ContextSectionResult
from app.services.nmt import TerrainExtremes
from tests.test_terrain_result import _relief_outcome, _source

FIXTURES = Path(__file__).parent / "fixtures"


def test_real_prg_has_all_communes_and_separates_local_data_from_ru_legal_status():
    boundaries = PrgCommuneBoundaries()
    assert len(boundaries.features()) == 2479
    entries = [
        {"terc": "141801", "imported_acts": [{"legal_status": "binding", "release_id": 1}]},
        {"terc": "246101", "imported_acts": [{"legal_status": "project", "release_id": 2}]},
        {"terc": "241203", "imported_acts": [{"legal_status": "unknown", "release_id": 3}]},
        {"terc": "226301", "ru_acts": [{"legal_status": "binding"}]},
    ]
    features = coverage_features(boundaries, entries, catalog_fresh=True)["features"]
    by_terc = {f["properties"]["terc"]: f["properties"] for f in features}
    assert by_terc["141801"]["state"] == "binding"
    assert by_terc["246101"]["state"] == "project"
    assert by_terc["241203"]["state"] == "unknown"
    assert by_terc["226301"]["state"] == "no_local_data"
    assert by_terc["146501"]["state"] == "no_act_in_ru"
    stale = coverage_features(boundaries, entries, catalog_fresh=False)
    assert all(f["properties"]["state"] != "no_act_in_ru" for f in stale["features"])
    assert boundaries.features()[0]["properties"].keys() == {"teryt", "nazwa"}  # cache niezmieniony


def test_coverage_db_error_is_controlled_503():
    with patch("app.routers.coverage.get_pog_coverage", side_effect=SQLAlchemyError("offline")):
        response = TestClient(app).get("/api/v1/coverage")
    assert response.status_code == 503
    assert response.json()["error"] and "offline" not in response.text


@pytest.mark.parametrize("terc,code", [("bad", 422), ("1234567", 422), ("123456", 409)])
def test_import_request_validation_and_no_enqueue(terc, code):
    with patch("app.routers.coverage.request_commune_import", return_value=None):
        assert TestClient(app).post(f"/api/v1/coverage/pog/commune/{terc}/import").status_code == code


def test_import_request_uses_existing_queue_contract():
    from app.services.pog_on_demand import CommuneImportRequest
    request = CommuneImportRequest("246101", 42, "queued", True, "catalog")
    with patch("app.routers.coverage.request_commune_import", return_value=request):
        body = TestClient(app).post("/api/v1/coverage/pog/commune/246101/import").json()
    assert body["job_id"] == 42 and body["status"] == "queued"


def test_iso19139_from_real_ru_metadata_and_missing_dates_from_real_services():
    iso = (FIXTURES / "ru/csw_getrecords_iso_226401.xml").read_bytes()
    result = response_dataset_date(httpx.Response(200, content=iso, headers={"content-type": "application/xml"}))
    assert result["data_as_of"] is not None
    assert result["data_as_of_basis"].startswith("ISO19139:")
    for filename in ("isok_getfeature.xml", "gdos_getfeature.xml", "nmt_getminmaxbypolygon.txt"):
        content = (FIXTURES / "source_contracts" / filename).read_bytes()
        fields = response_dataset_date(httpx.Response(200, content=content))
        source = SourceMetadata(source_name=filename, confidence=1, manual_review_required=False, **fields)
        assert source.data_as_of is None and source.data_as_of_status == "unknown" and source.data_as_of_reason
        # Nawet czas serwera nie zastępuje daty danych.
        with_date = response_dataset_date(httpx.Response(200, content=content, headers={"date": "Wed, 14 Oct 2026 12:00:00 GMT"}))
        assert with_date["data_as_of"] is None


@pytest.mark.parametrize("value", [None, "invalid", "2026-13-01"])
def test_invalid_dataset_date_has_reason(value):
    assert dataset_date(value, "test")["data_as_of_reason"]


def test_dates_headers_aggregation_and_legacy_reason():
    response = httpx.Response(200, headers={"last-modified": "Wed, 14 Oct 2026 12:00:00 GMT"})
    result = response_dataset_date(response)
    assert result["data_as_of"] == date(2026, 10, 14)
    assert response_dataset_date(httpx.Response(200, headers={"last-modified": "bad"}))["data_as_of"] is None
    assert aggregate_dataset_dates([result, dataset_date(date(2024, 1, 1), "uchwała")])["data_as_of"] == date(2024, 1, 1)
    assert aggregate_dataset_dates([result, dataset_date(None, "absent")])["data_as_of"] is None
    assert aggregate_dataset_dates([])["data_as_of_reason"]
    assert dataset_date(datetime(2026, 10, 14), "attribute")["data_as_of"] == date(2026, 10, 14)
    assert response_dataset_date(httpx.Response(200, content=b"<broken", headers={"content-type": "text/xml"}))["data_as_of"] is None
    assert SourceMetadata(source_name="old", confidence=1, manual_review_required=False, data_as_of_reason="").data_as_of_reason


@pytest.mark.parametrize("gap,warning", [(0, False), (0.4999, False), (0.5, False), (0.5001, True), (1, True)])
def test_crosscheck_strict_half_meter_boundary(gap, warning):
    relief = _relief_outcome()
    low, high = relief.derivatives.min_height_m, relief.derivatives.max_height_m
    measurement = TerrainExtremes(low + gap, high + gap, round(high - low, 4), 2, 676, _source())
    result = build_terrain_result(ContextSectionResult(section="nmt", status="available", data=[measurement]), relief)
    assert (result.min_height_m, result.max_height_m) == (low, high)
    assert result.grid_size_m == 1 and result.source.source_id == "nmt_wcs"
    assert bool(result.crosscheck.warning_code) == warning
    assert bool([w for w in terrain_warnings(result) if w.code == "NMT_CROSSCHECK_DIFF"]) == warning


def test_raster_survives_failed_crosscheck_and_fallback_identifies_real_grid():
    result = build_terrain_result(ContextSectionResult(section="nmt", status="unavailable", reason_code="SERVICE_TIMEOUT"), _relief_outcome())
    assert result.status == "available" and result.elevation_method == "wcs_raster" and result.crosscheck is None
    result = build_terrain_result(ContextSectionResult(section="nmt", status="available", data=[TerrainExtremes(117.9, 118.5, 0.6, 2, 676, _source())]), None)
    assert result.elevation_method == "polygon_service" and "2,00 m, mniejsza dokładność" in result.elevation_note


def test_real_audit_parcel_uses_one_range_in_report():
    root = Path(__file__).resolve().parents[2]
    # Zamrożona rzeczywista odpowiedź AU-004 zawiera dokładnie obie serie z audytu B9 i geometrię działki.
    import os
    root = Path(os.environ.get("REPO_ROOT", root))
    body = json.loads((root / "docs/evaluation/results/au-004/01-api-response-141801_4.0701.23_8.json").read_text())
    terrain = terrain_from_snapshot(body["terrain"])
    assert (terrain.min_height_m, terrain.max_height_m) == (118.269, 118.959)
    assert terrain.crosscheck.max_difference_m == 0.459 and terrain.crosscheck.warning_code is None
    from app.schemas.analyze import AnalyzeResponse
    from app.services.report import _build_report_context, _render_report_html
    body["terrain"] = terrain.model_dump(mode="json")
    html = _render_report_html(_build_report_context(AnalyzeResponse.model_validate(body)))
    assert "118,269" in html and "118,959" in html
    assert "Wysokości z rastra (min / śr. / max)" not in html
    assert "Najniższa wysokość (Hmin)" in html and "117,9" not in html
