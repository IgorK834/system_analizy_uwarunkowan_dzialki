"""versioned_data_model

Revision ID: 007_versioned_data_model
Revises: 006_map_layer_geojson
Create Date: 2026-07-22

Addytywna migracja wersjonowanego modelu danych źródłowych (Faza 10.3).
Dodaje rejestr źródeł, artefakty, wydania danych, przebiegi importu oraz
wersjonowane encje działek, aktów planistycznych i dokumentów wraz z pełnym
śladem pochodzenia. Nie usuwa ani nie modyfikuje starego modelu analiz; tabelę
``parcels`` rozszerza wyłącznie o kolumnę ``teryt`` (nullable).

Geometrie kanoniczne są w EPSG:2180 z indeksem GiST i zakazem pustej geometrii.
Zakres obowiązywania wersji jest prawostronnie otwarty [valid_from, valid_to);
w obrębie jednego wydania danych dopuszczalna jest jedna aktywna wersja rekordu
(częściowy indeks unikalny WHERE valid_to IS NULL).
"""

from typing import Sequence, Union

from alembic import op
import geoalchemy2
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "007_versioned_data_model"
down_revision: Union[str, None] = "006_map_layer_geojson"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_REVIEW_STATUS_CHECK = (
    "review_status IN ('unreviewed', 'verified', 'rejected', 'superseded')"
)


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )


def _version_columns() -> list[sa.Column]:
    """Wspólne kolumny tabel wersji (temporalnych)."""
    return [
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column(
            "review_status",
            sa.String(length=20),
            server_default="unreviewed",
            nullable=False,
        ),
    ]


def _version_checks(table: str) -> list[sa.CheckConstraint]:
    return [
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from",
            name=f"ck_{table}_valid_range",
        ),
        sa.CheckConstraint("content_hash <> ''", name=f"ck_{table}_content_hash"),
        sa.CheckConstraint(_REVIEW_STATUS_CHECK, name=f"ck_{table}_review_status"),
    ]


def _create_active_version_index(table: str, owner_column: str) -> None:
    # Częściowy indeks unikalny: max. jedna AKTYWNA wersja rekordu w wydaniu.
    op.create_index(
        f"uq_{table}_active",
        table,
        [owner_column, "data_release_id"],
        unique=True,
        postgresql_where=sa.text("valid_to IS NULL"),
    )


def _create_geometry_gist_index(table: str) -> None:
    op.create_index(
        f"ix_{table}_geometry_gist",
        table,
        ["geometry"],
        unique=False,
        postgresql_using="gist",
    )


