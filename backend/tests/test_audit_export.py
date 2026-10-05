"""Pakiet audytowy analizy (BK-505): zawartość, SHA-256, redystrybucja, determinizm, limity.

Testy jednostkowe na zamrożonym fixture raportu (bez bazy i sieci). Pełną ścieżkę
HTTP → PostGIS → ZIP sprawdza ``tests/test_audit_e2e.py``.
"""

from __future__ import annotations

import copy
import importlib.util
import io
import json
import random
import re
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from geoalchemy2.shape import from_shape

from app.core.data_sources import DataSourceCatalog, parse_catalog
from app.modules.reporting.application.audit_export import (
    AuditInput,
    AuditLayer,
    AuditRawBlock,
    build_audit_package,
)
from app.modules.reporting.domain.audit_package import (
    AUDIT_EXPORTER_VERSION,
    FIXED_ZIP_TIMESTAMP,
    AuditExportLimitError,
    AuditLimits,
    OmittedArtifact,
    PackageFile,
    UnsafeEntryNameError,
    build_manifest,
    canonical_bytes,
    render_json,
    safe_entry_name,
    sanitize_component,
    sha256_hex,
    verify_manifest,
)
from app.modules.reporting.infrastructure.archive import (
    ArchiveResult,
    iter_archive,
    write_deterministic_zip,
)
from app.services.audit_package import (
    GEOMETRY_MOVED,
    _features,
    _strip_ui_fields,
    audit_limits,
    build_audit_input,
)
from app.shared.safe_archive import ArchiveLimits, read_zip_members
from tests.repo_structure import find_repo_root
from tests.report_map_reference import load_fixture

CATALOG_PATH = find_repo_root() / "docs" / "data_sources" / "catalog.yaml"
LIMITS = AuditLimits(max_files=50, max_file_bytes=4_000_000, max_total_bytes=8_000_000)
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_audit_package.py"


def _catalog(**redistribution: str) -> DataSourceCatalog:
    data = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))
    for entry in data["sources"]:
        if entry["source_id"] in redistribution:
            entry["redistribution"] = redistribution[entry["source_id"]]
    return parse_catalog(data)


def _input(
    fixture: str = "multizone", catalog: DataSourceCatalog | None = None, response_update: Any = None
) -> AuditInput:
    response, parcel = load_fixture(fixture)
    if response_update is not None:
        response = response_update(response)
    analysis = SimpleNamespace(
        id=42,
        parcel=SimpleNamespace(geometry=from_shape(parcel, srid=2180), parcel_identifier="126101_1.0001.2417/5"),
        result_contract_version="contract-x",
        data_release_ids=[7, 12],
        cache_signature="sig-1",
    )
    artifacts = {
        "c" * 63 + "3": {"uri": "https://ru.example.test/pog.gml", "content_hash": "c" * 63 + "3",
                         "size_bytes": 1234, "media_type": "application/gml+xml",
                         "fetched_at": "2026-09-20T09:29:00Z"}
    }
    releases = {12: {"release_id": 12, "source_id": "pog_app", "version_label": "2026-09-19",
                     "published_at": "2026-09-19T00:00:00Z"}}
    return build_audit_input(
        analysis, response, catalog or _catalog(), artifacts=artifacts, releases=releases
    )


def _package(catalog: DataSourceCatalog | None = None, fixture: str = "multizone"):
    return build_audit_package(_input(fixture, catalog), LIMITS)


def _load_script() -> Any:
    spec = importlib.util.spec_from_file_location("verify_audit_package", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _zip_of(files) -> bytes:
    result = write_deterministic_zip(files)
    try:
        return result.file.read()
    finally:
        result.close()


def _zip_bytes(package) -> bytes:
    return _zip_of(package.files)


# --- Bezpieczne nazwy --------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["analysis.json", "README.md", "layers/flood_risk.geojson", "a/b/c/d.txt", "parcel.geojson"],
)
def test_safe_entry_names_are_accepted(name: str) -> None:
    assert safe_entry_name(name) == name


@pytest.mark.parametrize(
    "name",
    [
        "", "../etc/passwd", "/abs/path.json", "a/../b.json", "a\\b.json", "layers//x.json",
        ".hidden", "layers/.hidden", "a/./b", "name.", "dir/name.", "ż.json", "a b.json",
        "a/b/c/d/e.txt", "x" * 129, "MANIFEST.json", "CON:", "a\x00b", "C:\\x",
    ],
)
def test_unsafe_entry_names_are_rejected(name: str) -> None:
    with pytest.raises(UnsafeEntryNameError):
        safe_entry_name(name)
    with pytest.raises(UnsafeEntryNameError):
        PackageFile(name, b"x", "text/plain")


