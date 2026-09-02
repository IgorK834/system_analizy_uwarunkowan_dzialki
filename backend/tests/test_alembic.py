from pathlib import Path

from tests.repo_structure import find_repo_root


def test_alembic_files_exist() -> None:
    backend_dir = Path(__file__).resolve().parents[1]

    assert (backend_dir / "alembic.ini").is_file()
    assert (backend_dir / "alembic" / "env.py").is_file()
    assert (backend_dir / "alembic" / "versions" / "001_initial_schema.py").is_file()


def test_initial_migration_contains_core_tables_in_upgrade() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "001_initial_schema.py"
    ).read_text(encoding="utf-8")
    upgrade_body = migration.split("def upgrade() -> None:", maxsplit=1)[1].split(
        "def downgrade() -> None:",
        maxsplit=1,
    )[0]

    assert '"parcels"' in upgrade_body
    assert '"analyses"' in upgrade_body
    assert 'postgresql_using="gist"' in upgrade_body


def test_main_and_session_do_not_use_create_all() -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    checked_files = [
        backend_dir / "app" / "main.py",
        backend_dir / "app" / "db" / "session.py",
    ]

    for checked_file in checked_files:
        assert "create_all" not in checked_file.read_text(encoding="utf-8")


def test_backend_container_runs_alembic_before_starting_api() -> None:
    # Obraz runtime celowo nie kopiuje Dockerfile. W CI repozytorium jest
    # montowane tylko do odczytu pod REPO_ROOT, więc test musi sprawdzać źródła,
    # a nie przypadkową zawartość /app w zbudowanym obrazie.
    backend_dir = find_repo_root() / "backend"
    dockerfile = (backend_dir / "Dockerfile").read_text(encoding="utf-8")
    entrypoint = (backend_dir / "docker-entrypoint.sh").read_text(encoding="utf-8")

    assert 'ENTRYPOINT ["dzialki-entrypoint"]' in dockerfile
    assert "alembic upgrade head" in entrypoint
    assert 'exec "$@"' in entrypoint


def test_pog_audit_migration_extends_existing_table_without_renames() -> None:
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "004_add_pog_audit_fields.py"
    )
    migration = migration_path.read_text(encoding="utf-8")

    assert 'revision: str = "004_pog_audit_fields"' in migration
    assert 'down_revision: Union[str, None] = "003_source_audit"' in migration
    assert '"raw_attributes"' in migration
    assert "postgresql.JSONB" in migration
    assert "server_default=sa.false()" in migration
    assert "alter_column" not in migration
    assert "rename" not in migration.lower()
    assert 'op.drop_column("pog_data", "ouz_intersection_area_sqm")' not in migration


def test_infrastructure_audit_migration_follows_pog_migration() -> None:
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "005_add_infrastructure_audit_fields.py"
    )
    migration = migration_path.read_text(encoding="utf-8")

    assert 'revision: str = "005_infrastructure_audit"' in migration
    assert 'down_revision: Union[str, None] = "004_pog_audit_fields"' in migration
    assert '"zone_area_sqm"' in migration
    assert '"rule_source"' in migration
    assert '"affects_buildable_area"' in migration
    assert "server_default=sa.false()" in migration


def test_map_layer_geojson_migration_follows_infrastructure_audit() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "006_add_map_layer_geojson.py"
    ).read_text(encoding="utf-8")

    assert 'revision: str = "006_map_layer_geojson"' in migration
    assert 'down_revision: Union[str, None] = "005_infrastructure_audit"' in migration
    assert '"network_geometry_geojson"' in migration
    assert '"protection_zone_geojson"' in migration
    assert '"geometry_geojson"' in migration
    assert "postgresql.JSONB" in migration


def test_utilities_preview_migration_extends_analysis_snapshot() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "014_utilities_preview.py"
    ).read_text(encoding="utf-8")

    assert 'revision: str = "014_utilities_preview"' in migration
    assert 'down_revision: Union[str, None] = "013_raster_assets"' in migration
    assert '"analyses"' in migration
    assert '"utilities_preview"' in migration
    assert "postgresql.JSONB" in migration
    assert 'op.drop_column("analyses", "utilities_preview")' in migration
