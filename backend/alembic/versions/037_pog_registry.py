"""Krajowy rejestr aktywnych wersji aktów POG, katalog RU, kolejka importu i tabele mapy (ADR-026).

Revision ID: 037_pog_registry
Revises: 036_bdot10k_context
Create Date: 2026-10-13

Import drugiej gminy ukrywał pierwszą, bo „stan mapy i analizy” wyznaczało jedno aktywne wydanie na źródło ``pog_app``.
Od tej migracji o widoczności wersji aktu decyduje czasowy rejestr:

* ``pog_registry_states`` — numer stanu krajowego (rośnie przy każdej zmianie rejestru);
* ``pog_registry_entries`` — okres aktywności wersji aktu ``[state_from_id, state_to_id)``; częściowy indeks unikalny
  pilnuje, by najwyżej jedna wersja aktu była aktywna;
* ``pog_ru_catalog`` — migawka listy aktów POG opublikowanych w Rejestrze Urbanistycznym (stan gminy: „brak aktu
  w RU” / „akt w RU, niezaimportowany”);
* ``import_jobs`` — kolejka zleceń importu (import na żądanie, harmonogram);
* ``pog_map_acts``, ``pog_map_zones``, ``pog_map_overlays`` — kopie do wyświetlania (EPSG:3857, uproszczenie zależne
  od zoomu, gotowe atrybuty kafla) z indeksami GiST;
* ``pog_area_summaries.registry_state_id`` — agregat gminy jest wersjonowany numerem stanu.

Dane istniejące: dla każdego aktu POG aktywna staje się jego otwarta wersja (``valid_to IS NULL``), z pierwszeństwem
wersji z wydania ``is_active`` — dzięki temu akty ukryte przez dawny model (wydania nieaktywne) wracają do stanu
krajowego. Powstaje jeden stan ``migration``; identyfikatory dawnych aktywnych wydań trafiają do jego ``summary``,
a wydania źródła ``pog_app`` przestają być „aktywne” (są rekordami audytowymi). Tabele ``pog_map_*`` zostają puste:
wypełnia je ``python -m app.modules.imports pog-map-rebuild`` (atrybuty kafla liczy ta sama funkcja co w analizie,
więc nie dublujemy jej w SQL).

Rollback jest stratny dla danych pochodnych: usuwa tabele rejestru i mapy, przywraca ``is_active`` dawnych aktywnych
wydań i dawny indeks agregatów gmin (po usunięciu agregatów powstałych po migracji). Wersje aktów i ich geometrie
pozostają nietknięte.
"""

from typing import Sequence, Union

from alembic import op
import geoalchemy2
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "037_pog_registry"
down_revision: Union[str, None] = "036_bdot10k_context"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _geometry(name: str, *, nullable: bool = True) -> sa.Column:
    return sa.Column(
        name,
        geoalchemy2.Geometry(geometry_type="GEOMETRY", srid=3857, spatial_index=False),
        nullable=nullable,
    )