def test_sanitize_component_never_returns_unsafe_text() -> None:
    assert sanitize_component("ISOK WFS / żółć") == "isok_wfs"
    assert sanitize_component("../..") == "x"
    assert sanitize_component(None, fallback="brak") == "brak"
    assert safe_entry_name(f"layers/{sanitize_component('Ą/../b')}.geojson")


# --- Zawartość pakietu ---------------------------------------------------------------------


def test_package_contains_the_required_files_sorted_and_only_them() -> None:
    package = _package()
    names = [file.name for file in package.files]
    assert names == sorted(names)
    assert {"analysis.json", "sources.json", "parcel.geojson", "manifest.json", "README.md"} <= set(names)
    layers = {name for name in names if name.startswith("layers/")}
    assert layers == {
        "layers/buildable_area.geojson", "layers/flood_risk.geojson", "layers/mpzp_zones.geojson",
        "layers/nature_protection.geojson", "layers/pog_overlays.geojson", "layers/pog_zones.geojson",
    }
    for file in package.files:
        safe_entry_name(file.name)


def test_manifest_lists_every_file_but_itself_with_bytes_and_sha256() -> None:
    package = _package()
    manifest = json.loads(package.by_name("manifest.json").data)
    assert manifest["schema_version"] == "audit-package/1"
    assert manifest["exporter_version"] == AUDIT_EXPORTER_VERSION
    assert manifest["hash_algorithm"] == "SHA-256"
    listed = {entry["name"]: entry for entry in manifest["files"]}
    assert set(listed) == {file.name for file in package.files} - {"manifest.json"}
    assert [entry["name"] for entry in manifest["files"]] == sorted(listed)
    for file in package.files:
        if file.name == "manifest.json":
            continue
        assert listed[file.name]["bytes"] == len(file.data)
        assert listed[file.name]["sha256"] == sha256_hex(file.data)
    assert manifest["analysis_id"] == 42
    assert manifest["analyzed_at"] == "2026-09-20T09:30:00Z"
    assert "X-Audit-Package-SHA256" in manifest["package_hash_note"]
    assert manifest["limits"] == {
        "max_files": LIMITS.max_files, "max_file_bytes": LIMITS.max_file_bytes,
        "max_total_bytes": LIMITS.max_total_bytes,
    }


def test_analysis_json_is_the_snapshot_without_secrets_geometry_and_source_registry() -> None:
    package = _package()
    document = json.loads(package.by_name("analysis.json").data)
    response, _ = load_fixture("multizone")
    assert document["schema_version"] == "audit-analysis/1"
    assert document["analysis"]["analysis_id"] == 42
    assert document["analysis"]["data_release_ids"] == [7, 12]
    result = document["result"]
    assert "access_token" not in result and "sources" not in result and "section_quality" not in result
    assert result["parcel"]["geometry_geojson"] == GEOMETRY_MOVED
    assert result["parcel"]["metrics"]["area_sqm"] == response.parcel.metrics.area_sqm  # type: ignore[union-attr]
    assert len(result["mpzp_zones"]) == 2
    assert all(zone["intersection_geojson"] == GEOMETRY_MOVED for zone in result["mpzp_zones"])
    assert result["mpzp_zones"][0]["max_building_height_m"] == response.mpzp_zones[0].max_building_height_m
    # Macierz jakości jest zapisana w całości (bez legendy, która jest wyliczana).
    matrix = document["section_quality"]
    assert "legend" not in matrix and matrix["matrix_sha256"] == response.section_quality.matrix_sha256  # type: ignore[union-attr]
    assert len(matrix["sections"]) == 10
    assert matrix["policy_version"].startswith("quality-policy/1+")
    assert document["sources_file"] == "sources.json"
    assert {item["path"] for item in document["geometry_files"]} >= {"parcel.geojson", "layers/flood_risk.geojson"}


def test_crs_are_described_separately_and_geojson_is_wgs84() -> None:
    package = _package()
    document = json.loads(package.by_name("analysis.json").data)
    assert document["crs"] == {"computation": "EPSG:2180", "geojson_files": "EPSG:4326"}
    parcel_2180 = document["computation"]["parcel_geometry_epsg2180"]
    assert parcel_2180["properties"]["crs"] == "EPSG:2180"
    x, y = _first_coordinate(parcel_2180["geometry"])
    assert 500_000 < x < 700_000 and 200_000 < y < 300_000  # metry PUWG 1992 (rejon Krakowa)

    parcel = json.loads(package.by_name("parcel.geojson").data)
    assert parcel["type"] == "FeatureCollection"
    lon, lat = _first_coordinate(parcel["features"][0]["geometry"])
    assert 14 < lon < 25 and 48 < lat < 56  # stopnie WGS 84: długość, szerokość
    assert "EPSG:4326" in parcel["metadata"]["coordinate_reference_system"]
    assert parcel["metadata"]["computation_crs"] == "EPSG:2180"
    for file in package.files:
        if file.name.startswith("layers/"):
            layer = json.loads(file.data)
            lon, lat = _first_coordinate(layer["features"][0]["geometry"])
            assert 14 < lon < 25 and 48 < lat < 56, file.name
            assert layer["metadata"]["feature_count"] == len(layer["features"])


