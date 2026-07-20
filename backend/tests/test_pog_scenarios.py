from types import SimpleNamespace

import pytest
from shapely.geometry import Polygon

import app.services.pog_scenarios as pog_scenarios
from app.core.planning_compatibility import PlanningCompatibilityResult
from app.services.ouz import calculate_ouz_status
from app.services.pog_scenarios import (
    LEGAL_INFORMATION_DISCLAIMER,
    build_pog_scenario_result,
)


def _ouz_available():
    parcel = Polygon.from_bounds(0, 0, 10, 10)
    return calculate_ouz_status(parcel, [parcel])


@pytest.mark.parametrize("status", ["not_available", "in_progress"])
def test_missing_or_in_progress_pog_is_controlled_transition(status: str) -> None:
    result = build_pog_scenario_result(
        None,
        SimpleNamespace(status=status, planning_zone=None),
        _ouz_available(),
    )

    assert result.status == status
    assert result.conflict is False
    assert result.conflict_uncertain is True
    assert result.manual_review_required is True
    assert "1 stycznia 2026" in result.message


def test_conflict_comes_only_from_compatibility_function(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, object]] = []

    def fake_check(mpzp_function, pog_zone_type):
        calls.append((mpzp_function, pog_zone_type))
        return PlanningCompatibilityResult(
            result="incompatible",
            reasoning="Kontrolowany wynik tabeli.",
            confidence=0.9,
        )

    monkeypatch.setattr(
        pog_scenarios,
        "check_mpzp_pog_compatibility",
        fake_check,
    )

    result = build_pog_scenario_result(
        SimpleNamespace(primary_use="production"),
        SimpleNamespace(status="adopted", planning_zone="SP"),
        _ouz_available(),
    )

    assert len(calls) == 1
    assert str(calls[0][0]) == "production"
    assert calls[0][1] == "SP"
    assert result.conflict is True
    assert result.compatibility is not None
    assert result.compatibility.reasoning == "Kontrolowany wynik tabeli."


def test_compatible_pair_does_not_create_false_conflict() -> None:
    result = build_pog_scenario_result(
        SimpleNamespace(primary_use="single_family_housing"),
        SimpleNamespace(status="adopted", planning_zone="SJ"),
        _ouz_available(),
    )

    assert result.conflict is False
    assert result.conflict_uncertain is False
    assert result.compatibility is not None
    assert result.compatibility.result == "compatible"


@pytest.mark.parametrize("compatibility_value", ["unknown", "uncertain"])
def test_unknown_or_uncertain_is_not_promoted_to_conflict(
    monkeypatch: pytest.MonkeyPatch,
    compatibility_value: str,
) -> None:
    monkeypatch.setattr(
        pog_scenarios,
        "check_mpzp_pog_compatibility",
        lambda *_: PlanningCompatibilityResult(
            result=compatibility_value,  # type: ignore[arg-type]
            reasoning="Brak jednoznacznej reguły.",
            confidence=0.2,
        ),
    )

    result = build_pog_scenario_result(
        SimpleNamespace(primary_use="services"),
        SimpleNamespace(status="adopted", planning_zone="SJ"),
        _ouz_available(),
    )

    assert result.conflict is False
    assert result.conflict_uncertain is True
    assert result.manual_review_required is True


def test_unknown_pog_status_gracefully_requires_review() -> None:
    result = build_pog_scenario_result(
        None,
        SimpleNamespace(status="unexpected", planning_zone=None),
        _ouz_available(),
    )

    assert result.status == "unknown"
    assert result.conflict is False
    assert result.warnings[0].code == "POG_SCENARIO_UNKNOWN"


def test_missing_normalized_mpzp_function_does_not_guess_from_text() -> None:
    result = build_pog_scenario_result(
        SimpleNamespace(primary_use="zabudowa produkcyjna i magazyny"),
        SimpleNamespace(status="adopted", planning_zone="SN"),
        _ouz_available(),
    )

    assert result.conflict is False
    assert result.conflict_uncertain is True
    assert result.compatibility is None
    assert result.warnings[0].code == "MPZP_POG_INPUT_INCOMPLETE"


@pytest.mark.parametrize(
    ("mpzp_result", "pog_result"),
    [
        (None, SimpleNamespace(status="not_available", planning_zone=None)),
        (None, None),
        (
            SimpleNamespace(primary_use="single_family_housing"),
            SimpleNamespace(status="adopted", planning_zone="SJ"),
        ),
    ],
)
def test_every_result_contains_legal_information_clause(
    mpzp_result,
    pog_result,
) -> None:
    result = build_pog_scenario_result(
        mpzp_result,
        pog_result,
        _ouz_available(),
    )

    assert result.legal_disclaimer == LEGAL_INFORMATION_DISCLAIMER
    assert LEGAL_INFORMATION_DISCLAIMER in result.message
