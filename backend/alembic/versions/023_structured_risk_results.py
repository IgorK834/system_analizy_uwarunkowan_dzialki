"""Strukturalne wyniki powodzi i ochrony przyrody (BK-303).

Revision ID: 023_structured_risks
Revises: 022_analysis_terrain
Create Date: 2026-09-28

``risk_records`` dostaje typowane pola domenowe (sekcja, ID obiektu, severity,
klasa prawdopodobieństwa, okres powtarzalności, rodzaj i nazwa formy ochrony,
pole i udział przecięcia, styk granicy) oraz pełny snapshot ``RiskResult``.
``analyses.risk_sections`` przechowuje status i provenance sekcji ``flood`` i
``nature`` niezależnie od listy obiektów.

Stare wiersze NIE są uzupełniane parsowaniem tekstu ``description`` — pola
pozostają ``NULL`` (nieznane), a brak ``risk_sections`` jest odczytywany jako
status ``unknown``. Jedyna wartość ustalana dla starych wierszy to ``section``,
wyprowadzona ze strukturalnej kolumny ``risk_type`` (``flood``/``flood_zone`` →
``flood``, pozostałe → ``nature``).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "023_structured_risks"
down_revision: Union[str, None] = "022_analysis_terrain"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _risk_columns() -> tuple[sa.Column, ...]:
    return (
        sa.Column("section", sa.String(20), nullable=True),
        sa.Column("feature_id", sa.String(500), nullable=True),
        sa.Column("severity", sa.String(20), nullable=True),
        sa.Column("probability_class", sa.String(500), nullable=True),
        sa.Column("return_period_years", sa.Integer(), nullable=True),
        sa.Column("protection_type", sa.String(80), nullable=True),
        sa.Column("name", sa.String(500), nullable=True),
        sa.Column("intersection_area_sqm", sa.Float(), nullable=True),
        sa.Column("intersection_pct", sa.Float(), nullable=True),
        sa.Column("touches_boundary", sa.Boolean(), nullable=True),
        sa.Column("result_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def upgrade() -> None:
    for column in _risk_columns():
        op.add_column("risk_records", column)
    op.create_index("ix_risk_records_section", "risk_records", ["section"])
    op.execute(
        "UPDATE risk_records SET section = CASE "
        "WHEN risk_type IN ('flood', 'flood_zone') THEN 'flood' ELSE 'nature' END"
    )
    op.add_column(
        "analyses",
        sa.Column(
            "risk_sections",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("analyses", "risk_sections")
    op.drop_index("ix_risk_records_section", table_name="risk_records")
    for column in reversed(_risk_columns()):
        op.drop_column("risk_records", column.name)
