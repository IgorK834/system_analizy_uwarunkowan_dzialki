"""Lokalny indeks prefiksowego wyszukiwania adresów GUGiK.

Revision ID: 008_address_search_index
Revises: 007_versioned_data_model
Create Date: 2026-07-23

Rekordy indeksu należą do wersjonowanego ``data_release``. Nowe wydanie jest
budowane poza aktywnym zbiorem, a po kontroli jakości importer przełącza flagę
``data_releases.is_active`` w jednej transakcji. Dzięki temu API nigdy nie widzi
częściowo zaimportowanego słownika.
"""

from typing import Sequence, Union

from alembic import op
import geoalchemy2
import sqlalchemy as sa


revision: str = "008_address_search_index"
down_revision: Union[str, None] = "007_versioned_data_model"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # pg_trgm wspiera indeksowane wyszukiwanie prefiksów i fragmentów bez
    # uzależniania aplikacji od rozszerzenia unaccent.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "address_search_entries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("data_release_id", sa.Integer(), nullable=False),
        sa.Column("source_artifact_id", sa.Integer(), nullable=True),
        sa.Column("source_object_id", sa.String(length=255), nullable=False),
        sa.Column("source_version", sa.String(length=120), nullable=True),
        sa.Column("result_type", sa.String(length=30), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("normalized_label", sa.Text(), nullable=False),
        sa.Column("country", sa.String(length=80), nullable=True),
        sa.Column("voivodeship", sa.String(length=160), nullable=True),
        sa.Column("county", sa.String(length=160), nullable=True),
        sa.Column("municipality", sa.String(length=160), nullable=True),
        sa.Column("city", sa.String(length=200), nullable=True),
        sa.Column("street", sa.String(length=240), nullable=True),
        sa.Column("house_number", sa.String(length=80), nullable=True),
        sa.Column("postal_code", sa.String(length=20), nullable=True),
        sa.Column("teryt", sa.String(length=20), nullable=True),
        sa.Column("simc", sa.String(length=20), nullable=True),
        sa.Column("ulic", sa.String(length=20), nullable=True),
        sa.Column(
            "geometry",
            geoalchemy2.Geometry(
                geometry_type="POINT", srid=2180, spatial_index=False
            ),
            nullable=False,
        ),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "result_type IN ('city', 'street', 'house_number')",
            name="ck_address_search_entries_result_type",
        ),
        sa.CheckConstraint(
            "normalized_label <> ''",
            name="ck_address_search_entries_normalized_label",
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from",
            name="ck_address_search_entries_valid_range",
        ),
        sa.CheckConstraint(
            "NOT ST_IsEmpty(geometry)",
            name="ck_address_search_entries_geometry_not_empty",
        ),
        sa.ForeignKeyConstraint(
            ["data_release_id"], ["data_releases.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_artifact_id"], ["source_artifacts.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "data_release_id",
            "source_object_id",
            "result_type",
            name="uq_address_search_entries_release_object_type",
        ),
    )
    op.create_index(
        "ix_address_search_entries_release_type",
        "address_search_entries",
        ["data_release_id", "result_type"],
    )
    op.create_index(
        "ix_address_search_entries_teryt",
        "address_search_entries",
        ["teryt"],
    )
    op.create_index(
        "ix_address_search_entries_geometry_gist",
        "address_search_entries",
        ["geometry"],
        postgresql_using="gist",
    )
    op.create_index(
        "ix_address_search_entries_normalized_trgm",
        "address_search_entries",
        ["normalized_label"],
        postgresql_using="gin",
        postgresql_ops={"normalized_label": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index(
        "ix_address_search_entries_normalized_trgm",
        table_name="address_search_entries",
    )
    op.drop_index(
        "ix_address_search_entries_geometry_gist",
        table_name="address_search_entries",
    )
    op.drop_index(
        "ix_address_search_entries_teryt",
        table_name="address_search_entries",
    )
    op.drop_index(
        "ix_address_search_entries_release_type",
        table_name="address_search_entries",
    )
    op.drop_table("address_search_entries")

