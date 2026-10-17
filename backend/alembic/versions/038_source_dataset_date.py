"""Daty zestawów danych w rejestrze źródeł (AU-111); starsze zapisy pozostają nieznane."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "038_source_dataset_date"
down_revision = "037_pog_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("source_records", sa.Column("dataset_metadata", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("source_records", "dataset_metadata")
