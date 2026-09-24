"""Walidacja rzeczywistego, redystrybuowalnego korpusu BK-002."""

from __future__ import annotations

import copy
import inspect
import json
import socket
from datetime import date
from pathlib import Path

import pytest

from tests import parcel_fixtures_config as corpus_module
from tests.parcel_fixtures_config import (
    REFERENCE_EXPECTED_SECTIONS,
    REFERENCE_REQUIRED_SCENARIOS,
    REFERENCE_VALID_STATUSES,
    ReferenceCorpusError,
    find_repo_root,
    load_cases,
    load_reference_corpus,
    load_verification_log,
    validate_reference_corpus,
    validate_verification_log,
)


@pytest.fixture(scope="module")
def repo_root() -> Path:
    return find_repo_root()


@pytest.fixture(scope="module")
def corpus_dir(repo_root: Path) -> Path:
    return repo_root / "backend/tests/fixtures/reference_corpus"


@pytest.fixture(scope="module")
def manifest(repo_root: Path) -> dict:
    return load_reference_corpus(repo_root)


def test_reference_corpus_loads_and_has_recommended_size(manifest: dict) -> None:
    assert len(manifest["cases"]) == 30
    assert len({case["parcel_identifier"] for case in manifest["cases"]}) == 30
    assert len(manifest["artifacts"]) == 60


def test_reference_corpus_has_required_geographic_distribution(manifest: dict) -> None:
    cases = manifest["cases"]
    assert len({case["teryt"][:6] for case in cases}) >= 5
    assert len({case["voivodeship"] for case in cases}) >= 3
    assert {case["settlement_type"] for case in cases} == {"urban", "rural"}


def test_reference_corpus_covers_domain_matrix(manifest: dict) -> None:
    covered = {
        tag for case in manifest["cases"] for tag in case["scenario_tags"]
    }
    assert REFERENCE_REQUIRED_SCENARIOS <= covered


def test_two_pog_multizone_cases_have_manual_measured_shares(manifest: dict) -> None:
    cases = [
        case
        for case in manifest["cases"]
        if "pog_multi_zone" in case["scenario_tags"]
    ]
    assert len(cases) == 2
    for case in cases:
        assert "ręczn" in case["verification_method"].casefold()
        zones = case["expected"]["pog"]["values"]["zones"]
        assert len(zones) == 2
        assert sum(zone["share"]["value"] for zone in zones) == pytest.approx(
            100.0, abs=0.2
        )
        assert all(zone["area"]["unit"] == "square_metre" for zone in zones)
        assert all(zone["share"]["unit"] == "percent" for zone in zones)


def test_every_case_has_expected_status_method_units_and_tolerances(
    manifest: dict,
) -> None:
    for case in manifest["cases"]:
        date.fromisoformat(case["verified_at"])
        assert set(case["expected"]) == REFERENCE_EXPECTED_SECTIONS
        assert case["verification_method"].strip()
        assert case["redistribution_basis"].strip()
        for section in case["expected"].values():
            assert section["status"] in REFERENCE_VALID_STATUSES
            assert section["method"].strip()
            assert section["values"]
        for tolerance in case["tolerances"].values():
            assert tolerance["value"] >= 0
            assert tolerance["unit"]


def test_ambiguous_boundary_cases_remain_marked(manifest: dict) -> None:
    ouz = next(
        case for case in manifest["cases"] if "ouz_boundary" in case["scenario_tags"]
    )
    assert ouz["expected"]["ouz"]["status"] == "manual_review"
    assert ouz["ambiguity_notes"]

    flood = next(
        case
        for case in manifest["cases"]
        if "flood_boundary" in case["scenario_tags"]
    )
    assert flood["expected"]["flood"]["status"] == "manual_review"
    assert "boundary" in flood["expected"]["flood"]["values"]["relation"]["value"]
    assert flood["ambiguity_notes"]


def test_geometries_are_local_uldk_features_with_crs_and_hash(
    manifest: dict, corpus_dir: Path
) -> None:
    geometry_artifacts = {
        artifact_id: artifact
        for artifact_id, artifact in manifest["artifacts"].items()
        if artifact["media_type"] == "application/geo+json"
    }
    assert len(geometry_artifacts) == len(manifest["cases"])
    for artifact in geometry_artifacts.values():
        assert artifact["source_id"] == "uldk"
        assert artifact["crs"] == "EPSG:2180"
        assert len(artifact["sha256"]) == 64
        feature = json.loads((corpus_dir / artifact["path"]).read_text())
        assert feature["type"] == "Feature"
        assert feature["geometry"]["type"] in {"Polygon", "MultiPolygon"}
        assert feature["properties"]["source"] == "ULDK"
        assert feature["properties"]["parcel_identifier"]


