"""Adapter SQLAlchemy zapisu reguł planistycznych."""

from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models.versioned import PlanningRule
from app.modules.planning.application.ports import PlanningRuleRepository
from app.modules.planning.domain.rules import (
    PlanningRuleCandidate,
    validate_planning_rule,
)


class SqlAlchemyPlanningRuleRepository(PlanningRuleRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def replace_for_legal_units(
        self,
        legal_unit_ids: list[int],
        rules: list[PlanningRuleCandidate],
    ) -> list[PlanningRuleCandidate]:
        if legal_unit_ids:
            self._session.execute(
                delete(PlanningRule).where(
                    PlanningRule.legal_unit_id.in_(legal_unit_ids)
                )
            )
        for rule in rules:
            validate_planning_rule(rule)
            self._session.add(
                PlanningRule(
                    legal_unit_id=rule.legal_unit_id,
                    code=rule.code,
                    operator=rule.operator,
                    value=rule.value,
                    min_value=rule.min_value,
                    max_value=rule.max_value,
                    text_value=rule.text_value,
                    unit=rule.unit,
                    conditions=list(rule.conditions) or None,
                    source_text=rule.source_text,
                    raw_value=rule.raw_value,
                    parser_version=rule.parser_version,
                    confidence=rule.confidence,
                    review_status=rule.review_status,
                    conflict_group=rule.conflict_group,
                )
            )
        self._session.flush()
        return rules
