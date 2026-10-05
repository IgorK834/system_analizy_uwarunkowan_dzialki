"""Artefakt kalibracji pewności: aktualność, odtwarzalność z danych i progi akceptacji PV3-09.

Cele z zadania (ECE ≤ 0,10 i monotoniczne pasma na zbiorze, którego nie użyto do kalibracji; odsetek
błędów w paśmie ``high`` ≤ 5% przy co najmniej 100 wartościach) są tu SPRAWDZANE pomiarem, nie
zakładane. Zbiór oceny (podział ``final`` BK-603) nie jest niezależny od projektu leksykonu, co artefakt
zapisuje wprost w ``measurements.independence_note``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.modules.planning.domain import evidence_confidence as ec
from app.modules.planning.domain.quantity_engine import ENGINE_VERSION
from app.modules.planning.domain.quantity_lexicon import LEXICON_VERSION
from app.modules.planning.domain.value_conditions import CONDITIONS_VERSION
from app.services.mpzp_parser import MPZP_PARSER_VERSION
from app.services.mpzp_parser_blocks import MPZP_PARSER_VERSION_BLOCKS
from scripts import calibrate_mpzp_confidence as cal
from scripts import evaluate_mpzp_parser as ev
from tests.parcel_fixtures_config import find_repo_root

FIXTURES = find_repo_root() / "backend/tests/fixtures/mpzp_evaluation"
ARTIFACT = ec.default_artifact()
RECALIBRATE = "Rekalibruj: python3 scripts/calibrate_mpzp_confidence.py (z katalogu backend/)"


@pytest.fixture(scope="module")
def manifest_and_hash() -> tuple[dict[str, Any], str]:
    return ev.load_corpus(FIXTURES / "manifest.json")


@pytest.fixture(scope="module")
def rows(manifest_and_hash: tuple[dict[str, Any], str]) -> list[dict[str, Any]]:
    return cal.collect_rows(manifest_and_hash[0], FIXTURES)


def test_artifact_was_made_for_the_current_engine_lexicon_and_parser() -> None:
    engine = ARTIFACT.payload["engine"]
    assert engine["versions"] == sorted({MPZP_PARSER_VERSION, MPZP_PARSER_VERSION_BLOCKS}), RECALIBRATE
    assert (engine["quantity_engine"], engine["lexicon"], engine["conditions"]) == (
        ENGINE_VERSION, LEXICON_VERSION, CONDITIONS_VERSION,
    ), RECALIBRATE


def test_calibration_never_sees_the_evaluation_split(rows: list[dict[str, Any]], manifest_and_hash: tuple[dict[str, Any], str]) -> None:
    data = ARTIFACT.payload["data"]
    assert data["calibration_split"] == "development" and data["test_split"] == "final"
    manifest = manifest_and_hash[0]
    development = {s["sample_id"] for s in manifest["samples"] if s["split"] == data["calibration_split"]}
    training = [row for row in rows if row["split"] == data["calibration_split"]]
    assert training and {row["sample_id"] for row in training} <= development
    assert not {row["sample_id"] for row in training} & {
        s["sample_id"] for s in manifest["samples"] if s["split"] == data["test_split"]
    }
    assert data["n"] == len(training) and data["errors"] == sum(not row["correct"] for row in training)
    with pytest.raises(SystemExit):
        cal.build_artifact(manifest, manifest_and_hash[1], rows, "development", "development")


def test_artifact_is_reproducible_from_its_data_hash(rows: list[dict[str, Any]], manifest_and_hash: tuple[dict[str, Any], str]) -> None:
    rebuilt = cal.build_artifact(manifest_and_hash[0], manifest_and_hash[1], rows)
    assert cal._comparable(rebuilt) == cal._comparable(dict(ARTIFACT.payload)), RECALIBRATE
    assert rebuilt["data"]["sha256"] == ARTIFACT.data_sha256
    # kolejność wierszy nie wpływa na skrót, a zmiana jednej etykiety — tak
    training = [{k: r[k] for k in ("engine", "sample_id", "zone", "parameter", "value", "features", "correct")}
                for r in rows if r["split"] == "development"]
    assert ec.data_digest(list(reversed(training))) == ARTIFACT.data_sha256
    flipped = [dict(training[0], correct=not training[0]["correct"]), *training[1:]]
    assert ec.data_digest(flipped) != ARTIFACT.data_sha256


def test_the_script_check_mode_confirms_the_artifact(capsys: pytest.CaptureFixture[str]) -> None:
    assert cal.main(["--check"]) == 0
    assert "odtwarza się z danych" in capsys.readouterr().out


def test_stored_measurements_meet_the_acceptance_targets() -> None:
    held_out = ARTIFACT.payload["measurements"]["held_out"]
    assert held_out["split"] == "final" and held_out["n"] >= 100
    assert held_out["expected_calibration_error"] <= 0.10
    assert held_out["bands_monotone"] is True
    high = held_out["bands"]["high"]
    assert high["n"] >= 100 and high["error_rate"] <= 0.05
    assert "NIE jest niezależny" in ARTIFACT.payload["measurements"]["independence_note"]


def test_evaluator_reproduces_the_stored_held_out_numbers_on_the_real_pipeline(manifest_and_hash: tuple[dict[str, Any], str]) -> None:
    manifest, _ = manifest_and_hash
    values: list[dict[str, Any]] = []
    for engine in (ev.LegacyEngine(), ev.V3Engine()):
        values.extend(v for v in ev.evaluate(manifest, FIXTURES, engine=engine)["values"] if v["split"] == "final")
    metrics = ev.reliability_metrics(values)
    stored = ARTIFACT.payload["measurements"]["held_out"]
    assert metrics["n_values"] == stored["n"]
    assert metrics["expected_calibration_error"] == pytest.approx(stored["expected_calibration_error"], abs=1e-8)
    assert metrics["brier_score"] == pytest.approx(stored["brier_score"], abs=1e-8)
    assert metrics["bands_monotone"] is True and metrics["calibration"]["id"] == ARTIFACT.calibration_id
    for band in ("low", "medium", "high"):
        assert metrics["bands"][band]["n"] == stored["bands"][band]["n"]
        assert metrics["bands"][band]["errors"] == stored["bands"][band]["errors"]
    # progi z pomiaru, nie z góry: wszystkie błędy zbioru oceny leżą w paśmie niższym niż ``high``
    assert metrics["auto_accepted"]["error_rate"] <= 0.05 and metrics["auto_accepted"]["n"] >= 100
    assert metrics["error_recall_of_manual_review"]["value"] >= 0.95  # ręczna weryfikacja wyłapuje prawie wszystkie błędy


def test_fixed_thresholds_are_not_hard_coded_anywhere_in_the_validation_stage() -> None:
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "app/services/mpzp_parser_validate.py").read_text(encoding="utf-8")
    for removed in ("_OCR_WARNING_CONFIDENCE_PENALTY", "_AMBIGUOUS_ZONE_CONFIDENCE_PENALTY", "_MISSING_PAGE_NUMBER", "_MANUAL_REVIEW_CONFIDENCE_THRESHOLD"):
        assert removed not in source
    payload = json.loads(json.dumps(ARTIFACT.payload))
    assert payload["manual_review"]["threshold"] == payload["bands"]["medium"]["lower"]