def test_loader_replays_with_egress_blocked(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def blocked(*_args, **_kwargs):
        raise AssertionError("test próbował użyć sieci")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    loaded = load_reference_corpus(repo_root)
    assert len(loaded["cases"]) == 30


def test_repo_root_fallback_works_without_environment(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("REPO_ROOT", raising=False)
    start = repo_root / "backend/tests/test_reference_corpus.py"
    assert find_repo_root(start) == repo_root


def test_loader_has_no_http_client_dependency() -> None:
    source = inspect.getsource(corpus_module)
    assert "import httpx" not in source
    assert "import requests" not in source


def test_synthetic_regressions_remain_separate(repo_root: Path) -> None:
    synthetic_cases = load_cases(repo_root)
    real_cases = load_reference_corpus(repo_root)["cases"]
    assert synthetic_cases
    assert real_cases
    assert not ({case["case_id"] for case in synthetic_cases} & {
        case["case_id"] for case in real_cases
    })


def test_validator_detects_duplicate_parcel(
    manifest: dict, corpus_dir: Path
) -> None:
    broken = copy.deepcopy(manifest)
    broken["cases"][1]["parcel_identifier"] = broken["cases"][0][
        "parcel_identifier"
    ]
    errors = validate_reference_corpus(broken, corpus_dir)
    assert any("Zduplikowane parcel_identifier" in error for error in errors)


def test_validator_detects_missing_source(manifest: dict, corpus_dir: Path) -> None:
    broken = copy.deepcopy(manifest)
    first = next(iter(broken["artifacts"].values()))
    first["source_id"] = "missing-source"
    errors = validate_reference_corpus(broken, corpus_dir)
    assert any("brak źródła" in error for error in errors)


def test_validator_detects_bad_distribution(manifest: dict, corpus_dir: Path) -> None:
    broken = copy.deepcopy(manifest)
    broken["cases"] = broken["cases"][:10]
    errors = validate_reference_corpus(broken, corpus_dir)
    assert any("wymagane minimum to 24" in error for error in errors)


def test_validator_detects_hash_mismatch(manifest: dict, corpus_dir: Path) -> None:
    broken = copy.deepcopy(manifest)
    first = next(iter(broken["artifacts"].values()))
    first["sha256"] = "0" * 64
    errors = validate_reference_corpus(broken, corpus_dir)
    assert any("SHA-256 nie zgadza" in error for error in errors)


def test_validator_rejects_unsafe_artifact_path(
    manifest: dict, corpus_dir: Path
) -> None:
    broken = copy.deepcopy(manifest)
    first = next(iter(broken["artifacts"].values()))
    first["path"] = "../outside.json"
    errors = validate_reference_corpus(broken, corpus_dir)
    assert any("względna i lokalna" in error for error in errors)


def test_validator_rejects_incomplete_expected_and_negative_tolerance(
    manifest: dict, corpus_dir: Path
) -> None:
    broken = copy.deepcopy(manifest)
    case = broken["cases"][0]
    case["expected"].pop("terrain")
    case["expected"]["geometry"]["status"] = "optimistic"
    case["expected"]["geometry"]["values"]["area"] = {"value": 1}
    case["tolerances"]["geometry_area_abs"]["value"] = -1
    errors = validate_reference_corpus(broken, corpus_dir)
    assert any("brak sekcji expected" in error for error in errors)
    assert any("niepoprawny status" in error for error in errors)
    assert any("dokładnie value i unit" in error for error in errors)
    assert any("niepoprawna tolerancja" in error for error in errors)


def test_validator_detects_duplicate_case_id_and_wrong_teryt(
    manifest: dict, corpus_dir: Path
) -> None:
    broken = copy.deepcopy(manifest)
    broken["cases"][1]["case_id"] = broken["cases"][0]["case_id"]
    broken["cases"][2]["teryt"] = "9999999"
    errors = validate_reference_corpus(broken, corpus_dir)
    assert any("Zduplikowane case_id" in error for error in errors)
    assert any("nie odpowiada identyfikatorowi" in error for error in errors)


def test_loader_reports_invalid_manifest(tmp_path: Path) -> None:
    manifest_dir = tmp_path / "backend/tests/fixtures/reference_corpus"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ReferenceCorpusError):
        load_reference_corpus(tmp_path)


def test_verification_log_has_two_sessions_for_at_least_twenty_percent(
    repo_root: Path, manifest: dict
) -> None:
    rows = load_verification_log(repo_root, manifest)
    verified_cases = {row["case_id"] for row in rows}
    assert len(verified_cases) >= 6
    for verification_id in {row["verification_id"] for row in rows}:
        observations = [row for row in rows if row["verification_id"] == verification_id]
        assert {row["observation_index"] for row in observations} == {"1", "2"}
        assert len({row["session_id"] for row in observations}) == 2


@pytest.mark.parametrize("field", ["unit", "observed_at", "source_id", "tolerance_value"])
def test_verification_validator_rejects_missing_required_measurement_metadata(
    field: str, repo_root: Path, manifest: dict
) -> None:
    rows = copy.deepcopy(load_verification_log(repo_root, manifest))
    rows[0][field] = ""
    errors = validate_verification_log(rows, manifest)
    assert any(field in error for error in errors)


def test_ambiguous_verification_has_metric_scope_and_no_forced_value(
    repo_root: Path, manifest: dict
) -> None:
    rows = load_verification_log(repo_root, manifest)
    ambiguous = [row for row in rows if row["ambiguous"] == "true"]
    assert ambiguous
    assert {row["case_id"] for row in ambiguous} <= {
        case["case_id"] for case in manifest["cases"]
    }
    assert all(row["ambiguity_scope"] == "ouz.relation" for row in ambiguous)
    second = next(row for row in ambiguous if row["observation_index"] == "2")
    assert second["resolved_value"] == ""


def test_verification_validator_rejects_same_session_and_missing_resolution(
    repo_root: Path, manifest: dict
) -> None:
    rows = copy.deepcopy(load_verification_log(repo_root, manifest))
    pair = [row for row in rows if row["verification_id"] == "GT-001"]
    pair[1]["session_id"] = pair[0]["session_id"]
    pair[1]["resolution"] = ""
    errors = validate_verification_log(rows, manifest)
    assert any("innej sesji" in error for error in errors)
    assert any("uzasadnienia rozstrzygnięcia" in error for error in errors)


def test_verification_validator_reports_invalid_references_and_types(
    repo_root: Path, manifest: dict
) -> None:
    rows = copy.deepcopy(load_verification_log(repo_root, manifest))
    row = rows[0]
    row["observed_at"] = "not-a-date"
    row["case_id"] = "missing-case"
    row["source_id"] = "missing-source"
    row["evidence_artifact_id"] = "missing-artifact"
    row["observation_index"] = "3"
    row["tolerance_value"] = "-1"
    row["ambiguous"] = "maybe"
    errors = validate_verification_log(rows, manifest)
    assert any("observed_at" in error for error in errors)
    assert any("nieznany case_id" in error for error in errors)
    assert any("nieznany source_id" in error for error in errors)
    assert any("nieznany artefakt" in error for error in errors)
    assert any("observation_index" in error for error in errors)
    assert any("nieujemną liczbą" in error for error in errors)
    assert any("ambiguous" in error for error in errors)


def test_verification_validator_requires_full_sample_and_consistent_pair(
    repo_root: Path, manifest: dict
) -> None:
    rows = copy.deepcopy(load_verification_log(repo_root, manifest))[:2]
    rows[1]["case_id"] = manifest["cases"][1]["case_id"]
    rows[1]["metric_path"] = "geometry.perimeter"
    rows[1]["unit"] = "metre"
    rows[1]["tolerance_unit"] = "metre"
    errors = validate_verification_log(rows, manifest)
    assert any("różnych metryk albo przypadków" in error for error in errors)
    assert any("różne jednostki" in error for error in errors)
    assert any("różne jednostki tolerancji" in error for error in errors)
    assert any("wymagane minimum" in error for error in errors)


def test_verification_validator_rejects_forced_ambiguous_resolution(
    repo_root: Path, manifest: dict
) -> None:
    rows = copy.deepcopy(load_verification_log(repo_root, manifest))
    second = next(
        row
        for row in rows
        if row["verification_id"] == "GT-004" and row["observation_index"] == "2"
    )
    second["resolved_value"] = "inside"
    errors = validate_verification_log(rows, manifest)
    assert any("nie może mieć wymuszonego" in error for error in errors)
