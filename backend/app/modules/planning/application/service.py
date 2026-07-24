"""Przypadek użycia ekstrakcji reguł dla zapisanych jednostek prawnych."""

from __future__ import annotations

from app.modules.planning.application.ports import PlanningRuleRepository
from app.modules.planning.domain.rules import (
    LegalTextUnit,
    PlanningRuleCandidate,
    assign_conflict_groups,
    extract_planning_rules,
)


class PlanningRuleService:
    def __init__(
        self,
        repository: PlanningRuleRepository,
        *,
        parser_version: str,
    ) -> None:
        self._repository = repository
        self._parser_version = parser_version

    def extract_and_replace(
        self, units: list[LegalTextUnit]
    ) -> list[PlanningRuleCandidate]:
        rules = [
            rule
            for unit in units
            for rule in extract_planning_rules(
                unit, parser_version=self._parser_version
            )
        ]
        with_conflicts = assign_conflict_groups(rules)
        return self._repository.replace_for_legal_units(
            [unit.legal_unit_id for unit in units],
            with_conflicts,
        )