def _first_coordinate(geometry: dict[str, Any]) -> tuple[float, float]:
    node: Any = geometry["coordinates"]
    while isinstance(node[0], (list, tuple)):
        node = node[0]
    return float(node[0]), float(node[1])


def test_layers_carry_identifiers_and_share_of_the_parcel() -> None:
    package = _package()
    mpzp = json.loads(package.by_name("layers/mpzp_zones.geojson").data)
    assert {feature["properties"]["zone_symbol"] for feature in mpzp["features"]} == {
        zone.zone_symbol for zone in load_fixture("multizone")[0].mpzp_zones
    }
    pog = json.loads(package.by_name("layers/pog_zones.geojson").data)
    assert len(pog["features"]) == 3
    assert all(feature["properties"]["layer"] == "pog_zone" for feature in pog["features"])
    overlays = json.loads(package.by_name("layers/pog_overlays.geojson").data)
    assert {feature["properties"]["kind"] for feature in overlays["features"]} == {"OUZ", "OZS", "OSDIS"}
    buildable = json.loads(package.by_name("layers/buildable_area.geojson").data)
    assert buildable["features"][0]["properties"]["is_technical_approximation"] is True


def test_sources_json_keeps_every_reference_with_catalog_contract_and_artifact() -> None:
    package = _package()
    document = json.loads(package.by_name("sources.json").data)
    response, _ = load_fixture("multizone")
    assert document["analysis_id"] == 42
    sources = document["sources"]
    assert len(sources) == len(response.sources)
    ids = [item["source_id"] for item in sources]
    assert ids == sorted(ids, key=str)
    pog = next(item for item in sources if item["source_id"] == "pog_app")
    assert pog["artifact"]["uri"] == "https://ru.example.test/pog.gml"
    assert pog["release"]["version_label"] == "2026-09-19"
    assert "is_active" not in pog["release"]  # wartość zmienna w czasie łamałaby determinizm
    assert pog["catalog"]["license"] and pog["catalog"]["status"] == "production"
    assert pog["redistribution"] == "derived_only"
    assert pog["package_content"] == {"derived_layers": True, "raw_data": False}
    isok = next(item for item in sources if item["source_id"] == "isok")
    assert isok["catalog"]["freshness_policy"]["max_age_days"] == 7
    assert isok["package_content"] == {"derived_layers": True, "raw_data": True}
    for item in sources:
        assert item["fetched_at"] and item["source_name"]


def test_readme_describes_crs_analysis_date_statuses_and_verification() -> None:
    package = _package()
    readme = package.by_name("README.md").data.decode("utf-8")
    assert "EPSG:2180" in readme and "EPSG:4326" in readme
    assert "2026-09-20T09:30:00Z" in readme  # data analizy, nie eksportu
    assert "nie zawiera czasu eksportu" in readme
    for status in ("available", "partial", "no_coverage", "unavailable", "error", "unknown",
                   "out_of_scope", "awaiting_input"):
        assert f"`{status}`" in readme
    for state in ("fresh", "stale", "unknown"):
        assert f"`{state}`" in readme
    assert "manual_review_required" in readme
    assert "sha256sum" in readme and "verify_audit_package.py" in readme
    assert "X-Audit-Package-SHA256" in readme
    assert "## Macierz jakości tej analizy" in readme
    assert "raw_redistribution_not_allowed" in readme  # pominięcie surowych atrybutów POG
    assert "Brak pliku warstwy nie dowodzi braku ograniczeń" in readme


def test_export_time_never_enters_the_package() -> None:
    package = _package()
    year = re.compile(r"20(2[7-9]|[3-9]\d)-\d\d-\d\dT")  # daty po dniu analizy fixture
    for file in package.files:
        assert not year.search(file.data.decode("utf-8")), file.name


# --- Redystrybucja ---------------------------------------------------------------------------


