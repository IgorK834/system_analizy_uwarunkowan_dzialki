"""Deterministyczna ekstrakcja walidowalnych reguł planistycznych."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid5

from app.shared.numbers import parse_numeric_range, parse_polish_number

RuleOperator = Literal["eq", "lte", "gte", "range", "contains", "prohibits"]
ReviewStatus = Literal[
    "unreviewed",
    "verified",
    "rejected",
    "superseded",
    "ai_candidate",
]

_NUMBER = r"(\d+(?:[.,]\d+)?)"

_SINGLE_NUMBER_PATTERNS: tuple[
    tuple[str, RuleOperator, str | None, re.Pattern[str]], ...
] = (
    (
        "max_building_height",
        "lte",
        "m",
        re.compile(
            rf"maksymaln\w*\s+wysoko\w*(?:\s+zabudowy)?\D{{0,50}}?{_NUMBER}\s*m\b",
            re.IGNORECASE,
        ),
    ),
    (
        "max_storeys",
        "lte",
        None,
        re.compile(
            rf"(?:maksymalnie|nie\s+więcej\s+niż)\s+{_NUMBER}\s+kondygnacj\w*",
            re.IGNORECASE,
        ),
    ),
    (
        "min_biologically_active",
        "gte",
        "percent",
        re.compile(
            rf"(?:minimaln\w*\s+)?(?:powierzchni|teren)\w*\s+"
            rf"biologicznie\s+czynn\w*\D{{0,50}}?{_NUMBER}\s*%",
            re.IGNORECASE,
        ),
    ),
    (
        "max_building_coverage",
        "lte",
        "percent",
        re.compile(
            rf"maksymaln\w*(?:\s+wskaźnik)?\s+powierzchni\w*\s+"
            rf"zabudowy\D{{0,50}}?{_NUMBER}\s*%",
            re.IGNORECASE,
        ),
    ),
    (
        "setback",
        "gte",
        "m",
        re.compile(
            rf"w\s+odległości(?:\s+nie\s+mniejszej\s+niż)?\s+{_NUMBER}\s*m"
            r"\s+od\s+granicy",
            re.IGNORECASE,
        ),
    ),
    (
        "parking_minimum",
        "gte",
        "space",
        re.compile(
            rf"{_NUMBER}\s+miejsc\w*\s+(?:parkingow\w*\s+)?na\b",
            re.IGNORECASE,
        ),
    ),
)

_INTENSITY_PATTERN = re.compile(
    r"(?:wskaźnik\w*\s+)?intensywno\w*\s+zabudowy(?P<context>.{0,120})",
    re.IGNORECASE | re.DOTALL,
)
_ROOF_ANGLE_PATTERN = re.compile(
    r"(?:kąt\w*\s+nachylenia\w*|nachyleni\w*\s+połaci\w*)"
    r"(?P<context>.{0,100})",
    re.IGNORECASE | re.DOTALL,
)

_PRIMARY_USE_PATTERN = re.compile(
    r"przeznaczeni\w*\s+podstawow\w*\s*[:–-]\s*([^.;\n]+)",
    re.IGNORECASE,
)
_SUPPLEMENTARY_USE_PATTERN = re.compile(
    r"przeznaczeni\w*\s+(?:uzupełniając\w*|dopuszczaln\w*)\s*[:–-]\s*"
    r"([^.;\n]+)",
    re.IGNORECASE,
)
_ROOF_GEOMETRY_PATTERN = re.compile(
    r"dach\w*\s+(dwuspadow\w*|wielospadow\w*|płask\w*)",
    re.IGNORECASE,
)
_PROHIBITION_PATTERN = re.compile(r"zakaz\s+[^.;\n]+", re.IGNORECASE)
_ENVIRONMENT_PATTERN = re.compile(
    r"(?:nakaz\s+ochrony|ograniczeni\w*\s+środowisk\w*)[^.;\n]*",
    re.IGNORECASE,
)
_LARGE_RETAIL_PATTERN = re.compile(
    r"(?:zakaz\w*\s+)?obiekt\w*\s+handlow\w*.{0,80}?"
    r"(\d[\d\s]*)\s*m\s*(?:2|²|kw\.?)",
    re.IGNORECASE,
)

_PERCENT_CODES = {
    "min_biologically_active",
    "max_building_coverage",
}
_POSITIVE_CODES = {
    "max_building_height",
    "max_storeys",
    "min_intensity",
    "max_intensity",
    "setback",
    "parking_minimum",
}


class PlanningRuleValidationError(ValueError):
    """Reguła narusza twarde warunki dowodu albo zakresu domenowego."""


@dataclass(frozen=True)
class LegalTextUnit:
    """Minimalny, niezależny od modułu documents widok jednostki prawnej."""

    legal_unit_id: int
    source_text: str


@dataclass(frozen=True)
class PlanningRuleCandidate:
    """Reguła gotowa do zapisu wraz z pełnym provenance."""

    legal_unit_id: int
    code: str
    operator: RuleOperator
    parser_version: str
    confidence: float
    source_text: str | None
    raw_value: str | None
    value: float | None = None
    min_value: float | None = None
    max_value: float | None = None
    text_value: str | None = None
    unit: str | None = None
    conditions: tuple[dict[str, Any], ...] = ()
    review_status: ReviewStatus = "unreviewed"
    conflict_group: str | None = None


def validate_planning_rule(rule: PlanningRuleCandidate) -> None:
    """Wymusza zakresy oraz zakaz publikacji bez dosłownego dowodu."""
    if not 0 <= rule.confidence <= 1:
        raise PlanningRuleValidationError("Confidence musi mieścić się w 0–1.")
    if not (rule.source_text or "").strip():
        if rule.review_status == "verified" or rule.confidence > 0.8:
            raise PlanningRuleValidationError(
                "Reguła bez source_text nie może być verified ani mieć "
                "confidence > 0.8."
            )
    numeric_values = [
        value
        for value in (rule.value, rule.min_value, rule.max_value)
        if value is not None
    ]
    if rule.code in _PERCENT_CODES and any(
        value < 0 or value > 100 for value in numeric_values
    ):
        raise PlanningRuleValidationError(
            f"Procent dla {rule.code} musi mieścić się w 0–100."
        )
    if rule.code in _POSITIVE_CODES and any(value <= 0 for value in numeric_values):
        raise PlanningRuleValidationError(
            f"Wartość {rule.code} musi być dodatnia."
        )
    if (
        rule.min_value is not None
        and rule.max_value is not None
        and rule.min_value > rule.max_value
    ):
        raise PlanningRuleValidationError("Minimum nie może przekraczać maksimum.")


def extract_planning_rules(
    unit: LegalTextUnit,
    *,
    parser_version: str,
) -> list[PlanningRuleCandidate]:
    """Ekstrahuje liczby, przeznaczenia, dachy, parking i ograniczenia."""
    text = unit.source_text
    rules: list[PlanningRuleCandidate] = []

    for code, operator, measurement_unit, pattern in _SINGLE_NUMBER_PATTERNS:
        for match in pattern.finditer(text):
            value = parse_polish_number(match.group(1))
            rules.append(
                _numeric_rule(
                    unit,
                    code,
                    operator,
                    value,
                    measurement_unit,
                    match.group(0),
                    parser_version,
                )
            )

    for anchor in _INTENSITY_PATTERN.finditer(text):
        context = anchor.group("context")
        numeric_range = parse_numeric_range(context)
        if numeric_range is not None:
            excerpt = f"{anchor.group(0)[:160]}"
            rules.extend(
                [
                    _numeric_rule(
                        unit,
                        "min_intensity",
                        "gte",
                        numeric_range.minimum,
                        None,
                        excerpt,
                        parser_version,
                    ),
                    _numeric_rule(
                        unit,
                        "max_intensity",
                        "lte",
                        numeric_range.maximum,
                        None,
                        excerpt,
                        parser_version,
                    ),
                ]
            )
            continue
        minimum = re.search(
            rf"minimaln\w*\D{{0,20}}?{_NUMBER}", context, re.IGNORECASE
        )
        maximum = re.search(
            rf"maksymaln\w*\D{{0,20}}?{_NUMBER}", context, re.IGNORECASE
        )
        if minimum is not None:
            rules.append(
                _numeric_rule(
                    unit,
                    "min_intensity",
                    "gte",
                    parse_polish_number(minimum.group(1)),
                    None,
                    minimum.group(0),
                    parser_version,
                )
            )
        if maximum is not None:
            rules.append(
                _numeric_rule(
                    unit,
                    "max_intensity",
                    "lte",
                    parse_polish_number(maximum.group(1)),
                    None,
                    maximum.group(0),
                    parser_version,
                )
            )

    for anchor in _ROOF_ANGLE_PATTERN.finditer(text):
        numeric_range = parse_numeric_range(anchor.group("context"))
        if numeric_range is not None:
            rules.append(
                PlanningRuleCandidate(
                    legal_unit_id=unit.legal_unit_id,
                    code="roof_angle",
                    operator="range",
                    min_value=numeric_range.minimum,
                    max_value=numeric_range.maximum,
                    unit="deg",
                    raw_value=numeric_range.raw_value,
                    source_text=anchor.group(0),
                    parser_version=parser_version,
                    confidence=0.86,
                )
            )

    text_patterns: tuple[
        tuple[str, RuleOperator, re.Pattern[str]], ...
    ] = (
        ("primary_use", "eq", _PRIMARY_USE_PATTERN),
        ("supplementary_use", "contains", _SUPPLEMENTARY_USE_PATTERN),
        ("roof_geometry", "eq", _ROOF_GEOMETRY_PATTERN),
        ("prohibition", "prohibits", _PROHIBITION_PATTERN),
        ("environmental_restriction", "contains", _ENVIRONMENT_PATTERN),
    )
    for code, operator, pattern in text_patterns:
        for match in pattern.finditer(text):
            raw = " ".join(match.group(0).split())
            text_value = " ".join(
                (match.group(1) if match.lastindex else match.group(0)).split()
            )
            rules.append(
                PlanningRuleCandidate(
                    legal_unit_id=unit.legal_unit_id,
                    code=code,
                    operator=operator,
                    text_value=text_value,
                    raw_value=raw,
                    source_text=raw,
                    parser_version=parser_version,
                    confidence=0.84,
                )
            )

    for match in _LARGE_RETAIL_PATTERN.finditer(text):
        area = parse_polish_number(match.group(1))
        rules.append(
            _numeric_rule(
                unit,
                "large_retail_area",
                "lte",
                area,
                "m2",
                match.group(0),
                parser_version,
            )
        )

    for rule in rules:
        validate_planning_rule(rule)
    return rules


def assign_conflict_groups(
    rules: list[PlanningRuleCandidate],
) -> list[PlanningRuleCandidate]:
    """Oznacza sprzeczne wartości grupą, zamiast arbitralnie wybierać jedną."""
    by_code: dict[str, list[PlanningRuleCandidate]] = {}
    for rule in rules:
        by_code.setdefault(rule.code, []).append(rule)

    result: list[PlanningRuleCandidate] = []
    for code, grouped in by_code.items():
        fingerprints = {
            (rule.value, rule.min_value, rule.max_value, rule.text_value)
            for rule in grouped
        }
        if len(fingerprints) <= 1 or len(grouped) <= 1:
            result.extend(grouped)
            continue
        seed = "|".join(
            [
                code,
                *sorted(
                    f"{rule.legal_unit_id}:{rule.value}:{rule.min_value}:"
                    f"{rule.max_value}:{rule.text_value}"
                    for rule in grouped
                ),
            ]
        )
        conflict_group = str(uuid5(NAMESPACE_URL, seed))
        result.extend(
            replace(
                rule,
                conflict_group=conflict_group,
                review_status="unreviewed",
                confidence=min(rule.confidence, 0.6),
            )
            for rule in grouped
        )
    return result


def _numeric_rule(
    unit: LegalTextUnit,
    code: str,
    operator: RuleOperator,
    value: float,
    measurement_unit: str | None,
    raw_value: str,
    parser_version: str,
) -> PlanningRuleCandidate:
    return PlanningRuleCandidate(
        legal_unit_id=unit.legal_unit_id,
        code=code,
        operator=operator,
        value=value,
        unit=measurement_unit,
        raw_value=" ".join(raw_value.split()),
        source_text=" ".join(raw_value.split()),
        parser_version=parser_version,
        confidence=0.88,
    )
