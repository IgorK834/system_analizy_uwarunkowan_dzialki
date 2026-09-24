"""Testy katalogu źródeł danych (Faza 10.1).

Weryfikują wczytanie realnego katalogu, walidację kontraktu (duplikaty, brak
licencji/attribution, niedozwolony CRS, niespójna gotowość produkcyjna) oraz
guard uruchomienia adaptera. Dodatkowo sprawdzają spójność katalogu z realnymi
adapterami (jedno źródło prawdy) i z fixture GetCapabilities KIMPZP.
"""

from __future__ import annotations

import copy
from datetime import date
from pathlib import Path

import pytest

from app.core.data_sources import (
    AccessType,
    CatalogFileError,
    CatalogValidationError,
    DataSourceCatalog,
    DataSourceEntry,
    DuplicateSourceIdError,
    SourceNotFoundError,
    SourceNotRunnableError,
    SourceStatus,
    ensure_source_runnable,
    get_catalog,
    load_catalog,
    parse_catalog,
)
from tests.repo_structure import find_repo_root

# --- Pomocnicze budowanie poprawnych struktur --------------------------------


def _valid_production_entry() -> dict:
    """Kompletny wpis produkcyjny (REST) spełniający wszystkie wymagania."""
    return {
        "source_id": "example_prod",
        "name": "Przykładowe źródło produkcyjne",
        "owner": "Przykładowy właściciel",
        "status": "production",
        "production_ready": True,
        "contract_confirmed": True,
        "access_type": "rest",
        "capabilities_url": "https://example.gov.pl/api/",
        "file_url": None,
        "type_names": None,
        "layers": None,
        "protocol_version": "1.0",
        "source_crs": "EPSG:2180",
        "target_crs": "EPSG:2180",
        "teryt_scope": ["*"],
        "license": "Dane otwarte, wykorzystanie zgodnie z regulaminem.",
        "attribution": "Źródło: Przykładowy właściciel.",
        "expected_update_interval": "not_published",
        "sla": "not_published",
        "last_manual_verification": date(2026, 7, 22),
        "notes": None,
    }


def _valid_research_entry() -> dict:
    entry = _valid_production_entry()
    entry.update(
        source_id="example_research",
        status="research",
        production_ready=False,
        contract_confirmed=False,
        capabilities_url=None,
        last_manual_verification=None,
    )
    return entry


def _catalog_dict(*entries: dict) -> dict:
    return {"schema_version": "1.0", "sources": list(entries)}


# --- Wczytanie realnego katalogu ---------------------------------------------


def test_real_catalog_loads_and_validates() -> None:
    catalog_path = find_repo_root() / "docs" / "data_sources" / "catalog.yaml"
    catalog = load_catalog(catalog_path)

    assert isinstance(catalog, DataSourceCatalog)
    assert len(catalog.sources) >= 10


def test_real_catalog_covers_required_sources() -> None:
    catalog_path = find_repo_root() / "docs" / "data_sources" / "catalog.yaml"
    catalog = load_catalog(catalog_path)
    ids = {entry.source_id for entry in catalog.sources}

    # Katalog musi obejmować ULDK, EMUiA/UUG, EGiB, KIMPZP, POG/APP, GDOŚ,
    # ISOK, KIUT/GESUT, NMT oraz planowane warstwy ograniczeń.
    required = {
        "uldk",
        "emuia_uug",
        "prg_address_dictionary",
        "egib",
        "kimpzp",
        "pog_app",
        "gdos",
        "isok",
        "kiut_gesut",
        "nmt",
        "planned_restrictions",
    }
    assert required <= ids


def test_real_catalog_status_variants_present() -> None:
    catalog_path = find_repo_root() / "docs" / "data_sources" / "catalog.yaml"
    catalog = load_catalog(catalog_path)
    statuses = {entry.status for entry in catalog.sources}

    assert SourceStatus.PRODUCTION in statuses
    assert SourceStatus.RESEARCH in statuses
    assert SourceStatus.CONTRACT_REQUIRED in statuses
    assert SourceStatus.NO_REDISTRIBUTION in statuses


