"""Pomocnicze funkcje ładowania i walidacji datasetu referencyjnego działek testowych.

Dataset (backend/tests/fixtures/parcels/cases.json) opisuje syntetyczne,
OCZEKIWANE wyniki domenowe. Serwisy MPZP, POG, ISOK i GDOŚ istnieją;
ten moduł waliduje tylko STRUKTURĘ cases.json. Brak nagranych odpowiedzi
dla większości przypadków nie pozwala nim ocenić jakości działających serwisów.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import csv
import math
from datetime import date
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

REFERENCE_CORPUS_RELATIVE_PATH = (
    "backend/tests/fixtures/reference_corpus/manifest.json"
)
REFERENCE_CORPUS_DIR_RELATIVE_PATH = "backend/tests/fixtures/reference_corpus"
VERIFICATION_LOG_RELATIVE_PATH = "docs/evaluation/verification_log.csv"
REFERENCE_REQUIRED_CASE_FIELDS: frozenset[str] = frozenset(
    {
        "case_id",
        "parcel_identifier",
        "teryt",
        "municipality",
        "voivodeship",
        "settlement_type",
        "geometry_crs",
        "artifact_ids",
        "scenario_tags",
        "sample_strategy",
        "verified_at",
        "verification_method",
        "expected",
        "tolerances",
        "ambiguity_notes",
        "redistribution_basis",
    }
)
REFERENCE_EXPECTED_SECTIONS: frozenset[str] = frozenset(
    {"geometry", "pog", "ouz", "mpzp", "flood", "nature", "terrain"}
)
REFERENCE_REQUIRED_SCENARIOS: frozenset[str] = frozenset(
    {
        "urban",
        "rural",
        "pog_single_zone",
        "pog_multi_zone",
        "pog_legal_force",
        "pog_unknown",
        "ouz_inside",
        "ouz_outside",
        "ouz_boundary",
        "ouz_partial",
        "mpzp_vector",
        "mpzp_document",
        "mpzp_raster_manual",
        "flood_none",
        "flood_intersection",
        "flood_boundary",
        "nature_none",
        "nature_intersection",
        "terrain_flat",
        "terrain_relief",
    }
)
REFERENCE_VALID_STATUSES: frozenset[str] = frozenset(
    {"available", "unknown", "manual_review"}
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TERYT_RE = re.compile(r"^\d{7}$")
VERIFICATION_REQUIRED_FIELDS: frozenset[str] = frozenset(
    {
        "verification_id",
        "case_id",
        "metric_path",
        "observation_index",
        "session_id",
        "observed_at",
        "observer",
        "source_id",
        "source_release_id",
        "tool",
        "tool_version",
        "crs",
        "axis_order",
        "value",
        "unit",
        "tolerance_value",
        "tolerance_unit",
        "absolute_difference",
        "within_tolerance",
        "ambiguous",
        "ambiguity_scope",
        "evidence_artifact_id",
        "resolution",
        "resolved_value",
        "notes",
    }
)


class ReferenceCorpusError(ValueError):
    """Manifest rzeczywistego korpusu albo jego artefakty są niespójne."""


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


def load_reference_corpus(repo_root: Path) -> dict:
    """Wczytuje i w pełni waliduje rzeczywisty korpus wyłącznie z dysku.

    Funkcja nie importuje klienta HTTP i nie ma ścieżki odświeżania danych.
    Dzięki temu ten sam manifest i artefakty mogą być odtwarzane przy całkowicie
    zablokowanym egress.
    """

    manifest_path = repo_root / REFERENCE_CORPUS_RELATIVE_PATH
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReferenceCorpusError(
            f"Nie można odczytać manifestu korpusu: {exc}"
        ) from exc

    errors = validate_reference_corpus(
        manifest,
        repo_root / REFERENCE_CORPUS_DIR_RELATIVE_PATH,
    )
    if errors:
        raise ReferenceCorpusError("; ".join(errors))
    return manifest


def load_verification_log(repo_root: Path, manifest: dict | None = None) -> list[dict[str, str]]:
    """Wczytuje i waliduje niezależne, powtórzone obserwacje BK-003."""

    path = repo_root / VERIFICATION_LOG_RELATIVE_PATH
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
            fieldnames = set(reader.fieldnames or [])
    except OSError as exc:
        raise ReferenceCorpusError(f"Nie można odczytać logu weryfikacji: {exc}") from exc

    missing_columns = sorted(VERIFICATION_REQUIRED_FIELDS - fieldnames)
    if missing_columns:
        raise ReferenceCorpusError(
            f"Log weryfikacji nie ma kolumn: {missing_columns}."
        )
    corpus = manifest if manifest is not None else load_reference_corpus(repo_root)
    errors = validate_verification_log(rows, corpus)
    if errors:
        raise ReferenceCorpusError("; ".join(errors))
    return rows


def validate_verification_log(rows: object, manifest: object) -> list[str]:
    """Waliduje próbę 20%, dwa niezależne zapisy i rozstrzygnięcie różnic."""

    if not isinstance(rows, list):
        return ["Log weryfikacji musi być listą wierszy CSV."]
    if not isinstance(manifest, dict) or not isinstance(manifest.get("cases"), list):
        return ["Do walidacji logu wymagany jest poprawny manifest korpusu."]

    errors: list[str] = []
    cases = {case.get("case_id"): case for case in manifest["cases"]}
    sources = manifest.get("sources", {})
    artifacts = manifest.get("artifacts", {})
    grouped: dict[str, list[dict[str, str]]] = {}

    for index, row in enumerate(rows):
        label = f"verification_log[{index}]"
        if not isinstance(row, dict):
            errors.append(f"{label} musi być obiektem.")
            continue
        missing = sorted(VERIFICATION_REQUIRED_FIELDS - row.keys())
        if missing:
            errors.append(f"{label}: brakujące pola {missing}.")
            continue
        for field in (
            "verification_id",
            "case_id",
            "metric_path",
            "session_id",
            "observed_at",
            "observer",
            "source_id",
            "source_release_id",
            "tool",
            "tool_version",
            "unit",
            "tolerance_value",
            "tolerance_unit",
            "evidence_artifact_id",
        ):
            if not str(row.get(field, "")).strip():
                errors.append(f"{label}: pole {field} nie może być puste.")
        try:
            date.fromisoformat(str(row.get("observed_at", ""))[:10])
        except (TypeError, ValueError):
            errors.append(f"{label}: observed_at nie jest datą ISO.")
        if row.get("case_id") not in cases:
            errors.append(f"{label}: nieznany case_id {row.get('case_id')!r}.")
        if row.get("source_id") not in sources:
            errors.append(f"{label}: nieznany source_id {row.get('source_id')!r}.")
        artifact_id = row.get("evidence_artifact_id")
        if artifact_id not in artifacts:
            errors.append(f"{label}: nieznany artefakt dowodowy {artifact_id!r}.")
        try:
            observation_index = int(row.get("observation_index", ""))
            if observation_index not in {1, 2}:
                raise ValueError
        except (TypeError, ValueError):
            errors.append(f"{label}: observation_index musi wynosić 1 albo 2.")
        try:
            tolerance = float(row.get("tolerance_value", ""))
            if not math.isfinite(tolerance) or tolerance < 0:
                raise ValueError
        except (TypeError, ValueError):
            errors.append(f"{label}: tolerancja musi być nieujemną liczbą.")
        if row.get("ambiguous") not in {"true", "false"}:
            errors.append(f"{label}: ambiguous musi mieć wartość true albo false.")
        if row.get("ambiguous") == "true" and not row.get("ambiguity_scope", "").strip():
            errors.append(f"{label}: ambiguous wymaga ambiguity_scope.")

        grouped.setdefault(str(row.get("verification_id", "")), []).append(row)

    verified_cases: set[str] = set()
    for verification_id, observations in grouped.items():
        label = f"Weryfikacja {verification_id!r}"
        indices = {row.get("observation_index") for row in observations}
        if len(observations) != 2 or indices != {"1", "2"}:
            errors.append(f"{label}: wymagane są dokładnie obserwacje 1 i 2.")
            continue
        first, second = sorted(observations, key=lambda row: row["observation_index"])
        verified_cases.add(first["case_id"])
        if first["case_id"] != second["case_id"] or first["metric_path"] != second["metric_path"]:
            errors.append(f"{label}: obserwacje dotyczą różnych metryk albo przypadków.")
        if first["session_id"] == second["session_id"]:
            errors.append(f"{label}: druga obserwacja musi pochodzić z innej sesji.")
        if first["unit"] != second["unit"]:
            errors.append(f"{label}: obserwacje mają różne jednostki.")
        if first["tolerance_unit"] != second["tolerance_unit"]:
            errors.append(f"{label}: obserwacje mają różne jednostki tolerancji.")
        if not second.get("absolute_difference", "").strip():
            errors.append(f"{label}: brak zapisanej rozbieżności.")
        if second.get("within_tolerance") not in {"true", "false", "not_applicable"}:
            errors.append(f"{label}: brak wyniku porównania z tolerancją.")
        if not second.get("resolution", "").strip():
            errors.append(f"{label}: brak uzasadnienia rozstrzygnięcia.")
        ambiguous = first.get("ambiguous") == "true" or second.get("ambiguous") == "true"
        if ambiguous and second.get("resolved_value", "").strip():
            errors.append(
                f"{label}: nieokreślona metryka nie może mieć wymuszonego resolved_value."
            )
        if not ambiguous and not second.get("resolved_value", "").strip():
            errors.append(f"{label}: rozstrzygnięta metryka wymaga resolved_value.")

    required_count = math.ceil(0.2 * len(cases))
    if len(verified_cases) < required_count:
        errors.append(
            f"Druga sesja obejmuje {len(verified_cases)} przypadków; wymagane minimum to {required_count}."
        )
    return errors


def validate_reference_corpus(manifest: object, corpus_dir: Path) -> list[str]:
    """Zwraca wykryte błędy manifestu, artefaktów i rozkładu przypadków."""

    errors: list[str] = []
    if not isinstance(manifest, dict):
        return ["Manifest korpusu musi być obiektem JSON."]

    sources = manifest.get("sources")
    artifacts = manifest.get("artifacts")
    cases = manifest.get("cases")
    if not isinstance(sources, dict) or not sources:
        errors.append("Manifest musi zawierać niepusty rejestr sources.")
        sources = {}
    if not isinstance(artifacts, dict) or not artifacts:
        errors.append("Manifest musi zawierać niepusty rejestr artifacts.")
        artifacts = {}
    if not isinstance(cases, list):
        errors.append("Manifest musi zawierać listę cases.")
        return errors

    _validate_reference_sources(sources, errors)
    _validate_reference_artifacts(artifacts, sources, corpus_dir, errors)

    case_ids: list[str] = []
    parcel_ids: list[str] = []
    scenario_tags: set[str] = set()
    teryt_scopes: set[str] = set()
    voivodeships: set[str] = set()
    settlement_types: set[str] = set()
    referenced_artifacts: set[str] = set()
    multi_zone_cases: list[dict] = []

    for index, case in enumerate(cases):
        label = f"cases[{index}]"
        if not isinstance(case, dict):
            errors.append(f"{label} musi być obiektem.")
            continue
        missing = sorted(REFERENCE_REQUIRED_CASE_FIELDS - case.keys())
        if missing:
            errors.append(f"{label}: brakujące pola {missing}.")
            continue

        case_id = case.get("case_id")
        parcel_id = case.get("parcel_identifier")
        if not isinstance(case_id, str) or not case_id.strip():
            errors.append(f"{label}: case_id musi być niepustym tekstem.")
        else:
            case_ids.append(case_id)
        if not isinstance(parcel_id, str) or not PARCEL_ID_RE.fullmatch(parcel_id):
            errors.append(f"{label}: niepoprawny parcel_identifier {parcel_id!r}.")
        else:
            parcel_ids.append(parcel_id)

        teryt = case.get("teryt")
        if not isinstance(teryt, str) or not _TERYT_RE.fullmatch(teryt):
            errors.append(f"{label}: TERYT musi mieć siedem cyfr.")
        elif isinstance(parcel_id, str):
            expected_prefix = f"{teryt[:6]}_{teryt[-1]}."
            if not parcel_id.startswith(expected_prefix):
                errors.append(
                    f"{label}: TERYT {teryt!r} nie odpowiada identyfikatorowi działki."
                )
            teryt_scopes.add(teryt[:6])

        if case.get("geometry_crs") != "EPSG:2180":
            errors.append(f"{label}: geometry_crs musi być EPSG:2180.")
        if case.get("settlement_type") not in {"urban", "rural"}:
            errors.append(f"{label}: settlement_type musi być urban albo rural.")
        else:
            settlement_types.add(case["settlement_type"])
        if isinstance(case.get("voivodeship"), str) and case["voivodeship"].strip():
            voivodeships.add(case["voivodeship"])
        else:
            errors.append(f"{label}: brak województwa.")

        artifact_ids = case.get("artifact_ids")
        if not isinstance(artifact_ids, list) or not artifact_ids:
            errors.append(f"{label}: artifact_ids musi być niepustą listą.")
        else:
            for artifact_id in artifact_ids:
                if artifact_id not in artifacts:
                    errors.append(f"{label}: brak artefaktu {artifact_id!r}.")
                elif isinstance(artifact_id, str):
                    referenced_artifacts.add(artifact_id)

        tags = case.get("scenario_tags")
        if not isinstance(tags, list) or not tags or not all(
            isinstance(tag, str) and tag for tag in tags
        ):
            errors.append(f"{label}: scenario_tags musi być niepustą listą tekstów.")
        else:
            scenario_tags.update(tags)
            if "pog_multi_zone" in tags:
                multi_zone_cases.append(case)

        try:
            date.fromisoformat(case.get("verified_at", ""))
        except (TypeError, ValueError):
            errors.append(f"{label}: verified_at nie jest datą ISO.")
        if not isinstance(case.get("verification_method"), str) or not case[
            "verification_method"
        ].strip():
            errors.append(f"{label}: brak verification_method.")
        if not isinstance(case.get("redistribution_basis"), str) or not case[
            "redistribution_basis"
        ].strip():
            errors.append(f"{label}: brak redistribution_basis.")
        if not isinstance(case.get("ambiguity_notes"), list) or not all(
            isinstance(note, str) and note.strip()
            for note in case.get("ambiguity_notes", [])
        ):
            errors.append(f"{label}: ambiguity_notes musi być listą tekstów.")
        ambiguous_metrics = case.get("ambiguous_metrics", [])
        if not isinstance(ambiguous_metrics, list) or not all(
            isinstance(metric, str) and "." in metric
            for metric in ambiguous_metrics
        ):
            errors.append(f"{label}: ambiguous_metrics musi być listą ścieżek metryk.")

        _validate_expected_sections(case.get("expected"), label, errors)
        _validate_tolerances(case.get("tolerances"), label, errors)

    if len(cases) < 24:
        errors.append(f"Korpus zawiera {len(cases)} działek; wymagane minimum to 24.")
    _report_duplicates(case_ids, "case_id", errors)
    _report_duplicates(parcel_ids, "parcel_identifier", errors)
    if len(teryt_scopes) < 5:
        errors.append("Korpus musi obejmować co najmniej 5 jednostek TERYT gmin.")
    if len(voivodeships) < 3:
        errors.append("Korpus musi obejmować co najmniej 3 województwa.")
    if settlement_types != {"urban", "rural"}:
        errors.append("Korpus musi obejmować przypadki miejskie i wiejskie.")

    missing_scenarios = sorted(REFERENCE_REQUIRED_SCENARIOS - scenario_tags)
    if missing_scenarios:
        errors.append(f"Brak wymaganych scenariuszy: {missing_scenarios}.")
    if len(multi_zone_cases) < 2:
        errors.append("Wymagane są co najmniej 2 działki wielostrefowe POG.")
    for case in multi_zone_cases:
        _validate_manual_multi_zone(case, errors)

    unreferenced = sorted(set(artifacts) - referenced_artifacts)
    if unreferenced:
        errors.append(f"Artefakty bez przypadku: {unreferenced}.")
    return errors


def _validate_reference_sources(sources: dict, errors: list[str]) -> None:
    required = {"name", "url", "official_terms_url", "captured_at", "redistribution_basis"}
    for source_id, source in sources.items():
        if not isinstance(source_id, str) or not source_id:
            errors.append("Identyfikator źródła musi być niepustym tekstem.")
            continue
        if not isinstance(source, dict):
            errors.append(f"Źródło {source_id!r} musi być obiektem.")
            continue
        missing = sorted(required - source.keys())
        if missing:
            errors.append(f"Źródło {source_id!r}: brakujące pola {missing}.")
        for field in required:
            if field in source and (
                not isinstance(source[field], str) or not source[field].strip()
            ):
                errors.append(f"Źródło {source_id!r}: pole {field} jest puste.")


def _validate_reference_artifacts(
    artifacts: dict,
    sources: dict,
    corpus_dir: Path,
    errors: list[str],
) -> None:
    root = corpus_dir.resolve()
    required = {
        "path",
        "media_type",
        "source_id",
        "crs",
        "sha256",
        "redistribution_basis",
    }
    for artifact_id, artifact in artifacts.items():
        label = f"Artefakt {artifact_id!r}"
        if not isinstance(artifact, dict):
            errors.append(f"{label} musi być obiektem.")
            continue
        missing = sorted(required - artifact.keys())
        if missing:
            errors.append(f"{label}: brakujące pola {missing}.")
            continue
        if artifact.get("source_id") not in sources:
            errors.append(f"{label}: brak źródła {artifact.get('source_id')!r}.")
        if not isinstance(artifact.get("redistribution_basis"), str) or not artifact[
            "redistribution_basis"
        ].strip():
            errors.append(f"{label}: brak podstawy redystrybucji.")
        digest = artifact.get("sha256")
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            errors.append(f"{label}: niepoprawny SHA-256.")

        relative = artifact.get("path")
        if not isinstance(relative, str) or not relative:
            errors.append(f"{label}: brak lokalnej ścieżki.")
            continue
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            errors.append(f"{label}: ścieżka musi być względna i lokalna.")
            continue
        resolved = (root / path).resolve()
        if not resolved.is_relative_to(root):
            errors.append(f"{label}: ścieżka wychodzi poza katalog korpusu.")
            continue
        if not resolved.is_file():
            errors.append(f"{label}: brak pliku {relative!r}.")
            continue
        actual_digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
        if actual_digest != digest:
            errors.append(f"{label}: SHA-256 nie zgadza się z plikiem.")

        if artifact.get("media_type") == "application/geo+json":
            _validate_geometry_artifact(resolved, artifact, label, errors)


def _validate_geometry_artifact(
    path: Path,
    artifact: dict,
    label: str,
    errors: list[str],
) -> None:
    try:
        feature = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{label}: niepoprawny GeoJSON: {exc}.")
        return
    if feature.get("type") != "Feature" or not isinstance(feature.get("geometry"), dict):
        errors.append(f"{label}: geometria musi być obiektem GeoJSON Feature.")
    geometry_type = (feature.get("geometry") or {}).get("type")
    if geometry_type not in {"Polygon", "MultiPolygon"}:
        errors.append(f"{label}: geometria działki musi być poligonowa.")
    if artifact.get("crs") != "EPSG:2180":
        errors.append(f"{label}: artefakt geometrii musi deklarować EPSG:2180.")
    properties = feature.get("properties") or {}
    if properties.get("source") != "ULDK" or not properties.get("parcel_identifier"):
        errors.append(f"{label}: brak pochodzenia ULDK lub identyfikatora działki.")


def _validate_expected_sections(expected: object, label: str, errors: list[str]) -> None:
    if not isinstance(expected, dict):
        errors.append(f"{label}: expected musi być obiektem.")
        return
    missing = sorted(REFERENCE_EXPECTED_SECTIONS - expected.keys())
    if missing:
        errors.append(f"{label}: brak sekcji expected {missing}.")
    for section_name in REFERENCE_EXPECTED_SECTIONS & expected.keys():
        section = expected[section_name]
        section_label = f"{label}.expected.{section_name}"
        if not isinstance(section, dict):
            errors.append(f"{section_label} musi być obiektem.")
            continue
        if section.get("status") not in REFERENCE_VALID_STATUSES:
            errors.append(f"{section_label}: niepoprawny status.")
        if not isinstance(section.get("method"), str) or not section["method"].strip():
            errors.append(f"{section_label}: brak metody ustalenia expected.")
        values = section.get("values")
        if not isinstance(values, dict) or not values:
            errors.append(f"{section_label}: values musi być niepustym obiektem.")
        else:
            _validate_measurement_tree(values, section_label, errors)


def _validate_measurement_tree(value: object, label: str, errors: list[str]) -> None:
    if isinstance(value, dict):
        if "value" in value or "unit" in value:
            if set(value) != {"value", "unit"} or not isinstance(
                value.get("unit"), str
            ) or not value["unit"].strip():
                errors.append(f"{label}: pomiar musi mieć dokładnie value i unit.")
            return
        for key, child in value.items():
            _validate_measurement_tree(child, f"{label}.{key}", errors)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _validate_measurement_tree(child, f"{label}[{index}]", errors)
        return
    errors.append(f"{label}: wartość oczekiwana nie ma jawnej jednostki.")


def _validate_tolerances(tolerances: object, label: str, errors: list[str]) -> None:
    if not isinstance(tolerances, dict) or not tolerances:
        errors.append(f"{label}: tolerances musi być niepustym obiektem.")
        return
    for name, tolerance in tolerances.items():
        if (
            not isinstance(tolerance, dict)
            or set(tolerance) != {"value", "unit"}
            or not isinstance(tolerance.get("value"), (int, float))
            or tolerance["value"] < 0
            or not isinstance(tolerance.get("unit"), str)
            or not tolerance["unit"].strip()
        ):
            errors.append(f"{label}.tolerances.{name}: niepoprawna tolerancja.")


def _report_duplicates(values: list[str], field: str, errors: list[str]) -> None:
    duplicates = sorted({value for value in values if values.count(value) > 1})
    if duplicates:
        errors.append(f"Zduplikowane {field}: {duplicates}.")


def _validate_manual_multi_zone(case: dict, errors: list[str]) -> None:
    case_id = case.get("case_id", "<brak>")
    if "ręczn" not in str(case.get("verification_method", "")).casefold():
        errors.append(f"{case_id}: działka wielostrefowa nie ma ręcznego pomiaru.")
    zones = (
        case.get("expected", {})
        .get("pog", {})
        .get("values", {})
        .get("zones", [])
    )
    if not isinstance(zones, list) or len(zones) < 2:
        errors.append(f"{case_id}: oczekiwano co najmniej dwóch stref POG.")
        return
    shares = [zone.get("share", {}).get("value") for zone in zones]
    if not all(isinstance(share, (int, float)) for share in shares):
        errors.append(f"{case_id}: udziały POG muszą być liczbami.")
    elif abs(sum(shares) - 100.0) > 0.2:
        errors.append(f"{case_id}: udziały POG nie sumują się do 100%.")