def _marked_input(policy: dict[str, str], marker: str = "ZAKAZANA-TRESC-7f3a") -> tuple[AuditInput, str]:
    """Wejście, w którym warstwa ISOK i surowe atrybuty POG niosą rozpoznawalny znacznik."""
    base = _input(catalog=_catalog(**policy))
    layers = tuple(
        AuditLayer(
            name=layer.name, title=layer.title, description=layer.description, kind=layer.kind,
            source_ids=layer.source_ids,
            features=tuple(
                {**feature, "properties": {**feature["properties"], "marker": marker}}
                for feature in layer.features
            ),
        )
        for layer in base.layers
    )
    analysis = copy.deepcopy(dict(base.analysis))
    analysis["pog"]["raw_attributes"] = {"OBJECTID": 1, "tajne": marker}
    return (
        AuditInput(**{**base.__dict__, "layers": layers, "analysis": analysis}),
        marker,
    )


def test_forbidden_source_is_not_copied_but_reference_and_hash_are_kept() -> None:
    source, marker = _marked_input({"isok": "forbidden"})
    package = build_audit_package(source, LIMITS)
    names = {file.name for file in package.files}
    assert "layers/flood_risk.geojson" not in names
    assert "layers/nature_protection.geojson" in names  # inne źródło (gdos) jest dozwolone

    omitted = {item.name: item for item in package.omitted}
    flood = omitted["layers/flood_risk.geojson"]
    assert flood.kind == "derived_layer"
    assert flood.reason == "redistribution_forbidden"
    assert flood.source_ids == ("isok",)
    assert flood.policy == {"isok": "forbidden"}
    assert re.fullmatch(r"[0-9a-f]{64}", flood.sha256) and flood.bytes > 0

    manifest = json.loads(package.by_name("manifest.json").data)
    entry = next(item for item in manifest["omitted_artifacts"] if item["name"] == "layers/flood_risk.geojson")
    assert entry["sha256"] == flood.sha256 and entry["bytes"] == flood.bytes
    assert entry["reason_label"] and entry["source_ids"] == ["isok"]
    assert "layers/flood_risk.geojson" not in {item["name"] for item in manifest["files"]}

    # Referencja źródła nie ginie: rejestr źródeł nadal opisuje ISOK, ale bez treści.
    sources = json.loads(package.by_name("sources.json").data)["sources"]
    isok = next(item for item in sources if item["source_id"] == "isok")
    assert isok["source_url"] and isok["fetched_at"] and isok["redistribution"] == "forbidden"
    assert isok["package_content"] == {"derived_layers": False, "raw_data": False}

    # Zawartość zakazanej warstwy nie występuje w żadnym pliku (po dekompresji ani w ZIP).
    flood_marker_free = [file for file in package.files if b"flood_risk" in file.data and file.name.endswith(".geojson")]
    assert flood_marker_free == []
    archive = _zip_bytes(package)
    zipped = zipfile.ZipFile(io.BytesIO(archive))
    assert "layers/flood_risk.geojson" not in zipped.namelist()


def test_omitted_layer_hash_matches_the_canonical_content_that_was_left_out() -> None:
    source, _ = _marked_input({"isok": "forbidden"})
    package = build_audit_package(source, LIMITS)
    layer = next(item for item in source.layers if item.name == "flood_risk")
    from app.modules.reporting.application.audit_export import _layer_document

    expected = canonical_bytes(_layer_document(layer, source))
    flood = next(item for item in package.omitted if item.name == "layers/flood_risk.geojson")
    assert flood.sha256 == sha256_hex(expected) and flood.bytes == len(expected)


def test_marker_of_forbidden_layer_and_raw_attributes_never_reaches_the_archive() -> None:
    source, marker = _marked_input({"isok": "forbidden", "gdos": "unconfirmed", "pog_app": "derived_only"})
    package = build_audit_package(source, LIMITS)
    archive = _zip_bytes(package)
    # Pozostałe warstwy (mpzp, pog) niosą znacznik, bo ich źródła są dozwolone —
    # nie może go być natomiast w zakazanych: ISOK/GDOŚ i surowych atrybutach POG.
    for file in package.files:
        if file.name in {"layers/flood_risk.geojson", "layers/nature_protection.geojson"}:
            raise AssertionError(f"pominięta warstwa trafiła do pakietu: {file.name}")
    analysis = json.loads(package.by_name("analysis.json").data)
    assert analysis["result"]["pog"]["raw_attributes"] is None
    assert marker not in json.dumps(analysis["result"]["pog"]["raw_attributes"])
    assert any(item["pointer"] == "/result/pog/raw_attributes" for item in analysis["redactions"])
    assert "tajne" not in package.by_name("analysis.json").data.decode("utf-8")
    del archive


