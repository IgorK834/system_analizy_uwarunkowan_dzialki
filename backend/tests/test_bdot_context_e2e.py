"""Droga i zabudowa sąsiednia od końca do końca (Task 23.1–23.3): import paczki → orkiestrator → PostGIS → API → odczyt
historyczny → cache → raport PDF → pakiet audytowy ZIP.

Paczka powiatu 1261 to zamrożony wycinek prawdziwego wydania BDOT10k, a wartości oczekiwane liczono niezależnie od aplikacji
(``tests/fixtures/bdot10k/1261/expected.json``). Usługi zewnętrzne (NMT, POG, ISOK, GDOŚ, KIMPZP) są zamrożone — bez internetu.
Trzy scenariusze: działka z wydaniem (pełna kompletność), działka bez pokrycia (nie „brak drogi”) i awaria zapytania
(analiza dalej działa).
"""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.access_control import make_analysis_token
from app.db.session import SessionLocal
from app.models.analysis import Analysis
from app.models.source_record import SourceRecord
from app.modules.analysis.application.bdot_ports import BdotIndexTimeout
from app.modules.analysis.domain.roads import ROAD_ACCESS_DISCLAIMER
from app.modules.analysis.infrastructure.bdot_repository import PostgisBdotIndex
from app.services import audit_package
from app.services.pog_fetch import PogVectorData
from tests import test_terrain_e2e as base
from tests.bdot_support import corpus_parcels, expected, installed_releases
from tests.test_analysis_orchestrator import _found_discovery
from tests.test_audit_e2e import _catalog_with
from tests.test_completeness_e2e import _analyze  # noqa: F401 - ten sam szkielet analizy
from tests.test_terrain_e2e import (  # noqa: F401 - fixtures pytest
    CONTROL,
    _PREFIX,
    _nmt,
    _pdf_text,
    _post_analyze,
    _reread,
    cleanup_rows,
    client,
)

pytestmark = pytest.mark.integration

KRAKOW = "real-005-126105-9-0001-580-4"  # droga przecina działkę, trzy budynki na działce
SENTENCE = "nie jest potwierdzeniem prawnego dostępu działki do drogi publicznej"


