"""Cytowalne evidence parametrów MPZP w snapshotcie analizy (BK-203).

Revision ID: 019_mpzp_parameter_evidence
Revises: 018_mpzp_vector_zones

Każdy wiersz ``mpzp_parameters`` zachowuje wartość surową, segment i jednostkę
redakcyjną uchwały, SHA-256 dokumentu, wersję dokumentu, wersję parsera, metodę
ekstrakcji oraz grupę konfliktu. Sprzeczne kandydatury są osobnymi wierszami
ze wspólnym ``conflict_group_id``. Starsze wiersze pozostają bez evidence.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "019_mpzp_parameter_evidence"
down_revision: Union[str, None] = "018_mpzp_vector_zones"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = (
    ("raw_value", sa.Text()),
    ("segment_id", sa.String(120)),
    ("legal_unit_id", sa.Integer()),
    ("document_sha256", sa.String(64)),
    ("document_version_id", sa.Integer()),
    ("parser_version", sa.String(60)),
    ("extraction_method", sa.String(20)),
    ("conflict_group_id", sa.String(300)),
)


def upgrade() -> None:
    for name, column in COLUMNS:
        op.add_column("mpzp_parameters", sa.Column(name, column, nullable=True))
    op.create_index(
        "ix_mpzp_parameters_conflict_group_id", "mpzp_parameters", ["conflict_group_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_mpzp_parameters_conflict_group_id", table_name="mpzp_parameters")
    for name, _column in reversed(COLUMNS):
        op.drop_column("mpzp_parameters", name)
