"""Pokrycie wydań BDOT10k w API (Task 23.1): ``GET /api/v1/coverage/bdot10k``."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.bdot_support import POWIATS, expected, installed_releases

pytestmark = pytest.mark.integration

client = TestClient(app)


def test_without_imports_the_list_is_empty_and_a_point_is_not_covered(tmp_path: Path) -> None:
    with installed_releases(tmp_path):
        body = client.get("/api/v1/coverage/bdot10k", params={"lon": 19.94, "lat": 50.06}).json()

    assert body["counties"] == []
    assert body["point"] == {"lon": 19.94, "lat": 50.06, "covered": False, "teryt_powiat": None}
    assert "no_coverage" in body["note"] and "a nie brak drogi lub budynków" in body["note"]


def test_imported_counties_are_listed_with_counts_dates_hashes_and_wgs84_bounds(tmp_path: Path) -> None:
    with installed_releases(tmp_path, "1261", "0262"):
        response = client.get("/api/v1/coverage/bdot10k")

    assert response.status_code == 200
    body = response.json()
    assert body["source_id"] == "bdot10k" and body["point"] is None
    by_teryt = {item["teryt_powiat"]: item for item in body["counties"]}
    assert list(by_teryt) == ["0262", "1261"]  # posortowane po TERYT
    krakow = by_teryt["1261"]
    assert krakow["road_count"] == expected("1261")["road_feature_count"]
    assert krakow["building_count"] == expected("1261")["building_feature_count"]
    assert krakow["package_sha256"] == expected("1261")["fixture_package"]["sha256"]
    assert krakow["version_label"].startswith("bdot10k-1261-") and krakow["data_as_of"] and krakow["imported_at"]
    min_lon, min_lat, max_lon, max_lat = krakow["bounds_wgs84"]
    assert 19.7 < min_lon < 19.9 and 20.1 < max_lon < 20.3  # obwiednia Krakowa (EPSG:4326)
    assert 49.9 < min_lat < 50.05 and 50.1 < max_lat < 50.2


def test_a_point_is_covered_only_inside_an_imported_county(tmp_path: Path) -> None:
    with installed_releases(tmp_path, "1261"):
        inside = client.get("/api/v1/coverage/bdot10k", params={"lon": 19.94, "lat": 50.06}).json()["point"]
        outside = client.get("/api/v1/coverage/bdot10k", params={"lon": 21.0, "lat": 52.2}).json()["point"]  # Warszawa

    assert inside == {"lon": 19.94, "lat": 50.06, "covered": True, "teryt_powiat": "1261"}
    assert outside["covered"] is False and outside["teryt_powiat"] is None


@pytest.mark.parametrize("params", [{"lon": 200, "lat": 50}, {"lon": 19, "lat": 95}, {"lon": "x", "lat": 50}])
def test_invalid_coordinates_are_a_validation_error_not_a_500(params: dict) -> None:
    assert client.get("/api/v1/coverage/bdot10k", params=params).status_code == 422


def test_all_six_corpus_counties_are_listed_when_imported(tmp_path: Path) -> None:
    with installed_releases(tmp_path, *POWIATS):
        counties = client.get("/api/v1/coverage/bdot10k").json()["counties"]

    assert [item["teryt_powiat"] for item in counties] == sorted(POWIATS)
