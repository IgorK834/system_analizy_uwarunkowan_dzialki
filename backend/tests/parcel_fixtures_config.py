"""Pomocnicze funkcje ładowania i walidacji datasetu referencyjnego działek testowych.

Dataset (backend/tests/fixtures/parcels/cases.json) opisuje OCZEKIWANE wyniki
domenowe (liczba stref MPZP, status POG, ryzyka) jako specyfikację na przyszłość —
serwisy mpzp.py/pog.py/isok.py/gdos.py jeszcze nie istnieją w repo. Ten moduł
waliduje wyłącznie STRUKTURĘ pliku cases.json, a nie zgodność z działającym kodem.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

# Wzorzec identyczny z app.services.uldk._PARCEL_ID_RE — powtórzony lokalnie,
# żeby testy fixtures nie zależały od importu modułu serwisowego (helper ma
# pozostać czysto plikową operacją, bez zależności na httpx/app.*).
PARCEL_ID_RE = re.compile(r"^\d{6}_\d{1,2}\.\d{4}(\.[A-Z]+_\d+)?\.([\d]+(/[\d]+)*)$")

REQUIRED_CASE_FIELDS: frozenset[str] = frozenset(
    {
        "case_id",
        "name",
        "gmina",
        "parcel_identifier",
        "scenario_type",
        "description",
        "input",
        "expected_result",
        "expected_warnings",
        "fixture_files",
    }
)

VALID_SCENARIO_TYPES: frozenset[str] = frozenset(
    {
        "mpzp_vector",
        "mpzp_raster",
        "mpzp_multi_zone",
        "ouz_inside",
        "ouz_outside",
        "ouz_touches_boundary",
        "flood_risk",
        "nature_protection",
        "pog_missing",
        "mpzp_pog_conflict",
        "mpzp_problematic_pdf",
    }
)

CASES_JSON_RELATIVE_PATH = "backend/tests/fixtures/parcels/cases.json"
README_RELATIVE_PATH = "backend/tests/fixtures/parcels/README.md"
RESPONSES_RELATIVE_PATH = "backend/tests/fixtures/parcels/responses"


def find_repo_root(start: Path | None = None) -> Path:
    """Szuka katalogu głównego repozytorium (zawiera README.md i backend/).

    Logika identyczna z tests/repo_structure.py i tests/docker_compose_config.py,
    powtórzona tutaj, żeby helper fixtures nie tworzył zależności między modułami
    testowymi, które mogą być rozwijane niezależnie.
    """
    repo_root_env = os.environ.get("REPO_ROOT")
    if repo_root_env:
        candidate = Path(repo_root_env).resolve()
        if (candidate / "README.md").is_file() and (candidate / "backend").is_dir():
            return candidate

    current = (start or Path(__file__)).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "README.md").is_file() and (candidate / "backend").is_dir():
            return candidate
    raise FileNotFoundError("Nie znaleziono katalogu głównego repozytorium.")


def load_cases(repo_root: Path) -> list[dict]:
    """Wczytuje backend/tests/fixtures/parcels/cases.json jako listę słowników."""
    cases_path = repo_root / CASES_JSON_RELATIVE_PATH
    if not cases_path.is_file():
        raise FileNotFoundError(f"Brak pliku {CASES_JSON_RELATIVE_PATH}")
    with cases_path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        raise ValueError("cases.json musi zawierać listę na poziomie głównym")
    return data


def validate_case(case: dict) -> list[str]:
    """Zwraca listę błędów walidacji pojedynczego przypadku (pusta lista = OK).

    Sprawdza kompletność pól, poprawność scenario_type oraz format
    parcel_identifier — walidacja uniqueness case_id jest osobno na poziomie listy
    w find_duplicate_case_ids, bo wymaga znajomości całego zbioru przypadków.
    """
    errors: list[str] = []

    missing_fields = sorted(REQUIRED_CASE_FIELDS - case.keys())
    if missing_fields:
        case_label = case.get("case_id", "<brak case_id>")
        errors.append(
            f"Przypadek {case_label!r}: brakujące pola {missing_fields}"
        )
        # Bez wymaganych pól dalsza walidacja nie ma sensu (np. brak scenario_type).
        return errors

    scenario_type = case.get("scenario_type")
    if scenario_type not in VALID_SCENARIO_TYPES:
        errors.append(
            f"Przypadek {case['case_id']!r}: nieprawidłowy scenario_type "
            f"{scenario_type!r}"
        )

    parcel_identifier = case.get("parcel_identifier", "")
    if not PARCEL_ID_RE.fullmatch(str(parcel_identifier)):
        errors.append(
            f"Przypadek {case['case_id']!r}: parcel_identifier {parcel_identifier!r} "
            "nie jest zgodny z formatem ULDK"
        )

    return errors


def find_duplicate_case_ids(cases: list[dict]) -> list[str]:
    """Zwraca case_id, które występują więcej niż raz w liście przypadków."""
    seen: dict[str, int] = {}
    for case in cases:
        case_id = case.get("case_id")
        if case_id is None:
            continue
        seen[case_id] = seen.get(case_id, 0) + 1
    return sorted(case_id for case_id, count in seen.items() if count > 1)


def covered_scenario_types(cases: list[dict]) -> set[str]:
    """Zwraca zbiór unikalnych scenario_type obecnych w cases."""
    return {case["scenario_type"] for case in cases if "scenario_type" in case}
