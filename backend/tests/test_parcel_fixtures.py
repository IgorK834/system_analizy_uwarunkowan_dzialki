"""Testy walidujące strukturę i kompletność datasetu referencyjnego działek testowych.

Zgodnie z sekcją 15 i 16 context.md dataset musi pokrywać minimum 10 działek
z różnych gmin i wszystkie wymagane typy scenariuszy E2E. Te testy sprawdzają
tylko STRUKTURĘ cases.json — logika parsera MPZP/POG/ryzyk nie istnieje jeszcze
w repo, więc nie asertujemy tu nic względem nieistniejącego kodu domenowego.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from tests import parcel_fixtures_config as fixtures_module
from tests.parcel_fixtures_config import (
    README_RELATIVE_PATH,
    RESPONSES_RELATIVE_PATH,
    VALID_SCENARIO_TYPES,
    covered_scenario_types,
    find_duplicate_case_ids,
    find_repo_root,
    load_cases,
    validate_case,
)

# Wymagane typy scenariuszy z sekcji 15 context.md (E2E minimum).
REQUIRED_SCENARIO_TYPES: frozenset[str] = frozenset(
    {
        "mpzp_vector",
        "mpzp_raster",
        "mpzp_multi_zone",
        "ouz_inside",
        "ouz_outside",
        "flood_risk",
        "nature_protection",
        "pog_missing",
        "mpzp_pog_conflict",
        "mpzp_problematic_pdf",
    }
)


@pytest.fixture
def repo_root() -> Path:
    """Katalog główny repozytorium."""
    return find_repo_root()


@pytest.fixture
def cases(repo_root: Path) -> list[dict]:
    """Wczytany dataset przypadków testowych działek."""
    return load_cases(repo_root)


def test_cases_json_file_exists(repo_root: Path) -> None:
    """Plik cases.json istnieje w oczekiwanej lokalizacji."""
    cases_path = repo_root / "backend/tests/fixtures/parcels/cases.json"
    assert cases_path.is_file(), "Brak pliku cases.json"


def test_cases_json_is_valid_json(repo_root: Path) -> None:
    """Plik cases.json parsuje się bez błędu."""
    # load_cases już wywołuje json.load — sukces oznacza poprawny JSON.
    load_cases(repo_root)


def test_cases_json_is_a_list(cases: list[dict]) -> None:
    """Top-level struktura cases.json to lista."""
    assert isinstance(cases, list)


def test_at_least_ten_cases(cases: list[dict]) -> None:
    """Dataset zawiera minimum 10 przypadków testowych (sekcja 15 context.md)."""
    assert len(cases) >= 10, f"Oczekiwano >=10 przypadków, jest {len(cases)}"


def test_all_cases_have_required_fields(cases: list[dict]) -> None:
    """Każdy przypadek ma pełny zestaw wymaganych pól."""
    all_errors: list[str] = []
    for case in cases:
        all_errors.extend(validate_case(case))
    assert all_errors == [], f"Błędy walidacji przypadków: {all_errors}"


def test_all_case_ids_are_unique(cases: list[dict]) -> None:
    """Żaden case_id nie powtarza się w datasecie."""
    duplicates = find_duplicate_case_ids(cases)
    assert duplicates == [], f"Zduplikowane case_id: {duplicates}"


def test_all_scenario_types_are_valid(cases: list[dict]) -> None:
    """Każdy scenario_type jest zgodny z enum VALID_SCENARIO_TYPES."""
    invalid = [
        case["scenario_type"]
        for case in cases
        if case.get("scenario_type") not in VALID_SCENARIO_TYPES
    ]
    assert invalid == [], f"Nieprawidłowe scenario_type: {invalid}"


def test_covers_mpzp_vector_scenario(cases: list[dict]) -> None:
    """Dataset zawiera przypadek MPZP wektorowy."""
    assert "mpzp_vector" in covered_scenario_types(cases)


def test_covers_mpzp_raster_scenario(cases: list[dict]) -> None:
    """Dataset zawiera przypadek MPZP rastrowy."""
    assert "mpzp_raster" in covered_scenario_types(cases)


def test_covers_mpzp_multi_zone_scenario(cases: list[dict]) -> None:
    """Dataset zawiera przypadek działki w wielu strefach MPZP."""
    assert "mpzp_multi_zone" in covered_scenario_types(cases)


def test_covers_ouz_inside_scenario(cases: list[dict]) -> None:
    """Dataset zawiera przypadek działki w OUZ."""
    assert "ouz_inside" in covered_scenario_types(cases)


def test_covers_ouz_outside_scenario(cases: list[dict]) -> None:
    """Dataset zawiera przypadek działki poza OUZ."""
    assert "ouz_outside" in covered_scenario_types(cases)


def test_covers_flood_risk_scenario(cases: list[dict]) -> None:
    """Dataset zawiera przypadek ryzyka powodziowego."""
    assert "flood_risk" in covered_scenario_types(cases)


def test_covers_nature_protection_scenario(cases: list[dict]) -> None:
    """Dataset zawiera przypadek formy ochrony przyrody."""
    assert "nature_protection" in covered_scenario_types(cases)


def test_covers_pog_missing_scenario(cases: list[dict]) -> None:
    """Dataset zawiera przypadek braku POG."""
    assert "pog_missing" in covered_scenario_types(cases)


def test_covers_mpzp_pog_conflict_scenario(cases: list[dict]) -> None:
    """Dataset zawiera przypadek konfliktu MPZP-POG."""
    assert "mpzp_pog_conflict" in covered_scenario_types(cases)


def test_covers_mpzp_problematic_pdf_scenario(cases: list[dict]) -> None:
    """Dataset zawiera przypadek problematycznego PDF (skan bez tekstu)."""
    assert "mpzp_problematic_pdf" in covered_scenario_types(cases)


def test_all_required_scenario_types_covered(cases: list[dict]) -> None:
    """Zbiór wymaganych typów scenariuszy jest podzbiorem pokrytych typów."""
    covered = covered_scenario_types(cases)
    missing = REQUIRED_SCENARIO_TYPES - covered
    assert missing == set(), f"Brakujące wymagane scenario_type: {missing}"


def test_each_case_parcel_identifier_matches_uldk_format(cases: list[dict]) -> None:
    """Każdy parcel_identifier jest zgodny z formatem ULDK."""
    invalid = [
        case["case_id"]
        for case in cases
        if not fixtures_module.PARCEL_ID_RE.fullmatch(str(case.get("parcel_identifier", "")))
    ]
    assert invalid == [], f"Przypadki z niepoprawnym parcel_identifier: {invalid}"


def test_each_case_input_parcel_identifier_matches_top_level(cases: list[dict]) -> None:
    """Pole input.parcel_identifier jest identyczne z parcel_identifier na górnym poziomie."""
    mismatched = [
        case["case_id"]
        for case in cases
        if case.get("input", {}).get("parcel_identifier") != case.get("parcel_identifier")
    ]
    assert mismatched == [], f"Niezgodność input.parcel_identifier: {mismatched}"


def test_each_case_has_non_empty_description(cases: list[dict]) -> None:
    """Każdy przypadek ma opisowe, niepuste pole description (>10 znaków)."""
    too_short = [
        case["case_id"]
        for case in cases
        if len(case.get("description", "").strip()) <= 10
    ]
    assert too_short == [], f"Przypadki z za krótkim opisem: {too_short}"


def test_readme_exists_in_fixtures_directory(repo_root: Path) -> None:
    """README.md istnieje w katalogu fixtures/parcels."""
    readme_path = repo_root / README_RELATIVE_PATH
    assert readme_path.is_file(), "Brak pliku README.md w fixtures/parcels"


def test_readme_mentions_synthetic_data(repo_root: Path) -> None:
    """README wspomina, że dane są syntetyczne lub anonimizowane."""
    readme_path = repo_root / README_RELATIVE_PATH
    content = readme_path.read_text(encoding="utf-8").lower()
    assert "syntetyczn" in content or "anonimizowan" in content, (
        "README nie zawiera wzmianki o danych syntetycznych/anonimizowanych"
    )


def test_readme_lists_all_required_scenario_types(repo_root: Path) -> None:
    """README wspomina każdy z typów scenariuszy z VALID_SCENARIO_TYPES."""
    readme_path = repo_root / README_RELATIVE_PATH
    content = readme_path.read_text(encoding="utf-8")
    missing = [
        scenario_type
        for scenario_type in VALID_SCENARIO_TYPES
        if scenario_type not in content
    ]
    assert missing == [], f"README nie wspomina scenario_type: {missing}"


def test_responses_directory_exists(repo_root: Path) -> None:
    """Katalog responses/ na nagrane odpowiedzi usług zewnętrznych istnieje."""
    responses_path = repo_root / RESPONSES_RELATIVE_PATH
    assert responses_path.is_dir(), "Brak katalogu responses/ w fixtures/parcels"


def test_no_real_http_calls_needed_for_fixture_loading() -> None:
    """Helper fixtures nie importuje httpx/requests — to czysto plikowa operacja.

    Sekcja 10 context.md: w testach nie odpytujemy prawdziwych usług. load_cases
    ma działać wyłącznie na plikach z dysku, bez zależności sieciowych.
    """
    source = inspect.getsource(fixtures_module)
    assert "import httpx" not in source
    assert "import requests" not in source


# --- Testy jednostkowe helpera (ścieżki błędów, których nie wywołuje realny
# cases.json, bo jest poprawny) -----------------------------------------------


def test_find_repo_root_uses_repo_root_env_var(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """find_repo_root respektuje zmienną środowiskową REPO_ROOT, jeśli poprawna."""
    monkeypatch.setenv("REPO_ROOT", str(repo_root))
    assert find_repo_root() == repo_root


def test_find_repo_root_raises_when_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """find_repo_root rzuca FileNotFoundError, gdy nie ma README.md/backend."""
    monkeypatch.delenv("REPO_ROOT", raising=False)
    isolated_dir = tmp_path / "no_repo_here"
    isolated_dir.mkdir()
    with pytest.raises(FileNotFoundError):
        find_repo_root(start=isolated_dir / "fake_file.py")


def test_load_cases_raises_when_file_missing(tmp_path: Path) -> None:
    """load_cases rzuca FileNotFoundError, gdy cases.json nie istnieje."""
    with pytest.raises(FileNotFoundError):
        load_cases(tmp_path)


def test_load_cases_raises_when_top_level_not_a_list(tmp_path: Path) -> None:
    """load_cases rzuca ValueError, gdy JSON nie ma listy na poziomie głównym."""
    cases_dir = tmp_path / "backend/tests/fixtures/parcels"
    cases_dir.mkdir(parents=True)
    (cases_dir / "cases.json").write_text('{"not": "a list"}', encoding="utf-8")
    with pytest.raises(ValueError):
        load_cases(tmp_path)


def test_validate_case_reports_missing_fields() -> None:
    """validate_case zwraca błąd, gdy w przypadku brakuje wymaganych pól."""
    errors = validate_case({"case_id": "incomplete_case"})
    assert errors != []
    assert "incomplete_case" in errors[0]


def test_validate_case_reports_invalid_scenario_type() -> None:
    """validate_case zwraca błąd dla scenario_type poza VALID_SCENARIO_TYPES."""
    case = {
        "case_id": "bad_scenario",
        "name": "x",
        "gmina": "x",
        "parcel_identifier": "146101_1.0001.123/4",
        "scenario_type": "nieznany_typ",
        "description": "opis testowy dłuższy niż 10 znaków",
        "input": {"method": "parcel_id", "parcel_identifier": "146101_1.0001.123/4"},
        "expected_result": {},
        "expected_warnings": [],
        "fixture_files": {},
    }
    errors = validate_case(case)
    assert any("scenario_type" in error for error in errors)


def test_validate_case_reports_invalid_parcel_identifier() -> None:
    """validate_case zwraca błąd, gdy parcel_identifier nie ma formatu ULDK."""
    case = {
        "case_id": "bad_parcel_id",
        "name": "x",
        "gmina": "x",
        "parcel_identifier": "not-a-valid-id",
        "scenario_type": "mpzp_vector",
        "description": "opis testowy dłuższy niż 10 znaków",
        "input": {"method": "parcel_id", "parcel_identifier": "not-a-valid-id"},
        "expected_result": {},
        "expected_warnings": [],
        "fixture_files": {},
    }
    errors = validate_case(case)
    assert any("parcel_identifier" in error for error in errors)


def test_find_duplicate_case_ids_detects_duplicates() -> None:
    """find_duplicate_case_ids wykrywa case_id powtórzone więcej niż raz."""
    duplicated = [
        {"case_id": "same_id"},
        {"case_id": "same_id"},
        {"case_id": "unique_id"},
    ]
    assert find_duplicate_case_ids(duplicated) == ["same_id"]
