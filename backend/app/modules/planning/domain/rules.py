"""Deterministyczna ekstrakcja walidowalnych reguł planistycznych.

Moduł NIE zawiera własnych wzorców ustaleń (PV3-21): wartości liczbowe (wysokość, kondygnacje,
udziały, odsunięcie, parkowanie, intensywność, kąt dachu, powierzchnia sprzedaży, minimalna działka)
pochodzą z silnika leksykonu ``quantity_engine`` (PV3-07), a zapisy opisowe (przeznaczenie, rodzaj
dachu, zakazy, ograniczenia środowiskowe) z ``descriptive_engine`` — tych samych, których używa parser
MPZP. Tu zostaje mapowanie ustaleń na kody i operatory reguł oraz walidacja.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid5

from app.modules.planning.domain import descriptive_engine as descriptive
from app.modules.planning.domain.quantity_engine import QuantityMatch, find_quantities
from app.modules.planning.domain import evidence_confidence

RuleOperator = Literal["eq", "lte", "gte", "range", "contains", "prohibits"]
ReviewStatus = Literal[
    "unreviewed",
    "verified",
    "rejected",
    "superseded",
    "ai_candidate",
]

# Parametr silnika → ``(kod reguły, operator, jednostka)``. Kąt dachu składa się osobno (zakres).
_NUMERIC_RULES: dict[str, tuple[str, RuleOperator, str | None]] = {
    "max_building_height_m": ("max_building_height", "lte", "m"),
    "max_storeys": ("max_storeys", "lte", None),
    "min_biologically_active_percent": ("min_biologically_active", "gte", "percent"),
    "max_building_coverage_percent": ("max_building_coverage", "lte", "percent"),
    "min_building_coverage_percent": ("min_building_coverage", "gte", "percent"),
    "setback_m": ("setback", "gte", "m"),
    "parking_minimum": ("parking_minimum", "gte", "space"),
    "min_intensity": ("min_intensity", "gte", None),
    "max_intensity": ("max_intensity", "lte", None),
    "max_retail_sales_area_m2": ("large_retail_area", "lte", "m2"),
    "min_plot_area_m2": ("min_plot_area", "gte", "m2"),
}
_ROOF_PARAMETERS = frozenset({"roof_angle_min_deg", "roof_angle_max_deg"})
# Metoda ekstrakcji wartości z modelu językowego po bramkach deterministycznych (PV3-12). Taka reguła
# jest wyłącznie kandydatem do ręcznej weryfikacji: status ``ai_candidate``, nigdy ``verified``.
EXTRACTION_METHOD_LLM_VERIFIED = "llm_verified"

# Ustalenie opisowe silnika → ``(kod reguły, operator)``. Dopuszczenia i opisowy nakaz parkowania nie
# mają kodu reguły (nie są ograniczeniem do sprawdzenia), więc reguły ich nie zapisują.
_TEXT_RULES: dict[str, tuple[str, RuleOperator]] = {
    "primary_use": ("primary_use", "eq"),
    "supplementary_use": ("supplementary_use", "contains"),
    "roof_geometry": ("roof_geometry", "eq"),
    "prohibition": ("prohibition", "prohibits"),
    "environmental_restriction": ("environmental_restriction", "contains"),
}
_PERCENT_CODES = {
    "min_biologically_active",
    "max_building_coverage",
    "min_building_coverage",
}
_POSITIVE_CODES = {
    "max_building_height",
    "max_storeys",
    "min_intensity",
    "max_intensity",
    "setback",
    "parking_minimum",
    "min_plot_area",
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
    # ``None`` — reguła z silnika deterministycznego; ``llm_verified`` — kandydat modelu po bramkach.
    extraction_method: str | None = None


def numeric_rule_spec(parameter: str) -> tuple[str, RuleOperator, str | None]:
    """Kod reguły, operator i jednostka dla parametru liczbowego silnika (także kąta dachu)."""
    if parameter in _ROOF_PARAMETERS:
        return "roof_angle", ("gte" if parameter == "roof_angle_min_deg" else "lte"), "deg"
    try:
        return _NUMERIC_RULES[parameter]
    except KeyError:
        raise PlanningRuleValidationError(f"Nieznany parametr liczbowy {parameter!r}.") from None


def validate_planning_rule(rule: PlanningRuleCandidate) -> None:
    """Wymusza zakresy oraz zakaz publikacji bez dosłownego dowodu."""
    if not 0 <= rule.confidence <= 1:
        raise PlanningRuleValidationError("Confidence musi mieścić się w 0–1.")
    if rule.extraction_method == EXTRACTION_METHOD_LLM_VERIFIED:
        # Wartość z modelu nie jest źródłem prawdy (ADR-012): tylko kandydat z dosłownym dowodem.
        if rule.review_status != "ai_candidate":
            raise PlanningRuleValidationError(
                "Reguła z modelu językowego musi mieć status ai_candidate (nigdy verified)."
            )
        if not (rule.source_text or "").strip():
            raise PlanningRuleValidationError("Reguła z modelu językowego wymaga zweryfikowanego cytatu.")
    elif rule.review_status == "ai_candidate":
        raise PlanningRuleValidationError(
            "Status ai_candidate przysługuje wyłącznie wartościom z modelu po bramkach (llm_verified)."
        )
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


def _conflict_confidence() -> float:
    """Pułap pewności reguły w grupie sprzeczności: model dla cechy ``conflict`` (poniżej progu weryfikacji)."""
    return round(
        evidence_confidence.probability(
            evidence_confidence.ConfidenceFeatures(value_kind="conflict", candidate_count=2)
        ),
        4,
    )


def _rule_confidence(match: QuantityMatch) -> float:
    """Pewność reguły liczbowej z cech dopasowania (PV3-09); jednostka prawna nie niesie cech dokumentu.

    Brak metody ekstrakcji i zakresu strefy to cechy neutralne, więc liczy się strategia i flagi przeróbek
    zapisu — dokładnie ten sam model, który ocenia parametry parsera MPZP.
    """
    return round(
        evidence_confidence.probability(
            evidence_confidence.ConfidenceFeatures(strategy=match.strategy, flags=match.flags, quote_verified=True)
        ),
        4,
    )


def _phrase(text: str, match: QuantityMatch) -> str:
    """Fraza źródłowa od rzeczownika parametru do końca wartości (dowód reguły)."""
    left = match.noun_start if match.noun_start is not None and match.noun_start < match.start else match.start
    return " ".join(text[left : match.end].split())


def _numeric_rules_from_engine(
    unit: LegalTextUnit, text: str, parser_version: str
) -> list[PlanningRuleCandidate]:
    rules: list[PlanningRuleCandidate] = []
    roof: dict[tuple[int, int], list[QuantityMatch]] = {}
    for match in find_quantities(text):
        if match.parameter in _ROOF_PARAMETERS:
            roof.setdefault((match.start, match.end), []).append(match)
            continue
        code, operator, measurement_unit = _NUMERIC_RULES[match.parameter]
        rules.append(
            PlanningRuleCandidate(
                legal_unit_id=unit.legal_unit_id,
                code=code,
                operator=operator,
                value=match.value,
                unit=measurement_unit,
                raw_value=match.raw_value,
                source_text=_phrase(text, match),
                parser_version=parser_version,
                confidence=_rule_confidence(match),
            )
        )
    for span_matches in roof.values():
        bounds = {m.parameter: m for m in span_matches}
        first = span_matches[0]
        minimum = bounds.get("roof_angle_min_deg")
        maximum = bounds.get("roof_angle_max_deg")
        common = dict(
            legal_unit_id=unit.legal_unit_id,
            code="roof_angle",
            unit="deg",
            raw_value=first.raw_value,
            source_text=_phrase(text, first),
            parser_version=parser_version,
            confidence=min(_rule_confidence(m) for m in span_matches),
        )
        if minimum is not None and maximum is not None and minimum.value != maximum.value:
            rules.append(
                PlanningRuleCandidate(operator="range", min_value=minimum.value, max_value=maximum.value, **common)  # type: ignore[arg-type]
            )
        elif minimum is not None and maximum is not None:
            rules.append(PlanningRuleCandidate(operator="eq", value=minimum.value, **common))  # type: ignore[arg-type]
        elif minimum is not None:
            rules.append(PlanningRuleCandidate(operator="gte", value=minimum.value, **common))  # type: ignore[arg-type]
        elif maximum is not None:
            rules.append(PlanningRuleCandidate(operator="lte", value=maximum.value, **common))  # type: ignore[arg-type]
    return rules


def extract_planning_rules(
    unit: LegalTextUnit,
    *,
    parser_version: str,
) -> list[PlanningRuleCandidate]:
    """Ekstrahuje liczby, przeznaczenia, dachy, parking i ograniczenia."""
    text = unit.source_text
    rules: list[PlanningRuleCandidate] = _numeric_rules_from_engine(unit, text, parser_version)

    findings = [
        *descriptive.find_use_designations(text),
        *descriptive.find_roof_geometries(text),
        *descriptive.find_prohibitions(text),
        # Jednostka prawna jest już wydzielonym zakresem, więc nagłówek „ochrony środowiska” nie jest wymagany.
        *descriptive.find_environmental_restrictions(text, heading_required=False),
    ]
    for finding in findings:
        code, operator = _TEXT_RULES[finding.code]
        rules.append(
            PlanningRuleCandidate(
                legal_unit_id=unit.legal_unit_id,
                code=code,
                operator=operator,
                text_value=finding.value,
                raw_value=finding.quote,
                source_text=finding.quote,
                parser_version=parser_version,
                confidence=evidence_confidence.uncalibrated_text_confidence(),
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
                confidence=min(rule.confidence, _conflict_confidence()),
            )
            for rule in grouped
        )
    return result
