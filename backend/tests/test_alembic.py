from pathlib import Path


def test_alembic_files_exist() -> None:
    backend_dir = Path(__file__).resolve().parents[1]

    assert (backend_dir / "alembic.ini").is_file()
    assert (backend_dir / "alembic" / "env.py").is_file()
    assert (
        backend_dir / "alembic" / "versions" / "001_initial_schema.py"
    ).is_file()


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
    assert "postgresql_using=\"gist\"" in upgrade_body


def test_main_and_session_do_not_use_create_all() -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    checked_files = [
        backend_dir / "app" / "main.py",
        backend_dir / "app" / "db" / "session.py",
    ]

    for checked_file in checked_files:
        assert "create_all" not in checked_file.read_text(encoding="utf-8")
