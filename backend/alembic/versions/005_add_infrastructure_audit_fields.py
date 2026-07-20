"""add_infrastructure_audit_fields

Revision ID: 005_infrastructure_audit
Revises: 004_pog_audit_fields
Create Date: 2026-07-20

Zachowuje w snapshotach analizy faktyczną powierzchnię strefy sieciowej oraz
źródło, pewność i ograniczenia reguły bufora. Dzięki temu wartość
``buildable_area_sqm`` jest odtwarzalna i audytowalna także po odczycie cache.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "005_infrastructure_audit"
down_revision: Union[str, None] = "004_pog_audit_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "infrastructure_records",
        sa.Column("zone_area_sqm", sa.Float(), nullable=True),
    )
    op.add_column(
        "infrastructure_records",
        sa.Column("rule_source", sa.String(length=1000), nullable=True),
    )
    op.add_column(
        "infrastructure_records",
        sa.Column("rule_confidence", sa.Float(), nullable=True),
    )
    op.add_column(
        "infrastructure_records",
        sa.Column("rule_note", sa.Text(), nullable=True),
    )
    op.add_column(
        "infrastructure_records",
        sa.Column(
            "affects_buildable_area",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("infrastructure_records", "affects_buildable_area")
    op.drop_column("infrastructure_records", "rule_note")
    op.drop_column("infrastructure_records", "rule_confidence")
    op.drop_column("infrastructure_records", "rule_source")
    op.drop_column("infrastructure_records", "zone_area_sqm")
