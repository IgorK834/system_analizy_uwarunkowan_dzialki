"""Testy konfiguracji usługi db w docker-compose.yml."""

from pathlib import Path

import pytest

from tests.docker_compose_config import (
    DB_IMAGE,
    DB_SERVICE_NAME,
    POSTGRES_VOLUME,
    find_repo_root,
    get_service,
    load_compose,
    validate_db_service,
    validate_env_example,
)


@pytest.fixture
def repo_root() -> Path:
    return find_repo_root()


@pytest.fixture
def compose(repo_root: Path) -> dict:
    return load_compose(repo_root)


def test_compose_file_exists(repo_root: Path) -> None:
    assert (repo_root / "docker-compose.yml").is_file()


def test_db_service_exists(compose: dict) -> None:
    db = get_service(compose, DB_SERVICE_NAME)
    assert db["image"] == DB_IMAGE


def test_db_service_uses_postgis_image(compose: dict) -> None:
    db = get_service(compose, DB_SERVICE_NAME)
    assert db["image"] == "postgis/postgis:16-3.4"


def test_db_service_has_postgres_volume(compose: dict) -> None:
    db = get_service(compose, DB_SERVICE_NAME)
    assert any(
        str(volume).startswith(f"{POSTGRES_VOLUME}:")
        for volume in db.get("volumes", [])
    )


def test_db_service_has_pg_isready_healthcheck(compose: dict) -> None:
    db = get_service(compose, DB_SERVICE_NAME)
    test_cmd = db["healthcheck"]["test"]
    assert "pg_isready" in " ".join(str(part) for part in test_cmd)


def test_db_service_env_uses_env_file_variables(compose: dict) -> None:
    db = get_service(compose, DB_SERVICE_NAME)
    environment = db["environment"]
    for var in ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"):
        assert str(environment[var]).startswith("${")


def test_named_volume_postgres_data_defined(compose: dict) -> None:
    assert POSTGRES_VOLUME in compose.get("volumes", {})


def test_validate_db_service_reports_no_errors(compose: dict) -> None:
    assert validate_db_service(compose) == []


def test_env_example_has_required_database_variables(repo_root: Path) -> None:
    assert validate_env_example(repo_root) == []


def test_env_example_database_url_uses_db_host(repo_root: Path) -> None:
    content = (repo_root / ".env.example").read_text(encoding="utf-8")
    database_url_line = next(
        line for line in content.splitlines() if line.startswith("DATABASE_URL=")
    )
    assert "@db:5432/" in database_url_line
    assert "localhost" not in database_url_line
