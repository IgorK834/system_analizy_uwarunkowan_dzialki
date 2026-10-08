"""Snapshot punktowego rozpoznania aktów MPZP z KIMPZP (AU-004, Task 21.4).

Revision ID: 032_mpzp_discovery
Revises: 031_source_url_text
Create Date: 2026-10-06

``analyses.mpzp_discovery`` przechowuje sekcję ``MpzpDiscoverySection`` odpowiedzi
``/analyze``: listę aktów wskazanych przez KIMPZP w punktach działki (numer uchwały,
daty, linki tekstu, legendy i BIP, zmiany planu), rozłączny status źródła
(``available|no_match|no_coverage|unavailable|unknown``) i flagę wielu aktów.

Kolumna jest nullable i nie jest uzupełniana wstecz: ``NULL`` oznacza zapis sprzed
AU-004 i jest odczytywany jako brak sekcji (nie „brak planu”). Rollback usuwa kolumnę
— dane sekcji są wtedy tracone, pozostałe sekcje analizy pozostają bez zmian.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "032_mpzp_discovery"
down_revision: Union[str, None] = "031_source_url_text"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "analyses",
        sa.Column(
            "mpzp_discovery",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("analyses", "mpzp_discovery")
