"""Cache i provenance wywołań modelu językowego dla bloków stref MPZP (PV3-13).

Revision ID: 029_mpzp_llm_extractions
Revises: 028_mpzp_parameter_condition
Create Date: 2026-10-05

Issue Task 20.13 nazywa tę migrację „027 po head 026”, ale numery 027 (PV3-04) i 028 (PV3-08)
zajęły wcześniejsze zadania serii, więc migracja dołącza się po faktycznym head ``028`` i niczego
w historii nie przepisuje.

Tabela ``mpzp_llm_extractions`` przechowuje WYŁĄCZNIE wyjście modelu i skróty wejścia: klucz
``cache_key`` (SHA-256 z ``document_sha256``, ``block_sha256``, ``prompt_version``,
``schema_version``, ``model_id`` i ``params_hash``; unikalny), te pola osobno, ``response_sha256``,
``response`` (JSONB), tokeny, opóźnienie, koszt szacowany, status (``ok``, ``rejected_schema``,
``error``) i ``created_at``. Nie ma kolumny na treść żądania ani na identyfikator działki, analizy
czy użytkownika. ``mpzp_parameters`` dostaje kolumny provenance wartości z modelu
(``review_status``, ``model_id``, ``prompt_version``, ``response_sha256``) i więzy: metoda
``llm_verified`` wymaga statusu ``ai_candidate``. Nowe kolumny są NULL-owalne; istniejące wiersze
nie są przeliczane. Rollback usuwa tabelę (cache jest odtwarzalny wywołaniem modelu) i kolumny;
provenance zostaje w ``mpzp_zones.result_snapshot``, który jest źródłem prawdy odczytu.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "029_mpzp_llm_extractions"
down_revision: Union[str, None] = "028_mpzp_parameter_condition"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mpzp_llm_extractions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cache_key", sa.String(length=64), nullable=False),
        sa.Column("document_sha256", sa.String(length=64), nullable=False),
        sa.Column("block_sha256", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=60), nullable=False),
        sa.Column("schema_version", sa.String(length=60), nullable=False),
        sa.Column("model_id", sa.String(length=64), nullable=False),
        sa.Column("params_hash", sa.String(length=64), nullable=False),
        sa.Column("response_sha256", sa.String(length=64), nullable=True),
        sa.Column("response", postgresql.JSONB(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("cost_estimate_usd", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error_code", sa.String(length=60), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("cache_key", name="uq_mpzp_llm_extractions_cache_key"),
        sa.CheckConstraint("status IN ('ok', 'rejected_schema', 'error')", name="ck_mpzp_llm_extractions_status"),
    )
    op.create_index("ix_mpzp_llm_extractions_document_sha256", "mpzp_llm_extractions", ["document_sha256"])
    op.create_index("ix_mpzp_llm_extractions_created_at", "mpzp_llm_extractions", ["created_at"])
    op.add_column("mpzp_parameters", sa.Column("review_status", sa.String(length=20), nullable=True))
    op.add_column("mpzp_parameters", sa.Column("model_id", sa.String(length=64), nullable=True))
    op.add_column("mpzp_parameters", sa.Column("prompt_version", sa.String(length=60), nullable=True))
    op.add_column("mpzp_parameters", sa.Column("response_sha256", sa.String(length=64), nullable=True))
    op.create_check_constraint(
        "ck_mpzp_parameters_llm_candidate",
        "mpzp_parameters",
        "extraction_method IS DISTINCT FROM 'llm_verified' OR review_status = 'ai_candidate'",
    )


def downgrade() -> None:
    op.drop_constraint("ck_mpzp_parameters_llm_candidate", "mpzp_parameters", type_="check")
    op.drop_column("mpzp_parameters", "response_sha256")
    op.drop_column("mpzp_parameters", "prompt_version")
    op.drop_column("mpzp_parameters", "model_id")
    op.drop_column("mpzp_parameters", "review_status")
    op.drop_index("ix_mpzp_llm_extractions_created_at", table_name="mpzp_llm_extractions")
    op.drop_index("ix_mpzp_llm_extractions_document_sha256", table_name="mpzp_llm_extractions")
    op.drop_table("mpzp_llm_extractions")