def upgrade() -> None:
    # --- Rejestr źródeł i pochodzenie ---------------------------------------
    op.create_table(
        "data_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.String(length=80), nullable=False),
        sa.Column("owner", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("access_type", sa.String(length=30), nullable=True),
        sa.Column("license", sa.Text(), nullable=True),
        sa.Column("attribution", sa.Text(), nullable=True),
        _created_at(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", name="uq_data_sources_source_id"),
    )
    op.create_index(
        "ix_data_sources_source_id", "data_sources", ["source_id"], unique=False
    )

    op.create_table(
        "source_artifacts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("data_source_id", sa.Integer(), nullable=False),
        sa.Column("uri", sa.String(length=1000), nullable=False),
        sa.Column("media_type", sa.String(length=120), nullable=True),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("etag", sa.String(length=255), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(["data_source_id"], ["data_sources.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "data_source_id", "content_hash", name="uq_source_artifacts_dedup"
        ),
        sa.CheckConstraint(
            "content_hash <> ''", name="ck_source_artifacts_content_hash"
        ),
    )
    op.create_index(
        "ix_source_artifacts_data_source_id",
        "source_artifacts",
        ["data_source_id"],
        unique=False,
    )

    op.create_table(
        "data_releases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("data_source_id", sa.Integer(), nullable=False),
        sa.Column("version_label", sa.String(length=120), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("importer_version", sa.String(length=80), nullable=True),
        _created_at(),
        sa.ForeignKeyConstraint(["data_source_id"], ["data_sources.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "data_source_id", "version_label", name="uq_data_releases_version"
        ),
    )
    op.create_index(
        "ix_data_releases_data_source_id",
        "data_releases",
        ["data_source_id"],
        unique=False,
    )
    op.create_index(
        "uq_data_releases_active",
        "data_releases",
        ["data_source_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    op.create_table(
        "import_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("data_source_id", sa.Integer(), nullable=False),
        sa.Column("data_release_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("importer_version", sa.String(length=80), nullable=True),
        sa.Column("stats", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("checkpoint", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.ForeignKeyConstraint(["data_source_id"], ["data_sources.id"]),
        sa.ForeignKeyConstraint(["data_release_id"], ["data_releases.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_import_runs_data_source_id",
        "import_runs",
        ["data_source_id"],
        unique=False,
    )
    op.create_index(
        "ix_import_runs_data_release_id",
        "import_runs",
        ["data_release_id"],
        unique=False,
    )

    # --- Rozszerzenie istniejącej tabeli parcels (kompatybilne) -------------
    op.add_column("parcels", sa.Column("teryt", sa.String(length=20), nullable=True))
    op.create_index("ix_parcels_teryt", "parcels", ["teryt"], unique=False)

    # --- Wersje działek -----------------------------------------------------
    op.create_table(
        "parcel_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("parcel_id", sa.Integer(), nullable=False),
        sa.Column(
            "geometry",
            geoalchemy2.Geometry(
                geometry_type="MULTIPOLYGON", srid=2180, spatial_index=False
            ),
            nullable=False,
        ),
        sa.Column("source_artifact_id", sa.Integer(), nullable=False),
        sa.Column("data_release_id", sa.Integer(), nullable=False),
        *_version_columns(),
        _created_at(),
        sa.ForeignKeyConstraint(["parcel_id"], ["parcels.id"]),
        sa.ForeignKeyConstraint(["source_artifact_id"], ["source_artifacts.id"]),
        sa.ForeignKeyConstraint(["data_release_id"], ["data_releases.id"]),
        sa.PrimaryKeyConstraint("id"),
        *_version_checks("parcel_versions"),
        sa.CheckConstraint(
            "NOT ST_IsEmpty(geometry)",
            name="ck_parcel_versions_geometry_not_empty",
        ),
    )
    op.create_index(
        "ix_parcel_versions_parcel_id", "parcel_versions", ["parcel_id"], unique=False
    )
    op.create_index(
        "ix_parcel_versions_source_artifact_id",
        "parcel_versions",
        ["source_artifact_id"],
        unique=False,
    )
    op.create_index(
        "ix_parcel_versions_data_release_id",
        "parcel_versions",
        ["data_release_id"],
        unique=False,
    )
    _create_geometry_gist_index("parcel_versions")
    _create_active_version_index("parcel_versions", "parcel_id")

    # --- Akty planistyczne --------------------------------------------------
    op.create_table(
        "planning_acts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("act_identifier", sa.String(length=200), nullable=False),
        sa.Column("teryt", sa.String(length=20), nullable=True),
        sa.Column("kind", sa.String(length=30), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("act_identifier", name="uq_planning_acts_identifier"),
    )
    op.create_index(
        "ix_planning_acts_act_identifier",
        "planning_acts",
        ["act_identifier"],
        unique=False,
    )
    op.create_index(
        "ix_planning_acts_teryt", "planning_acts", ["teryt"], unique=False
    )

    op.create_table(
        "planning_act_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("planning_act_id", sa.Integer(), nullable=False),
        sa.Column("legal_status", sa.String(length=30), nullable=True),
        sa.Column("version_label", sa.String(length=120), nullable=True),
        sa.Column("source_artifact_id", sa.Integer(), nullable=False),
        sa.Column("data_release_id", sa.Integer(), nullable=False),
        *_version_columns(),
        _created_at(),
        sa.ForeignKeyConstraint(["planning_act_id"], ["planning_acts.id"]),
        sa.ForeignKeyConstraint(["source_artifact_id"], ["source_artifacts.id"]),
        sa.ForeignKeyConstraint(["data_release_id"], ["data_releases.id"]),
        sa.PrimaryKeyConstraint("id"),
        *_version_checks("planning_act_versions"),
    )
    op.create_index(
        "ix_planning_act_versions_planning_act_id",
        "planning_act_versions",
        ["planning_act_id"],
        unique=False,
    )
    op.create_index(
        "ix_planning_act_versions_source_artifact_id",
        "planning_act_versions",
        ["source_artifact_id"],
        unique=False,
    )
    op.create_index(
        "ix_planning_act_versions_data_release_id",
        "planning_act_versions",
        ["data_release_id"],
        unique=False,
    )
    _create_active_version_index("planning_act_versions", "planning_act_id")

    op.create_table(
        "plan_boundaries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("planning_act_version_id", sa.Integer(), nullable=False),
        sa.Column(
            "geometry",
            geoalchemy2.Geometry(
                geometry_type="MULTIPOLYGON", srid=2180, spatial_index=False
            ),
            nullable=False,
        ),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["planning_act_version_id"], ["planning_act_versions.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "NOT ST_IsEmpty(geometry)",
            name="ck_plan_boundaries_geometry_not_empty",
        ),
    )
    op.create_index(
        "ix_plan_boundaries_planning_act_version_id",
        "plan_boundaries",
        ["planning_act_version_id"],
        unique=False,
    )
    _create_geometry_gist_index("plan_boundaries")

    op.create_table(
        "planning_symbols",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("planning_act_version_id", sa.Integer(), nullable=False),
        sa.Column("local_symbol", sa.String(length=60), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("normalized_category", sa.String(length=120), nullable=True),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["planning_act_version_id"], ["planning_act_versions.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "planning_act_version_id",
            "local_symbol",
            name="uq_planning_symbols_symbol",
        ),
    )
    op.create_index(
        "ix_planning_symbols_planning_act_version_id",
        "planning_symbols",
        ["planning_act_version_id"],
        unique=False,
    )

    op.create_table(
        "land_use_areas",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("planning_act_version_id", sa.Integer(), nullable=False),
        sa.Column("planning_symbol_id", sa.Integer(), nullable=True),
        sa.Column("symbol", sa.String(length=60), nullable=True),
        sa.Column(
            "geometry",
            geoalchemy2.Geometry(
                geometry_type="MULTIPOLYGON", srid=2180, spatial_index=False
            ),
            nullable=False,
        ),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["planning_act_version_id"], ["planning_act_versions.id"]
        ),
        sa.ForeignKeyConstraint(["planning_symbol_id"], ["planning_symbols.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "NOT ST_IsEmpty(geometry)",
            name="ck_land_use_areas_geometry_not_empty",
        ),
    )
    op.create_index(
        "ix_land_use_areas_planning_act_version_id",
        "land_use_areas",
        ["planning_act_version_id"],
        unique=False,
    )
    op.create_index(
        "ix_land_use_areas_planning_symbol_id",
        "land_use_areas",
        ["planning_symbol_id"],
        unique=False,
    )
    _create_geometry_gist_index("land_use_areas")

    op.create_table(
        "planning_features",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("planning_act_version_id", sa.Integer(), nullable=False),
        sa.Column("feature_type", sa.String(length=80), nullable=False),
        sa.Column(
            "geometry",
            geoalchemy2.Geometry(
                geometry_type="GEOMETRY", srid=2180, spatial_index=False
            ),
            nullable=False,
        ),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["planning_act_version_id"], ["planning_act_versions.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "NOT ST_IsEmpty(geometry)",
            name="ck_planning_features_geometry_not_empty",
        ),
    )
    op.create_index(
        "ix_planning_features_planning_act_version_id",
        "planning_features",
        ["planning_act_version_id"],
        unique=False,
    )
    _create_geometry_gist_index("planning_features")

    # --- Dokumenty źródłowe -------------------------------------------------
    op.create_table(
        "source_documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("planning_act_id", sa.Integer(), nullable=True),
        sa.Column("data_source_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("document_type", sa.String(length=60), nullable=True),
        _created_at(),
        sa.ForeignKeyConstraint(["planning_act_id"], ["planning_acts.id"]),
        sa.ForeignKeyConstraint(["data_source_id"], ["data_sources.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_source_documents_planning_act_id",
        "source_documents",
        ["planning_act_id"],
        unique=False,
    )
    op.create_index(
        "ix_source_documents_data_source_id",
        "source_documents",
        ["data_source_id"],
        unique=False,
    )

    op.create_table(
        "document_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_document_id", sa.Integer(), nullable=False),
        sa.Column("source_artifact_id", sa.Integer(), nullable=False),
        sa.Column("data_release_id", sa.Integer(), nullable=False),
        *_version_columns(),
        _created_at(),
        sa.ForeignKeyConstraint(["source_document_id"], ["source_documents.id"]),
        sa.ForeignKeyConstraint(["source_artifact_id"], ["source_artifacts.id"]),
        sa.ForeignKeyConstraint(["data_release_id"], ["data_releases.id"]),
        sa.PrimaryKeyConstraint("id"),
        *_version_checks("document_versions"),
    )
    op.create_index(
        "ix_document_versions_source_document_id",
        "document_versions",
        ["source_document_id"],
        unique=False,
    )
    op.create_index(
        "ix_document_versions_source_artifact_id",
        "document_versions",
        ["source_artifact_id"],
        unique=False,
    )
    op.create_index(
        "ix_document_versions_data_release_id",
        "document_versions",
        ["data_release_id"],
        unique=False,
    )
    _create_active_version_index("document_versions", "source_document_id")

    op.create_table(
        "document_pages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_version_id", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column(
            "ocr_used",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("quality", sa.Float(), nullable=True),
        _created_at(),
        sa.ForeignKeyConstraint(["document_version_id"], ["document_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_version_id", "page_number", name="uq_document_pages_page"
        ),
        sa.CheckConstraint("page_number >= 1", name="ck_document_pages_page_number"),
    )
    op.create_index(
        "ix_document_pages_document_version_id",
        "document_pages",
        ["document_version_id"],
        unique=False,
    )

    op.create_table(
        "legal_units",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_version_id", sa.Integer(), nullable=False),
        sa.Column("chapter", sa.String(length=60), nullable=True),
        sa.Column("paragraph", sa.String(length=60), nullable=True),
        sa.Column("section", sa.String(length=60), nullable=True),
        sa.Column("point", sa.String(length=60), nullable=True),
        sa.Column("position", sa.String(length=60), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        _created_at(),
        sa.ForeignKeyConstraint(["document_version_id"], ["document_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_legal_units_document_version_id",
        "legal_units",
        ["document_version_id"],
        unique=False,
    )

    op.create_table(
        "symbol_legal_units",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("planning_symbol_id", sa.Integer(), nullable=False),
        sa.Column("legal_unit_id", sa.Integer(), nullable=False),
        sa.Column(
            "relation_type",
            sa.String(length=40),
            server_default="describes",
            nullable=False,
        ),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column(
            "review_status",
            sa.String(length=20),
            server_default="unreviewed",
            nullable=False,
        ),
        _created_at(),
        sa.ForeignKeyConstraint(["planning_symbol_id"], ["planning_symbols.id"]),
        sa.ForeignKeyConstraint(["legal_unit_id"], ["legal_units.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "planning_symbol_id",
            "legal_unit_id",
            "relation_type",
            name="uq_symbol_legal_units_relation",
        ),
        sa.CheckConstraint(
            _REVIEW_STATUS_CHECK, name="ck_symbol_legal_units_review_status"
        ),
    )
    op.create_index(
        "ix_symbol_legal_units_planning_symbol_id",
        "symbol_legal_units",
        ["planning_symbol_id"],
        unique=False,
    )
    op.create_index(
        "ix_symbol_legal_units_legal_unit_id",
        "symbol_legal_units",
        ["legal_unit_id"],
        unique=False,
    )

    op.create_table(
        "manual_reviews",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("subject_type", sa.String(length=60), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("reviewer", sa.String(length=120), nullable=True),
        sa.Column("decision", sa.String(length=40), nullable=True),
        sa.Column(
            "review_status",
            sa.String(length=20),
            server_default="unreviewed",
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        _created_at(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            _REVIEW_STATUS_CHECK, name="ck_manual_reviews_review_status"
        ),
    )
    op.create_index(
        "ix_manual_reviews_subject",
        "manual_reviews",
        ["subject_type", "subject_id"],
        unique=False,
    )


def downgrade() -> None:
    # Kolejność odwrotna do tworzenia; stary model analiz pozostaje nietknięty.
    op.drop_index("ix_manual_reviews_subject", table_name="manual_reviews")
    op.drop_table("manual_reviews")

    op.drop_index(
        "ix_symbol_legal_units_legal_unit_id", table_name="symbol_legal_units"
    )
    op.drop_index(
        "ix_symbol_legal_units_planning_symbol_id", table_name="symbol_legal_units"
    )
    op.drop_table("symbol_legal_units")

    op.drop_index("ix_legal_units_document_version_id", table_name="legal_units")
    op.drop_table("legal_units")

    op.drop_index(
        "ix_document_pages_document_version_id", table_name="document_pages"
    )
    op.drop_table("document_pages")

    op.drop_index("uq_document_versions_active", table_name="document_versions")
    op.drop_index(
        "ix_document_versions_data_release_id", table_name="document_versions"
    )
    op.drop_index(
        "ix_document_versions_source_artifact_id", table_name="document_versions"
    )
    op.drop_index(
        "ix_document_versions_source_document_id", table_name="document_versions"
    )
    op.drop_table("document_versions")

    op.drop_index(
        "ix_source_documents_data_source_id", table_name="source_documents"
    )
    op.drop_index(
        "ix_source_documents_planning_act_id", table_name="source_documents"
    )
    op.drop_table("source_documents")

    op.drop_index("ix_planning_features_geometry_gist", table_name="planning_features")
    op.drop_index(
        "ix_planning_features_planning_act_version_id",
        table_name="planning_features",
    )
    op.drop_table("planning_features")

    op.drop_index("ix_land_use_areas_geometry_gist", table_name="land_use_areas")
    op.drop_index(
        "ix_land_use_areas_planning_symbol_id", table_name="land_use_areas"
    )
    op.drop_index(
        "ix_land_use_areas_planning_act_version_id", table_name="land_use_areas"
    )
    op.drop_table("land_use_areas")

    op.drop_index(
        "ix_planning_symbols_planning_act_version_id",
        table_name="planning_symbols",
    )
    op.drop_table("planning_symbols")

    op.drop_index("ix_plan_boundaries_geometry_gist", table_name="plan_boundaries")
    op.drop_index(
        "ix_plan_boundaries_planning_act_version_id", table_name="plan_boundaries"
    )
    op.drop_table("plan_boundaries")

    op.drop_index("uq_planning_act_versions_active", table_name="planning_act_versions")
    op.drop_index(
        "ix_planning_act_versions_data_release_id",
        table_name="planning_act_versions",
    )
    op.drop_index(
        "ix_planning_act_versions_source_artifact_id",
        table_name="planning_act_versions",
    )
    op.drop_index(
        "ix_planning_act_versions_planning_act_id",
        table_name="planning_act_versions",
    )
    op.drop_table("planning_act_versions")

    op.drop_index("ix_planning_acts_teryt", table_name="planning_acts")
    op.drop_index("ix_planning_acts_act_identifier", table_name="planning_acts")
    op.drop_table("planning_acts")

    op.drop_index("uq_parcel_versions_active", table_name="parcel_versions")
    op.drop_index("ix_parcel_versions_geometry_gist", table_name="parcel_versions")
    op.drop_index(
        "ix_parcel_versions_data_release_id", table_name="parcel_versions"
    )
    op.drop_index(
        "ix_parcel_versions_source_artifact_id", table_name="parcel_versions"
    )
    op.drop_index("ix_parcel_versions_parcel_id", table_name="parcel_versions")
    op.drop_table("parcel_versions")

    # Wycofanie kompatybilnego rozszerzenia parcels.
    op.drop_index("ix_parcels_teryt", table_name="parcels")
    op.drop_column("parcels", "teryt")

    op.drop_index("ix_import_runs_data_release_id", table_name="import_runs")
    op.drop_index("ix_import_runs_data_source_id", table_name="import_runs")
    op.drop_table("import_runs")

    op.drop_index("uq_data_releases_active", table_name="data_releases")
    op.drop_index("ix_data_releases_data_source_id", table_name="data_releases")
    op.drop_table("data_releases")

    op.drop_index(
        "ix_source_artifacts_data_source_id", table_name="source_artifacts"
    )
    op.drop_table("source_artifacts")

    op.drop_index("ix_data_sources_source_id", table_name="data_sources")
    op.drop_table("data_sources")
