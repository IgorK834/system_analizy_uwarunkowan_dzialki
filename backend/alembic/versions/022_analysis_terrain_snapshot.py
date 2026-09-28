"""Snapshot rzeźby terenu (NMT) w wyniku analizy (BK-301/BK-302).

Revision ID: 022_analysis_terrain
Revises: 021_compatibility_assessment
Create Date: 2026-09-28

Kolumna ``analyses.terrain`` przechowuje pełną sekcję ``TerrainResult``
(Hmin/Hmax, deniwelacja, siatka, liczba próbek, status pokrycia, provenance
oraz pochodne rastra: spadek, klasy, ekspozycja i profil). Dzięki temu cache,
odczyt historyczny, wznowienie MPZP i raport PDF prezentują ten sam pomiar bez
ponownego odpytywania usług GUGiK.

Migracja jest addytywna i celowo NIE wypełnia istniejących wierszy: stare
snapshoty nie zawierały wartości NMT, więc pozostają ``NULL`` i są odczytywane
jako status ``unknown``. Dopisanie 0 m udawałoby zmierzony płaski teren.

``analyses.result_contract_version`` jest poszerzana z 20 do 64 znaków, bo
etykieta kontraktu zyskała człon ``+terrain-v1.0``. Downgrade przycina
etykietę przed zwężeniem kolumny — sygnatura cache (``cache_signature``) i tak
nie pasuje wtedy do kodu sprzed migracji, więc nie powstaje fałszywe trafienie.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "022_analysis_terrain"
down_revision: Union[str, None] = "021_compatibility_assessment"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "analyses",
        sa.Column(
            "terrain",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.alter_column(
        "analyses",
        "result_contract_version",
        existing_type=sa.String(20),
        type_=sa.String(64),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.execute(
        "UPDATE analyses SET result_contract_version = "
        "left(result_contract_version, 20) "
        "WHERE length(result_contract_version) > 20"
    )
    op.alter_column(
        "analyses",
        "result_contract_version",
        existing_type=sa.String(64),
        type_=sa.String(20),
        existing_nullable=True,
    )
    op.drop_column("analyses", "terrain")
