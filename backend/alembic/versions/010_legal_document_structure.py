"""Cytowalna, hierarchiczna struktura dokumentów prawnych.

Revision ID: 010_legal_document_structure
Revises: 009_import_pipeline_fields
Create Date: 2026-07-24

Istniejący płaski ``legal_units`` nie był używany przez kod aplikacji, ale
migracja zachowuje jego dane: każdy rekord staje się jednostką najgłębszego
rozpoznanego typu, a dotychczasowy ``text`` trafia do ``source_text``.
Nie powstaje równoległa tabela, dzięki czemu wszystkie przyszłe odwołania mają
jedno kanoniczne miejsce.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "010_legal_document_structure"
down_revision: Union[str, None] = "009_import_pipeline_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DOCUMENT_TYPE_CHECK = (
    "document_type IS NULL OR document_type IN "
    "('uchwala', 'zalacznik_tekstowy', 'rysunek', 'uzasadnienie')"
)


def upgrade() -> None:
    op.create_check_constraint(
        "ck_source_documents_document_type",
        "source_documents",
        _DOCUMENT_TYPE_CHECK,
    )

    op.add_column(
        "document_versions",
        sa.Column("media_type", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "document_versions",
        sa.Column("extraction_method", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "document_versions",
        sa.Column("ocr_engine_version", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "document_versions",
        sa.Column("quality_score", sa.Float(), nullable=True),
    )
    op.create_check_constraint(
        "ck_document_versions_quality_score",
        "document_versions",
        "quality_score IS NULL OR (quality_score >= 0 AND quality_score <= 1)",
    )

    op.add_column(
        "document_pages",
        sa.Column(
            "blocks",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )

    op.add_column(
        "legal_units",
        sa.Column("unit_type", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "legal_units",
        sa.Column("number", sa.String(length=60), nullable=True),
    )
    op.add_column(
        "legal_units",
        sa.Column("parent_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "legal_units",
        sa.Column("order_index", sa.Integer(), nullable=True),
    )
    op.add_column(
        "legal_units",
        sa.Column("page_from", sa.Integer(), nullable=True),
    )
    op.add_column(
        "legal_units",
        sa.Column("page_to", sa.Integer(), nullable=True),
    )
    op.add_column(
        "legal_units",
        sa.Column("source_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "legal_units",
        sa.Column("normalized_text", sa.Text(), nullable=True),
    )

    op.execute(
        """
        UPDATE legal_units
        SET unit_type = CASE
                WHEN position IS NOT NULL THEN 'position'
                WHEN point IS NOT NULL THEN 'point'
                WHEN section IS NOT NULL THEN 'section'
                WHEN paragraph IS NOT NULL THEN 'paragraph'
                WHEN chapter IS NOT NULL THEN 'chapter'
                ELSE 'document_fragment'
            END,
            number = COALESCE(position, point, section, paragraph, chapter),
            source_text = COALESCE(text, '')
        """
    )
    op.execute(
        """
        WITH ordered AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY document_version_id ORDER BY id
                   ) - 1 AS position
            FROM legal_units
        )
        UPDATE legal_units
        SET order_index = ordered.position
        FROM ordered
        WHERE legal_units.id = ordered.id
        """
    )
    op.alter_column("legal_units", "unit_type", nullable=False)
    op.alter_column("legal_units", "order_index", nullable=False)
    op.alter_column("legal_units", "source_text", nullable=False)

    op.create_foreign_key(
        "fk_legal_units_parent_id",
        "legal_units",
        "legal_units",
        ["parent_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_legal_units_parent_id",
        "legal_units",
        ["parent_id"],
        unique=False,
    )
    op.create_check_constraint(
        "ck_legal_units_order_index",
        "legal_units",
        "order_index >= 0",
    )
    op.create_check_constraint(
        "ck_legal_units_page_from",
        "legal_units",
        "page_from IS NULL OR page_from >= 1",
    )
    op.create_check_constraint(
        "ck_legal_units_page_to",
        "legal_units",
        "page_to IS NULL OR page_to >= 1",
    )
    op.create_check_constraint(
        "ck_legal_units_page_range",
        "legal_units",
        "page_from IS NULL OR page_to IS NULL OR page_to >= page_from",
    )

    for column in ("chapter", "paragraph", "section", "point", "position", "text"):
        op.drop_column("legal_units", column)


def downgrade() -> None:
    for name in ("chapter", "paragraph", "section", "point", "position"):
        op.add_column(
            "legal_units",
            sa.Column(name, sa.String(length=60), nullable=True),
        )
    op.add_column("legal_units", sa.Column("text", sa.Text(), nullable=True))
    op.execute(
        """
        UPDATE legal_units
        SET chapter = CASE WHEN unit_type = 'chapter' THEN number END,
            paragraph = CASE WHEN unit_type = 'paragraph' THEN number END,
            section = CASE WHEN unit_type = 'section' THEN number END,
            point = CASE WHEN unit_type = 'point' THEN number END,
            position = CASE WHEN unit_type = 'position' THEN number END,
            text = source_text
        """
    )

    op.drop_constraint(
        "ck_legal_units_page_range", "legal_units", type_="check"
    )
    op.drop_constraint("ck_legal_units_page_to", "legal_units", type_="check")
    op.drop_constraint("ck_legal_units_page_from", "legal_units", type_="check")
    op.drop_constraint(
        "ck_legal_units_order_index", "legal_units", type_="check"
    )
    op.drop_index("ix_legal_units_parent_id", table_name="legal_units")
    op.drop_constraint(
        "fk_legal_units_parent_id", "legal_units", type_="foreignkey"
    )
    for column in (
        "normalized_text",
        "source_text",
        "page_to",
        "page_from",
        "order_index",
        "parent_id",
        "number",
        "unit_type",
    ):
        op.drop_column("legal_units", column)

    op.drop_column("document_pages", "blocks")
    op.drop_constraint(
        "ck_document_versions_quality_score",
        "document_versions",
        type_="check",
    )
    for column in (
        "quality_score",
        "ocr_engine_version",
        "extraction_method",
        "media_type",
    ):
        op.drop_column("document_versions", column)
    op.drop_constraint(
        "ck_source_documents_document_type",
        "source_documents",
        type_="check",
    )
