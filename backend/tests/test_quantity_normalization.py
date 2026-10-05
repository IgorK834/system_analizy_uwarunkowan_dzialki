"""Normalizacja ilości: jedna implementacja dla aplikacji i ewaluatora (PV3-07)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.modules.planning.domain import quantity_lexicon as lexicon
from app.modules.planning.domain.quantity_normalization import (
    NORMALIZATION_RULES,
    QuantityNormalizationError,
    engine_confidence,
    extract_numbers,
    flags_penalty,
    normalize_annotation_value,
    normalize_quantity,
    number_word_value,
    ratio_to_percent,
)
from scripts import evaluate_mpzp_parser as ev
from tests.parcel_fixtures_config import find_repo_root

FIXTURES = find_repo_root() / "backend/tests/fixtures/mpzp_evaluation"


@pytest.fixture(scope="module")
def manifest() -> dict[str, Any]:
    return json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))


# --- adnotacje: ewaluator i aplikacja dają te same wartości ------------------------------------


def test_evaluator_and_application_normalize_every_annotation_identically(manifest: dict[str, Any]) -> None:
    checked = 0
    for sample in manifest["samples"]:
        for zone in sample["zones"]:
            for annotation in zone["annotations"]:
                rule, raw = annotation["normalization"], annotation["raw_value"]
                from_app = normalize_annotation_value(rule, raw)
                from_evaluator = ev.normalize_value(rule, raw)
                assert from_evaluator == from_app, (sample["sample_id"], rule, raw)
                if rule == "manual":
                    assert from_app is None  # decyzja człowieka, nie wyliczenie
                else:
                    assert from_app == pytest.approx(annotation["normalized_value"]), (rule, raw)
                checked += 1
    assert checked == sum(len(z["annotations"]) for s in manifest["samples"] for z in s["zones"]) > 300


def test_evaluator_has_no_second_implementation_of_the_rules() -> None:
    assert ev.NORMALIZATION_RULES is NORMALIZATION_RULES
    assert ev.normalize_value.__module__ == "scripts.evaluate_mpzp_parser"
    assert not hasattr(ev, "_numbers") and not hasattr(ev, "_WORD_NUMBERS")
    with pytest.raises(ev.EvaluationError):
        ev.normalize_value("identity", "bez liczby")
    with pytest.raises(ev.EvaluationError):
        ev.normalize_value("word_number", "bez liczebnika")
    with pytest.raises(ev.EvaluationError):
        ev.normalize_value("range_upper", "tylko 5")
    with pytest.raises(ev.EvaluationError):
        ev.normalize_value("nieznana", "5")


@pytest.mark.parametrize(
    ("rule", "raw", "expected"),
    [
        ("identity", "11,0 m", 11.0),
        ("identity", "minimum 20 %", 20.0),
        ("ratio_to_percent", "0,35", 35.0),
        ("ratio_to_percent", "0,05", 5.0),
        ("range_lower", "od 0 do 0,9", 0.0),
        ("range_lower", "0,01 – 0,9", 0.01),
        ("range_upper", "25°– 40°", 40.0),
        ("range_upper", "0,01- 1,2", 1.2),
        ("word_number", "dwie kondygnacje", 2.0),
        ("word_number", "do trzech kondygnacji", 3.0),
        ("word_number", "jednokondygnacyjne", 1.0),
        ("manual", "od 300 do 450", None),
    ],
)
def test_annotation_rules(rule: str, raw: str, expected: float | None) -> None:
    result = normalize_annotation_value(rule, raw)
    assert result == (pytest.approx(expected) if expected is not None else None)


def test_annotation_rule_errors_are_explicit() -> None:
    for rule, raw in [("identity", "brak"), ("range_upper", "5"), ("word_number", "x y"), ("nieznana", "1")]:
        with pytest.raises(QuantityNormalizationError):
            normalize_annotation_value(rule, raw)
    assert set(NORMALIZATION_RULES) == {"identity", "ratio_to_percent", "range_lower", "range_upper", "word_number", "manual"}


def test_helpers_numbers_and_words() -> None:
    assert extract_numbers("od 0,01 do 1,2 m") == [0.01, 1.2]
    assert ratio_to_percent(0.35) == 35.0 and ratio_to_percent(0.1) == 10.0  # bez szumu 10.000000000000002
    assert number_word_value("dwóch") == 2.0 and number_word_value("czterokondygnacyjny") == 4.0
    assert number_word_value("żadne") is None


# --- wartości z tekstu uchwały: rodziny, flagi i granice sensu ----------------------------------


def _norm(family: str, number: float, unit: str, raw: str = "", **kwargs: Any):
    return normalize_quantity(family, number, unit_kind=unit, raw_number=raw or str(number), **kwargs)


def test_percent_values_flag_every_rewrite_of_the_notation() -> None:
    plain = _norm(lexicon.FAMILY_COVERAGE, 30.0, lexicon.UNIT_PERCENT)
    assert (plain.value, plain.flags) == (30.0, ())
    ratio = _norm(lexicon.FAMILY_COVERAGE, 0.35, lexicon.UNIT_NONE, "0,35")
    assert (ratio.value, ratio.flags) == (35.0, ("ratio_to_percent",))
    double = _norm(lexicon.FAMILY_COVERAGE, 0.5, lexicon.UNIT_NONE, "0,50", parenthesized_percent=50.0)
    assert (double.value, double.flags) == (50.0, ("double_notation",))
    mismatch = _norm(lexicon.FAMILY_COVERAGE, 0.5, lexicon.UNIT_NONE, "0,50", parenthesized_percent=30.0)
    assert mismatch.value == 50.0 and "double_notation_mismatch" in mismatch.flags
    implicit = _norm(lexicon.FAMILY_BIO, 20.0, lexicon.UNIT_NONE, "20")
    assert (implicit.value, implicit.flags) == (20.0, ("implicit_percent",))
    assert _norm(lexicon.FAMILY_BIO, 150.0, lexicon.UNIT_PERCENT) is None  # poza 0–100
    assert _norm(lexicon.FAMILY_BIO, 3.5, lexicon.UNIT_NONE, "3,5") is None  # ułamek >1 bez znaku %
    assert _norm(lexicon.FAMILY_COVERAGE, 12.0, lexicon.UNIT_METER) is None  # inna jednostka


def test_degree_values_flag_artifacts_instead_of_silently_fixing_them() -> None:
    ok = _norm(lexicon.FAMILY_ROOF, 45.0, lexicon.UNIT_DEGREE, degree_symbol="°")
    assert (ok.value, ok.flags) == (45.0, ())
    letter = _norm(lexicon.FAMILY_ROOF, 45.0, lexicon.UNIT_DEGREE, degree_symbol="o")
    assert letter.flags == ("degree_letter",)
    ordinal = _norm(lexicon.FAMILY_ROOF, 30.0, lexicon.UNIT_DEGREE, degree_symbol="º")
    assert ordinal.flags == ("degree_letter",)
    implied = _norm(lexicon.FAMILY_ROOF, 35.0, lexicon.UNIT_NONE)
    assert implied.flags == ("unit_implied",)
    artifact = _norm(lexicon.FAMILY_ROOF, 450.0, lexicon.UNIT_NONE)
    assert (artifact.value, artifact.flags) == (45.0, ("degree_artifact",))
    assert _norm(lexicon.FAMILY_ROOF, 455.0, lexicon.UNIT_NONE) is None  # nie wielokrotność 10
    assert _norm(lexicon.FAMILY_ROOF, 95.0, lexicon.UNIT_DEGREE) is None  # kąt > 90°
    assert _norm(lexicon.FAMILY_ROOF, 30.0, lexicon.UNIT_METER) is None


def test_other_families_enforce_sane_ranges() -> None:
    assert _norm(lexicon.FAMILY_HEIGHT, 12.5, lexicon.UNIT_METER).value == 12.5
    assert _norm(lexicon.FAMILY_HEIGHT, 0.0, lexicon.UNIT_METER) is None  # 0 to nie wysokość
    assert _norm(lexicon.FAMILY_HEIGHT, 900.0, lexicon.UNIT_METER) is None
    assert _norm(lexicon.FAMILY_STOREYS, 3.0, lexicon.UNIT_STOREY).value == 3.0
    assert _norm(lexicon.FAMILY_STOREYS, 2.5, lexicon.UNIT_STOREY) is None
    assert _norm(lexicon.FAMILY_INTENSITY, 1.2, lexicon.UNIT_NONE).value == 1.2
    assert _norm(lexicon.FAMILY_INTENSITY, 50.0, lexicon.UNIT_NONE) is None
    converted = _norm(lexicon.FAMILY_INTENSITY, 150.0, lexicon.UNIT_PERCENT)
    assert (converted.value, converted.flags) == (1.5, ("percent_to_ratio",))
    assert _norm(lexicon.FAMILY_SETBACK, 6.0, lexicon.UNIT_METER).value == 6.0
    assert _norm(lexicon.FAMILY_PARKING, 2.0, lexicon.UNIT_PLACE).value == 2.0
    hectares = _norm(lexicon.FAMILY_PLOT, 0.1, lexicon.UNIT_HECTARE)
    assert (hectares.value, hectares.flags) == (1000.0, ("hectare_to_m2",))
    assert _norm(lexicon.FAMILY_RETAIL, 2000.0, lexicon.UNIT_AREA).value == 2000.0
    assert normalize_quantity("nieznana", 1.0, unit_kind=lexicon.UNIT_NONE) is None
    assert normalize_quantity(lexicon.FAMILY_HEIGHT, float("nan"), unit_kind=lexicon.UNIT_METER) is None
    assert normalize_quantity(lexicon.FAMILY_HEIGHT, -3.0, unit_kind=lexicon.UNIT_METER) is None


def test_flag_penalties_lower_confidence_and_unknown_flags_do_not() -> None:
    assert engine_confidence(()) == lexicon.BASE_CONFIDENCE
    assert engine_confidence(("degree_artifact",)) < engine_confidence(("degree_letter",)) < engine_confidence(())
    assert flags_penalty(("nieznana_flaga",)) == 1.0
    assert flags_penalty(("ratio_to_percent", "ratio_to_percent")) == flags_penalty(("ratio_to_percent",))  # bez dublowania
    assert _norm(lexicon.FAMILY_ROOF, 450.0, lexicon.UNIT_NONE).penalty == lexicon.FLAG_PENALTIES["degree_artifact"]