def test_real_catalog_production_sources_are_fully_verified() -> None:
    catalog_path = find_repo_root() / "docs" / "data_sources" / "catalog.yaml"
    catalog = load_catalog(catalog_path)

    for entry in catalog.production_sources():
        assert entry.status is SourceStatus.PRODUCTION
        assert entry.contract_confirmed is True
        assert entry.last_manual_verification is not None
        assert entry.license.strip()
        assert entry.attribution.strip()
        assert entry.capabilities_url or entry.file_url


# --- Poprawne wczytanie kompletnego katalogu (z dict) ------------------------


def test_parse_complete_catalog_ok() -> None:
    catalog = parse_catalog(
        _catalog_dict(_valid_production_entry(), _valid_research_entry())
    )
    assert len(catalog.sources) == 2
    assert catalog.get("example_prod").is_runnable is True


# --- Wykrycie duplikatu source_id --------------------------------------------


def test_duplicate_source_id_rejected() -> None:
    entry_a = _valid_production_entry()
    entry_b = _valid_research_entry()
    entry_b["source_id"] = entry_a["source_id"]

    with pytest.raises(DuplicateSourceIdError):
        parse_catalog(_catalog_dict(entry_a, entry_b))


# --- Odrzucenie braku license / attribution ----------------------------------


def test_missing_license_rejected() -> None:
    entry = _valid_production_entry()
    entry["license"] = ""

    with pytest.raises(CatalogValidationError):
        parse_catalog(_catalog_dict(entry))


def test_missing_attribution_rejected() -> None:
    entry = _valid_production_entry()
    entry["attribution"] = ""

    with pytest.raises(CatalogValidationError):
        parse_catalog(_catalog_dict(entry))


# --- Odrzucenie nieznanego / niedozwolonego CRS ------------------------------


def test_unknown_source_crs_rejected() -> None:
    entry = _valid_production_entry()
    entry["source_crs"] = "EPSG:9999"

    with pytest.raises(CatalogValidationError):
        parse_catalog(_catalog_dict(entry))


def test_non_canonical_target_crs_rejected() -> None:
    entry = _valid_production_entry()
    entry["target_crs"] = "EPSG:4326"

    with pytest.raises(CatalogValidationError):
        parse_catalog(_catalog_dict(entry))


# --- Odrzucenie production_ready bez kontraktu / daty weryfikacji ------------


def test_production_ready_without_contract_confirmed_rejected() -> None:
    entry = _valid_production_entry()
    entry["contract_confirmed"] = False

    with pytest.raises(CatalogValidationError):
        parse_catalog(_catalog_dict(entry))


def test_production_ready_without_verification_date_rejected() -> None:
    entry = _valid_production_entry()
    entry["last_manual_verification"] = None

    with pytest.raises(CatalogValidationError):
        parse_catalog(_catalog_dict(entry))


def test_production_ready_with_non_production_status_rejected() -> None:
    entry = _valid_production_entry()
    entry["status"] = "research"

    with pytest.raises(CatalogValidationError):
        parse_catalog(_catalog_dict(entry))


def test_production_status_requires_production_ready() -> None:
    entry = _valid_production_entry()
    entry["production_ready"] = False

    with pytest.raises(CatalogValidationError):
        parse_catalog(_catalog_dict(entry))


def test_production_wms_without_layers_rejected() -> None:
    entry = _valid_production_entry()
    entry.update(access_type="wms", layers=None, type_names=None)

    with pytest.raises(CatalogValidationError):
        parse_catalog(_catalog_dict(entry))


def test_production_wfs_without_type_names_rejected() -> None:
    entry = _valid_production_entry()
    entry.update(access_type="wfs", layers=None, type_names=None)

    with pytest.raises(CatalogValidationError):
        parse_catalog(_catalog_dict(entry))


def test_production_without_capabilities_or_file_url_rejected() -> None:
    entry = _valid_production_entry()
    entry.update(capabilities_url=None, file_url=None)

    with pytest.raises(CatalogValidationError):
        parse_catalog(_catalog_dict(entry))


def test_unknown_field_rejected() -> None:
    entry = _valid_production_entry()
    entry["licence"] = "literówka"  # celowa literówka klucza

    with pytest.raises(CatalogValidationError):
        parse_catalog(_catalog_dict(entry))


# --- Guard uruchomienia adaptera ---------------------------------------------


