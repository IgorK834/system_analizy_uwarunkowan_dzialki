"""Przypięty dokument trybu ręcznego MPZP i nieustalony udział stref (BK-204).

Revision ID: 020_manual_zone_pending_doc
Revises: 019_mpzp_parameter_evidence

1. ``analysis_pending_documents`` — bajty, SHA-256 i wersja uchwały zapisane w
   chwili wstrzymania analizy (``waiting_for_zone_symbol``). Wznowienie parsuje
   wyłącznie ten artefakt, więc podmiana dokumentu pod tym samym URL nie zmienia
   wyniku. Rekord jest usuwany kaskadowo razem z analizą.
2. Strefy przypisane bez wektora (``manual_user_input`` i ``document_candidate``)
   miały technicznie wpisany udział 100% i pole całej działki — wartość nie była
   pomiarem. Migracja zamienia ją na ``NULL`` (udział nieustalony) w kolumnach i
   w ``result_snapshot``; downgrade odtwarza dawną konwencję (100% i pole
   działki), więc jest odwracalny.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "020_manual_zone_pending_doc"
down_revision: Union[str, None] = "019_mpzp_parameter_evidence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NON_VECTOR_METHODS = ("manual_user_input", "document_candidate")


def upgrade() -> None:
    op.create_table(
        "analysis_pending_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "analysis_id",
            sa.Integer(),
            sa.ForeignKey("analyses.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("requested_url", sa.String(1000), nullable=False),
        sa.Column("final_url", sa.String(1000), nullable=True),
        sa.Column("media_type", sa.String(120), nullable=False),
        sa.Column("filename", sa.String(500), nullable=True),
        sa.Column("content", postgresql.BYTEA(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column(
            "document_version_id",
            sa.Integer(),
            sa.ForeignKey("document_versions.id"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(content_sha256) = 64",
            name="ck_analysis_pending_documents_sha256",
        ),
        sa.CheckConstraint(
            "size_bytes >= 0", name="ck_analysis_pending_documents_size"
        ),
    )

    op.execute(
        sa.text(
            """
            UPDATE mpzp_zones
               SET intersection_area_sqm = NULL,
                   intersection_pct = NULL,
                   is_dominant = false,
                   result_snapshot = CASE
                       WHEN result_snapshot IS NULL THEN NULL
                       ELSE result_snapshot
                            || '{"intersection_area_sqm": null,
                                 "intersection_pct": null,
                                 "is_dominant": false}'::jsonb
                   END
             WHERE assignment_method IN ('manual_user_input', 'document_candidate')
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE mpzp_zones AS zone
               SET intersection_area_sqm = parcel.area_sqm,
                   intersection_pct = 100.0,
                   is_dominant = true,
                   result_snapshot = CASE
                       WHEN zone.result_snapshot IS NULL THEN NULL
                       ELSE (zone.result_snapshot - 'manual_selection')
                            || jsonb_build_object(
                                   'intersection_area_sqm', COALESCE(parcel.area_sqm, 0),
                                   'intersection_pct', 100.0,
                                   'is_dominant', true)
                   END
              FROM analyses AS analysis
              JOIN parcels AS parcel ON parcel.id = analysis.parcel_id
             WHERE zone.analysis_id = analysis.id
               AND zone.assignment_method IN ('manual_user_input', 'document_candidate')
            """
        )
    )
    op.drop_table("analysis_pending_documents")
