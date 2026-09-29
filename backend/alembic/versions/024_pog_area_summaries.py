"""Trwałe agregaty powierzchniowe stref POG na poziomie wydania (BK-405).

Revision ID: 024_pog_area_summaries
Revises: 023_structured_risks
Create Date: 2026-09-29

- ``pog_area_summaries``: jeden wiersz na (wydanie, wersja aktu) albo
  (wydanie, gmina TERYT, edycja ``binding``/``project``). Przechowuje jawny
  mianownik (powierzchnia granicy aktu ze źródła, EPSG:2180), powierzchnię
  brakującą, nakładania, obszar stref poza granicą, sumę udziałów, flagę
  ``is_complete`` z listą przyczyn oraz provenance metody (wersja, tolerancje,
  chwila obliczenia). Brak mianownika jest ``NULL`` — ograniczenie CHECK
  zabrania wtedy flagi kompletności, sumy udziałów i powierzchni brakującej.
- ``pog_area_summary_zones``: powierzchnia (m² i km²), udział i liczność
  każdego typu strefy.

Agregaty są liczone przy publikacji wydania w tej samej transakcji co jego
aktywacja; wydania opublikowane przed tą migracją nie mają agregatów (API
zwraca 404 z wyjaśnieniem) do czasu ponownego importu artefaktu (ADR-008:
ponowny import trafia w to samo wydanie i je uzupełnia). Rollback usuwa obie
tabele — dane źródłowe wydań pozostają nietknięte.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "024_pog_area_summaries"
down_revision: Union[str, None] = "023_structured_risks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pog_area_summaries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "data_release_id",
            sa.Integer(),
            sa.ForeignKey("data_releases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope", sa.String(20), nullable=False),
        sa.Column(
            "planning_act_version_id",
            sa.Integer(),
            sa.ForeignKey("planning_act_versions.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("act_identifier", sa.String(200), nullable=True),
        sa.Column("act_version", sa.String(120), nullable=True),
        sa.Column("teryt", sa.String(20), nullable=True),
        sa.Column("edition", sa.String(20), nullable=True),
        sa.Column("legal_status", sa.String(30), nullable=True),
        sa.Column("denominator_area_sqm", sa.Float(), nullable=True),
        sa.Column("denominator_source", sa.String(40), nullable=True),
        sa.Column("zones_area_sqm", sa.Float(), nullable=False),
        sa.Column("missing_area_sqm", sa.Float(), nullable=True),
        sa.Column("overlap_area_sqm", sa.Float(), nullable=False),
        sa.Column("outside_area_sqm", sa.Float(), nullable=False),
        sa.Column("deduplicated_area_sqm", sa.Float(), nullable=True),
        sa.Column("share_sum_pct", sa.Float(), nullable=True),
        sa.Column("zone_count", sa.Integer(), nullable=False),
        sa.Column("act_count", sa.Integer(), nullable=False),
        sa.Column("is_complete", sa.Boolean(), nullable=False),
        sa.Column(
            "incomplete_reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "act_identifiers", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("area_tolerance_sqm", sa.Float(), nullable=False),
        sa.Column("share_tolerance_pct", sa.Float(), nullable=False),
        sa.Column("method_version", sa.String(40), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "scope IN ('act', 'municipality')", name="ck_pog_area_summaries_scope"
        ),
        sa.CheckConstraint(
            "edition IS NULL OR edition IN ('binding', 'project')",
            name="ck_pog_area_summaries_edition",
        ),
        sa.CheckConstraint(
            "(scope = 'act' AND planning_act_version_id IS NOT NULL AND edition IS NULL)"
            " OR (scope = 'municipality' AND teryt IS NOT NULL AND edition IS NOT NULL)",
            name="ck_pog_area_summaries_scope_key",
        ),
        sa.CheckConstraint(
            "denominator_area_sqm IS NULL OR denominator_area_sqm > 0",
            name="ck_pog_area_summaries_denominator",
        ),
        sa.CheckConstraint(
            "denominator_area_sqm IS NOT NULL OR "
            "(is_complete = false AND share_sum_pct IS NULL AND missing_area_sqm IS NULL)",
            name="ck_pog_area_summaries_null_denominator",
        ),
    )
    op.create_index(
        "ix_pog_area_summaries_data_release_id", "pog_area_summaries", ["data_release_id"]
    )
    op.create_index(
        "ix_pog_area_summaries_planning_act_version_id",
        "pog_area_summaries",
        ["planning_act_version_id"],
    )
    op.create_index("ix_pog_area_summaries_teryt", "pog_area_summaries", ["teryt"])
    op.create_index(
        "uq_pog_area_summaries_act",
        "pog_area_summaries",
        ["data_release_id", "planning_act_version_id"],
        unique=True,
        postgresql_where=sa.text("scope = 'act'"),
    )
    op.create_index(
        "uq_pog_area_summaries_municipality",
        "pog_area_summaries",
        ["data_release_id", "teryt", "edition"],
        unique=True,
        postgresql_where=sa.text("scope = 'municipality'"),
    )

    op.create_table(
        "pog_area_summary_zones",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "summary_id",
            sa.Integer(),
            sa.ForeignKey("pog_area_summaries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("zone_code", sa.String(20), nullable=False),
        sa.Column("area_sqm", sa.Float(), nullable=False),
        sa.Column("area_sqkm", sa.Float(), nullable=False),
        sa.Column("share_pct", sa.Float(), nullable=True),
        sa.Column("zone_count", sa.Integer(), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "summary_id", "zone_code", name="uq_pog_area_summary_zones_code"
        ),
        sa.CheckConstraint("area_sqm >= 0", name="ck_pog_area_summary_zones_area"),
        sa.CheckConstraint(
            "share_pct IS NULL OR share_pct >= 0", name="ck_pog_area_summary_zones_share"
        ),
        sa.CheckConstraint("zone_count >= 0", name="ck_pog_area_summary_zones_count"),
    )
    op.create_index(
        "ix_pog_area_summary_zones_summary_id", "pog_area_summary_zones", ["summary_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_pog_area_summary_zones_summary_id", table_name="pog_area_summary_zones")
    op.drop_table("pog_area_summary_zones")
    for index in (
        "uq_pog_area_summaries_municipality",
        "uq_pog_area_summaries_act",
        "ix_pog_area_summaries_teryt",
        "ix_pog_area_summaries_planning_act_version_id",
        "ix_pog_area_summaries_data_release_id",
    ):
        op.drop_index(index, table_name="pog_area_summaries")
    op.drop_table("pog_area_summaries")
