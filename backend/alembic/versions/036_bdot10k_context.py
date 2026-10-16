"""Lokalne wydania BDOT10k, kontekst drogowy i zabudowa sąsiednia (Task 23.1–23.3, BK-304/BK-305).

Revision ID: 036_bdot10k_context
Revises: 035_completeness
Create Date: 2026-10-13

* ``data_releases.scope_key`` — zakres wydania źródła importowanego partiami. Dotychczasowy częściowy indeks unikalny
  ``uq_data_releases_active`` (jedno aktywne wydanie na źródło) zastępuje indeks na ``(data_source_id,
  COALESCE(scope_key, ''))``: źródła bez zakresu (``NULL``) zachowują dotychczasową regułę, a paczki powiatowe BDOT10k
  mogą być aktywne równocześnie, po jednej na powiat.
* ``bdot_releases`` (opis wydania i zasięg powiatu), ``bdot_roads`` (osie jezdni ``OT_SKJZ_L``) i ``bdot_buildings``
  (obrysy budynków ``OT_BUBD_A``) — geometrie w EPSG:2180 z indeksem GiST, powiązane z wydaniem danych.
* ``analyses.result_contract_version`` poszerzone ze 128 do 256 znaków: ``RESULT_CONTRACT_VERSION`` ma 97 znaków po
  dołożeniu składników ``road``/``buildings``, więc zapasu do 128 zostawało za mało na kolejne sekcje (przekroczenie
  daje 503 ``PERSISTENCE_FAILED`` dopiero na żywo — patrz 035). Poszerzenie ``VARCHAR`` nie przepisuje tabeli;
* ``analyses.road_context`` i ``analyses.neighbour_buildings`` — snapshoty sekcji; ``NULL`` oznacza zapis sprzed tej
  migracji (odczyt: status ``unknown``, nie „brak drogi” ani „brak budynków”).

Migracja jest addytywna i niczego nie uzupełnia wstecz. Rollback usuwa tabele BDOT10k (dane wydań są odtwarzalne
z paczek o znanym SHA-256) i kolumny snapshotów, a indeks aktywnego wydania wraca do postaci jednego wydania na źródło —
o ile wydania z zakresem zostały wcześniej wycofane (w przeciwnym razie rollback przerwie się jawnym błędem unikalności,
zamiast po cichu wyłączać wydania).
"""

from typing import Sequence, Union

