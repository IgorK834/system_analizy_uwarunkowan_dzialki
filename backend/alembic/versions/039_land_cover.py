"""Pokrycie terenu BDOT10k i snapshot analizy (AU-204)."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from geoalchemy2 import Geometry

revision = "039_land_cover"
down_revision = "038_source_dataset_date"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("analyses", sa.Column("land_cover", postgresql.JSONB(), nullable=True))
    op.create_table(
        "bdot_landcover",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("data_release_id", sa.Integer(), sa.ForeignKey("data_releases.id"), nullable=False),
        sa.Column("local_id", sa.String(64), nullable=False),
        sa.Column("class_code", sa.String(9), nullable=False),
        sa.Column("kind", sa.String(160)),
        sa.Column("feature_version_at", sa.DateTime(timezone=True)),
        sa.Column("geometry", Geometry("MULTIPOLYGON", srid=2180, spatial_index=False), nullable=False),
        sa.UniqueConstraint("data_release_id", "local_id", name="uq_bdot_landcover_release_local_id"),
        sa.CheckConstraint("NOT ST_IsEmpty(geometry) AND ST_IsValid(geometry)", name="ck_bdot_landcover_geometry"),
    )
    op.create_index("ix_bdot_landcover_geometry_gist", "bdot_landcover", ["geometry"], postgresql_using="gist")
    op.create_index("ix_bdot_landcover_release_class", "bdot_landcover", ["data_release_id", "class_code"])


def downgrade() -> None:
    op.drop_table("bdot_landcover")
    op.drop_column("analyses", "land_cover")
