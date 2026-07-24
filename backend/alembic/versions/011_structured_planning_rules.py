"""Ustrukturyzowane reguły planistyczne z provenance i konfliktami.

Revision ID: 011_structured_planning_rules
Revises: 010_legal_document_structure
Create Date: 2026-07-24
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "011_structured_planning_rules"
down_revision: Union[str, None] = "010_legal_document_structure"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "planning_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("legal_unit_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("operator", sa.String(length=20), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("min_value", sa.Float(), nullable=True),
        sa.Column("max_value", sa.Float(), nullable=True),
        sa.Column("text_value", sa.Text(), nullable=True),
        sa.Column("unit", sa.String(length=40), nullable=True),
        sa.Column(
            "conditions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("source_text", sa.Text(), nullable=True),
        sa.Column("raw_value", sa.Text(), nullable=True),
        sa.Column("parser_version", sa.String(length=80), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column(
            "review_status",
            sa.String(length=20),
            server_default="unreviewed",
            nullable=False,
        ),
        sa.Column("conflict_group", sa.String(length=36), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["legal_unit_id"],
            ["legal_units.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "review_status IN ('unreviewed', 'verified', 'rejected', "
            "'superseded', 'ai_candidate')",
            name="ck_planning_rules_review_status",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_planning_rules_confidence",
        ),
        sa.CheckConstraint(
            "NOT ((source_text IS NULL OR btrim(source_text) = '') "
            "AND (review_status = 'verified' OR confidence > 0.8))",
            name="ck_planning_rules_evidence_required",
        ),
        sa.CheckConstraint(
            "min_value IS NULL OR max_value IS NULL OR min_value <= max_value",
            name="ck_planning_rules_value_range",
        ),
    )
    op.create_index(
        "ix_planning_rules_legal_unit_id",
        "planning_rules",
        ["legal_unit_id"],
        unique=False,
    )
    op.create_index(
        "ix_planning_rules_code",
        "planning_rules",
        ["code"],
        unique=False,
    )
    op.create_index(
        "ix_planning_rules_conflict_group",
        "planning_rules",
        ["conflict_group"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_planning_rules_conflict_group", table_name="planning_rules"
    )
    op.drop_index("ix_planning_rules_code", table_name="planning_rules")
    op.drop_index(
        "ix_planning_rules_legal_unit_id", table_name="planning_rules"
    )
    op.drop_table("planning_rules")
