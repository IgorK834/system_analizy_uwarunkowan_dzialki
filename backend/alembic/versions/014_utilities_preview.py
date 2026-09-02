"""Snapshot statusu podglądu uzbrojenia KIUT.

Revision ID: 014_utilities_preview
Revises: 013_raster_assets
Create Date: 2026-09-02

Status pokrycia jest częścią snapshotu analizy, aby odpowiedź z cache i raport
PDF nie odpytwały ponownie publicznej usługi WMS i prezentowały ten sam wynik.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "014_utilities_preview"
down_revision: Union[str, None] = "013_raster_assets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "analyses",
        sa.Column(
            "utilities_preview",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("analyses", "utilities_preview")