from alembic import op
import geoalchemy2
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "036_bdot10k_context"
down_revision: Union[str, None] = "035_completeness"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "analyses",
        "result_contract_version",
        existing_type=sa.String(length=128),
        type_=sa.String(length=256),
        existing_nullable=True,
    )
    op.add_column("data_releases", sa.Column("scope_key", sa.String(length=40), nullable=True))
    op.drop_index("uq_data_releases_active", table_name="data_releases")
    op.create_index(
        "uq_data_releases_active",
        "data_releases",
        ["data_source_id", sa.text("COALESCE(scope_key, '')")],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    op.create_table(
        "bdot_releases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("data_release_id", sa.Integer(), nullable=False),
        sa.Column("teryt_powiat", sa.String(length=4), nullable=False),
        sa.Column("powiat_name", sa.String(length=255), nullable=True),
        sa.Column("package_name", sa.String(length=255), nullable=False),
        sa.Column("package_sha256", sa.String(length=64), nullable=False),
        sa.Column("data_as_of", sa.Date(), nullable=True),
        sa.Column("road_count", sa.Integer(), nullable=False),
        sa.Column("building_count", sa.Integer(), nullable=False),
        sa.Column("class_stats", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "imported_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "extent",
            geoalchemy2.Geometry(geometry_type="MULTIPOLYGON", srid=2180, spatial_index=False),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["data_release_id"], ["data_releases.id"]),
        sa.UniqueConstraint("data_release_id"),
        sa.CheckConstraint("NOT ST_IsEmpty(extent)", name="ck_bdot_releases_extent_not_empty"),
        sa.CheckConstraint("road_count >= 0 AND building_count >= 0", name="ck_bdot_releases_counts"),
    )
    op.create_index("ix_bdot_releases_teryt_powiat", "bdot_releases", ["teryt_powiat"])
    op.create_index("ix_bdot_releases_extent_gist", "bdot_releases", ["extent"], postgresql_using="gist")

    op.create_table(
        "bdot_roads",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("data_release_id", sa.Integer(), nullable=False),
        sa.Column("local_id", sa.String(length=64), nullable=False),
        sa.Column("existence_category", sa.String(length=40), nullable=True),
        sa.Column("management_category", sa.String(length=40), nullable=True),
        sa.Column("road_class", sa.String(length=80), nullable=True),
        sa.Column("surface_material", sa.String(length=80), nullable=True),
        sa.Column("road_position", sa.String(length=80), nullable=True),
        sa.Column("road_number", sa.String(length=40), nullable=True),
        sa.Column("street_name", sa.String(length=255), nullable=True),
        sa.Column("surface_width_m", sa.Float(), nullable=True),
        sa.Column("feature_version_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "geometry",
            geoalchemy2.Geometry(geometry_type="MULTILINESTRING", srid=2180, spatial_index=False),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["data_release_id"], ["data_releases.id"]),
        sa.UniqueConstraint("data_release_id", "local_id", name="uq_bdot_roads_release_local_id"),
        sa.CheckConstraint("NOT ST_IsEmpty(geometry)", name="ck_bdot_roads_geometry_not_empty"),
    )
    op.create_index("ix_bdot_roads_geometry_gist", "bdot_roads", ["geometry"], postgresql_using="gist")

    op.create_table(
        "bdot_buildings",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("data_release_id", sa.Integer(), nullable=False),
        sa.Column("local_id", sa.String(length=64), nullable=False),
        sa.Column("existence_category", sa.String(length=40), nullable=True),
        sa.Column("general_function", sa.String(length=160), nullable=True),
        sa.Column("detailed_function", sa.String(length=160), nullable=True),
        sa.Column("predominant_function", sa.String(length=160), nullable=True),
        sa.Column("floors", sa.Integer(), nullable=True),
        sa.Column("geometry_source", sa.String(length=60), nullable=True),
        sa.Column("feature_version_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("area_sqm", sa.Float(), nullable=False),
        sa.Column(
            "geometry",
            geoalchemy2.Geometry(geometry_type="MULTIPOLYGON", srid=2180, spatial_index=False),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["data_release_id"], ["data_releases.id"]),
        sa.UniqueConstraint("data_release_id", "local_id", name="uq_bdot_buildings_release_local_id"),
        sa.CheckConstraint("NOT ST_IsEmpty(geometry)", name="ck_bdot_buildings_geometry_not_empty"),
        sa.CheckConstraint("area_sqm > 0", name="ck_bdot_buildings_area_positive"),
    )
    op.create_index("ix_bdot_buildings_geometry_gist", "bdot_buildings", ["geometry"], postgresql_using="gist")

    op.add_column(
        "analyses", sa.Column("road_context", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.add_column(
        "analyses", sa.Column("neighbour_buildings", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )


def downgrade() -> None:
    op.alter_column(
        "analyses",
        "result_contract_version",
        existing_type=sa.String(length=256),
        type_=sa.String(length=128),
        existing_nullable=True,
        postgresql_using="left(result_contract_version, 128)",
    )
    op.drop_column("analyses", "neighbour_buildings")
    op.drop_column("analyses", "road_context")
    op.drop_index("ix_bdot_buildings_geometry_gist", table_name="bdot_buildings")
    op.drop_table("bdot_buildings")
    op.drop_index("ix_bdot_roads_geometry_gist", table_name="bdot_roads")
    op.drop_table("bdot_roads")
    op.drop_index("ix_bdot_releases_extent_gist", table_name="bdot_releases")
    op.drop_index("ix_bdot_releases_teryt_powiat", table_name="bdot_releases")
    op.drop_table("bdot_releases")
    op.drop_index("uq_data_releases_active", table_name="data_releases")
    op.create_index(
        "uq_data_releases_active",
        "data_releases",
        ["data_source_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.drop_column("data_releases", "scope_key")
