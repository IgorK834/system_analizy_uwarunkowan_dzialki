"""Adresy źródeł i odnośniki zasilane z zewnątrz jako ``Text`` (AU-001, Task 21.1).

Revision ID: 031_source_url_text
Revises: 030_mpzp_llm_usage
Create Date: 2026-10-06

Adres zapytania NMT ``GetMinMaxByPolygon`` zawierał cały wielokąt działki (do kilku tysięcy
znaków), a ``source_records.source_url`` miało ``VARCHAR(1000)``. Zapis analizy działki o dużej
liczbie wierzchołków kończył się ``StringDataRightTruncation`` i HTTP 500 po 10–36 s pracy.

Migracja zmienia na ``TEXT`` wszystkie kolumny adresów i odnośników, których długość zależy od
zapytania lub od zewnętrznego źródła (``source_url``, ``uri``, ``requested_url``/``final_url``,
adresy dokumentów i odniesienia APP/CSW). Zmiana ``VARCHAR(n)`` → ``TEXT`` nie przepisuje tabel
i nie zmienia istniejących wartości. Pozostałe kolumny ``String(n)`` zasilane z zewnątrz są
przycinane przy zapisie (``ClippedString``) — to zmiana wyłącznie w aplikacji, bez migracji.

Rollback przywraca ``VARCHAR(n)``. PostgreSQL odmawia zawężenia kolumny, gdy któraś wartość jest
dłuższa, więc przed zmianą typu wartości dłuższe niż limit są przycinane do ``n`` znaków ze
znacznikiem ``…`` na końcu (wiersz zostaje, traci tylko końcówkę adresu), a liczba przyciętych
wierszy każdej kolumny trafia do logu migracji.
"""

import logging
from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "031_source_url_text"
down_revision: Union[str, None] = "030_mpzp_llm_usage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

# (tabela, kolumna, poprzedni limit, czy NULL jest dozwolone)
_URL_COLUMNS: tuple[tuple[str, str, int, bool], ...] = (
    ("source_records", "source_url", 1000, True),
    ("risk_records", "source_url", 1000, True),
    ("mpzp_zones", "source_url", 1000, True),
    ("infrastructure_records", "source_url", 1000, True),
    ("pog_data", "source_url", 1000, True),
    ("analyses", "pending_uchwala_url", 1000, True),
    ("analysis_pending_documents", "requested_url", 1000, False),
    ("analysis_pending_documents", "final_url", 1000, True),
    ("source_artifacts", "uri", 1000, False),
    ("planning_act_versions", "source_reference", 1000, True),
    ("planning_act_versions", "document_url", 1000, True),
    ("planning_features", "act_reference", 1000, True),
    ("planning_features", "source_reference", 1000, True),
    ("pog_formal_documents", "act_reference", 1000, True),
    ("pog_formal_documents", "link", 1000, True),
    ("pog_formal_documents", "source_reference", 1000, True),
    ("pog_act_metadata_records", "resource_identifier", 1000, True),
    ("pog_act_metadata_records", "metadata_url", 2000, True),
)


def upgrade() -> None:
    for table, column, limit, nullable in _URL_COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=sa.String(length=limit),
            type_=sa.Text(),
            existing_nullable=nullable,
        )


def downgrade() -> None:
    bind = None if context.is_offline_mode() else op.get_bind()
    for table, column, limit, nullable in _URL_COLUMNS:
        # Identyfikatory pochodzą ze stałej krotki powyżej, nie z danych wejściowych.
        too_long = f"char_length({column}) > {limit}"
        if bind is not None:
            clipped = bind.execute(
                sa.text(f"SELECT count(*) FROM {table} WHERE {too_long}")
            ).scalar_one()
            if clipped:
                logger.warning(
                    "031 downgrade: %s.%s — przycięto %d wartości dłuższych niż %d znaków (znacznik …)",
                    table,
                    column,
                    clipped,
                    limit,
                )
        op.execute(
            sa.text(
                f"UPDATE {table} SET {column} = left({column}, {limit - 1}) || '…' WHERE {too_long}"
            )
        )
        op.alter_column(
            table,
            column,
            existing_type=sa.Text(),
            type_=sa.String(length=limit),
            existing_nullable=nullable,
        )
