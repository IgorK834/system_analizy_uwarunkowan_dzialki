"""Testy konfiguracji Dockerfile backendu."""

from pathlib import Path

import pytest

from tests.docker_compose_config import (
    find_repo_root,
    load_dockerfile,
    validate_backend_service,
    validate_dockerfile,
)
from tests.docker_compose_config import load_compose


@pytest.fixture
def repo_root() -> Path:
    return find_repo_root()


@pytest.fixture
def dockerfile_content(repo_root: Path) -> str:
    return load_dockerfile(repo_root)


def test_dockerfile_exists(repo_root: Path) -> None:
    assert (repo_root / "backend" / "Dockerfile").is_file()


def test_dockerfile_uses_python_313_slim(dockerfile_content: str) -> None:
    assert "python:3.13-slim" in dockerfile_content


def test_dockerfile_installs_required_system_packages(dockerfile_content: str) -> None:
    for package in (
        "build-essential",
        "libpq-dev",
        "gdal-bin",
        "proj-bin",
        "libgeos-dev",
        "libpango-1.0-0",
        "libcairo2",
        "libffi-dev",
        "fonts-dejavu-core",
    ):
        assert package in dockerfile_content


def test_dockerfile_copies_requirements_before_app(dockerfile_content: str) -> None:
    assert dockerfile_content.find("COPY requirements.txt") < dockerfile_content.find(
        "COPY app/"
    )


def test_dockerfile_cmd_runs_uvicorn(dockerfile_content: str) -> None:
    assert "uvicorn" in dockerfile_content
    assert "app.main:app" in dockerfile_content
    assert "0.0.0.0" in dockerfile_content
    assert "8000" in dockerfile_content


def test_validate_dockerfile_reports_no_errors(dockerfile_content: str) -> None:
    assert validate_dockerfile(dockerfile_content) == []


def test_backend_service_in_compose(repo_root: Path) -> None:
    compose = load_compose(repo_root)
    assert validate_backend_service(compose) == []
