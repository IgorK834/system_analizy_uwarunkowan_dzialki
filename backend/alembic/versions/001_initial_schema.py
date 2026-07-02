"""initial_schema

Revision ID: 001_initial_schema
Revises: None
Create Date: 2026-07-02
"""
from typing import Sequence, Union

from alembic import op
import geoalchemy2
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "parcels",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("parcel_identifier", sa.String(length=50), nullable=False),
        sa.Column(
            "geometry",
            geoalchemy2.Geometry(
                geometry_type="MULTIPOLYGON",
                srid=2180,
                spatial_index=False,
            ),
            nullable=False,
        ),
        sa.Column("area_sqm", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("parcel_identifier"),
    )
    op.create_index(
        "ix_parcels_parcel_identifier",
        "parcels",
        ["parcel_identifier"],
        unique=False,
    )
    op.create_index(
        "ix_parcels_geometry_gist",
        "parcels",
        ["geometry"],
        unique=False,
        postgresql_using="gist",
    )

    op.create_table(
        "analyses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("parcel_id", sa.Integer(), nullable=False),
        sa.Column(
            "analyzed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("cache_valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("buildable_area_sqm", sa.Float(), nullable=True),
        sa.Column("warnings", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["parcel_id"], ["parcels.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_analyses_parcel_id", "analyses", ["parcel_id"], unique=False)

    op.create_table(
        "mpzp_zones",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("analysis_id", sa.Integer(), nullable=False),
        sa.Column("zone_symbol", sa.String(length=50), nullable=False),
        sa.Column("primary_use", sa.String(length=255), nullable=True),
        sa.Column("intersection_area_sqm", sa.Float(), nullable=True),
        sa.Column("intersection_pct", sa.Float(), nullable=True),
        sa.Column("is_dominant", sa.Boolean(), nullable=False),
        sa.Column("source_url", sa.String(length=1000), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["analysis_id"], ["analyses.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_mpzp_zones_analysis_id",
        "mpzp_zones",
        ["analysis_id"],
        unique=False,
    )

    op.create_table(
        "pog_data",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("analysis_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("planning_zone", sa.String(length=120), nullable=True),
        sa.Column("ouz_intersection_area_sqm", sa.Float(), nullable=True),
        sa.Column("touches_ouz_boundary", sa.Boolean(), nullable=False),
        sa.Column("source_url", sa.String(length=1000), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["analysis_id"], ["analyses.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pog_data_analysis_id", "pog_data", ["analysis_id"], unique=False)

    op.create_table(
        "infrastructure_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("analysis_id", sa.Integer(), nullable=False),
        sa.Column("network_type", sa.String(length=80), nullable=False),
        sa.Column("buffer_m", sa.Float(), nullable=True),
        sa.Column("source_url", sa.String(length=1000), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("manual_review_required", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["analysis_id"], ["analyses.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_infrastructure_records_analysis_id",
        "infrastructure_records",
        ["analysis_id"],
        unique=False,
    )

    op.create_table(
        "risk_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("analysis_id", sa.Integer(), nullable=False),
        sa.Column("risk_type", sa.String(length=80), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source_url", sa.String(length=1000), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("manual_review_required", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["analysis_id"], ["analyses.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_risk_records_analysis_id",
        "risk_records",
        ["analysis_id"],
        unique=False,
    )

    op.create_table(
        "source_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("analysis_id", sa.Integer(), nullable=False),
        sa.Column("source_name", sa.String(length=120), nullable=False),
        sa.Column("source_url", sa.String(length=1000), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("manual_review_required", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["analysis_id"], ["analyses.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_source_records_analysis_id",
        "source_records",
        ["analysis_id"],
        unique=False,
    )

    op.create_table(
        "mpzp_parameters",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("mpzp_zone_id", sa.Integer(), nullable=False),
        sa.Column("parameter_name", sa.String(length=120), nullable=False),
        sa.Column("normalized_value", sa.String(length=255), nullable=True),
        sa.Column("unit", sa.String(length=30), nullable=True),
        sa.Column("source_fragment", sa.Text(), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("manual_review_required", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["mpzp_zone_id"], ["mpzp_zones.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_mpzp_parameters_mpzp_zone_id",
        "mpzp_parameters",
        ["mpzp_zone_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_mpzp_parameters_mpzp_zone_id", table_name="mpzp_parameters")
    op.drop_table("mpzp_parameters")
    op.drop_index("ix_source_records_analysis_id", table_name="source_records")
    op.drop_table("source_records")
    op.drop_index("ix_risk_records_analysis_id", table_name="risk_records")
    op.drop_table("risk_records")
    op.drop_index(
        "ix_infrastructure_records_analysis_id",
        table_name="infrastructure_records",
    )
    op.drop_table("infrastructure_records")
    op.drop_index("ix_pog_data_analysis_id", table_name="pog_data")
    op.drop_table("pog_data")
    op.drop_index("ix_mpzp_zones_analysis_id", table_name="mpzp_zones")
    op.drop_table("mpzp_zones")
    op.drop_index("ix_analyses_parcel_id", table_name="analyses")
    op.drop_table("analyses")
    op.drop_index("ix_parcels_geometry_gist", table_name="parcels")
    op.drop_index("ix_parcels_parcel_identifier", table_name="parcels")
    op.drop_table("parcels")
