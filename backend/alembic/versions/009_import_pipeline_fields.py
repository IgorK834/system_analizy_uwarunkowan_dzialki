"""Pola potrzebne przez wersjonowany import MPZP.

Revision ID: 009_import_pipeline_fields
Revises: 008_address_search_index
Create Date: 2026-07-24
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "009_import_pipeline_fields"
down_revision: Union[str, None] = "008_address_search_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "land_use_areas",
        sa.Column(
            "raw_attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "planning_act_versions",
        sa.Column("resolution_number", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "planning_act_versions",
        sa.Column("resolution_date", sa.Date(), nullable=True),
    )
    op.add_column(
        "planning_act_versions",
        sa.Column("name", sa.Text(), nullable=True),
    )
    op.add_column(
        "planning_act_versions",
        sa.Column(
            "manual_review_required",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("planning_act_versions", "manual_review_required")
    op.drop_column("planning_act_versions", "name")
    op.drop_column("planning_act_versions", "resolution_date")
    op.drop_column("planning_act_versions", "resolution_number")
    op.drop_column("land_use_areas", "raw_attributes")
