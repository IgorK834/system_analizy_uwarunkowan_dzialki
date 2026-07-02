"""Pomocnicze funkcje weryfikacji struktury monorepo."""

from __future__ import annotations

from pathlib import Path

REQUIRED_DIRECTORIES: tuple[str, ...] = (
    "backend",
    "backend/app",
    "backend/app/routers",
    "backend/app/services",
    "backend/app/schemas",
    "backend/app/models",
    "backend/app/db",
    "backend/app/core",
    "backend/tests",
    "frontend",
    "docs",
    "scripts",
)

REQUIRED_INIT_FILES: tuple[str, ...] = (
    "backend/app/__init__.py",
    "backend/app/routers/__init__.py",
    "backend/app/services/__init__.py",
    "backend/app/schemas/__init__.py",
    "backend/app/models/__init__.py",
    "backend/app/db/__init__.py",
    "backend/app/core/__init__.py",
    "backend/tests/__init__.py",
)

REQUIRED_GITIGNORE_ENTRIES: tuple[str, ...] = (
    "context.md",
    ".env",
    "backend/.env",
    "frontend/.env.local",
    ".venv",
    "node_modules",
    ".next",
    "__pycache__",
    ".pytest_cache",
    "tmp",
)

ENV_FILE_NAMES: frozenset[str] = frozenset({".env", ".env.local"})


def find_repo_root(start: Path | None = None) -> Path:
    """Szuka katalogu głównego repozytorium (zawiera README.md i backend/)."""
    current = (start or Path(__file__)).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "README.md").is_file() and (candidate / "backend").is_dir():
            return candidate
    raise FileNotFoundError("Nie znaleziono katalogu głównego repozytorium.")


def missing_directories(repo_root: Path) -> list[str]:
    """Zwraca listę brakujących wymaganych katalogów."""
    return [
        relative_path
        for relative_path in REQUIRED_DIRECTORIES
        if not (repo_root / relative_path).is_dir()
    ]


def missing_init_files(repo_root: Path) -> list[str]:
    """Zwraca listę brakujących plików __init__.py."""
    return [
        relative_path
        for relative_path in REQUIRED_INIT_FILES
        if not (repo_root / relative_path).is_file()
    ]


def readme_mentions_docker_compose(repo_root: Path) -> bool:
    """Sprawdza, czy README.md wspomina o Docker Compose."""
    readme_path = repo_root / "README.md"
    if not readme_path.is_file():
        return False
    content = readme_path.read_text(encoding="utf-8").lower()
    return "docker compose" in content


def missing_gitignore_entries(repo_root: Path) -> list[str]:
    """Zwraca wpisy .gitignore, których brakuje w pliku."""
    gitignore_path = repo_root / ".gitignore"
    if not gitignore_path.is_file():
        return list(REQUIRED_GITIGNORE_ENTRIES)

    content = gitignore_path.read_text(encoding="utf-8")
    return [entry for entry in REQUIRED_GITIGNORE_ENTRIES if entry not in content]


def find_env_files(repo_root: Path) -> list[str]:
    """Wykrywa pliki .env w repozytorium (nie powinny być commitowane)."""
    found: list[str] = []
    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        if path.name in ENV_FILE_NAMES or path.name.startswith(".env."):
            # Pomijamy szablony bez sekretów
            if path.name.endswith(".example"):
                continue
            found.append(str(path.relative_to(repo_root)))
    return sorted(found)