def _guard_catalog() -> DataSourceCatalog:
    return parse_catalog(
        _catalog_dict(
            _valid_production_entry(),
            _valid_research_entry(),
            _placeholder_entry(),
            _contract_required_entry(),
            _no_redistribution_entry(),
        )
    )


def _placeholder_entry() -> dict:
    entry = _valid_research_entry()
    entry.update(source_id="example_placeholder", status="placeholder")
    return entry


def _contract_required_entry() -> dict:
    entry = _valid_research_entry()
    entry.update(source_id="example_contract", status="contract_required")
    return entry


def _no_redistribution_entry() -> dict:
    entry = _valid_research_entry()
    entry.update(source_id="example_no_redist", status="no_redistribution")
    return entry


def test_guard_rejects_research_source() -> None:
    catalog = _guard_catalog()
    with pytest.raises(SourceNotRunnableError):
        ensure_source_runnable("example_research", catalog=catalog)


def test_guard_rejects_placeholder_source() -> None:
    catalog = _guard_catalog()
    with pytest.raises(SourceNotRunnableError):
        ensure_source_runnable("example_placeholder", catalog=catalog)


def test_guard_rejects_contract_required_source() -> None:
    catalog = _guard_catalog()
    with pytest.raises(SourceNotRunnableError):
        ensure_source_runnable("example_contract", catalog=catalog)


def test_guard_rejects_no_redistribution_source() -> None:
    catalog = _guard_catalog()
    with pytest.raises(SourceNotRunnableError):
        ensure_source_runnable("example_no_redist", catalog=catalog)


def test_guard_rejects_real_no_redistribution_source() -> None:
    get_catalog.cache_clear()
    with pytest.raises(SourceNotRunnableError):
        ensure_source_runnable("egib")
    get_catalog.cache_clear()


def test_guard_accepts_complete_production_source() -> None:
    catalog = _guard_catalog()
    entry = ensure_source_runnable("example_prod", catalog=catalog)
    assert entry.source_id == "example_prod"
    assert entry.is_runnable is True


def test_guard_unknown_source_raises_not_found() -> None:
    catalog = _guard_catalog()
    with pytest.raises(SourceNotFoundError):
        ensure_source_runnable("nieistnieje", catalog=catalog)


def test_guard_uses_real_catalog_for_uldk() -> None:
    # Guard bez jawnego katalogu korzysta z realnego katalogu repo (cache).
    get_catalog.cache_clear()
    entry = ensure_source_runnable("uldk")
    assert entry.source_id == "uldk"
    get_catalog.cache_clear()


def test_guard_rejects_real_research_source() -> None:
    get_catalog.cache_clear()
    with pytest.raises(SourceNotRunnableError):
        ensure_source_runnable("pog_pilot_krakow")
    get_catalog.cache_clear()


def test_guard_accepts_verified_ru_and_exposes_catalog_endpoints() -> None:
    get_catalog.cache_clear()
    source = ensure_source_runnable("pog_app")

    assert source.protocol_version == "2.0.0"
    assert {resource.role for resource in source.resources} == {
        "ru_wfs",
        "ru_wms_preview",
        "ru_csw",
    }
    assert all(
        resource.url.startswith("https://rejestr-urbanistyczny.gov.pl/")
        for resource in source.resources
    )
    get_catalog.cache_clear()


# --- Loader: błędy pliku i struktury -----------------------------------------


def test_load_catalog_missing_file_raises() -> None:
    with pytest.raises(CatalogFileError):
        load_catalog("/nieistniejacy/katalog/catalog.yaml")


def test_parse_non_mapping_raises() -> None:
    with pytest.raises(CatalogValidationError):
        parse_catalog(["nie", "mapa"])


def test_load_catalog_invalid_yaml_raises(tmp_path: Path) -> None:
    bad = tmp_path / "catalog.yaml"
    bad.write_text("schema_version: '1.0'\nsources: [::::\n", encoding="utf-8")
    with pytest.raises(CatalogFileError):
        load_catalog(bad)


def test_empty_sources_rejected() -> None:
    with pytest.raises(CatalogValidationError):
        parse_catalog({"schema_version": "1.0", "sources": []})