def test_derived_only_keeps_derived_layers_but_drops_raw_attributes() -> None:
    package = build_audit_package(_input(catalog=_catalog(pog_app="derived_only")), LIMITS)
    names = {file.name for file in package.files}
    assert {"layers/pog_zones.geojson", "layers/pog_overlays.geojson"} <= names
    raw = next(item for item in package.omitted if item.kind == "raw_attributes")
    assert raw.name == "analysis.json#/result/pog/raw_attributes"
    assert raw.reason == "raw_redistribution_not_allowed"
    assert raw.source_ids == ("pog_app",)


def test_allowed_source_keeps_raw_attributes() -> None:
    package = build_audit_package(_input(catalog=_catalog(pog_app="allowed")), LIMITS)
    analysis = json.loads(package.by_name("analysis.json").data)
    assert analysis["result"]["pog"]["raw_attributes"]
    assert analysis["redactions"] == [] and package.omitted == ()


def test_unconfirmed_and_unknown_sources_are_treated_as_forbidden() -> None:
    package = build_audit_package(_input(catalog=_catalog(isok="unconfirmed")), LIMITS)
    flood = next(item for item in package.omitted if item.name == "layers/flood_risk.geojson")
    assert flood.reason == "redistribution_unconfirmed"

    source = _input()
    unresolved = AuditInput(
        **{**source.__dict__, "layers": tuple(
            AuditLayer(name=layer.name, title=layer.title, description=layer.description, kind=layer.kind,
                       features=layer.features, source_ids=(None,) if layer.name == "mpzp_zones" else layer.source_ids)
            for layer in source.layers)}
    )
    package = build_audit_package(unresolved, LIMITS)
    mpzp = next(item for item in package.omitted if item.name == "layers/mpzp_zones.geojson")
    assert mpzp.reason == "redistribution_unconfirmed" and mpzp.source_ids == ("(nieustalone)",)
    assert "layers/mpzp_zones.geojson" not in {file.name for file in package.files}


def test_layer_with_mixed_sources_needs_every_source_to_allow() -> None:
    source = _input(catalog=_catalog())
    mixed = AuditInput(
        **{
            **source.__dict__,
            "layers": tuple(
                AuditLayer(name=layer.name, title=layer.title, description=layer.description, kind=layer.kind,
                           features=layer.features,
                           source_ids=("isok", "egib") if layer.name == "flood_risk" else layer.source_ids)
                for layer in source.layers
            ),
            "redistribution": {**source.redistribution, "egib": "forbidden"},
        }
    )
    package = build_audit_package(mixed, LIMITS)
    flood = next(item for item in package.omitted if item.name == "layers/flood_risk.geojson")
    assert flood.reason == "redistribution_forbidden"
    assert flood.policy == {"egib": "forbidden", "isok": "allowed"}


def test_forbidden_parcel_source_keeps_only_reference_and_hash_for_the_geometry() -> None:
    package = build_audit_package(_input(catalog=_catalog(uldk="forbidden")), LIMITS)
    names = {file.name for file in package.files}
    assert "parcel.geojson" not in names and "layers/buildable_area.geojson" not in names
    omitted = {item.name: item for item in package.omitted}
    assert omitted["parcel.geojson"].reason == "redistribution_forbidden"
    assert "analysis.json#/computation/parcel_geometry_epsg2180" in omitted
    analysis = json.loads(package.by_name("analysis.json").data)
    assert analysis["computation"]["parcel_geometry_epsg2180"] is None
    assert analysis["analysis"]["parcel_identifier"] == "126101_1.0001.2417/5"  # identyfikator zostaje


def test_empty_layers_are_not_written_and_missing_geometry_is_not_a_finding() -> None:
    source = _input()
    empty = AuditInput(**{**source.__dict__, "layers": (
        AuditLayer("flood_risk", "ISOK", "opis", (), ("isok",)),
    ), "raw_blocks": (AuditRawBlock(("pog", "raw_attributes"), "pog_app", "x"),)})
    package = build_audit_package(empty, LIMITS)
    assert {file.name for file in package.files} == {"analysis.json", "sources.json", "README.md", "manifest.json"}


# --- Determinizm ---------------------------------------------------------------------------


def test_two_builds_of_the_same_snapshot_are_byte_identical() -> None:
    first, second = _package(), _package()
    assert [(f.name, f.data) for f in first.files] == [(f.name, f.data) for f in second.files]
    assert _zip_bytes(first) == _zip_bytes(second)
    left, right = write_deterministic_zip(first.files), write_deterministic_zip(second.files)
    try:
        assert left.sha256 == right.sha256 and left.size == right.size
    finally:
        left.close(), right.close()


