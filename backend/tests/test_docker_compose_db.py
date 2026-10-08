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
    assert db["image"].startswith(DB_IMAGE + "@sha256:")


def test_db_service_uses_postgis_image(compose: dict) -> None:
    db = get_service(compose, DB_SERVICE_NAME)
    assert db["image"].startswith("postgis/postgis:16-3.4@sha256:")


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


# --- AU-006: bezpieczne domyślne ustawienia limitów --------------------------------


def _env_assignments(repo_root: Path) -> dict[str, str]:
    lines = (repo_root / ".env.example").read_text(encoding="utf-8").splitlines()
    return {
        line.split("=", 1)[0]: line.split("=", 1)[1]
        for line in lines
        if "=" in line and not line.startswith("#")
    }


def test_env_example_does_not_trust_forwarded_for_by_default(repo_root: Path) -> None:
    assignments = _env_assignments(repo_root)

    assert assignments["RATE_LIMIT_TRUST_FORWARDED_FOR"] == "false"
    assert assignments["RATE_LIMIT_TRUSTED_PROXIES"] == ""
    assert assignments["TRUSTED_PROXY_COUNT"] == "1"


def test_env_example_documents_when_to_trust_the_proxy(repo_root: Path) -> None:
    text = (repo_root / ".env.example").read_text(encoding="utf-8")

    assert "RATE_LIMIT_TRUSTED_PROXIES" in text and "TRUSTED_PROXY_COUNT" in text
    assert "reverse proxy" in text


def test_env_example_values_match_the_settings_defaults(repo_root: Path) -> None:
    from app.core.settings import Settings

    assignments = _env_assignments(repo_root)
    defaults = Settings(_env_file=None)

    assert assignments["RATE_LIMIT_TRUST_FORWARDED_FOR"] == str(defaults.rate_limit_trust_forwarded_for).lower()
    assert assignments["RATE_LIMIT_TRUSTED_PROXIES"] == defaults.rate_limit_trusted_proxies
    assert int(assignments["TRUSTED_PROXY_COUNT"]) == defaults.trusted_proxy_count


def test_compose_defaults_to_not_trusting_forwarded_for(compose: dict) -> None:
    environment = get_service(compose, "backend")["environment"]

    assert environment["RATE_LIMIT_TRUST_FORWARDED_FOR"] == "${RATE_LIMIT_TRUST_FORWARDED_FOR:-false}"
    assert environment["RATE_LIMIT_TRUSTED_PROXIES"] == "${RATE_LIMIT_TRUSTED_PROXIES:-}"
    assert environment["TRUSTED_PROXY_COUNT"] == "${TRUSTED_PROXY_COUNT:-1}"


@pytest.mark.parametrize(
    "variable",
    [
        "RATE_LIMIT_TILES_PER_MINUTE",
        "RATE_LIMIT_GEOCODE_PER_MINUTE",
        "RATE_LIMIT_ADDRESS_SEARCH_PER_MINUTE",
        "RATE_LIMIT_DATA_PER_MINUTE",
    ],
)
def test_compose_passes_the_new_rate_limit_thresholds(compose: dict, variable: str) -> None:
    environment = get_service(compose, "backend")["environment"]

    assert str(environment[variable]).startswith("${" + variable + ":-")


def test_env_example_and_compose_expose_the_single_flight_settings(repo_root: Path, compose: dict) -> None:
    from app.core.settings import Settings

    assignments = _env_assignments(repo_root)
    environment = get_service(compose, "backend")["environment"]
    defaults = Settings(_env_file=None)

    assert assignments["ANALYSIS_SINGLEFLIGHT_ENABLED"] == "true" and defaults.analysis_singleflight_enabled
    assert float(assignments["ANALYSIS_SINGLEFLIGHT_WAIT_SECONDS"]) == defaults.analysis_singleflight_wait_seconds
    assert assignments["ANALYSIS_SINGLEFLIGHT_CROSS_PROCESS"] == "true" and defaults.analysis_singleflight_cross_process
    for variable in (
        "ANALYSIS_SINGLEFLIGHT_ENABLED",
        "ANALYSIS_SINGLEFLIGHT_WAIT_SECONDS",
        "ANALYSIS_SINGLEFLIGHT_CROSS_PROCESS",
    ):
        assert str(environment[variable]).startswith("${" + variable + ":-")
