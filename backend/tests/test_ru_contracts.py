"""Offline testy zamrozonego kontraktu uslug Rejestru Urbanistycznego."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

import pytest

from app.core.ru_contracts import (
    EXPECTED_WMS_CRS,
    REQUIRED_WFS_FEATURE_TYPES,
    RuContractError,
    assert_csw_contract,
    assert_wfs_contract,
    assert_wfs_getfeature_contract,
    assert_wms_contract,
    load_ru_fixture_dir,
    parse_csw_capabilities,
    parse_wfs_capabilities,
    parse_wfs_getfeature,
    parse_wms_capabilities,
)
from app.core.data_sources import AccessType, ensure_source_runnable, load_catalog
from scripts.fetch_ru_contracts import REQUESTS

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "ru"
EXPECTED_FILES = {
    "wms_pog_capabilities_1_3_0.xml",
    "wfs_pog_capabilities_2_0_0.xml",
    "csw_capabilities_2_0_2.xml",
    "wfs_pog_getfeature_246101.xml",
    "wfs_pog_describe_feature_type_3_0.xsd",
    "planowaniePrzestrzenne_3_0.xsd",
    "wfs_pog_getfeature_act.xml",
    "wfs_pog_getfeature_document.xml",
    "wfs_pog_getfeature_osdis.xml",
    "wfs_pog_getfeature_ouz.xml",
    "wfs_pog_getfeature_ozs.xml",
    "wfs_pog_getfeature_zone.xml",
    "csw_getrecords_iso_226401.xml",
}
MANIFEST_FIELDS = {
    "url",
    "fetched_at",
    "sha256",
    "service",
    "version",
    "official_services_url",
    "fees",
    "access_constraints",
    "access_basis",
}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _copy_fixture(tmp_path: Path, filename: str) -> Path:
    target = tmp_path / filename
    shutil.copy2(FIXTURE_DIR / filename, target)
    return target


def test_manifest_has_exact_schema_and_matching_sha256() -> None:
    manifest = json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))

    assert set(manifest) == EXPECTED_FILES
    for filename, entry in manifest.items():
        assert set(entry) == MANIFEST_FIELDS
        assert entry["service"] in {"wms", "wfs", "csw"}
        assert entry["url"].startswith((
            "https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe/",
            "https://www.gov.pl/static/zagospodarowanieprzestrzenne/schemas/",
        ))
        assert datetime.fromisoformat(entry["fetched_at"].replace("Z", "+00:00"))
        assert entry["official_services_url"] == (
            "https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe"
        )
        assert entry["fees"] == "Brak ograniczeń w publicznym dostępie"
        assert entry["access_constraints"] == ("Brak warunków dostępu i użytkowania")
        assert "GetCapabilities" in entry["access_basis"]
        payload = (FIXTURE_DIR / filename).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == entry["sha256"]


def test_refresh_script_recreates_every_frozen_ru_artifact() -> None:
    assert {request.filename for request in REQUESTS} == EXPECTED_FILES


def test_loader_reads_complete_fixture_set_from_local_directory(tmp_path: Path) -> None:
    local_copy = tmp_path / "ru"
    shutil.copytree(FIXTURE_DIR, local_copy)

    fixtures = load_ru_fixture_dir(local_copy)

    assert set(fixtures.manifest) == EXPECTED_FILES
    assert set(fixtures.xml) == EXPECTED_FILES
    assert all(fixtures.xml.values())


def test_wms_pog_contract() -> None:
    fixtures = load_ru_fixture_dir(FIXTURE_DIR)
    capabilities = parse_wms_capabilities(
        fixtures.xml["wms_pog_capabilities_1_3_0.xml"]
    )

    assert_wms_contract(capabilities)
    assert len(capabilities.layers) == 51
    assert capabilities.crs == EXPECTED_WMS_CRS
    assert "EPSG:3857" not in capabilities.crs
    assert "APP.POG.SW.PrawnieWiazacyLubRealizowany" in capabilities.layers
    assert (
        "APP.POG.ObszarUzupelnieniaZabudowy.PrawnieWiazacyLubRealizowany"
        in capabilities.layers
    )
    assert capabilities.fees == "Brak ograniczeń w publicznym dostępie"
    assert capabilities.access_constraints == "Brak warunków dostępu i użytkowania"


def test_wfs_pog_contract() -> None:
    fixtures = load_ru_fixture_dir(FIXTURE_DIR)
    capabilities = parse_wfs_capabilities(
        fixtures.xml["wfs_pog_capabilities_2_0_0.xml"]
    )

    assert_wfs_contract(capabilities)
    assert REQUIRED_WFS_FEATURE_TYPES <= capabilities.feature_types
    assert capabilities.feature_types == REQUIRED_WFS_FEATURE_TYPES
    assert capabilities.default_crs == {"EPSG:2180"}
    assert {"EPSG:2176", "EPSG:2177", "EPSG:2178", "EPSG:2179"} <= (
        capabilities.supported_crs
    )
    assert capabilities.count_default == 10
    assert capabilities.fees == "Brak ograniczeń w publicznym dostępie"
    assert capabilities.access_constraints == "Brak warunków dostępu i użytkowania"


def test_csw_contract() -> None:
    fixtures = load_ru_fixture_dir(FIXTURE_DIR)
    capabilities = parse_csw_capabilities(fixtures.xml["csw_capabilities_2_0_2.xml"])

    assert_csw_contract(capabilities)
    assert "http://www.opengis.net/cat/csw/2.0.2" in (
        capabilities.get_records_output_schemas
    )
    assert capabilities.fees == "Brak ograniczeń w publicznym dostępie"
    assert capabilities.access_constraints == "Brak warunków dostępu i użytkowania"


def test_small_getfeature_is_real_bielsko_biala_act() -> None:
    fixtures = load_ru_fixture_dir(FIXTURE_DIR)
    response = parse_wfs_getfeature(fixtures.xml["wfs_pog_getfeature_246101.xml"])

    assert_wfs_getfeature_contract(response)
    assert response.number_matched == "1"
    assert response.number_returned == 1
    assert response.feature_count == 1
    assert response.feature_types == {"AktPlanowaniaPrzestrzennego"}
    assert any("246101-POG" in identifier for identifier in response.identifiers)


def test_wms_version_change_fails_contract_on_copied_xml(tmp_path: Path) -> None:
    fixture = _copy_fixture(tmp_path, "wms_pog_capabilities_1_3_0.xml")
    tree = ElementTree.parse(fixture)
    tree.getroot().set("version", "1.1.1")
    tree.write(fixture, encoding="utf-8", xml_declaration=True)

    with pytest.raises(AssertionError):
        assert_wms_contract(parse_wms_capabilities(fixture))


def test_missing_required_wfs_type_fails_contract_on_copied_xml(
    tmp_path: Path,
) -> None:
    fixture = _copy_fixture(tmp_path, "wfs_pog_capabilities_2_0_0.xml")
    tree = ElementTree.parse(fixture)
    removed = False
    for parent in tree.getroot().iter():
        for child in list(parent):
            if _local_name(child.tag) != "FeatureType":
                continue
            name = next(
                (
                    element.text
                    for element in child
                    if _local_name(element.tag) == "Name"
                ),
                None,
            )
            if name and name.endswith(":DokumentFormalny"):
                parent.remove(child)
                removed = True
    assert removed
    tree.write(fixture, encoding="utf-8", xml_declaration=True)

    with pytest.raises(AssertionError):
        assert_wfs_contract(parse_wfs_capabilities(fixture))


def test_missing_epsg_2180_fails_wfs_contract_on_copied_xml(tmp_path: Path) -> None:
    fixture = _copy_fixture(tmp_path, "wfs_pog_capabilities_2_0_0.xml")
    tree = ElementTree.parse(fixture)
    changed = 0
    for element in tree.getroot().iter():
        if _local_name(element.tag) in {"DefaultCRS", "DefaultSRS"} and (
            element.text or ""
        ).endswith("/2180"):
            element.text = (element.text or "").replace("/2180", "/4326")
            changed += 1
    assert changed > 0
    tree.write(fixture, encoding="utf-8", xml_declaration=True)

    with pytest.raises(AssertionError):
        assert_wfs_contract(parse_wfs_capabilities(fixture))


def test_loader_rejects_silent_fixture_replacement(tmp_path: Path) -> None:
    local_copy = tmp_path / "ru"
    shutil.copytree(FIXTURE_DIR, local_copy)
    fixture = local_copy / "wms_pog_capabilities_1_3_0.xml"
    fixture.write_bytes(fixture.read_bytes().replace(b"1.3.0", b"1.1.1", 1))

    with pytest.raises(RuContractError, match="SHA-256"):
        load_ru_fixture_dir(local_copy)


def test_parser_rejects_malformed_xml(tmp_path: Path) -> None:
    fixture = tmp_path / "broken.xml"
    fixture.write_text("<WMS_Capabilities>", encoding="utf-8")

    with pytest.raises(RuContractError, match="Niepoprawny XML"):
        parse_wms_capabilities(fixture)


def test_catalog_ru_matches_frozen_capabilities_and_manifest() -> None:
    fixtures = load_ru_fixture_dir(FIXTURE_DIR)
    wms = parse_wms_capabilities(fixtures.xml["wms_pog_capabilities_1_3_0.xml"])
    wfs = parse_wfs_capabilities(fixtures.xml["wfs_pog_capabilities_2_0_0.xml"])
    csw = parse_csw_capabilities(fixtures.xml["csw_capabilities_2_0_2.xml"])
    catalog = load_catalog()
    source = ensure_source_runnable("pog_app", catalog)
    resources = {resource.role: resource for resource in source.resources}
    wms_resource = resources["ru_wms_preview"]
    wfs_resource = resources["ru_wfs"]
    csw_resource = resources["ru_csw"]

    assert (
        source.capabilities_url
        == fixtures.manifest["wfs_pog_capabilities_2_0_0.xml"].url
    )
    assert wms_resource.url in fixtures.manifest["wms_pog_capabilities_1_3_0.xml"].url
    assert wfs_resource.url in fixtures.manifest["wfs_pog_capabilities_2_0_0.xml"].url
    assert csw_resource.url in fixtures.manifest["csw_capabilities_2_0_2.xml"].url
    assert wms_resource.access_type is AccessType.WMS
    assert wfs_resource.access_type is AccessType.WFS
    assert csw_resource.access_type is AccessType.CSW
    assert wms_resource.protocol_version == wms.version == "1.3.0"
    assert wfs_resource.protocol_version == wfs.version == "2.0.0"
    assert csw_resource.protocol_version == csw.version == "2.0.2"
    assert set(wms_resource.layers) == set(wms.layers)
    assert set(wms_resource.supported_crs) == set(wms.crs)
    assert set(wms_resource.inherited_crs) == {"EPSG:2180"}
    assert "EPSG:3857" not in wms_resource.supported_crs
    assert {value.rsplit(":", 1)[-1] for value in wfs_resource.type_names} == set(
        wfs.feature_types
    )
    assert set(wfs_resource.supported_crs) == set(wfs.supported_crs)
    assert wfs_resource.count_default == wfs.count_default == 10
    assert set(wfs_resource.output_formats) == set(wfs.get_feature_formats)
    assert wfs_resource.namespace_uri is not None
    assert (
        f'xmlns:app-pog="{wfs_resource.namespace_uri}"'.encode()
        in fixtures.xml["wfs_pog_capabilities_2_0_0.xml"]
    )
