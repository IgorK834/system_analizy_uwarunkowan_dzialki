"""Scenariusz końcowy BK-505: ``GET /report/{id}/audit.zip`` na realnych warstwach.

Analiza powstaje przez ``/analyze`` (orkiestrator, adapter NMT, PostGIS), a pakiet
jest budowany wyłącznie z zapisanego snapshotu. Sprawdzamy nagłówki i strumień,
dostęp jak do raportu, każdy SHA-256, wykrycie zmiany jednego bajtu offline,
determinizm, regułę redystrybucji z katalogu (zakaz = tylko referencja i hash),
limity oraz brak sieci i ponownej analizy.

Jeżeli ustawiono ``EVIDENCE_OUT``, test zapisuje pakiet i jego części jako dowód.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import respx
import yaml
from sqlalchemy import text

from app.core.access_control import make_analysis_token
from app.core.data_sources import parse_catalog
from app.core.settings import settings
from app.db.session import SessionLocal
from app.routers import report as report_router
from app.schemas.source import SourceMetadata
from app.services import audit_package
from app.services.pog_fetch import PogVectorData
from tests import test_terrain_e2e as base
from tests.repo_structure import find_repo_root
from tests.test_quality_e2e import _post
from tests.test_terrain_e2e import _nmt, _reread, _write_evidence, client

pytestmark = pytest.mark.integration

_PREFIX = base._PREFIX
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_audit_package.py"
CATALOG_PATH = find_repo_root() / "docs" / "data_sources" / "catalog.yaml"


@pytest.fixture(autouse=True)
def cleanup_rows():
    base._cleanup()
    yield
    base._cleanup()


@pytest.fixture(autouse=True)
def sources_fetched_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base, "NOW", datetime.now(timezone.utc) - timedelta(seconds=30))


def _verifier():
    spec = importlib.util.spec_from_file_location("verify_audit_package", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _catalog_with(**redistribution: str):
    data = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))
    for entry in data["sources"]:
        if entry["source_id"] in redistribution:
            entry["redistribution"] = redistribution[entry["source_id"]]
    return parse_catalog(data)


def _pog_vectors_with_catalog_id() -> PogVectorData:
    vectors = base._pog_vectors()
    source = vectors.source_metadata
    assert source is not None
    return PogVectorData(
        **{
            **vectors.__dict__,
            "source_metadata": SourceMetadata(
                source_id="pog_app",
                source_name="POG_APP_VECTOR",
                source_url=source.source_url,
                fetched_at=source.fetched_at,
                response_status=200,
                confidence=0.75,
                manual_review_required=True,
            ),
        }
    )


def _create_analysis() -> tuple[int, dict]:
    identifier = f"{_PREFIX}A_{uuid4().hex[:6]}"
    response, _ = _post(
        identifier, _nmt("nmt_getminmaxbypolygon.txt"), pog_vectors=_pog_vectors_with_catalog_id()
    )
    assert response.status_code == 200
    body = response.json()
    return body["analysis_id"], body


def _url(analysis_id: int, token: str | None = None) -> str:
    return f"/report/{analysis_id}/audit.zip?access_token={token or make_analysis_token(analysis_id)}"


def _members(content: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        assert archive.testzip() is None
        return {info.filename: archive.read(info) for info in archive.infolist()}


# --- Odpowiedź HTTP, strumień, nagłówki -------------------------------------------------------


def test_audit_package_end_to_end_from_persisted_snapshot() -> None:
    analysis_id, body = _create_analysis()

    # Snapshot jest jedynym źródłem: bez ponownej analizy i bez żadnego ruchu HTTP.
    with respx.mock(assert_all_called=False) as router, patch(
        "app.services.analysis_orchestrator.run_analysis", new=AsyncMock(side_effect=AssertionError("re-analiza"))
    ):
        response = client.get(_url(analysis_id))
        assert router.calls.call_count == 0

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"] == (
        f'attachment; filename="analiza_{analysis_id}_pakiet_audytowy.zip"'
    )
    assert response.headers["cache-control"] == "private, no-store"
    package_sha = response.headers["x-audit-package-sha256"]
    assert package_sha == hashlib.sha256(response.content).hexdigest()  # hash poza archiwum
    assert int(response.headers["content-length"]) == len(response.content)
    assert response.headers["x-audit-exporter-version"] == "audit-exporter/1.1.0"

    members = _members(response.content)
    assert {"analysis.json", "sources.json", "parcel.geojson", "manifest.json", "README.md"} <= set(members)
    assert list(members) == sorted(members)
    manifest = json.loads(members["manifest.json"])
    assert {entry["name"] for entry in manifest["files"]} == set(members) - {"manifest.json"}
    for entry in manifest["files"]:
        assert hashlib.sha256(members[entry["name"]]).hexdigest() == entry["sha256"]
        assert len(members[entry["name"]]) == entry["bytes"]
    assert manifest["analysis_id"] == analysis_id
    assert package_sha not in json.dumps(manifest)  # hash paczki nie jest wewnątrz archiwum

    # analysis.json odpowiada zapisanemu wynikowi (odczyt z bazy), z macierzą jakości.
    analysis = json.loads(members["analysis.json"])
    reread = _reread(analysis_id)
    assert analysis["analysis"]["analysis_id"] == analysis_id
    assert analysis["result"]["terrain"] == reread["terrain"]
    assert analysis["result"]["risk_sections"] == reread["risk_sections"]
    assert analysis["section_quality"]["matrix_sha256"] == body["section_quality"]["matrix_sha256"]
    assert "access_token" not in analysis["result"] and "access_token" not in json.dumps(members["sources.json"].decode())
    assert manifest["quality_matrix_sha256"] == body["section_quality"]["matrix_sha256"]

    # Rejestr źródeł: identyfikatory z katalogu, czasy pobrania, licencje i decyzje redystrybucji.
    sources = json.loads(members["sources.json"])["sources"]
    by_id = {item["source_id"]: item for item in sources if item["source_id"]}
    assert {"uldk", "isok", "gdos", "nmt", "pog_app"} <= set(by_id), [
        (item["source_id"], item["source_name"]) for item in sources
    ]
    assert by_id["isok"]["redistribution"] == "allowed" and by_id["pog_app"]["redistribution"] == "derived_only"
    assert all(item["fetched_at"] for item in sources if item["source_name"] != "KIUT")

    # GeoJSON w EPSG:4326, obliczenia w 2180 opisane osobno.
    parcel = json.loads(members["parcel.geojson"])
    node = parcel["features"][0]["geometry"]["coordinates"]
    while isinstance(node[0], list):
        node = node[0]
    lon, lat = node[:2]
    assert 14 < lon < 25 and 48 < lat < 56
    assert analysis["crs"] == {"computation": "EPSG:2180", "geojson_files": "EPSG:4326"}
    assert analysis["computation"]["parcel_geometry_epsg2180"]["properties"]["crs"] == "EPSG:2180"

    # POG (derived_only): warstwy pochodne są, surowe atrybuty nie.
    assert "layers/pog_zones.geojson" in members
    assert analysis["result"]["pog"]["raw_attributes"] is None
    assert any(item["pointer"] == "/result/pog/raw_attributes" for item in analysis["redactions"])
    assert {item["name"] for item in manifest["omitted_artifacts"]} == {"analysis.json#/result/pog/raw_attributes"}

    # Offline: każdy SHA przechodzi, a zmiana jednego bajtu jest wykryta.
    verifier = _verifier()
    names = sorted(members)
    assert verifier.verify(names, members.__getitem__) == []
    for name in ("analysis.json", "parcel.geojson", "sources.json", "README.md"):
        tampered = dict(members)
        raw = bytearray(tampered[name])
        raw[len(raw) // 3] ^= 0x01
        tampered[name] = bytes(raw)
        assert verifier.verify(names, tampered.__getitem__) == [f"zła suma SHA-256: {name}"]

    _write_evidence("bk-504-505/06-audit-package.zip", response.content)
    _write_evidence("bk-504-505/07-manifest.json", members["manifest.json"].decode())
    _write_evidence("bk-504-505/08-README.md", members["README.md"].decode())
    _write_evidence("bk-504-505/09-sources.json", members["sources.json"].decode())
    _write_evidence("bk-504-505/10-package-sha256.txt", f"{package_sha}  analiza_{analysis_id}_pakiet_audytowy.zip\n")


def test_two_exports_of_the_same_snapshot_are_identical_even_later() -> None:
    analysis_id, _ = _create_analysis()
    first = client.get(_url(analysis_id))
    later_clock = datetime.now(timezone.utc) + timedelta(days=400)
    with patch("app.services.report.datetime") as fake:  # eksport „za rok” nie zmienia paczki
        fake.now.return_value = later_clock
        second = client.get(_url(analysis_id))
    assert first.status_code == second.status_code == 200
    assert first.content == second.content
    assert first.headers["x-audit-package-sha256"] == second.headers["x-audit-package-sha256"]
    assert _members(first.content) == _members(second.content)


def test_offline_verifier_cli_accepts_downloaded_package_and_rejects_a_modified_one(tmp_path: Path) -> None:
    analysis_id, _ = _create_analysis()
    response = client.get(_url(analysis_id))
    archive = tmp_path / "pakiet.zip"
    archive.write_bytes(response.content)
    verifier = _verifier()
    assert verifier.main([str(archive), "--package-sha256", response.headers["x-audit-package-sha256"]]) == 0

    members = _members(response.content)
    members["analysis.json"] = members["analysis.json"].replace(b"partial", b"partiaL", 1)
    modified = tmp_path / "zmieniony.zip"
    with zipfile.ZipFile(modified, "w", zipfile.ZIP_DEFLATED) as out:
        for name, data in members.items():
            out.writestr(name, data)
    assert verifier.main([str(modified)]) == 1


# --- Dostęp jak do raportu, 404, 413 -----------------------------------------------------------


def test_access_control_and_error_statuses_match_the_report() -> None:
    analysis_id, _ = _create_analysis()
    assert client.get(f"/report/{analysis_id}/audit.zip").status_code == 403
    assert client.get(_url(analysis_id, token="zly-token")).status_code == 403
    assert client.get(_url(analysis_id, token=make_analysis_token(analysis_id + 1))).status_code == 403
    assert client.get(f"/report/0/audit.zip?access_token={make_analysis_token(0)}").status_code == 422

    missing = 2_000_000_000
    not_found = client.get(_url(missing))
    assert not_found.status_code == 404
    assert not_found.json()["detail"] == "Analiza o podanym identyfikatorze nie istnieje."
    # Ten sam kod dla raportu PDF tej samej nieistniejącej analizy.
    assert client.get(f"/report/{missing}?access_token={make_analysis_token(missing)}").status_code == 404


def test_route_uses_the_same_guards_as_the_pdf_report() -> None:
    routes = {route.path: route for route in report_router.router.routes if hasattr(route, "dependant")}
    pdf, package = routes["/report/{analysis_id}"], routes["/report/{analysis_id}/audit.zip"]
    pdf_calls = {dependency.call for dependency in pdf.dependant.dependencies}  # type: ignore[attr-defined]
    package_calls = {dependency.call for dependency in package.dependant.dependencies}  # type: ignore[attr-defined]
    assert pdf_calls - {report_router.get_db} <= package_calls
    assert package.methods == {"GET"}  # type: ignore[attr-defined]


def test_package_over_the_limit_is_413_not_a_truncated_archive(monkeypatch: pytest.MonkeyPatch) -> None:
    analysis_id, _ = _create_analysis()
    monkeypatch.setattr(settings, "audit_export_max_total_bytes", 4096)
    response = client.get(_url(analysis_id))
    assert response.status_code == 413
    assert response.json()["detail"] == "Pakiet audytowy przekracza dopuszczalny rozmiar."
    assert "application/zip" not in response.headers.get("content-type", "")

    monkeypatch.setattr(settings, "audit_export_max_total_bytes", 64 * 1024 * 1024)
    monkeypatch.setattr(settings, "audit_export_max_files", 3)
    assert client.get(_url(analysis_id)).status_code == 413


def test_unexpected_failure_is_a_generic_500_without_internals() -> None:
    analysis_id, _ = _create_analysis()
    with patch.object(report_router, "generate_audit_package", side_effect=RuntimeError("sekret /tmp/x")):
        response = client.get(_url(analysis_id))
    assert response.status_code == 500
    assert response.json()["detail"] == "Nie udało się przygotować pakietu audytowego."
    assert "sekret" not in response.text


# --- Redystrybucja z katalogu na realnym snapshotcie ----------------------------------------------


def test_forbidden_sources_are_only_referenced_never_copied(monkeypatch: pytest.MonkeyPatch) -> None:
    analysis_id, _ = _create_analysis()
    baseline = _members(client.get(_url(analysis_id)).content)
    assert "layers/pog_zones.geojson" in baseline and "parcel.geojson" in baseline

    monkeypatch.setattr(
        audit_package, "_catalog", lambda: _catalog_with(pog_app="forbidden", uldk="unconfirmed")
    )
    response = client.get(_url(analysis_id))
    assert response.status_code == 200
    members = _members(response.content)
    assert not {"parcel.geojson", "layers/pog_zones.geojson", "layers/pog_overlays.geojson",
                "layers/buildable_area.geojson"} & set(members)

    manifest = json.loads(members["manifest.json"])
    omitted = {item["name"]: item for item in manifest["omitted_artifacts"]}
    assert omitted["layers/pog_zones.geojson"]["reason"] == "redistribution_forbidden"
    assert omitted["layers/pog_zones.geojson"]["source_ids"] == ["pog_app"]
    assert omitted["parcel.geojson"]["reason"] == "redistribution_unconfirmed"
    assert omitted["parcel.geojson"]["source_ids"] == ["uldk"]
    for item in omitted.values():
        assert len(item["sha256"]) == 64 and item["bytes"] > 0
    # Hash pominiętej warstwy zgadza się z warstwą, którą pakiet zawiera przy zgodzie katalogu.
    assert omitted["layers/pog_zones.geojson"]["bytes"] > 0

    # Referencje zostają: rejestr źródeł opisuje pominięte źródła (adres, czas, hash artefaktu).
    sources = json.loads(members["sources.json"])["sources"]
    pog = next(item for item in sources if item["source_id"] == "pog_app")
    assert pog["source_url"] and pog["fetched_at"] and pog["redistribution"] == "forbidden"
    assert pog["package_content"] == {"derived_layers": False, "raw_data": False}
    analysis = json.loads(members["analysis.json"])
    assert analysis["computation"]["parcel_geometry_epsg2180"] is None
    assert analysis["analysis"]["parcel_identifier"]  # identyfikator działki nie jest daną z zakazem

    # Zawartość pominiętych warstw nie występuje w żadnym pliku pakietu.
    baseline_pog = baseline["layers/pog_zones.geojson"]
    node = json.loads(baseline_pog)["features"][0]["geometry"]["coordinates"]
    while isinstance(node[0], list):
        node = node[0]
    needle = json.dumps(node[0])[:11]  # ułamek długości geograficznej wierzchołka strefy POG
    assert needle in baseline_pog.decode() and len(needle) >= 9
    assert all(needle.encode() not in data for data in members.values())
    assert _verifier().verify(sorted(members), members.__getitem__) == []


# --- Zapis sprzed BK-504 ------------------------------------------------------------------------------


def test_legacy_analysis_without_quality_matrix_exports_a_disclosed_reconstruction() -> None:
    analysis_id, _ = _create_analysis()
    with SessionLocal() as db:
        db.execute(text("UPDATE analyses SET section_quality = NULL WHERE id = :id"), {"id": analysis_id})
        db.commit()
    first, second = client.get(_url(analysis_id)), client.get(_url(analysis_id))
    assert first.status_code == 200 and first.content == second.content
    members = _members(first.content)
    matrix = json.loads(members["analysis.json"])["section_quality"]
    assert matrix["origin"] == "reconstructed"
    assert all("LEGACY_QUALITY_RECONSTRUCTED" in item["reason_codes"] for item in matrix["sections"])
    assert "Macierz jakości tej analizy" in members["README.md"].decode()
    with SessionLocal() as db:
        stored = db.execute(text("SELECT section_quality FROM analyses WHERE id = :id"), {"id": analysis_id}).scalar_one()
    assert stored is None  # eksport nie uzupełnia zapisu wstecz


def test_without_the_source_catalog_nothing_is_copied_but_every_reference_stays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Brak katalogu = brak potwierdzonej zgody: pakiet niesie tylko referencje i hashe."""
    analysis_id, _ = _create_analysis()
    monkeypatch.setattr(audit_package, "_catalog", lambda: None)

    response = client.get(_url(analysis_id))

    assert response.status_code == 200
    members = _members(response.content)
    assert set(members) == {"analysis.json", "sources.json", "README.md", "manifest.json"}
    manifest = json.loads(members["manifest.json"])
    omitted = {item["name"] for item in manifest["omitted_artifacts"]}
    assert {"parcel.geojson", "layers/pog_zones.geojson", "layers/buildable_area.geojson"} <= omitted
    assert all(item["reason"] == "redistribution_unconfirmed" for item in manifest["omitted_artifacts"])
    sources = json.loads(members["sources.json"])["sources"]
    assert len(sources) >= 5 and all(item["catalog"] is None for item in sources)
    assert all(item["package_content"] == {"derived_layers": False, "raw_data": False} for item in sources)
    assert _verifier().verify(sorted(members), members.__getitem__) == []
