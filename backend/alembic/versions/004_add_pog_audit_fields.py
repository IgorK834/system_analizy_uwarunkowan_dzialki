"""add_pog_audit_fields

Revision ID: 004_pog_audit_fields
Revises: 003_source_audit
Create Date: 2026-07-20

Rozszerza snapshot POG o wynik OUZ, dominującą strefę, zgodność z MPZP
i surowe atrybuty źródłowe. Istniejące kolumny, w tym odrębne pole
``ouz_intersection_area_sqm``, pozostają bez zmian. Domyślne wartości Boolean
chronią istniejące wiersze przed NULL podczas migracji.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "004_pog_audit_fields"
down_revision: Union[str, None] = "003_source_audit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "pog_data",
        sa.Column("zone_type", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "pog_data",
        sa.Column(
            "in_ouz",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.add_column(
        "pog_data",
        sa.Column("area_ratio", sa.Float(), nullable=True),
    )
    op.add_column(
        "pog_data",
        sa.Column(
            "in_downtown_area",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.add_column(
        "pog_data",
        sa.Column("uchwala_nr", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "pog_data",
        sa.Column("uchwala_date", sa.Date(), nullable=True),
    )
    op.add_column(
        "pog_data",
        sa.Column(
            "manual_review_required",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.add_column(
        "pog_data",
        sa.Column("conflict_with_mpzp", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "pog_data",
        sa.Column(
            "raw_attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("pog_data", "raw_attributes")
    op.drop_column("pog_data", "conflict_with_mpzp")
    op.drop_column("pog_data", "manual_review_required")
    op.drop_column("pog_data", "uchwala_date")
    op.drop_column("pog_data", "uchwala_nr")
    op.drop_column("pog_data", "in_downtown_area")
    op.drop_column("pog_data", "area_ratio")
    op.drop_column("pog_data", "in_ouz")
    op.drop_column("pog_data", "zone_type")
