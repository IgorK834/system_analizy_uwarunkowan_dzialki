from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from app.modules.planning.application.service import PlanningRuleService
from app.modules.planning.domain.rules import (
    LegalTextUnit,
    PlanningRuleCandidate,
    PlanningRuleValidationError,
    assign_conflict_groups,
    extract_planning_rules,
    validate_planning_rule,
)
from app.shared.numbers import parse_numeric_range, parse_polish_number


_RULE_TEXT = """
Maksymalna wysokość zabudowy wynosi 12,5 m.
Nie więcej niż 3 kondygnacje.
Minimalna powierzchnia biologicznie czynna wynosi 40%.
Maksymalna powierzchnia zabudowy wynosi 35%.
Linię ustala się w odległości nie mniejszej niż 6 m od granicy działki.
Należy zapewnić 2 miejsca parkingowe na lokal.
Wskaźnik intensywności zabudowy od 0,20 do 1,50.
Kąt nachylenia połaci od 30° do 45°.
Przeznaczenie podstawowe: zabudowa mieszkaniowa.
Przeznaczenie uzupełniające: usługi nieuciążliwe.
Dachy dwuspadowe.
Zakaz lokalizacji składów otwartych.
Nakaz ochrony istniejących drzew.
Zakaz lokalizacji obiektów handlowych o powierzchni sprzedaży 2 000 m².
"""


def test_shared_number_parser_supports_three_legal_variants() -> None:
    assert parse_polish_number("1 234,50") == pytest.approx(1234.5)
    assert parse_numeric_range("0.20–1.50").maximum == pytest.approx(1.5)
    angle = parse_numeric_range("30° do 45°")
    assert angle is not None
    assert (angle.minimum, angle.maximum) == (30.0, 45.0)
    reversed_range = parse_numeric_range("2,5 do 0,5")
    assert reversed_range is not None
    assert (reversed_range.minimum, reversed_range.maximum) == (0.5, 2.5)
    assert parse_numeric_range("brak zakresu") is None
    with pytest.raises(ValueError):
        parse_polish_number(" ")


def test_extracts_all_required_planning_rule_categories_with_provenance() -> None:
    rules = extract_planning_rules(
        LegalTextUnit(legal_unit_id=17, source_text=_RULE_TEXT),
        parser_version="rules-test/1",
    )
    by_code = {}
    for rule in rules:
        by_code.setdefault(rule.code, []).append(rule)

    assert {
        "max_building_height",
        "max_storeys",
        "min_biologically_active",
        "max_building_coverage",
        "setback",
        "parking_minimum",
        "min_intensity",
        "max_intensity",
        "roof_angle",
        "primary_use",
        "supplementary_use",
        "roof_geometry",
        "prohibition",
        "environmental_restriction",
        "large_retail_area",
    } <= set(by_code)
    assert by_code["max_building_height"][0].value == pytest.approx(12.5)
    assert by_code["min_intensity"][0].value == pytest.approx(0.2)
    assert by_code["max_intensity"][0].value == pytest.approx(1.5)
    roof = by_code["roof_angle"][0]
    assert (roof.min_value, roof.max_value, roof.unit) == (30.0, 45.0, "deg")
    assert by_code["large_retail_area"][0].value == 2000.0
    assert all(rule.legal_unit_id == 17 for rule in rules)
    assert all(rule.source_text for rule in rules)
    assert all(rule.parser_version == "rules-test/1" for rule in rules)
    assert all(0 <= rule.confidence <= 1 for rule in rules)


def test_extended_mpzp_fixture_corpus_covers_all_planning_rule_categories() -> None:
    fixture_root = Path(__file__).parent / "fixtures" / "mpzp"
    fixture_names = (
        "gdansk_wrzeszcz",
        "poznan_naramowice",
        "torun_bielawy",
        "wroclaw_wojszyce",
    )
    codes: set[str] = set()
    legal_unit_id = 1
    for fixture_name in fixture_names:
        payload = json.loads(
            (fixture_root / fixture_name / "pages.json").read_text(
                encoding="utf-8"
            )
        )
        for page in payload["pages"]:
            rules = extract_planning_rules(
                LegalTextUnit(legal_unit_id, page),
                parser_version="fixture-corpus/1",
            )
            codes.update(rule.code for rule in rules)
            legal_unit_id += 1

    assert {
        "max_building_height",
        "max_storeys",
        "min_biologically_active",
        "max_building_coverage",
        "setback",
        "parking_minimum",
        "min_intensity",
        "max_intensity",
        "roof_angle",
        "primary_use",
        "supplementary_use",
        "roof_geometry",
        "prohibition",
        "environmental_restriction",
        "large_retail_area",
    } <= codes


def _candidate(**changes) -> PlanningRuleCandidate:
    base = PlanningRuleCandidate(
        legal_unit_id=1,
        code="max_building_height",
        operator="lte",
        value=10.0,
        unit="m",
        parser_version="test",
        confidence=0.8,
        source_text="wysokość 10 m",
        raw_value="10 m",
    )
    return replace(base, **changes)


@pytest.mark.parametrize(
    "candidate",
    [
        _candidate(confidence=1.1),
        _candidate(source_text=None, confidence=0.81),
        _candidate(source_text=None, review_status="verified"),
        _candidate(code="min_biologically_active", value=101.0),
        _candidate(value=0.0),
        _candidate(value=None, min_value=5.0, max_value=2.0),
    ],
)
def test_hard_rule_validation_rejects_unsafe_values(
    candidate: PlanningRuleCandidate,
) -> None:
    with pytest.raises(PlanningRuleValidationError):
        validate_planning_rule(candidate)


def test_rule_without_evidence_can_only_remain_low_confidence_candidate() -> None:
    candidate = _candidate(source_text=None, confidence=0.8)
    validate_planning_rule(candidate)


def test_conflicting_values_receive_one_deterministic_group_and_review() -> None:
    rules = [_candidate(value=9.0), _candidate(legal_unit_id=2, value=12.0)]

    first = assign_conflict_groups(rules)
    second = assign_conflict_groups(list(reversed(rules)))

    groups = {rule.conflict_group for rule in first}
    assert len(groups) == 1
    assert None not in groups
    assert all(rule.review_status != "verified" for rule in first)
    assert all(rule.confidence <= 0.6 for rule in first)
    assert {rule.conflict_group for rule in second} == groups
    assert assign_conflict_groups([_candidate()])[0].conflict_group is None


class _RuleRepository:
    def __init__(self) -> None:
        self.ids: list[int] = []
        self.rules: list[PlanningRuleCandidate] = []

    def replace_for_legal_units(self, legal_unit_ids, rules):
        self.ids = legal_unit_ids
        self.rules = rules
        return rules


def test_planning_rule_service_extracts_and_replaces_for_exact_units() -> None:
    repository = _RuleRepository()
    service = PlanningRuleService(repository, parser_version="service/1")

    result = service.extract_and_replace(
        [
            LegalTextUnit(1, "Maksymalna wysokość zabudowy 9 m."),
            LegalTextUnit(2, "Maksymalna wysokość zabudowy 12 m."),
        ]
    )

    assert repository.ids == [1, 2]
    assert result == repository.rules
    assert all(rule.parser_version == "service/1" for rule in result)
    assert all(rule.conflict_group for rule in result)
