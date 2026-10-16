"""Snapshot kompletności analizy i komunikatu nagłówkowego (AU-104, Task 22.4).

Revision ID: 035_completeness
Revises: 034_mpzp_status
Create Date: 2026-10-12

``analyses.completeness`` przechowuje ``AnalyzeResponse.completeness``: liczbę sprawdzonych sekcji w zakresie analizy,
stan każdej sekcji (``checked|missing|out_of_scope``) wraz ze statusem z macierzy jakości i podstawą ustalenia MPZP,
zastrzeżenia oraz komunikat nagłówkowy (``headline``). Dzięki zapisowi ocena z chwili analizy nie zmienia się, gdy
reguła kompletności zostanie kiedyś zmieniona (``rule_version``).

Kolumna jest nullable i nie jest uzupełniana wstecz: ``NULL`` oznacza zapis sprzed AU-104. Odczyt odtwarza wtedy
kompletność z zapisanej macierzy jakości (``origin=reconstructed``), a zapisany ``status`` pozostaje bez zmian — jego
semantyka pochodzi ze starej definicji.

Dodatkowo ``analyses.result_contract_version`` jest poszerzone z 64 do 128 znaków: ``RESULT_CONTRACT_VERSION`` zyskało
składnik ``+completeness-v1.0`` i przekroczyło 64 znaki (live smoke 2026-10-12: ``DataError StringDataRightTruncation``,
503 ``PERSISTENCE_FAILED`` dla każdej analizy). Poszerzenie nie zmienia istniejących wartości. Rollback usuwa kolumnę
i przycina wartości dłuższe niż 64 znaki do 64 (kontrakt starszej wersji kodu).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "035_completeness"
down_revision: Union[str, None] = "034_mpzp_status"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "analyses",
        sa.Column(
            "completeness",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.alter_column(
        "analyses",
        "result_contract_version",
        existing_type=sa.String(length=64),
        type_=sa.String(length=128),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "analyses",
        "result_contract_version",
        existing_type=sa.String(length=128),
        type_=sa.String(length=64),
        existing_nullable=True,
        postgresql_using="left(result_contract_version, 64)",
    )
    op.drop_column("analyses", "completeness")
