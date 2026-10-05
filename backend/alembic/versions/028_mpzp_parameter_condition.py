"""Warunki i rodzaj wartości parametru MPZP w evidence analizy (PV3-08).

Revision ID: 028_mpzp_parameter_condition
Revises: 027_zone_symbol_length
Create Date: 2026-10-03

Wiersze ``mpzp_parameters`` zapisują evidence kolumnowo (BK-203), więc warunki wartości
(``dla dachu płaskiego``, ``dla budynków gospodarczych``) i rodzaj wartości
(``unconditional``/``conditional``/``conflict``) dostają własne kolumny: ``conditions``
(JSONB, lista ``{kind, label, quote}``) oraz ``value_kind``. Obie są NULL-owalne: NULL w
wierszu sprzed PV3-08 znaczy „zapis bez warunków” (czytany jako wartość bezwarunkowa;
``conflict`` wynika wtedy z ``conflict_group_id``). Migracja niczego nie przelicza i nie
przepisuje migracji historycznych; rollback usuwa kolumny, a warunki zostają w
``mpzp_zones.result_snapshot``, który jest źródłem prawdy odczytu historycznego.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "028_mpzp_parameter_condition"
down_revision: Union[str, None] = "027_zone_symbol_length"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

VALUE_KINDS = ("unconditional", "conditional", "conflict")


def upgrade() -> None:
    op.add_column("mpzp_parameters", sa.Column("conditions", postgresql.JSONB(), nullable=True))
    op.add_column("mpzp_parameters", sa.Column("value_kind", sa.String(length=20), nullable=True))
    op.create_check_constraint(
        "ck_mpzp_parameters_value_kind",
        "mpzp_parameters",
        "value_kind IS NULL OR value_kind IN ('unconditional', 'conditional', 'conflict')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_mpzp_parameters_value_kind", "mpzp_parameters", type_="check")
    op.drop_column("mpzp_parameters", "value_kind")
    op.drop_column("mpzp_parameters", "conditions")
