"""Root kompozycji ekstrakcji reguł planistycznych."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.modules.planning.application.service import PlanningRuleService
from app.modules.planning.infrastructure.repository import (
    SqlAlchemyPlanningRuleRepository,
)

PARSER_VERSION = "mpzp-rules/1.0"


def build_planning_rule_service(session: Session) -> PlanningRuleService:
    return PlanningRuleService(
        SqlAlchemyPlanningRuleRepository(session),
        parser_version=PARSER_VERSION,
    )
