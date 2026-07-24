from __future__ import annotations

import pytest

from app.modules.planning.composition import build_planning_rule_service
from app.modules.planning.domain.rules import (
    PlanningRuleCandidate,
    PlanningRuleValidationError,
)
from app.modules.planning.infrastructure.repository import (
    SqlAlchemyPlanningRuleRepository,
)


class _Session:
    def __init__(self) -> None:
        self.executed = []
        self.added = []
        self.flushes = 0

    def execute(self, statement):
        self.executed.append(statement)

    def add(self, record):
        self.added.append(record)

    def flush(self):
        self.flushes += 1


def _rule(**changes) -> PlanningRuleCandidate:
    values = {
        "legal_unit_id": 1,
        "code": "max_building_height",
        "operator": "lte",
        "value": 9.0,
        "unit": "m",
        "parser_version": "test",
        "confidence": 0.88,
        "source_text": "wysokość 9 m",
        "raw_value": "9 m",
    }
    values.update(changes)
    return PlanningRuleCandidate(**values)


def test_planning_repository_replaces_exact_units_and_maps_all_fields() -> None:
    session = _Session()
    rules = [
        _rule(
            conditions=({"when": "frontage"},),
            conflict_group="group",
            review_status="unreviewed",
        )
    ]

    result = SqlAlchemyPlanningRuleRepository(session).replace_for_legal_units(
        [1], rules
    )

    assert result == rules
    assert len(session.executed) == 1
    assert len(session.added) == 1
    assert session.added[0].conditions == [{"when": "frontage"}]
    assert session.added[0].conflict_group == "group"
    assert session.flushes == 1


def test_planning_repository_supports_empty_scope_and_revalidates() -> None:
    session = _Session()
    repository = SqlAlchemyPlanningRuleRepository(session)
    assert repository.replace_for_legal_units([], []) == []
    assert session.executed == []
    assert session.flushes == 1

    with pytest.raises(PlanningRuleValidationError):
        repository.replace_for_legal_units(
            [1], [_rule(source_text=None, confidence=0.9)]
        )


def test_planning_composition_builds_service() -> None:
    assert build_planning_rule_service(object()) is not None
