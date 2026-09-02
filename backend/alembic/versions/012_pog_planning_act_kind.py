"""Import POG: ograniczenie rodzaju aktu planistycznego (mpzp/pog).

Revision ID: 012_pog_planning_act_kind
Revises: 011_structured_planning_rules
Create Date: 2026-07-25

POG jest importowany do istniejących tabel wersjonowanych (planning_acts z
kind='pog', planning_act_versions, plan_boundaries, planning_features), więc nie
wymaga nowych tabel. Ta migracja dodaje jedynie CHECK constraint na kolumnie
``kind``, aby rodzaj aktu był ograniczony do potwierdzonego zbioru wartości
(mpzp, pog) i chronił przed literówką w importerze (gap_10).
"""

from typing import Sequence, Union

from alembic import op


revision: str = "012_pog_planning_act_kind"
down_revision: Union[str, None] = "011_structured_planning_rules"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Istniejące wiersze mają kind='mpzp' (jedyny dotychczasowy importer), więc
    # dodanie ograniczenia jest bezpieczne bez migracji danych.
    op.create_check_constraint(
        "ck_planning_acts_kind",
        "planning_acts",
        "kind IN ('mpzp', 'pog')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_planning_acts_kind", "planning_acts", type_="check"
    )
