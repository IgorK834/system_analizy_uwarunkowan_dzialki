"""Dłuższy symbol strefy ręcznie wskazanej przez użytkownika (PV3-04).

Revision ID: 027_zone_symbol_length
Revises: 026_section_quality_matrix
Create Date: 2026-10-02

Symbole stref w planach i odpowiedziach KIMPZP bywają dłuższe niż 20 znaków
(``22 KD G1/2(Z1/4)``), a forma kanoniczna dopuszcza do 40 znaków ze spacjami i
przecinkami. ``analyses.resolved_zone_symbol`` miało ``VARCHAR(20)``, więc zapis
ręcznego symbolu poprawnego wg nowej reguły kończyłby się błędem bazy. Kolumna jest
poszerzana do 50 znaków (tyle ma ``mpzp_zones.zone_symbol``); zmiana jest
bezstratna i nie dotyka istniejących wartości — zapisane symbole nie zmieniają
postaci. Rollback zawęża kolumnę do 20 znaków i jest możliwy tylko wtedy, gdy żaden
zapisany symbol nie jest dłuższy (w przeciwnym razie PostgreSQL odmówi zmiany).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "027_zone_symbol_length"
down_revision: Union[str, None] = "026_section_quality_matrix"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "analyses",
        "resolved_zone_symbol",
        existing_type=sa.String(length=20),
        type_=sa.String(length=50),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "analyses",
        "resolved_zone_symbol",
        existing_type=sa.String(length=50),
        type_=sa.String(length=20),
        existing_nullable=True,
    )
