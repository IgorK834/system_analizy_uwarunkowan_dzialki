"""Pipeline georeferencji rastrów planistycznych (COG).

Revision ID: 013_raster_assets
Revises: 012_pog_planning_act_kind
Create Date: 2026-07-25

Dodaje tabelę ``raster_assets`` przechowującą zgeoreferencjonowane rysunki planu
skonwertowane do Cloud Optimized GeoTIFF (COG). Raster służy do prezentacji i
wspomagania weryfikacji; dopóki ``review_status`` nie jest ``verified``, nie może
być źródłem precyzyjnych przecięć ani publikowany do warstwy mapy.

Zasięg jest przechowywany jako geometria POLYGON EPSG:2180 z indeksem GiST
(spójnie z resztą modelu), a nie jako luźne liczby.
"""

from typing import Sequence, Union

from alembic import op
import geoalchemy2
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "013_raster_assets"
down_revision: Union[str, None] = "012_pog_planning_act_kind"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_REVIEW_STATUS_CHECK = (
    "review_status IN ('unreviewed', 'verified', 'rejected', 'superseded')"
)


def upgrade() -> None:
    op.create_table(
        "raster_assets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("planning_act_version_id", sa.Integer(), nullable=True),
        sa.Column("source_artifact_id", sa.Integer(), nullable=False),
        sa.Column("cog_artifact_id", sa.Integer(), nullable=False),
        sa.Column("transform_method", sa.String(length=40), nullable=False),
        sa.Column(
            "control_points",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("rmse_m", sa.Float(), nullable=True),
        sa.Column("pixel_size", sa.Float(), nullable=True),
        sa.Column("nodata", sa.Float(), nullable=True),
        sa.Column("width_px", sa.Integer(), nullable=True),
        sa.Column("height_px", sa.Integer(), nullable=True),
        sa.Column(
            "bounds",
            geoalchemy2.Geometry(
                geometry_type="POLYGON", srid=2180, spatial_index=False
            ),
            nullable=False,
        ),
        sa.Column(
            "review_status",
            sa.String(length=20),
            server_default="unreviewed",
            nullable=False,
        ),
        sa.Column("qa_report", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["planning_act_version_id"], ["planning_act_versions.id"]
        ),
        sa.ForeignKeyConstraint(["source_artifact_id"], ["source_artifacts.id"]),
        sa.ForeignKeyConstraint(["cog_artifact_id"], ["source_artifacts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(_REVIEW_STATUS_CHECK, name="ck_raster_assets_review_status"),
        sa.CheckConstraint("rmse_m IS NULL OR rmse_m >= 0", name="ck_raster_assets_rmse"),
        sa.CheckConstraint(
            "NOT ST_IsEmpty(bounds)", name="ck_raster_assets_bounds_not_empty"
        ),
    )
    op.create_index(
        "ix_raster_assets_planning_act_version_id",
        "raster_assets",
        ["planning_act_version_id"],
        unique=False,
    )
    op.create_index(
        "ix_raster_assets_source_artifact_id",
        "raster_assets",
        ["source_artifact_id"],
        unique=False,
    )
    op.create_index(
        "ix_raster_assets_cog_artifact_id",
        "raster_assets",
        ["cog_artifact_id"],
        unique=False,
    )
    op.create_index(
        "ix_raster_assets_bounds_gist",
        "raster_assets",
        ["bounds"],
        unique=False,
        postgresql_using="gist",
    )


def downgrade() -> None:
    op.drop_index("ix_raster_assets_bounds_gist", table_name="raster_assets")
    op.drop_index(
        "ix_raster_assets_cog_artifact_id", table_name="raster_assets"
    )
    op.drop_index(
        "ix_raster_assets_source_artifact_id", table_name="raster_assets"
    )
    op.drop_index(
        "ix_raster_assets_planning_act_version_id", table_name="raster_assets"
    )
    op.drop_table("raster_assets")
