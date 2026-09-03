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

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "ru"
EXPECTED_FILES = {
    "wms_pog_capabilities_1_3_0.xml",
    "wfs_pog_capabilities_2_0_0.xml",
    "csw_capabilities_2_0_2.xml",
    "wfs_pog_getfeature_246101.xml",
}
MANIFEST_FIELDS = {"url", "fetched_at", "sha256", "service", "version"}


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
        assert entry["url"].startswith(
            "https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe/"
        )
        assert datetime.fromisoformat(entry["fetched_at"].replace("Z", "+00:00"))
        payload = (FIXTURE_DIR / filename).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == entry["sha256"]


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
    assert capabilities.feature_types == {
        *REQUIRED_WFS_FEATURE_TYPES,
        "ObszarStandardowDostepnosciInfrastrukturySpolecznej",
    }
    assert capabilities.default_crs == {"EPSG:2180"}
    assert {"EPSG:2176", "EPSG:2177", "EPSG:2178", "EPSG:2179"} <= (
        capabilities.supported_crs
    )
    assert capabilities.count_default == 100
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
    assert response.number_matched == "unknown"
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
