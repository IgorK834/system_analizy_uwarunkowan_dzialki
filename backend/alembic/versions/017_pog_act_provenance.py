"""Provenance aktu POG: wersja publikacji, dokumenty z SHA i metadane CSW (BK-107).

Revision ID: 017_pog_act_provenance
Revises: 016_pog_status_coverage

- ``planning_act_versions``: identyfikator publikacji (``gml:identifier``),
  początek wersji obiektu, okres obowiązywania z APP oraz URL usługi, z której
  wersję zaimportowano (niezależny od późniejszych zmian katalogu).
- ``pog_formal_documents``: pełne pola ``DokumentFormalny``, SHA-256 rekordu,
  wynik weryfikacji linku i stan powiązania z wersją aktu. Unikalność obejmuje
  wersję dokumentu, więc dwie wersje o tym samym tytule i identyfikatorze nie są
  scalane.
- ``pog_act_metadata_records``: rekordy ISO 19139 z CSW zamrożone w snapshotcie.

Rollback usuwa wyłącznie dodane kolumny/tabelę i przywraca unikalność 015;
wcześniej trzeba usunąć duplikaty wersji dokumentów (downgrade robi to jawnie,
zostawiając najstarszy rekord).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "017_pog_act_provenance"
down_revision: Union[str, None] = "016_pog_status_coverage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

RESOLUTION = ("resolved", "unresolved", "unavailable")


def upgrade() -> None:
    for name, column in (
        ("publication_id", sa.String(500)),
        ("version_started_at", sa.DateTime(timezone=True)),
        ("legal_valid_from", sa.Date()),
        ("legal_valid_to", sa.Date()),
        ("source_reference", sa.String(1000)),
    ):
        op.add_column("planning_act_versions", sa.Column(name, column, nullable=True))

    for name, column in (
        ("publication_id", sa.String(500)),
        ("short_name", sa.Text()),
        ("identification_number", sa.String(200)),
        ("relation", sa.String(40)),
        ("document_date", sa.Date()),
        ("effective_date", sa.Date()),
        ("repeal_date", sa.Date()),
        ("record_sha256", sa.String(64)),
        ("resolution_note", sa.Text()),
    ):
        op.add_column("pog_formal_documents", sa.Column(name, column, nullable=True))
    op.add_column(
        "pog_formal_documents",
        sa.Column("link_verified", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "pog_formal_documents",
        sa.Column("resolution_status", sa.String(20), server_default="resolved", nullable=False),
    )
    op.create_check_constraint(
        "ck_pog_formal_documents_resolution_status",
        "pog_formal_documents",
        f"resolution_status IN ({', '.join(repr(value) for value in RESOLUTION)})",
    )
    op.drop_constraint(
        "uq_pog_documents_version_identifier", "pog_formal_documents", type_="unique"
    )
    op.create_index(
        "uq_pog_documents_version_identifier_version",
        "pog_formal_documents",
        ["planning_act_version_id", "document_identifier", sa.text("coalesce(document_version, '')")],
        unique=True,
    )

    op.create_table(
        "pog_act_metadata_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "planning_act_version_id",
            sa.Integer(),
            sa.ForeignKey("planning_act_versions.id"),
            nullable=False,
        ),
        sa.Column("record_id", sa.String(200), nullable=False),
        sa.Column("resource_identifier", sa.String(1000), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("publication_date", sa.Date(), nullable=True),
        sa.Column("revision_date", sa.Date(), nullable=True),
        sa.Column("creation_date", sa.Date(), nullable=True),
        sa.Column("date_stamp", sa.Date(), nullable=True),
        sa.Column("metadata_url", sa.String(2000), nullable=True),
        sa.Column("reference_urls", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("record_sha256", sa.String(64), nullable=True),
        sa.Column("response_sha256", sa.String(64), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "planning_act_version_id", "record_id", name="uq_pog_act_metadata_records_record"
        ),
    )
    op.create_index(
        "ix_pog_act_metadata_records_planning_act_version_id",
        "pog_act_metadata_records",
        ["planning_act_version_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_pog_act_metadata_records_planning_act_version_id",
        table_name="pog_act_metadata_records",
    )
    op.drop_table("pog_act_metadata_records")
    # Przywrócenie unikalności 015 wymaga jednej wersji dokumentu na akt.
    op.execute(
        "DELETE FROM pog_formal_documents d USING pog_formal_documents keep "
        "WHERE d.planning_act_version_id = keep.planning_act_version_id "
        "AND d.document_identifier = keep.document_identifier AND d.id > keep.id"
    )
    op.drop_index("uq_pog_documents_version_identifier_version", table_name="pog_formal_documents")
    op.create_unique_constraint(
        "uq_pog_documents_version_identifier",
        "pog_formal_documents",
        ["planning_act_version_id", "document_identifier"],
    )
    op.drop_constraint(
        "ck_pog_formal_documents_resolution_status", "pog_formal_documents", type_="check"
    )
    for name in (
        "resolution_status", "link_verified", "resolution_note", "record_sha256",
        "repeal_date", "effective_date", "document_date", "relation",
        "identification_number", "short_name", "publication_id",
    ):
        op.drop_column("pog_formal_documents", name)
    for name in (
        "source_reference", "legal_valid_to", "legal_valid_from",
        "version_started_at", "publication_id",
    ):
        op.drop_column("planning_act_versions", name)
