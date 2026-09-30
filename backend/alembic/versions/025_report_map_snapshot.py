"""Zamrożony snapshot map raportu PDF (BK-503, ADR-010).

Revision ID: 025_report_map_snapshot
Revises: 024_pog_area_summaries
Create Date: 2026-09-29

``analyses.report_map_snapshot`` (JSONB) przechowuje specyfikację map raportu
zamrożoną przy zapisie analizy: obrys działki i geometrie analizowanych stref
oraz ryzyk w ``EPSG:2180`` (przycięte do kadru, 1 cm), kadr metryczny,
podziałkę, tryb tematyczny, kolejność warstw, style z wersją stylu POG, font,
identyfikatory wydań, daty danych i hash semantyczny. Raport renderuje mapy
wyłącznie z tego zapisu — bez pobierania WMS.

Istniejące analizy NIE są uzupełniane (wartość ``NULL``): zamrożenie wstecz
użyłoby bieżącej konfiguracji i udawałoby stan z chwili analizy. Raport takich
analiz odtwarza mapy z danych snapshotu analizy i oznacza to w PDF. Rollback
usuwa wyłącznie tę kolumnę.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "025_report_map_snapshot"
down_revision: Union[str, None] = "024_pog_area_summaries"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "analyses",
        sa.Column(
            "report_map_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("analyses", "report_map_snapshot")