def upgrade() -> None:
    op.create_table(
        "pog_registry_states",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("data_release_id", sa.Integer(), nullable=True),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["data_release_id"], ["data_releases.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "kind IN ('import', 'withdrawal', 'rollback', 'migration')", name="ck_pog_registry_states_kind"
        ),
    )
    op.create_index("ix_pog_registry_states_data_release_id", "pog_registry_states", ["data_release_id"])

    op.create_table(
        "pog_registry_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("planning_act_id", sa.Integer(), nullable=False),
        sa.Column("planning_act_version_id", sa.Integer(), nullable=False),
        sa.Column("state_from_id", sa.Integer(), nullable=False),
        sa.Column("state_to_id", sa.Integer(), nullable=True),
        sa.Column("end_reason", sa.String(length=20), nullable=True),
        sa.Column("ru_published_act_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["planning_act_id"], ["planning_acts.id"]),
        sa.ForeignKeyConstraint(["planning_act_version_id"], ["planning_act_versions.id"]),
        sa.ForeignKeyConstraint(["state_from_id"], ["pog_registry_states.id"]),
        sa.ForeignKeyConstraint(["state_to_id"], ["pog_registry_states.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("(state_to_id IS NULL) = (end_reason IS NULL)", name="ck_pog_registry_entries_end"),
        sa.CheckConstraint(
            "state_to_id IS NULL OR state_to_id > state_from_id", name="ck_pog_registry_entries_range"
        ),
        sa.CheckConstraint(
            "end_reason IS NULL OR end_reason IN ('replaced', 'withdrawn', 'not_current', 'rolled_back')",
            name="ck_pog_registry_entries_reason",
        ),
    )
    for column in ("planning_act_id", "planning_act_version_id", "state_from_id", "state_to_id"):
        op.create_index(f"ix_pog_registry_entries_{column}", "pog_registry_entries", [column])
    op.create_index(
        "uq_pog_registry_entries_active",
        "pog_registry_entries",
        ["planning_act_id"],
        unique=True,
        postgresql_where=sa.text("state_to_id IS NULL"),
    )

    op.create_table(
        "pog_ru_catalog",
        sa.Column("published_act_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("terc", sa.String(length=7), nullable=False),
        sa.Column("plan_type", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("voivodeship", sa.String(length=40), nullable=True),
        sa.Column("district", sa.String(length=80), nullable=True),
        sa.Column("commune", sa.String(length=120), nullable=True),
        sa.Column("status_text", sa.String(length=80), nullable=True),
        sa.Column("legal_status", sa.String(length=30), nullable=False),
        sa.Column("publish_date", sa.Date(), nullable=True),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_sync_run_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["last_sync_run_id"], ["import_runs.id"]),
        sa.PrimaryKeyConstraint("published_act_id"),
    )
    op.create_index("ix_pog_ru_catalog_terc", "pog_ru_catalog", ["terc"])
    op.create_index(
        "ix_pog_ru_catalog_current", "pog_ru_catalog", ["terc"], postgresql_where=sa.text("is_current")
    )

    op.create_table(
        "import_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("scope_key", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="queued", nullable=False),
        sa.Column("requested_by", sa.String(length=30), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("worker_id", sa.String(length=80), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')", name="ck_import_jobs_status"
        ),
        sa.CheckConstraint("kind IN ('pog_commune')", name="ck_import_jobs_kind"),
    )
    op.create_index(
        "uq_import_jobs_active",
        "import_jobs",
        ["kind", "scope_key"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index("ix_import_jobs_queue", "import_jobs", ["status", "not_before", "id"])

    op.create_table(
        "pog_map_acts",
        sa.Column("planning_act_version_id", sa.Integer(), nullable=False),
        sa.Column("act_id", sa.String(length=200), nullable=False),
        sa.Column("teryt", sa.String(length=20), nullable=True),
        sa.Column("legal_status", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=True),
        sa.Column("act_version", sa.String(length=120), nullable=True),
        sa.Column("resolution_number", sa.String(length=200), nullable=True),
        sa.Column("resolution_date", sa.Date(), nullable=True),
        sa.Column("legal_valid_from", sa.Date(), nullable=True),
        sa.Column("legal_valid_to", sa.Date(), nullable=True),
        sa.Column("version_started_on", sa.Date(), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("official_journal", sa.String(length=200), nullable=True),
        sa.Column("document_link", sa.Text(), nullable=True),
        _geometry("geom_overview"),
        _geometry("geom_low"),
        _geometry("geom_full", nullable=False),
        sa.ForeignKeyConstraint(["planning_act_version_id"], ["planning_act_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("planning_act_version_id"),
    )
    op.create_index("ix_pog_map_acts_teryt", "pog_map_acts", ["teryt"])
    op.create_index("ix_pog_map_acts_geom_full_gist", "pog_map_acts", ["geom_full"], postgresql_using="gist")

    op.create_table(
        "pog_map_zones",
        sa.Column("feature_id", sa.Integer(), nullable=False),
        sa.Column("planning_act_version_id", sa.Integer(), nullable=False),
        sa.Column("legal_status", sa.String(length=30), nullable=False),
        sa.Column("feature_ref", sa.String(length=500), nullable=False),
        sa.Column("feature_version", sa.String(length=120), nullable=True),
        sa.Column("symbol", sa.String(length=60), nullable=True),
        sa.Column("designation", sa.String(length=60), nullable=True),
        sa.Column("label", sa.String(length=200), nullable=True),
        sa.Column("zone_code", sa.String(length=20), nullable=True),
        sa.Column("max_overground_floor_area_ratio", sa.Float(), nullable=True),
        sa.Column("max_building_height_m", sa.Float(), nullable=True),
        sa.Column("max_building_coverage_pct", sa.Float(), nullable=True),
        sa.Column("min_biologically_active_pct", sa.Float(), nullable=True),
        sa.Column("parameters_informational", sa.Boolean(), nullable=True),
        sa.Column("primary_profiles", sa.String(length=256), nullable=True),
        sa.Column("additional_profiles", sa.String(length=256), nullable=True),
        sa.Column("teryt", sa.String(length=20), nullable=True),
        sa.Column("act_id", sa.String(length=200), nullable=False),
        _geometry("geom_low"),
        _geometry("geom_mid"),
        _geometry("geom_full", nullable=False),
        sa.ForeignKeyConstraint(["feature_id"], ["planning_features.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["planning_act_version_id"], ["planning_act_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("feature_id"),
    )
    op.create_index("ix_pog_map_zones_planning_act_version_id", "pog_map_zones", ["planning_act_version_id"])
    op.create_index("ix_pog_map_zones_geom_full_gist", "pog_map_zones", ["geom_full"], postgresql_using="gist")

    op.create_table(
        "pog_map_overlays",
        sa.Column("feature_id", sa.Integer(), nullable=False),
        sa.Column("planning_act_version_id", sa.Integer(), nullable=False),
        sa.Column("layer", sa.String(length=40), nullable=False),
        sa.Column("legal_status", sa.String(length=30), nullable=False),
        sa.Column("feature_ref", sa.String(length=500), nullable=False),
        sa.Column("feature_version", sa.String(length=120), nullable=True),
        sa.Column("symbol", sa.String(length=60), nullable=True),
        sa.Column("label", sa.String(length=200), nullable=True),
        sa.Column("teryt", sa.String(length=20), nullable=True),
        sa.Column("act_id", sa.String(length=200), nullable=False),
        _geometry("geom_low"),
        _geometry("geom_mid"),
        _geometry("geom_full", nullable=False),
        sa.ForeignKeyConstraint(["feature_id"], ["planning_features.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["planning_act_version_id"], ["planning_act_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("feature_id"),
        sa.CheckConstraint(
            "layer IN ('ouz', 'downtown', 'social_infrastructure_standard')", name="ck_pog_map_overlays_layer"
        ),
    )
    op.create_index("ix_pog_map_overlays_planning_act_version_id", "pog_map_overlays", ["planning_act_version_id"])
    op.create_index("ix_pog_map_overlays_geom_full_gist", "pog_map_overlays", ["geom_full"], postgresql_using="gist")

    op.add_column("pog_area_summaries", sa.Column("registry_state_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_pog_area_summaries_registry_state",
        "pog_area_summaries",
        "pog_registry_states",
        ["registry_state_id"],
        ["id"],
    )
    op.create_index("ix_pog_area_summaries_registry_state_id", "pog_area_summaries", ["registry_state_id"])
    op.drop_index("uq_pog_area_summaries_municipality", table_name="pog_area_summaries")
    op.create_index(
        "uq_pog_area_summaries_municipality",
        "pog_area_summaries",
        ["teryt", "edition", "registry_state_id"],
        unique=True,
        postgresql_where=sa.text("scope = 'municipality'"),
    )

    _seed_registry_from_legacy_releases()


def _seed_registry_from_legacy_releases() -> None:
    """Dawny model → rejestr: aktywna staje się otwarta wersja każdego aktu POG (z pierwszeństwem wydania aktywnego)."""
    connection = op.get_bind()
    versions = connection.execute(
        sa.text(
            """
            SELECT count(*) FROM planning_act_versions pav
            JOIN planning_acts pa ON pa.id = pav.planning_act_id
            WHERE pa.kind = 'pog' AND pav.valid_to IS NULL
            """
        )
    ).scalar_one()
    if not versions:
        return
    legacy_active = [
        int(row[0])
        for row in connection.execute(
            sa.text(
                """
                SELECT dr.id FROM data_releases dr JOIN data_sources ds ON ds.id = dr.data_source_id
                WHERE ds.source_id = 'pog_app' AND dr.is_active ORDER BY dr.id
                """
            )
        )
    ]
    state_id = connection.execute(
        sa.text(
            "INSERT INTO pog_registry_states (kind, data_release_id, summary) "
            "VALUES ('migration', :release_id, CAST(:summary AS jsonb)) RETURNING id"
        ),
        {
            "release_id": legacy_active[0] if legacy_active else None,
            "summary": '{"legacy_active_release_ids": %s, "source": "037_pog_registry"}'
            % str(legacy_active).replace(" ", ""),
        },
    ).scalar_one()
    connection.execute(
        sa.text(
            """
            INSERT INTO pog_registry_entries (planning_act_id, planning_act_version_id, state_from_id)
            SELECT DISTINCT ON (pa.id) pa.id, pav.id, :state_id
            FROM planning_act_versions pav
            JOIN planning_acts pa ON pa.id = pav.planning_act_id
            JOIN data_releases dr ON dr.id = pav.data_release_id
            WHERE pa.kind = 'pog' AND pav.valid_to IS NULL
            ORDER BY pa.id, dr.is_active DESC, pav.valid_from DESC, pav.id DESC
            """
        ),
        {"state_id": state_id},
    )
    connection.execute(
        sa.text(
            """
            UPDATE pog_area_summaries SET registry_state_id = :state_id
            WHERE scope = 'municipality' AND data_release_id = ANY(CAST(:releases AS integer[]))
            """
        ),
        {"state_id": state_id, "releases": legacy_active},
    )
    connection.execute(
        sa.text(
            """
            UPDATE data_releases SET is_active = false
            WHERE is_active AND data_source_id = (SELECT id FROM data_sources WHERE source_id = 'pog_app')
            """
        )
    )


def downgrade() -> None:
    connection = op.get_bind()
    legacy_active: list[int] = []
    row = connection.execute(
        sa.text(
            "SELECT summary->'legacy_active_release_ids' FROM pog_registry_states "
            "WHERE kind = 'migration' ORDER BY id LIMIT 1"
        )
    ).first()
    if row and row[0]:
        legacy_active = [int(item) for item in row[0]]
    # Agregaty gmin powstałe po migracji nie mają odpowiednika w dawnym modelu (jeden agregat na wydanie i gminę).
    connection.execute(
        sa.text(
            """
            DELETE FROM pog_area_summaries
            WHERE scope = 'municipality'
              AND registry_state_id IS NOT NULL
              AND registry_state_id NOT IN (SELECT id FROM pog_registry_states WHERE kind = 'migration')
            """
        )
    )
    op.drop_index("uq_pog_area_summaries_municipality", table_name="pog_area_summaries")
    op.drop_index("ix_pog_area_summaries_registry_state_id", table_name="pog_area_summaries")
    op.drop_constraint("fk_pog_area_summaries_registry_state", "pog_area_summaries", type_="foreignkey")
    op.drop_column("pog_area_summaries", "registry_state_id")
    op.create_index(
        "uq_pog_area_summaries_municipality",
        "pog_area_summaries",
        ["data_release_id", "teryt", "edition"],
        unique=True,
        postgresql_where=sa.text("scope = 'municipality'"),
    )
    for table in ("pog_map_overlays", "pog_map_zones", "pog_map_acts", "import_jobs", "pog_ru_catalog"):
        op.drop_table(table)
    op.drop_table("pog_registry_entries")
    op.drop_table("pog_registry_states")
    if legacy_active:
        connection.execute(
            sa.text("UPDATE data_releases SET is_active = true WHERE id = ANY(CAST(:ids AS integer[]))"),
            {"ids": legacy_active},
        )