def test_zip_layout_is_sorted_with_fixed_timestamps_and_attributes() -> None:
    files = list(_package().files)
    random.Random(7).shuffle(files)
    archive = zipfile.ZipFile(io.BytesIO(_zip_of(files)))
    infos = archive.infolist()
    assert [info.filename for info in infos] == sorted(info.filename for info in infos)
    for info in infos:
        assert info.date_time == FIXED_ZIP_TIMESTAMP
        assert info.compress_type == zipfile.ZIP_DEFLATED
        assert info.external_attr >> 16 == 0o644
        assert info.create_system == 3
        assert info.extra == b"" and not info.filename.startswith("/")
    assert archive.testzip() is None
    assert _zip_of(files) == _zip_bytes(_package())  # kolejność wejścia bez znaczenia


def test_archive_passes_the_project_safe_extraction_helper() -> None:
    package = _package()
    members = read_zip_members(_zip_bytes(package), limits=ArchiveLimits(max_entries=100))
    assert {name for name, _ in members} == {file.name for file in package.files}
    assert dict(members)["manifest.json"] == package.by_name("manifest.json").data


def test_case_insensitive_duplicates_are_rejected() -> None:
    with pytest.raises(ValueError, match="unikalne"):
        write_deterministic_zip([PackageFile("a.json", b"1", "x/y"), PackageFile("A.json", b"2", "x/y")])


# --- SHA-256 i wykrywanie zmian -------------------------------------------------------------


