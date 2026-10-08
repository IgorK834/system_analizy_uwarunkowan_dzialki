"""Model pewności z cech dowodu: cechy, dopasowanie, progi z pomiaru, niezawodność (PV3-09)."""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

import pytest

from app.modules.planning.domain import evidence_confidence as ec

ARTIFACT = ec.default_artifact()


# --- cechy -----------------------------------------------------------------------------------------


def test_feature_vector_has_one_slot_per_named_feature_and_neutral_defaults() -> None:
    neutral = ec.feature_vector(ec.ConfidenceFeatures(scope_kind="zone_section"))
    assert len(neutral) == len(ec.FEATURE_NAMES) == 22 and sum(neutral) == 0.0
    legacy = dict(zip(ec.FEATURE_NAMES, ec.feature_vector(ec.ConfidenceFeatures())))
    assert legacy["scope_legacy"] == 1.0 and sum(legacy.values()) == 1.0  # tryb dotychczasowy: jedyna cecha


def test_each_feature_group_is_switched_on_by_its_own_evidence() -> None:
    def on(**kwargs: object) -> set[str]:
        merged = {"scope_kind": "zone_section", **kwargs}
        vector = ec.feature_vector(ec.ConfidenceFeatures(**merged))  # type: ignore[arg-type]
        return {name for name, value in zip(ec.FEATURE_NAMES, vector) if value}

    assert on(extraction_method="ocr", ocr_quality=0.4) == {"ocr", "ocr_noise"}
    assert on(extraction_method="html") == {"html"}
    assert on(scope_strategy=0, scope_kind="fallback") == {"scope_fallback"}
    assert on(scope_confidence=0.6) == {"scope_uncertainty"}
    assert on(quote_verified=False) == {"quote_unverified"}
    assert on(strategy="comparative") == set() and on(strategy="range_dash") == {"strategy_range"}
    assert on(strategy="bare") == {"strategy_bare"} and on(strategy="frame_setback") == {"strategy_frame"}
    assert on(flags=("ratio_to_percent", "degree_artifact", "operator_implied", "number_word", "inherited_noun", "approximate")) == {
        "flag_conversion", "flag_ocr_artifact", "flag_implied", "flag_number_word", "flag_inherited", "flag_approximate",
    }
    assert on(value_kind="conflict", candidate_count=3) == {"conflict", "multi_candidates"}
    assert on(value_kind="conditional") == {"conditional"} and on(zone_ambiguous=True, symbols_inferred=True) == {"zone_ambiguous", "symbols_inferred"}
    general = ec.feature_vector(ec.ConfidenceFeatures(scope_kind="residual_clause"))
    assert dict(zip(ec.FEATURE_NAMES, general))["scope_general"] == 1.0


def test_candidate_count_feature_saturates_instead_of_growing_without_bound() -> None:
    vectors = [dict(zip(ec.FEATURE_NAMES, ec.feature_vector(ec.ConfidenceFeatures(scope_kind="zone_section", candidate_count=n))))
               for n in (1, 2, 4, 50)]
    values = [v["multi_candidates"] for v in vectors]
    assert values[0] == 0.0 and 0.0 < values[1] < values[2] == values[3] == 1.0


def test_there_is_no_field_for_the_self_assessment_of_a_language_model() -> None:
    names = [field.name for field in fields(ec.ConfidenceFeatures)] + list(ec.FEATURE_NAMES)
    assert not any(fragment in name for name in names for fragment in ec.FORBIDDEN_FEATURE_FRAGMENTS)
    with pytest.raises(ValueError):
        ec.ConfidenceFeatures.from_dict({"model_confidence": 0.99})
    roundtrip = ec.ConfidenceFeatures(strategy="adjective", flags=("a",), value_kind="conditional", ocr_quality=0.5)
    assert ec.ConfidenceFeatures.from_dict(roundtrip.as_dict()) == roundtrip


# --- ocena --------------------------------------------------------------------------------------------


def test_probability_is_bounded_and_every_risk_feature_lowers_it() -> None:
    base = ec.probability(ec.ConfidenceFeatures(scope_kind="zone_section", extraction_method="pdf_text"), ARTIFACT)
    assert ec.PROBABILITY_FLOOR <= base <= ec.PROBABILITY_CEILING
    for risk, kwargs in {
        "ocr": {"extraction_method": "ocr"},
        "quote": {"quote_verified": False},
        "conflict": {"value_kind": "conflict", "candidate_count": 2},
        "ambiguous": {"zone_ambiguous": True},
        "inferred": {"symbols_inferred": True},
        "fallback": {"scope_kind": "fallback", "scope_strategy": 0},
    }.items():
        features = ec.ConfidenceFeatures(**{"scope_kind": "zone_section", "extraction_method": "pdf_text", **kwargs})  # type: ignore[arg-type]
        assert ec.probability(features, ARTIFACT) < base, risk


