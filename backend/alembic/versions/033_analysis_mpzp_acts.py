"""Snapshot aktów MPZP przecinających działkę (AU-101, Task 22.1).

Revision ID: 033_mpzp_acts
Revises: 032_mpzp_discovery
Create Date: 2026-10-10

``analyses.mpzp_acts`` przechowuje listę ``AnalyzeResponse.mpzp_acts[]``: akty MPZP z Rejestru Urbanistycznego i
KIMPZP scalone po numerze uchwały, z udziałem działki objętym granicą aktu (EPSG:2180), dokumentem, statusem
prawnym i pierwszeństwem informacyjnym (reguła ``mpzp-act-precedence/1.0``).

Kolumna jest nullable i nie jest uzupełniana wstecz: ``NULL`` oznacza zapis sprzed AU-101 i jest odczytywany
jako pusta lista bez stanu MPZP (patrz migracja 034) — nigdy jako „brak aktu”. Rollback usuwa kolumnę; dane
aktów są wtedy tracone, pozostałe sekcje analizy pozostają bez zmian.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "033_mpzp_acts"
down_revision: Union[str, None] = "032_mpzp_discovery"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "analyses",
        sa.Column(
            "mpzp_acts",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("analyses", "mpzp_acts")
