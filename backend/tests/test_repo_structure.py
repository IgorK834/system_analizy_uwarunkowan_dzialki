"""Testy weryfikujące strukturę monorepo."""

from pathlib import Path

import pytest

from tests.repo_structure import (
    find_env_files,
    find_repo_root,
    missing_directories,
    missing_gitignore_entries,
    missing_init_files,
    readme_mentions_docker_compose,
)


@pytest.fixture
def repo_root() -> Path:
    """Katalog główny repozytorium."""
    return find_repo_root()


def test_repo_root_exists() -> None:
    """Katalog główny repozytorium jest wykrywalny."""
    root = find_repo_root()
    assert root.is_dir()
    assert (root / "README.md").is_file()


def test_required_directories_exist(repo_root: Path) -> None:
    """Wszystkie wymagane katalogi istnieją."""
    missing = missing_directories(repo_root)
    assert missing == [], f"Brakujące katalogi: {missing}"


def test_required_init_files_exist(repo_root: Path) -> None:
    """Wszystkie wymagane pliki __init__.py istnieją."""
    missing = missing_init_files(repo_root)
    assert missing == [], f"Brakujące pliki __init__.py: {missing}"


def test_readme_exists_and_mentions_docker_compose(repo_root: Path) -> None:
    """README.md istnieje i wskazuje Docker Compose jako sposób uruchomienia."""
    readme_path = repo_root / "README.md"
    assert readme_path.is_file(), "Brak pliku README.md"
    assert readme_mentions_docker_compose(repo_root), (
        "README.md nie zawiera wzmianki o Docker Compose"
    )


def test_gitignore_exists_and_has_required_entries(repo_root: Path) -> None:
    """Plik .gitignore istnieje i zawiera wymagane wpisy."""
    gitignore_path = repo_root / ".gitignore"
    assert gitignore_path.is_file(), "Brak pliku .gitignore"

    missing = missing_gitignore_entries(repo_root)
    assert missing == [], f"Brakujące wpisy w .gitignore: {missing}"


def test_no_env_files_in_repository(repo_root: Path) -> None:
    """Żaden plik .env nie jest obecny w repozytorium."""
    env_files = find_env_files(repo_root)
    assert env_files == [], f"W repozytorium znaleziono pliki .env: {env_files}"