@pytest.fixture(autouse=True)
def pog_over_the_parcel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zamrożone wektory POG z harnessu leżą w Warszawie; tu pokrywają działkę z Krakowa (inaczej POG byłby „brak”)."""

    original_factory = base._pog_vectors

    def vectors() -> PogVectorData:
        original = original_factory()
        zone = replace(original.planning_zones[0], geometry=corpus_parcels()[KRAKOW].buffer(5.0))
        return PogVectorData(**{**original.__dict__, "planning_zones": [zone]})

    monkeypatch.setattr(base, "_pog_vectors", vectors)


@pytest.fixture
def krakow_release(tmp_path: Path):
    with installed_releases(tmp_path, "1261"):
        yield


def _analyze_parcel(identifier: str, wkt: str):
    return _post_analyze(identifier, wkt, _nmt("nmt_getminmaxbypolygon.txt"), discovery=_found_discovery())


def _links(analysis_id: int) -> dict:
    response = client.post(
        f"/analyze/{analysis_id}/links", json={"purpose": "download"},
        headers={"X-Analysis-Token": make_analysis_token(analysis_id)},
    )
    assert response.status_code == 200
    return response.json()


def _members(content: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return {info.filename: archive.read(info) for info in archive.infolist()}


def test_road_and_buildings_survive_api_db_reread_cache_pdf_and_zip(
    krakow_release,
) -> None:
    identifier = f"{_PREFIX}BDOT_{uuid4().hex[:6]}"
    want = expected("1261")["cases"][KRAKOW]
    wkt = corpus_parcels()[KRAKOW].wkt

    first, _ = _analyze_parcel(identifier, wkt)

    # API: sekcje, wartości zgodne z niezależnym pomiarem, uwaga interpretacyjna i brak pola prawnego dostępu.
    assert first.status_code == 200
    body = first.json()
    road, buildings = body["road_context"], body["neighbour_buildings"]
    assert road["status"] == "available" and road["interpretation_note"].endswith(SENTENCE + ".") or SENTENCE in road["interpretation_note"]
    assert road["nearest"]["distance_m"] == want["roads"]["distance_m"] == 0.0
    assert road["nearest"]["relation"] == "intersects" and road["nearest"]["road_id"] == want["roads"]["road_id"]
    assert (road["nearest"]["category"], road["nearest"]["road_class"]) == (
        want["roads"]["management_category"], want["roads"]["road_class"],
    )
    assert road["search_radius_m"] == 25.0 and road["distance_reference"] == "parcel_polygon_to_carriageway_axis"
    assert road["source"]["source_id"] == "bdot10k" and len(road["source"]["artifact_sha256"]) == 64
    assert road["release"]["teryt_powiat"] == "1261" and road["release"]["data_as_of"]
    assert "legal_public_road_access" not in json.dumps(body)
    assert buildings["status"] == "available"
    assert buildings["on_parcel"]["count"] == want["buildings"]["on_parcel"]["count"] == 3
    for item in buildings["buffers"]:
        target = want["buildings"]["buffers"][str(int(item["radius_m"]))]
        assert item["count"] == target["count"]
        assert item["union_area_in_ring_sqm"] == pytest.approx(target["union_area_in_ring_sqm"], abs=0.05)
    assert buildings["buildings"] and "owner" not in json.dumps(buildings["buildings"][0])

    # Kompletność: transport i zabudowa są w zakresie i sprawdzone; status wynika z reguły completeness/1.1.
    completeness = body["completeness"]
    states = {item["section"]: item["state"] for item in completeness["sections"]}
    assert completeness["rule_version"] == "completeness/1.1"
    assert states["transport"] == states["buildings"] == "checked"
    assert body["status"] == "complete" and completeness["complete"] is True, (states, completeness["headline"])
    quality = {item["section"]: item for item in body["section_quality"]["sections"]}
    assert quality["transport"]["source_id"] == quality["buildings"]["source_id"] == "bdot10k"
    assert quality["transport"]["status"] == "available" and quality["transport"]["freshness"]["state"] == "fresh"
    assert quality["transport"]["freshness"]["max_age_days"] == 365
    analysis_id = body["analysis_id"]

    # PostGIS: snapshot JSONB i rejestr źródeł.
    with SessionLocal() as db:
        saved = db.get(Analysis, analysis_id)
        assert saved.road_context == road and saved.neighbour_buildings == buildings
        assert saved.result_contract_version and "+road-v1.0" in saved.result_contract_version
        names = {record.source_name for record in db.scalars(select(SourceRecord).where(SourceRecord.analysis_id == analysis_id))}
    assert "BDOT10k" in names

    # Odczyt historyczny: ten sam wynik z bazy (bez ponownego liczenia).
    reread = _reread(analysis_id)
    assert reread["road_context"] == road and reread["neighbour_buildings"] == buildings

    # Cache: drugie żądanie nie liczy od nowa i zwraca te same sekcje.
    again, nmt_calls = _analyze_parcel(identifier, wkt)
    assert again.status_code == 200 and nmt_calls == 0
    assert again.json()["analysis_id"] == analysis_id and again.json()["road_context"] == road

    # PDF: tabele, wartości i zdanie o braku potwierdzenia prawnego dostępu.
    links = _links(analysis_id)
    report = client.get(links["report_url"])
    assert report.status_code == 200
    text = _pdf_text(report.content)
    for needle in (
        "Transport i dostęp do drogi", "Tabela 7.3. Najbliższa oś jezdni (BDOT10k)", SENTENCE,
        "oś jezdni przecina działkę", "gminna", "droga lokalna", "Zabudowa sąsiednia",
        "Tabela 7.4. Zabudowa na działce i w buforach", "Tabela 7.5. Najbliższe budynki",
        "buffer(parcel, r) - parcel", "stan danych na",
    ):
        assert needle in text, needle
    assert text.count(SENTENCE) >= 2  # pod nagłówkiem sekcji i w ograniczeniach interpretacyjnych
    assert "legal_public_road_access" not in text and "dostęp do drogi publicznej: tak" not in text.lower()

    # ZIP: warstwy pochodne (redistribution: derived_only), uwaga w analysis.json, geometrie wyłącznie w plikach GeoJSON.
    package = client.get(links["audit_package_url"])
    assert package.status_code == 200
    members = _members(package.content)
    assert {"layers/road_context.geojson", "layers/neighbour_buildings.geojson"} <= set(members)
    analysis = json.loads(members["analysis.json"])["result"]
    assert analysis["road_context"]["interpretation_note"] == ROAD_ACCESS_DISCLAIMER
    assert analysis["road_context"]["nearest"]["road_geojson"] == {"moved_to_geojson_files": True}
    assert analysis["neighbour_buildings"]["buildings"][0]["geometry_geojson"] == {"moved_to_geojson_files": True}
    road_layer = json.loads(members["layers/road_context.geojson"])
    assert {feature["properties"]["part"] for feature in road_layer["features"]} == {"road_axis"}  # odległość 0 → bez odcinka
    sources = {item["source_id"]: item for item in json.loads(members["sources.json"])["sources"] if item["source_id"]}
    assert sources["bdot10k"]["redistribution"] == "derived_only" and sources["bdot10k"]["artifact_sha256"]
    assert "legal_public_road_access" not in b"".join(members.values()).decode("utf-8", "ignore")


def test_the_audit_layers_are_copied_only_when_the_catalog_allows_redistribution(
    krakow_release, monkeypatch
) -> None:
    identifier = f"{_PREFIX}BDOT_{uuid4().hex[:6]}"
    response, _ = _analyze_parcel(identifier, corpus_parcels()[KRAKOW].wkt)
    analysis_id = response.json()["analysis_id"]

    monkeypatch.setattr(audit_package, "_catalog", lambda: _catalog_with(bdot10k="forbidden"))
    members = _members(client.get(_links(analysis_id)["audit_package_url"]).content)

    assert "layers/road_context.geojson" not in members and "layers/neighbour_buildings.geojson" not in members
    manifest = json.loads(members["manifest.json"])
    omitted = {item["name"]: item for item in manifest["omitted_artifacts"]}
    assert omitted["layers/road_context.geojson"]["reason"] == "redistribution_forbidden"
    assert omitted["layers/neighbour_buildings.geojson"]["reason"] == "redistribution_forbidden"
    entry = {item["source_id"]: item for item in json.loads(members["sources.json"])["sources"] if item["source_id"]}["bdot10k"]
    assert entry["redistribution"] == "forbidden" and entry["source_url"] and entry["artifact_sha256"]  # referencja i hash zostają


def test_a_parcel_without_coverage_is_no_coverage_in_api_pdf_and_zip_never_no_road(
    krakow_release,
) -> None:
    identifier = f"{_PREFIX}BDOT_{uuid4().hex[:6]}"

    response, _ = _analyze_parcel(identifier, CONTROL.wkt)  # Warszawa, a zaimportowano tylko powiat 1261

    assert response.status_code == 200
    body = response.json()
    road, buildings = body["road_context"], body["neighbour_buildings"]
    assert (road["status"], road["reason_code"]) == ("no_coverage", "OUTSIDE_COVERAGE")
    assert (buildings["status"], buildings["reason_code"]) == ("no_coverage", "OUTSIDE_COVERAGE")
    assert road["nearest"] is None and buildings["on_parcel"] is None and buildings["buffers"] == []
    assert road["interpretation_note"] == ROAD_ACCESS_DISCLAIMER  # uwaga jest także przy braku danych
    states = {item["section"]: item["state"] for item in body["completeness"]["sections"]}
    assert states["transport"] == states["buildings"] == "missing"
    assert body["status"] == "partial"
    assert "droga i dostęp" in body["headline"] and "zabudowa sąsiednia" in body["headline"].split("Poza zakresem")[0]
    assert not any("brak drogi" == item["message"] for item in body["warnings"])

    links = _links(body["analysis_id"])
    text = _pdf_text(client.get(links["report_url"]).content)
    assert "Brak danych nie oznacza braku drogi" in text and "Brak danych nie oznacza braku budynków" in text
    # Tabel z wartościami nie ma: nie wymyślamy zer (opisy w załączniku mapowania pól wymieniają numery tabel).
    assert "Tabela 7.3. Najbliższa oś jezdni" not in text and "Tabela 7.4. Zabudowa na działce i w buforach" not in text
    members = _members(client.get(links["audit_package_url"]).content)
    assert "layers/road_context.geojson" not in members and "layers/neighbour_buildings.geojson" not in members
    analysis = json.loads(members["analysis.json"])["result"]
    assert analysis["road_context"]["status"] == "no_coverage" and analysis["road_context"]["nearest"] is None


def test_a_failing_query_does_not_break_the_analysis_and_is_unavailable_not_no_road(
    krakow_release,
) -> None:
    identifier = f"{_PREFIX}BDOT_{uuid4().hex[:6]}"

    with patch.object(PostgisBdotIndex, "nearest", side_effect=BdotIndexTimeout("x")), patch.object(
        PostgisBdotIndex, "candidates", side_effect=BdotIndexTimeout("x")
    ):
        response, _ = _analyze_parcel(identifier, corpus_parcels()[KRAKOW].wkt)

    assert response.status_code == 200  # żaden wyjątek źródła nie kończy się surowym 5xx
    body = response.json()
    assert (body["road_context"]["status"], body["road_context"]["reason_code"]) == ("unavailable", "QUERY_TIMEOUT")
    assert (body["neighbour_buildings"]["status"], body["neighbour_buildings"]["reason_code"]) == ("unavailable", "QUERY_TIMEOUT")
    assert body["road_context"]["nearest"] is None and body["road_context"]["source"]["manual_review_required"] is True
    quality = {item["section"]: item["status"] for item in body["section_quality"]["sections"]}
    assert quality["transport"] == quality["buildings"] == "unavailable"
    assert body["status"] == "partial"
    assert any(item["code"].startswith("ROAD_CONTEXT_") and item["severity"] == "warning" for item in body["warnings"])
