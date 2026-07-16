"""add_source_record_audit_fields

Revision ID: 003_source_audit
Revises: 002_manual_zone_resume
Create Date: 2026-07-16

``response_status`` jest tekstem, ponieważ rekord audytowy przechowuje nie
tylko kody HTTP, ale też semantyczne statusy sekcji bez odpowiedzi HTTP.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "003_source_audit"
down_revision: Union[str, None] = "002_manual_zone_resume"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "source_records",
        sa.Column("response_status", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "source_records",
        sa.Column(
            "warnings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "source_records",
        sa.Column("checksum", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("source_records", "checksum")
    op.drop_column("source_records", "warnings")
    op.drop_column("source_records", "response_status")