def test_every_sha_verifies_and_a_single_flipped_byte_is_detected() -> None:
    package = _package()
    manifest = json.loads(package.by_name("manifest.json").data)
    contents = {file.name: file.data for file in package.files}
    assert verify_manifest(manifest, contents.get) == []

    for name in sorted(contents):
        if name == "manifest.json":
            continue
        tampered = dict(contents)
        data = bytearray(tampered[name])
        data[len(data) // 2] ^= 0x01
        tampered[name] = bytes(data)
        problems = verify_manifest(manifest, tampered.get)
        assert problems == [f"zła suma SHA-256: {name}"], name

    missing = {key: value for key, value in contents.items() if key != "sources.json"}
    assert verify_manifest(manifest, missing.get) == ["brak pliku: sources.json"]
    resized = {**contents, "README.md": contents["README.md"] + b"x"}
    assert len(verify_manifest(manifest, resized.get)) == 2  # rozmiar i suma


def test_offline_verifier_accepts_unpacked_and_zipped_package_and_detects_changes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    verifier = _load_script()
    package = _package()
    archive_path = tmp_path / "pakiet.zip"
    data = _zip_bytes(package)
    archive_path.write_bytes(data)

    assert verifier.main([str(archive_path)]) == 0
    assert "OK: 11 plików" in capsys.readouterr().out
    assert verifier.main([str(archive_path), "--package-sha256", sha256_hex(data)]) == 0
    assert verifier.main([str(archive_path), "--package-sha256", "0" * 64]) == 1
    assert "zła suma SHA-256 całej paczki" in capsys.readouterr().out

    unpacked = tmp_path / "rozpakowany"
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(unpacked)
    assert verifier.main([str(unpacked)]) == 0
    capsys.readouterr()

    target = unpacked / "layers" / "flood_risk.geojson"
    raw = bytearray(target.read_bytes())
    raw[10] ^= 0x01  # zmiana jednego bajtu
    target.write_bytes(bytes(raw))
    assert verifier.main([str(unpacked)]) == 1
    assert "zła suma SHA-256: layers/flood_risk.geojson" in capsys.readouterr().out

    target.write_bytes(bytes(raw[:-1]))
    assert verifier.main([str(unpacked)]) == 1
    assert "zły rozmiar layers/flood_risk.geojson" in capsys.readouterr().out

    (unpacked / "layers" / "flood_risk.geojson").unlink()
    (unpacked / "dodatkowy.txt").write_text("x", encoding="utf-8")
    assert verifier.main([str(unpacked)]) == 1
    output = capsys.readouterr().out
    assert "brak pliku z manifestu: layers/flood_risk.geojson" in output
    assert "plik spoza manifestu: dodatkowy.txt" in output

    (unpacked / "manifest.json").unlink()
    assert verifier.main([str(unpacked)]) == 1
    assert "brak manifest.json" in capsys.readouterr().out


def test_offline_verifier_error_paths(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    verifier = _load_script()
    assert verifier.main([str(tmp_path / "brak.zip")]) == 2
    broken = tmp_path / "zepsuty.zip"
    broken.write_bytes(b"to nie jest zip")
    assert verifier.main([str(broken)]) == 2
    assert verifier.main([str(tmp_path), "--package-sha256", "0" * 64]) == 2
    capsys.readouterr()

    unreadable = tmp_path / "manifest_zly"
    unreadable.mkdir()
    (unreadable / "manifest.json").write_text("{nie json", encoding="utf-8")
    assert verifier.main([str(unreadable)]) == 1
    assert "manifest nieczytelny" in capsys.readouterr().out

    bad_schema = tmp_path / "schemat"
    bad_schema.mkdir()
    (bad_schema / "manifest.json").write_text(json.dumps({"schema_version": "x", "files": []}), encoding="utf-8")
    assert verifier.main([str(bad_schema)]) == 1
    assert "nieobsługiwany schemat" in capsys.readouterr().out

    slip = tmp_path / "slip.zip"
    with zipfile.ZipFile(slip, "w") as archive:
        archive.writestr("../uciekinier.txt", b"x")
        archive.writestr("manifest.json", json.dumps({"schema_version": "audit-package/1", "files": []}))
    assert verifier.main([str(slip)]) == 1
    assert "niebezpieczna nazwa wpisu" in capsys.readouterr().out


# --- Limity ------------------------------------------------------------------------------------


def test_limits_reject_too_many_files_too_large_files_and_too_large_package() -> None:
    source = _input()
    with pytest.raises(AuditExportLimitError, match="plików"):
        build_audit_package(source, AuditLimits(max_files=3, max_file_bytes=10**7, max_total_bytes=10**8))
    with pytest.raises(AuditExportLimitError, match="przekracza limit 1000"):
        build_audit_package(source, AuditLimits(max_files=50, max_file_bytes=1000, max_total_bytes=10**8))
    with pytest.raises(AuditExportLimitError, match="Pakiet audytowy"):
        build_audit_package(source, AuditLimits(max_files=50, max_file_bytes=10**7, max_total_bytes=5000))
    with pytest.raises(AuditExportLimitError):
        write_deterministic_zip(_package().files, AuditLimits(1, 10**7, 10**8))


def test_default_limits_come_from_settings_and_fit_the_fixture() -> None:
    limits = audit_limits()
    assert limits.max_files >= 50 and limits.max_total_bytes >= 8 * 1024 * 1024
    package = build_audit_package(_input(), limits)
    assert sum(len(file.data) for file in package.files) < limits.max_total_bytes


# --- Manifest, JSON i strumień -------------------------------------------------------------------


def test_manifest_builder_guards_against_duplicates_and_self_reference() -> None:
    files = [PackageFile("a.json", b"1", "application/json")]
    with pytest.raises(ValueError, match="unikalne"):
        build_manifest(files + files, [], {}, LIMITS)
    with pytest.raises(ValueError, match="samego siebie"):
        build_manifest([PackageFile("manifest.json", b"{}", "application/json")], [], {}, LIMITS)
    omitted = OmittedArtifact("layers/x.geojson", "derived_layer", ("z",), "redistribution_forbidden",
                              {"z": "forbidden"}, "a" * 64, 10)
    manifest = json.loads(build_manifest(files, [omitted], {"analysis_id": 1}, LIMITS).data)
    assert manifest["omitted_artifacts"][0]["redistribution"] == {"z": "forbidden"}
    assert manifest["files"][0]["sha256"] == sha256_hex(b"1")


def test_json_rendering_is_canonical_and_rejects_nan() -> None:
    assert render_json({"b": 1, "a": "ą"}) == '{\n  "a": "ą",\n  "b": 1\n}\n'.encode()
    with pytest.raises(ValueError):
        render_json({"x": float("inf")})
    assert canonical_bytes({"b": [1], "a": None}) == b'{"a":null,"b":[1]}'


def test_streaming_yields_all_bytes_in_chunks_and_always_closes_the_file() -> None:
    package = _package()
    result = write_deterministic_zip(package.files)
    chunks = list(iter_archive(result, chunk_size=4096))
    assert len(chunks) > 1 and all(len(chunk) <= 4096 for chunk in chunks)
    assert b"".join(chunks) == _zip_bytes(package)
    assert result.file.closed and sha256_hex(b"".join(chunks)) == result.sha256

    partial = write_deterministic_zip(package.files)
    stream = iter_archive(partial, chunk_size=1024)
    next(stream)
    stream.close()  # klient rozłączył się w trakcie pobierania
    assert partial.file.closed


def test_large_archive_spills_to_disk_but_hash_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.reporting.infrastructure import archive

    monkeypatch.setattr(archive, "SPOOL_MAX_BYTES", 1024)
    payload = bytes(random.Random(1).randbytes(200_000))  # nieściśliwe: wymusza zapis na dysk
    result = write_deterministic_zip([PackageFile("dane.bin", payload, "application/octet-stream")])
    try:
        data = result.file.read()
        assert isinstance(result, ArchiveResult) and len(data) == result.size > 200_000
        assert sha256_hex(data) == result.sha256
        assert zipfile.ZipFile(io.BytesIO(data)).read("dane.bin") == payload
    finally:
        result.close()


def test_export_modules_do_not_read_the_clock_or_the_network() -> None:
    root = Path(__file__).resolve().parents[1] / "app"
    for relative in (
        "modules/reporting/domain/audit_package.py",
        "modules/reporting/application/audit_export.py",
        "modules/reporting/infrastructure/archive.py",
    ):
        source = (root / relative).read_text(encoding="utf-8")
        assert "datetime.now" not in source and "time.time" not in source, relative
        assert "httpx" not in source and "requests" not in source, relative


def test_ui_only_fields_are_not_part_of_the_audit_document() -> None:
    payload = {
        "manual_zone_context": {
            "plan_id": "P1",
            "raster_preview_source_key": "kimpzp",
            "symbol_max_length": 20,
            "symbol_allowed_pattern": "^[A-Z]+$",
            "document": {"sha256": "a" * 64, "preview_path": "/api/x"},
        }
    }
    _strip_ui_fields(payload)
    assert payload == {"manual_zone_context": {"plan_id": "P1", "document": {"sha256": "a" * 64}}}
    empty: dict[str, Any] = {"manual_zone_context": None}
    _strip_ui_fields(empty)  # brak kontekstu ręcznego nie jest błędem
    assert empty == {"manual_zone_context": None}


def test_geojson_fields_are_normalised_to_features_with_properties() -> None:
    point = {"type": "Point", "coordinates": [19.9, 50.0]}
    feature = {"type": "Feature", "geometry": point, "properties": {"a": 1}}
    assert _features(None, {}) == [] and _features({"type": "Nieznany"}, {}) == []
    assert _features(point, {"layer": "x"}) == [{"type": "Feature", "geometry": point, "properties": {"layer": "x"}}]
    merged = _features(feature, {"layer": "x", "a": 0})
    assert merged[0]["properties"] == {"layer": "x", "a": 1}  # właściwości obiektu mają pierwszeństwo
    collection = {"type": "FeatureCollection", "features": [feature, feature]}
    assert len(_features(collection, {})) == 2
    geometry_collection = {"type": "GeometryCollection", "geometries": [point]}
    assert _features(geometry_collection, {})[0]["geometry"] == geometry_collection


# --- warunki wartości parametrów MPZP (PV3-08) ------------------------------------------------


def test_package_carries_value_conditions_and_documents_them() -> None:
    from app.schemas.analyze import MpzpParameterEvidence, MpzpValueCondition

    def with_conditions(response):
        zone = response.mpzp_zones[0]
        parameters = [
            MpzpParameterEvidence(
                name="max_building_height_m", normalized_value=9.5, unit="m", raw_value="9,5 m",
                evidence_text="a dla budynków przekrytych dachem płaskim: 9,5 m", page_number=18, confidence=0.8,
                conditions=[MpzpValueCondition(kind="roof_type", label="dach płaski", quote="dachem płaskim")],
            ),
            MpzpParameterEvidence(
                name="max_building_height_m", normalized_value=11.0, unit="m", raw_value="11 m",
                evidence_text="maksymalną wysokość zabudowy: 11 m", page_number=18, confidence=0.8,
            ),
        ]
        return response.model_copy(update={"mpzp_zones": [zone.model_copy(update={"parameters": parameters}), *response.mpzp_zones[1:]]})

    package = build_audit_package(_input(response_update=with_conditions), LIMITS)
    analysis = json.loads(package.by_name("analysis.json").data)
    parameters = analysis["result"]["mpzp_zones"][0]["parameters"]
    conditional, plain = parameters
    assert conditional["value_kind"] == "conditional"
    assert conditional["conditions"] == [{"kind": "roof_type", "label": "dach płaski", "quote": "dachem płaskim"}]
    assert plain["value_kind"] == "unconditional" and plain["conditions"] == []
    readme = package.by_name("README.md").data.decode("utf-8")
    assert "wartości warunkowe i sprzeczności" in readme and "`value_kind`" in readme and "`conditions[]`" in readme
    assert AUDIT_EXPORTER_VERSION == "audit-exporter/1.1.0" and AUDIT_EXPORTER_VERSION in readme
    manifest = json.loads(package.by_name("manifest.json").data)
    assert manifest["exporter_version"] == AUDIT_EXPORTER_VERSION
