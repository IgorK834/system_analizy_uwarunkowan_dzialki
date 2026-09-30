"""Testy jądra jakości danych (BK-504/505): świeżość na zamrożonym zegarze.

Zegar jest zamrożony przez jawny punkt odniesienia — funkcje nie czytają
bieżącego czasu, więc wynik zależy wyłącznie od argumentów.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.shared.data_quality import (
    CLOCK_SKEW_TOLERANCE,
    FRESHNESS_FUTURE_TIME,
    FRESHNESS_INVALID_TIME,
    FRESHNESS_NO_FETCH_TIME,
    FRESHNESS_NO_POLICY,
    FRESHNESS_OLDER_THAN_POLICY,
    QUALITY_REASON_LABELS_PL,
    QUALITY_STATUS_DESCRIPTIONS_PL,
    QUALITY_STATUS_LABELS_PL,
    QUALITY_STATUSES,
    FreshnessRule,
    allows_derived,
    allows_raw,
    canonical_json,
    canonical_sha256,
    evaluate_freshness,
    format_age_pl,
    reason_label_pl,
    unknown_freshness,
)

REFERENCE = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
RULE_7D = FreshnessRule(7, "project_decision", "Reguła testowa dla usługi na żywo.")
RULE_1D = FreshnessRule(1, "source_declared_interval", "Źródło deklaruje aktualizację dzienną.")


def test_fresh_within_rule() -> None:
    verdict = evaluate_freshness(REFERENCE - timedelta(days=3), REFERENCE, RULE_7D)
    assert verdict.state == "fresh"
    assert verdict.reason_code is None
    assert verdict.age_seconds == 3 * 86_400
    assert (verdict.max_age_days, verdict.basis) == (7, "project_decision")


def test_age_equal_to_limit_is_still_fresh_and_one_second_more_is_stale() -> None:
    boundary = evaluate_freshness(REFERENCE - timedelta(days=7), REFERENCE, RULE_7D)
    assert boundary.state == "fresh"
    over = evaluate_freshness(REFERENCE - timedelta(days=7, seconds=1), REFERENCE, RULE_7D)
    assert over.state == "stale"
    assert over.reason_code == FRESHNESS_OLDER_THAN_POLICY
    assert over.age_seconds == 7 * 86_400 + 1


def test_rules_are_per_source_not_global() -> None:
    fetched = REFERENCE - timedelta(days=3)
    assert evaluate_freshness(fetched, REFERENCE, RULE_7D).state == "fresh"
    assert evaluate_freshness(fetched, REFERENCE, RULE_1D).state == "stale"


def test_no_policy_is_unknown_but_reports_measured_age() -> None:
    verdict = evaluate_freshness(REFERENCE - timedelta(days=400), REFERENCE, None)
    assert verdict.state == "unknown"
    assert verdict.reason_code == FRESHNESS_NO_POLICY
    assert verdict.age_seconds == 400 * 86_400
    assert verdict.max_age_days is None and verdict.basis is None


def test_future_fetch_time_beyond_tolerance_is_invalid_not_fresh() -> None:
    verdict = evaluate_freshness(REFERENCE + CLOCK_SKEW_TOLERANCE + timedelta(seconds=1), REFERENCE, RULE_7D)
    assert verdict.state == "unknown"
    assert verdict.reason_code == FRESHNESS_FUTURE_TIME
    assert verdict.age_seconds is None
    # Reguła zostaje w werdykcie, żeby ocena pokazywała, wg czego oceniano.
    assert verdict.max_age_days == 7


def test_small_clock_skew_counts_as_age_zero() -> None:
    verdict = evaluate_freshness(REFERENCE + timedelta(minutes=2), REFERENCE, RULE_7D)
    assert verdict.state == "fresh"
    assert verdict.age_seconds == 0


def test_naive_fetch_time_is_invalid() -> None:
    verdict = evaluate_freshness(datetime(2026, 9, 28, 12, 0), REFERENCE, RULE_7D)
    assert verdict.state == "unknown"
    assert verdict.reason_code == FRESHNESS_INVALID_TIME


def test_missing_fetch_time_is_unknown() -> None:
    verdict = evaluate_freshness(None, REFERENCE, RULE_7D)
    assert verdict.state == "unknown"
    assert verdict.reason_code == FRESHNESS_NO_FETCH_TIME
    assert verdict.age_seconds is None


def test_reference_without_timezone_is_a_caller_error() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate_freshness(REFERENCE, datetime(2026, 9, 29, 12, 0), RULE_7D)


def test_fetch_time_in_other_timezone_is_compared_in_absolute_time() -> None:
    warsaw = timezone(timedelta(hours=2))
    fetched = datetime(2026, 9, 29, 12, 0, tzinfo=warsaw)  # 10:00 UTC
    verdict = evaluate_freshness(fetched, REFERENCE, RULE_1D)
    assert verdict.age_seconds == 2 * 3600 and verdict.state == "fresh"


@pytest.mark.parametrize("days", [0, 3651, -1])
def test_rule_rejects_out_of_range_age(days: int) -> None:
    with pytest.raises(ValueError):
        FreshnessRule(days, "project_decision", "uzasadnienie")


def test_rule_requires_rationale() -> None:
    with pytest.raises(ValueError, match="uzasadnienia"):
        FreshnessRule(7, "project_decision", "   ")


def test_unknown_freshness_keeps_rule_details_when_given() -> None:
    verdict = unknown_freshness("FRESHNESS_NO_SOURCE", RULE_7D)
    assert (verdict.state, verdict.max_age_days) == ("unknown", 7)
    assert unknown_freshness("FRESHNESS_NO_SOURCE").max_age_days is None


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (None, "brak danych"),
        (0, "mniej niż 1 dzień"),
        (86_399, "mniej niż 1 dzień"),
        (86_400, "1 dzień"),
        (5 * 86_400 + 10, "5 dni"),
    ],
)
def test_format_age_pl(seconds: int | None, expected: str) -> None:
    assert format_age_pl(seconds) == expected


def test_status_vocabulary_is_complete_and_distinguishes_no_coverage_from_error() -> None:
    assert set(QUALITY_STATUSES) == set(QUALITY_STATUS_LABELS_PL) == set(QUALITY_STATUS_DESCRIPTIONS_PL)
    labels = {QUALITY_STATUS_LABELS_PL[status] for status in ("no_coverage", "unavailable", "error")}
    assert len(labels) == 3
    assert "błąd" not in QUALITY_STATUS_LABELS_PL["no_coverage"]
    assert "to nie jest błąd" in QUALITY_STATUS_DESCRIPTIONS_PL["no_coverage"]


def test_reason_labels_fall_back_to_the_raw_code() -> None:
    assert reason_label_pl("SERVICE_TIMEOUT") == QUALITY_REASON_LABELS_PL["SERVICE_TIMEOUT"]
    assert reason_label_pl("NIEZNANY_KOD_XYZ") == "kod źródła: NIEZNANY_KOD_XYZ"


def test_canonical_hash_ignores_key_order_and_detects_any_change() -> None:
    left = {"b": [1, 2, {"z": 1, "a": "ą"}], "a": None}
    right = {"a": None, "b": [1, 2, {"a": "ą", "z": 1}]}
    assert canonical_sha256(left) == canonical_sha256(right)
    assert canonical_json(left) == '{"a":null,"b":[1,2,{"a":"ą","z":1}]}'
    assert canonical_sha256(left) != canonical_sha256({**left, "a": 0})  # null ≠ 0
    with pytest.raises(ValueError):
        canonical_json({"x": float("nan")})


@pytest.mark.parametrize(
    ("policy", "derived", "raw"),
    [
        ("allowed", True, True),
        ("derived_only", True, False),
        ("forbidden", False, False),
        ("unconfirmed", False, False),
        (None, False, False),
        ("nieznana", False, False),
    ],
)
def test_redistribution_gates(policy: str | None, derived: bool, raw: bool) -> None:
    assert allows_derived(policy) is derived
    assert allows_raw(policy) is raw