def test_score_gives_band_and_manual_review_from_the_artifact_thresholds() -> None:
    clean = ec.score(ec.ConfidenceFeatures(scope_kind="zone_section", extraction_method="pdf_text", strategy="comparative"), ARTIFACT)
    assert (clean.band, clean.manual_review_required, clean.calibrated) == ("high", False, True)
    assert clean.calibration_id == ARTIFACT.calibration_id
    risky = ec.score(ec.ConfidenceFeatures(extraction_method="ocr", ocr_quality=0.3, quote_verified=False), ARTIFACT)
    assert risky.band == "low" and risky.manual_review_required is True and risky.confidence < clean.confidence
    assert ec.band_for(ARTIFACT.band_thresholds["high"], ARTIFACT) == "high"
    assert ec.band_for(0.0, ARTIFACT) == "low"


def test_model_language_values_never_reach_the_high_band_or_skip_review() -> None:
    scored = ec.score(ec.ConfidenceFeatures(scope_kind="zone_section", origin=ec.ORIGIN_LLM, strategy="comparative"), ARTIFACT)
    assert scored.calibrated is False and scored.manual_review_required is True
    assert scored.band != "high" and scored.confidence < ARTIFACT.band_thresholds["high"]
    assert scored.confidence <= ARTIFACT.uncalibrated_cap


def test_uncalibrated_text_confidence_stays_below_the_high_band() -> None:
    assert ec.uncalibrated_text_confidence(ARTIFACT) <= ARTIFACT.uncalibrated_cap < ARTIFACT.band_thresholds["high"]


def test_neutral_artifact_and_override_are_scoped() -> None:
    neutral = ec.neutral_artifact()
    assert ec.probability(ec.ConfidenceFeatures(), neutral) == pytest.approx(0.5)
    with ec.override_artifact(neutral):
        assert ec.current_artifact() is neutral and ec.probability(ec.ConfidenceFeatures()) == pytest.approx(0.5)
    assert ec.current_artifact() is ARTIFACT


# --- dopasowanie --------------------------------------------------------------------------------------


def _separable() -> tuple[list[list[float]], list[int]]:
    rows = []
    labels = []
    for index in range(60):
        risky = index % 3 == 0
        rows.append([1.0 if risky else 0.0, 0.5 if index % 2 else 0.0])
        labels.append(0 if risky and index % 6 == 0 else 1)
    return rows, labels


def test_fit_is_deterministic_and_learns_the_direction_of_a_risk_feature() -> None:
    rows, labels = _separable()
    first = ec.fit_logistic(rows, labels, l2=1.0)
    assert first == ec.fit_logistic(rows, labels, l2=1.0)
    assert first[0][0] < 0  # cecha ryzyka obniża pewność
    assert ec.sigmoid(first[1]) > 0.5  # bazowo wartości są częściej poprawne


def test_prior_pulls_weights_and_sign_constraints_zero_a_wrong_signed_weight() -> None:
    rows = [[1.0], [1.0], [0.0], [0.0]] * 5
    labels = [1, 1, 1, 0] * 5  # cecha „ryzyka” w danych jest PRZEJAWEM poprawności (szum)
    free, _ = ec.fit_logistic(rows, labels, l2=1.0)
    assert free[0] > 0
    constrained, _ = ec.fit_logistic(rows, labels, l2=1.0, non_positive=[0])
    assert constrained[0] == 0.0  # waga zerowana, nie nagradza ryzyka
    pulled, _ = ec.fit_logistic(rows, labels, l2=50.0, prior=[-2.0])
    assert pulled[0] < free[0] and pulled[0] == pytest.approx(-2.0, abs=0.6)


def test_fit_rejects_empty_or_inconsistent_data_and_a_wrong_prior() -> None:
    with pytest.raises(ec.CalibrationError):
        ec.fit_logistic([], [])
    with pytest.raises(ec.CalibrationError):
        ec.fit_logistic([[0.0]], [1, 0])
    with pytest.raises(ec.CalibrationError):
        ec.fit_logistic([[0.0, 1.0]] * 3, [1, 0, 1], prior=[0.0])


# --- progi z pomiaru ----------------------------------------------------------------------------------


def test_isotonic_blocks_pool_violators_and_ties() -> None:
    blocks = ec.isotonic_blocks([0.2, 0.4, 0.4, 0.6, 0.8, 0.9], [False, True, False, True, False, True])
    accuracies = [block["accuracy"] for block in blocks]
    assert accuracies == sorted(accuracies) and sum(block["n"] for block in blocks) == 6
    assert blocks[0]["lower"] == 0.2


def test_threshold_is_not_diluted_by_many_good_values_at_high_confidence() -> None:
    # 100 wartości z pewnością 0,99 (bez błędów) i 10 z pewnością 0,7, z których połowa jest błędna.
    predictions = [0.99] * 100 + [0.7] * 10
    correct = [True] * 100 + [True] * 5 + [False] * 5
    # skumulowany odsetek błędów wszystkich 110 wartości (4,5%) mieści się w 5%, ale przedział 0,7 jest błędny w 50%
    assert 5 / 110 <= 0.05
    assert ec.choose_threshold(predictions, correct, 0.05, minimum_n=20) == 0.99
    assert ec.choose_threshold(predictions, correct, 0.60, minimum_n=20) == 0.7


