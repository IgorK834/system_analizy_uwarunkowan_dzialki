"""Rejestr zużycia modelu językowego dla twardych limitów dobowych i miesięcznych (PV3-15).

Revision ID: 030_mpzp_llm_usage
Revises: 029_mpzp_llm_extractions
Create Date: 2026-10-05

Issue Task 20.15 każe dodawać migracje „po aktualnym head”; head to ``029_mpzp_llm_extractions``
(PV3-13). Tabela ``mpzp_llm_usage`` ma jeden wiersz na żądanie do dostawcy: rezerwację przed
wysłaniem i rozliczenie po odpowiedzi (tokeny, koszt szacowany, kod wyniku). Nie ma kolumn na
treść żądania ani identyfikatory działki, analizy czy użytkownika. Migracja jest addytywna;
rollback usuwa tabelę (limity dobowe i miesięczne liczą się wtedy od zera — zachowanie zapisane w
ADR-012, aneks PV3-15).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "030_mpzp_llm_usage"
down_revision: Union[str, None] = "029_mpzp_llm_extractions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mpzp_llm_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("model_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reserved_tokens", sa.Integer(), nullable=False),
        sa.Column("reserved_cost_usd", sa.Float(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("outcome", sa.String(length=60), nullable=True),
        sa.CheckConstraint("status IN ('reserved', 'settled', 'released')", name="ck_mpzp_llm_usage_status"),
    )
    op.create_index("ix_mpzp_llm_usage_created_at", "mpzp_llm_usage", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_mpzp_llm_usage_created_at", table_name="mpzp_llm_usage")
    op.drop_table("mpzp_llm_usage")
