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
    from app.modules.planning.domain import evidence_confidence

    artifact = evidence_confidence.current_artifact()
    cap = max(rule.confidence for rule in first)
    assert cap < artifact.review_threshold  # sprzeczne reguły zawsze poniżej progu ręcznej weryfikacji
    assert all(rule.confidence <= cap for rule in first)
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


# --- reguły z silnika leksykonu (PV3-07) -----------------------------------------------------------


def test_rules_and_parser_share_one_engine_so_the_same_text_gives_the_same_values() -> None:
    from app.modules.planning.domain.quantity_engine import find_quantities

    engine_values = {(m.parameter, m.value) for m in find_quantities(_RULE_TEXT)}
    rules = extract_planning_rules(LegalTextUnit(1, _RULE_TEXT), parser_version="rules-test/1")
    rule_values = {(r.code, r.value) for r in rules if r.value is not None and r.code != "roof_angle"}
    mapping = {
        ("max_building_height_m", 12.5): ("max_building_height", 12.5),
        ("max_storeys", 3.0): ("max_storeys", 3.0),
        ("min_biologically_active_percent", 40.0): ("min_biologically_active", 40.0),
        ("max_building_coverage_percent", 35.0): ("max_building_coverage", 35.0),
        ("setback_m", 6.0): ("setback", 6.0),
        ("parking_minimum", 2.0): ("parking_minimum", 2.0),
        ("min_intensity", 0.2): ("min_intensity", 0.2),
        ("max_intensity", 1.5): ("max_intensity", 1.5),
        ("max_retail_sales_area_m2", 2000.0): ("large_retail_area", 2000.0),
    }
    for engine_pair, rule_pair in mapping.items():
        assert engine_pair in engine_values and rule_pair in rule_values


def test_rule_evidence_is_the_phrase_from_the_noun_to_the_value() -> None:
    rules = extract_planning_rules(
        LegalTextUnit(3, "Maksymalna wysokość zabudowy wynosi 12,5 m."), parser_version="rules-test/1"
    )
    (height,) = [r for r in rules if r.code == "max_building_height"]
    assert height.source_text == "wysokość zabudowy wynosi 12,5 m" and height.raw_value == "wynosi 12,5 m"
    assert height.operator == "lte" and height.unit == "m" and 0.9 < height.confidence <= 0.99  # skalibrowana, nie stała 0,88


def test_roof_angle_rules_cover_range_single_bound_and_equal_bounds() -> None:
    def roof(text: str):
        (rule,) = [r for r in extract_planning_rules(LegalTextUnit(1, text), parser_version="t") if r.code == "roof_angle"]
        return rule

    ranged = roof("Kąt nachylenia połaci od 30° do 45°.")
    assert (ranged.operator, ranged.min_value, ranged.max_value) == ("range", 30.0, 45.0)
    upper = roof("Kąt nachylenia połaci do 20°.")
    assert (upper.operator, upper.value, upper.min_value) == ("lte", 20.0, None)
    exact = roof("Kąt nachylenia połaci dachowych 35°.")
    assert (exact.operator, exact.value) == ("eq", 35.0)


def test_new_numeric_codes_are_validated_like_the_old_ones() -> None:
    rules = extract_planning_rules(
        LegalTextUnit(
            1,
            "Minimalny wskaźnik powierzchni zabudowy – 0,05. Minimalną powierzchnię nowo wydzielonej działki budowlanej – 800 m2.",
        ),
        parser_version="t",
    )
    by_code = {r.code: r for r in rules}
    assert by_code["min_building_coverage"].value == 5.0 and by_code["min_building_coverage"].unit == "percent"
    assert by_code["min_plot_area"].value == 800.0 and by_code["min_plot_area"].operator == "gte"
    with pytest.raises(PlanningRuleValidationError):
        validate_planning_rule(_candidate(code="min_building_coverage", value=120.0))


def test_roof_geometry_rule_has_the_canonical_lexicon_form_and_the_literal_quote() -> None:
    # PV3-21: reguła i parser MPZP czytają dach tym samym silnikiem — wartość to forma kanoniczna słownika
    # (obie alternatywy), a dosłowny zapis zostaje w ``raw_value``/``source_text``.
    rules = extract_planning_rules(LegalTextUnit(1, "Dachy dwuspadowe lub płaskie."), parser_version="t")
    (roof,) = [r for r in rules if r.code == "roof_geometry"]
    assert roof.text_value == "dwuspadowy_lub_płaski"
    assert roof.raw_value == roof.source_text == "Dachy dwuspadowe lub płaskie"


def test_definitions_from_the_glossary_are_not_use_designations() -> None:
    text = (
        "1) przeznaczenie podstawowe - przeznaczenie, które przeważa na danej działce,\n"
        "2) przeznaczeniu uzupełniającym – należy przez to rozumieć przeznaczenie inne niż podstawowe;"
    )
    assert not [r for r in extract_planning_rules(LegalTextUnit(1, text), parser_version="t")
                if r.code in {"primary_use", "supplementary_use"}]


def test_rules_and_the_mpzp_parser_read_descriptive_findings_with_the_same_engine() -> None:
    """Jeden silnik (PV3-21): ten sam tekst daje te same ustalenia opisowe w regule i w parserze MPZP."""
    from app.services.mpzp_parser_descriptive import extract_descriptive_parameters
    from app.services.mpzp_parser_segment import DocumentSegment, ZoneSectionCandidate, ZoneSectionResult

    text = json.loads(
        (Path(__file__).parent / "fixtures" / "mpzp" / "lodz_mw_u" / "pages.json").read_text(encoding="utf-8")
    )["pages"][0]
    rules = extract_planning_rules(LegalTextUnit(1, text), parser_version="t")
    segment = DocumentSegment(segment_id="s1", text=text, page_number=1, heading=None, source="paragraph")
    zone = ZoneSectionResult(zone_symbol="Z", candidates=[ZoneSectionCandidate("Z", "s1", text[:200], 1, 1.0, "test")])
    parameters = extract_descriptive_parameters("Z", zone, [segment])
    for code in ("primary_use", "supplementary_use", "prohibition"):
        assert sorted(r.text_value for r in rules if r.code == code) == sorted(
            p.normalized_value for p in parameters if p.name == code
        ), code
    assert {r.text_value for r in rules if r.code == "primary_use"} == {
        "tereny zabudowy mieszkaniowej wielorodzinnej", "tereny zabudowy usługowej",
    }
