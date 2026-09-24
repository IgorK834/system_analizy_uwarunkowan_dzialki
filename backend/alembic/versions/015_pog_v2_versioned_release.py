"""Pełny kontrakt POG v2 i wersjonowane obiekty APP.

Revision ID: 015_pog_v2_release
Revises: 014_utilities_preview

Istniejące rekordy ``pog_data`` pozostają snapshotami v1 oznaczonymi jako
częściowe. Migracja nie rekonstruuje nieistniejących stref ani parametrów.
Rollback usuwa jedynie nowe pola/tabelę; historyczne kolumny v1 pozostają.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "015_pog_v2_release"
down_revision: Union[str, None] = "014_utilities_preview"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("planning_act_versions", sa.Column("raw_legal_status", sa.Text(), nullable=True))
    op.add_column("planning_act_versions", sa.Column("object_version_id", sa.String(120), nullable=True))
    for name, column in (
        ("feature_identifier", sa.String(500)),
        ("feature_version", sa.String(120)),
        ("act_reference", sa.String(1000)),
        ("source_reference", sa.String(1000)),
        ("raw_legal_status", sa.Text()),
        ("symbol", sa.String(80)),
        ("label", sa.Text()),
        ("parameters", postgresql.JSONB(astext_type=sa.Text())),
        ("primary_profiles", postgresql.JSONB(astext_type=sa.Text())),
        ("additional_profiles", postgresql.JSONB(astext_type=sa.Text())),
        ("raw_attributes", postgresql.JSONB(astext_type=sa.Text())),
    ):
        op.add_column("planning_features", sa.Column(name, column, nullable=True))
    op.create_unique_constraint(
        "uq_planning_features_version_identifier",
        "planning_features",
        ["planning_act_version_id", "feature_identifier"],
    )
    op.create_table(
        "pog_formal_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("planning_act_version_id", sa.Integer(), sa.ForeignKey("planning_act_versions.id"), nullable=False),
        sa.Column("document_identifier", sa.String(500), nullable=False),
        sa.Column("document_version", sa.String(120), nullable=True),
        sa.Column("act_reference", sa.String(1000), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("link", sa.String(1000), nullable=True),
        sa.Column("source_reference", sa.String(1000), nullable=True),
        sa.Column("raw_attributes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("planning_act_version_id", "document_identifier", name="uq_pog_documents_version_identifier"),
    )
    op.create_index("ix_pog_formal_documents_planning_act_version_id", "pog_formal_documents", ["planning_act_version_id"])

    op.add_column("pog_data", sa.Column("schema_version", sa.String(20), server_default="1.0", nullable=False))
    op.add_column("pog_data", sa.Column("result_v2", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("pog_data", sa.Column("legacy_partial", sa.Boolean(), server_default=sa.true(), nullable=False))
    op.execute("UPDATE pog_data SET schema_version='1.0', legacy_partial=true")

    op.add_column("analyses", sa.Column("data_release_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("analyses", sa.Column("result_contract_version", sa.String(20), nullable=True))
    op.add_column("analyses", sa.Column("cache_signature", sa.String(128), nullable=True))
    op.create_index("ix_analyses_cache_signature", "analyses", ["cache_signature"])
    for name, column in (
        ("source_id", sa.String(80)),
        ("source_version", sa.String(120)),
        ("artifact_sha256", sa.String(64)),
        ("data_release_id", sa.Integer()),
        ("act_version", sa.String(120)),
    ):
        op.add_column("source_records", sa.Column(name, column, nullable=True))


def downgrade() -> None:
    for name in ("act_version", "data_release_id", "artifact_sha256", "source_version", "source_id"):
        op.drop_column("source_records", name)
    op.drop_index("ix_analyses_cache_signature", table_name="analyses")
    for name in ("cache_signature", "result_contract_version", "data_release_ids"):
        op.drop_column("analyses", name)
    for name in ("legacy_partial", "result_v2", "schema_version"):
        op.drop_column("pog_data", name)
    op.drop_index("ix_pog_formal_documents_planning_act_version_id", table_name="pog_formal_documents")
    op.drop_table("pog_formal_documents")
    op.drop_constraint("uq_planning_features_version_identifier", "planning_features", type_="unique")
    for name in (
        "raw_attributes", "additional_profiles", "primary_profiles", "parameters", "label", "symbol",
        "raw_legal_status", "source_reference", "act_reference", "feature_version", "feature_identifier",
    ):
        op.drop_column("planning_features", name)
    op.drop_column("planning_act_versions", "object_version_id")
    op.drop_column("planning_act_versions", "raw_legal_status")
