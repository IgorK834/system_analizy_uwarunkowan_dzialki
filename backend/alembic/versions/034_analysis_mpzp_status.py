"""Snapshot rozłącznego stanu ustalenia MPZP (AU-103, Task 22.3).

Revision ID: 034_mpzp_status
Revises: 033_mpzp_acts
Create Date: 2026-10-10

``analyses.mpzp_status`` przechowuje ``AnalyzeResponse.mpzp_status``: jeden z sześciu rozłącznych stanów
(``available|ambiguous|no_match|no_coverage|unavailable|unknown``) z kodem powodu, stanem kanałów KIMPZP i RU,
aktami odrzuconymi z diagnostyką oraz źródłem kanału rozstrzygającego (podstawa ``source_id`` w macierzy jakości).

Kolumna jest nullable i nie jest uzupełniana wstecz: ``NULL`` oznacza zapis sprzed AU-103 i jest odczytywany jako
brak stanu — UI i PDF pokazują wtedy osobny komunikat „zapis sprzed wprowadzenia stanów”, nie „nie znaleziono
planu”. Rollback usuwa kolumnę.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "034_mpzp_status"
down_revision: Union[str, None] = "033_mpzp_acts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "analyses",
        sa.Column(
            "mpzp_status",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("analyses", "mpzp_status")