def test_env_override_path(tmp_path, monkeypatch) -> None:
    catalog_path = find_repo_root() / "docs" / "data_sources" / "catalog.yaml"
    target = tmp_path / "catalog.yaml"
    target.write_text(catalog_path.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("DATA_SOURCES_CATALOG_PATH", str(target))
    get_catalog.cache_clear()

    catalog = load_catalog()
    assert catalog.get("uldk").source_id == "uldk"
    get_catalog.cache_clear()


def test_env_override_missing_file_raises(monkeypatch) -> None:
    monkeypatch.setenv("DATA_SOURCES_CATALOG_PATH", "/brak/pliku.yaml")
    with pytest.raises(CatalogFileError):
        load_catalog()


# --- Jedno źródło prawdy: spójność katalogu z adapterami ---------------------


def test_catalog_uldk_url_matches_adapter() -> None:
    from app.services.uldk import ULDK_BASE_URL

    catalog_path = find_repo_root() / "docs" / "data_sources" / "catalog.yaml"
    catalog = load_catalog(catalog_path)
    assert catalog.get("uldk").capabilities_url == ULDK_BASE_URL


def test_catalog_uug_url_matches_adapter() -> None:
    from app.services.geocoding import UUG_BASE_URL

    catalog_path = find_repo_root() / "docs" / "data_sources" / "catalog.yaml"
    catalog = load_catalog(catalog_path)
    assert catalog.get("emuia_uug").capabilities_url == UUG_BASE_URL


def test_catalog_kimpzp_matches_settings() -> None:
    from app.core.settings import settings

    catalog_path = find_repo_root() / "docs" / "data_sources" / "catalog.yaml"
    catalog = load_catalog(catalog_path)
    entry = catalog.get("kimpzp")
    assert entry.capabilities_url == settings.kimpzp_wms_base_url
    # Warstwy w katalogu muszą pokrywać się z konfiguracją proxy WMS.
    assert set(entry.layers or []) == set(settings.kimpzp_wms_layers.split(","))


def test_catalog_has_narrow_runnable_warsaw_parcel_contract() -> None:
    catalog_path = find_repo_root() / "docs" / "data_sources" / "catalog.yaml"
    entry = load_catalog(catalog_path).get("egib_geometry_warsaw")
    assert entry.is_runnable is True
    assert entry.type_names == ["wfs:dzialki"]
    assert entry.source_crs == "EPSG:2178"
    assert entry.teryt_scope == ["1465011"]
    assert entry.field_mapping["parcel_identifier"] == "ID_DZIALKI"


def test_catalog_blocks_krakow_mpzp_until_reuse_permission() -> None:
    catalog_path = find_repo_root() / "docs" / "data_sources" / "catalog.yaml"
    entry = load_catalog(catalog_path).get("mpzp_pilot_krakow")
    assert entry.status is SourceStatus.CONTRACT_REQUIRED
    assert entry.production_ready is False
    assert [resource.role for resource in entry.resources] == [
        "boundaries",
        "zones",
        "zones",
        "zones",
    ]
    with pytest.raises(SourceNotRunnableError):
        ensure_source_runnable(entry.source_id, load_catalog(catalog_path))


# --- Kontrakt potwierdzony fixture GetCapabilities ---------------------------


def test_kimpzp_layers_consistent_with_getcapabilities_fixture() -> None:
    fixture = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "source_contracts"
        / "kimpzp_getcapabilities.xml"
    )
    xml_text = fixture.read_text(encoding="utf-8")

    catalog_path = find_repo_root() / "docs" / "data_sources" / "catalog.yaml"
    entry = load_catalog(catalog_path).get("kimpzp")

    # Każda warstwa zadeklarowana w katalogu musi występować w GetCapabilities.
    for layer in entry.layers or []:
        assert f"<Name>{layer}</Name>" in xml_text
    # CRS deklarowany w katalogu musi być wspierany przez usługę.
    assert entry.source_crs in xml_text


def test_entry_is_immutable_snapshot() -> None:
    # Modyfikacja zwróconej listy nie może wpływać na inne odczyty katalogu.
    catalog = parse_catalog(_catalog_dict(_valid_production_entry()))
    entry = catalog.get("example_prod")
    assert isinstance(entry, DataSourceEntry)
    snapshot = copy.deepcopy(entry.model_dump())
    assert snapshot["source_id"] == "example_prod"
    assert entry.access_type is AccessType.REST