def test_threshold_requires_a_minimum_number_of_values_and_may_not_exist() -> None:
    assert ec.choose_threshold([0.9] * 5, [True] * 5, 0.05, minimum_n=20) is None
    assert ec.choose_threshold([0.9] * 30, [False] * 30, 0.05, minimum_n=20) is None


# --- niezawodność -------------------------------------------------------------------------------------


def test_reliability_brier_and_ece_match_a_hand_calculation() -> None:
    predictions = [0.9, 0.9, 0.9, 0.9, 0.3, 0.3]
    correct = [True, True, True, False, True, False]
    # przedział [0,9;1,0): 4 wartości, śr. pewność 0,9, trafność 0,75; [0,3;0,4): 2 wartości, 0,3 vs 0,5
    curve = {round(row["lower"], 1): row for row in ec.reliability_table(predictions, correct)}
    assert curve[0.9]["n"] == 4 and curve[0.9]["accuracy"] == 0.75 and curve[0.9]["mean_confidence"] == pytest.approx(0.9)
    assert curve[0.3]["n"] == 2 and curve[0.3]["errors"] == 1
    assert ec.expected_calibration_error(predictions, correct) == pytest.approx(4 / 6 * 0.15 + 2 / 6 * 0.2)
    brier = (3 * 0.01 + 0.81 + 0.49 + 0.09) / 6
    assert ec.brier_score(predictions, correct) == pytest.approx(brier)
    assert ec.brier_score([], []) is None and ec.expected_calibration_error([], []) is None
    assert ec.reliability_table([1.0], [True])[-1]["n"] == 1  # pewność 1,0 wpada do ostatniego przedziału


def test_band_table_and_monotonicity() -> None:
    predictions = [0.1, 0.2, 0.97, 0.98, 0.99, 0.5]
    correct = [False, False, True, True, True, True]
    table = ec.band_error_table(predictions, correct, ARTIFACT)
    assert sum(band["n"] for band in table.values()) == 6 and table["high"]["errors"] == 0
    assert ec.is_monotone(table)
    assert not ec.is_monotone({"low": {"n": 5, "error_rate": 0.1}, "medium": {"n": 5, "error_rate": 0.4}, "high": {"n": 5, "error_rate": 0.0}})
    assert ec.is_monotone({"low": {"n": 0, "error_rate": None}, "medium": {"n": 3, "error_rate": 0.0}, "high": {"n": 5, "error_rate": 0.0}})


def test_data_digest_is_stable_and_order_independent() -> None:
    rows = [{"a": 1, "features": {"x": 1.0}}, {"a": 2, "features": {"x": 0.0}}]
    assert ec.data_digest(rows) == ec.data_digest(list(reversed(rows)))
    assert ec.data_digest(rows) != ec.data_digest(rows[:1]) and len(ec.data_digest(rows)) == 64


# --- artefakt: wczytanie i walidacja -------------------------------------------------------------------


def test_shipped_artifact_loads_and_describes_its_origin() -> None:
    assert ARTIFACT.version.startswith("mpzp-confidence/") and len(ARTIFACT.data_sha256) == 64
    assert ARTIFACT.calibration_id == f"{ARTIFACT.version}+{ARTIFACT.data_sha256[:12]}"
    payload = ARTIFACT.payload
    assert tuple(payload["model"]["feature_names"]) == ec.FEATURE_NAMES
    assert payload["data"]["calibration_split"] != payload["data"]["test_split"]
    assert payload["engine"]["language_model"] is None and payload["engine"]["prompt"] is None
    assert 0.0 < ARTIFACT.review_threshold <= ARTIFACT.band_thresholds["high"] <= 1.0
    assert all(weight <= 0.0 for name, weight in zip(ec.FEATURE_NAMES, ARTIFACT.weights) if name in ec.NON_POSITIVE_FEATURES)
    assert ARTIFACT.engine_versions


def _mutated(directory: Path, **changes: object) -> Path:
    payload = json.loads(json.dumps(ARTIFACT.payload))
    for dotted, value in changes.items():
        node = payload
        *path, last = dotted.split(".")
        for part in path:
            node = node[part]
        node[last] = value
    target = directory / "mutated_artifact.json"  # testy zapisują wyłącznie w tmp_path
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": "inna"},
        {"model.feature_names": ["tylko_jedna"]},
        {"model.weights": [0.0]},
        {"bands.medium.lower": 0.9, "bands.high.lower": 0.5},
        {"manual_review.threshold": 3.0},
        {"data.sha256": ""},
    ],
)
def test_inconsistent_artifacts_are_rejected(change: dict[str, object], tmp_path: Path) -> None:
    with pytest.raises(ec.CalibrationError):
        ec.load_artifact(_mutated(tmp_path, **change))


def test_artifact_with_a_self_assessment_feature_is_rejected(tmp_path: Path) -> None:
    names = list(ec.FEATURE_NAMES)
    names[0] = "model_confidence"
    with pytest.raises(ec.CalibrationError):
        ec.load_artifact(_mutated(tmp_path, **{"model.feature_names": names}))
    with pytest.raises(ec.CalibrationError):
        ec.load_artifact(tmp_path / "nie-istnieje.json")
