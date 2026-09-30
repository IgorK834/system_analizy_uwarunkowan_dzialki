"""Trwała macierz kompletności i świeżości sekcji analizy (BK-504, ADR-011).

Revision ID: 026_section_quality_matrix
Revises: 025_report_map_snapshot
Create Date: 2026-09-29

``analyses.section_quality`` (JSONB) przechowuje macierz jakości sekcji
wystawioną przy zapisie analizy: dla każdej sekcji status według kontraktu
źródła, identyfikator źródła z katalogu, czas pobrania, wydanie i wersję danych,
flagę ręcznej weryfikacji, świeżość (fresh/stale/unknown) względem jawnej reguły
źródła i punktu odniesienia ``analyzed_at`` oraz wersję polityki i kody powodów.
Zapis niesie ``matrix_sha256`` — hash merytoryczny oceny, niezależny od chwili
eksportu raportu.

Istniejące analizy NIE są uzupełniane (wartość ``NULL``): ocena wstecz użyłaby
bieżącej polityki i udawałaby ocenę z chwili analizy. Odczyt takiego zapisu
odtwarza macierz z ``origin=reconstructed`` bez zapisu do bazy. Rollback usuwa
wyłącznie tę kolumnę.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "026_section_quality_matrix"
down_revision: Union[str, None] = "025_report_map_snapshot"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "analyses",
        sa.Column(
            "section_quality",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("analyses", "section_quality")
