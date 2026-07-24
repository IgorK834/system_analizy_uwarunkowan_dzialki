"""Port zapisu ustrukturyzowanych reguł planistycznych."""

from __future__ import annotations

from typing import Protocol

from app.modules.planning.domain.rules import PlanningRuleCandidate


class PlanningRuleRepository(Protocol):
    def replace_for_legal_units(
        self,
        legal_unit_ids: list[int],
        rules: list[PlanningRuleCandidate],
    ) -> list[PlanningRuleCandidate]:
        """Zastępuje reguły wyłącznie wskazanych jednostek prawnych."""
