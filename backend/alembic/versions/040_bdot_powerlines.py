"""Wersjonowany import linii energetycznych BDOT10k (AU-201)."""
from alembic import op
from geoalchemy2 import Geometry
import sqlalchemy as sa

revision = "040_bdot_powerlines"
down_revision = "039_land_cover"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_import_jobs_kind", "import_jobs", type_="check")
    op.create_check_constraint("ck_import_jobs_kind", "import_jobs", "kind IN ('pog_commune', 'bdot_county')")
    op.create_table(
        "bdot_powerlines",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("data_release_id", sa.Integer(), sa.ForeignKey("data_releases.id"), nullable=False),
        sa.Column("local_id", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(160), nullable=False),
        sa.Column("existence_category", sa.String(40)),
        sa.Column("feature_version_at", sa.DateTime(timezone=True)),
        sa.Column("geometry", Geometry("MULTILINESTRING", srid=2180, spatial_index=False), nullable=False),
        sa.UniqueConstraint("data_release_id", "local_id", name="uq_bdot_powerlines_release_local_id"),
        sa.CheckConstraint("NOT ST_IsEmpty(geometry) AND ST_IsValid(geometry)", name="ck_bdot_powerlines_geometry"),
    )
    op.create_index("ix_bdot_powerlines_geometry_gist", "bdot_powerlines", ["geometry"], postgresql_using="gist")
    op.create_index("ix_bdot_powerlines_release", "bdot_powerlines", ["data_release_id"])


def downgrade() -> None:
    # Zachowane zlecenia BDOT muszą być wcześniej zarchiwizowane przez operatora;
    # CHECK zatrzyma rollback zamiast usuwać historię zleceń.
    op.drop_constraint("ck_import_jobs_kind", "import_jobs", type_="check")
    op.create_check_constraint("ck_import_jobs_kind", "import_jobs", "kind IN ('pog_commune')")
    op.drop_table("bdot_powerlines")
