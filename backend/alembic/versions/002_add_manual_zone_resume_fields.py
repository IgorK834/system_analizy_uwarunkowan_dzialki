"""add_manual_zone_resume_fields

Revision ID: 002_manual_zone_resume
Revises: 001_initial_schema
Create Date: 2026-07-15

Dodaje do ``analyses`` pola potrzebne do wznowienia analizy, gdy discover_mpzp
zwraca brak_wektorow=True (gmina nie udostępnia wektorowych danych MPZP).
Nie przechowujemy bytes dokumentu — tylko URL do ponownego pobrania przy
wznowieniu; magazyn obiektowy oryginałów jest poza zakresem tego zadania
(patrz ANALIZA_ARCHITEKTURY_I_PLAN.md, sekcja E, ADR-004/ADR-005).

Uwaga: Revision ID jest skrócony względem nazwy pliku, bo kolumna
alembic_version.version_num w tej bazie ma limit VARCHAR(32).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "002_manual_zone_resume"
down_revision: Union[str, None] = "001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "analyses",
        sa.Column("pending_uchwala_url", sa.String(length=1000), nullable=True),
    )
    op.add_column(
        "analyses",
        sa.Column("pending_plan_id", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "analyses",
        sa.Column(
            "pending_zone_symbol_candidates",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "analyses",
        sa.Column("resolved_zone_symbol", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("analyses", "resolved_zone_symbol")
    op.drop_column("analyses", "pending_zone_symbol_candidates")
    op.drop_column("analyses", "pending_plan_id")
    op.drop_column("analyses", "pending_uchwala_url")
