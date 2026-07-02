"""Testy pliku requirements.txt backendu."""

from pathlib import Path

import pytest

from tests.docker_compose_config import (
    REQUIRED_PYTHON_PACKAGES,
    find_repo_root,
    missing_requirements,
    parse_requirement_names,
)


@pytest.fixture
def repo_root() -> Path:
    return find_repo_root()


@pytest.fixture
def requirements_path(repo_root: Path) -> Path:
    return repo_root / "backend" / "requirements.txt"


def test_requirements_file_exists(requirements_path: Path) -> None:
    assert requirements_path.is_file()


@pytest.mark.parametrize("package", REQUIRED_PYTHON_PACKAGES)
def test_required_package_is_listed(package: str, requirements_path: Path) -> None:
    names = parse_requirement_names(requirements_path)
    assert package.lower() in names


def test_no_missing_required_packages(repo_root: Path) -> None:
    assert missing_requirements(repo_root) == []


def test_pyyaml_present_for_compose_tests(requirements_path: Path) -> None:
    names = parse_requirement_names(requirements_path)
    assert "pyyaml" in names
