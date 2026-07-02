"""Pomocnicze funkcje walidacji konfiguracji Docker Compose, Dockerfile i requirements."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

DB_IMAGE = "postgis/postgis:16-3.4"
DB_SERVICE_NAME = "db"
BACKEND_SERVICE_NAME = "backend"
POSTGRES_VOLUME = "postgres_data"

REQUIRED_SYSTEM_PACKAGES: tuple[str, ...] = (
    "build-essential",
    "libpq-dev",
    "gdal-bin",
    "proj-bin",
    "libgeos-dev",
    "libpango-1.0-0",
    "libpangoft2-1.0-0",
    "libcairo2",
    "libffi-dev",
    "fonts-dejavu-core",
)

REQUIRED_PYTHON_PACKAGES: tuple[str, ...] = (
    "fastapi",
    "uvicorn",
    "pydantic",
    "pydantic-settings",
    "sqlalchemy",
    "geoalchemy2",
    "psycopg2-binary",
    "shapely",
    "pyproj",
    "httpx",
    "pdfplumber",
    "beautifulsoup4",
    "weasyprint",
    "alembic",
    "pytest",
    "respx",
    "pytest-cov",
)

REQUIRED_ENV_EXAMPLE_VARS: tuple[str, ...] = (
    "POSTGRES_DB=dzialki",
    "POSTGRES_USER=app",
    "POSTGRES_PASSWORD=app",
    "DATABASE_URL=postgresql+psycopg2://app:app@db:5432/dzialki",
)


def find_repo_root(start: Path | None = None) -> Path:
    """Szuka katalogu głównego repozytorium."""
    current = (start or Path(__file__)).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "README.md").is_file() and (candidate / "backend").is_dir():
            return candidate
    raise FileNotFoundError("Nie znaleziono katalogu głównego repozytorium.")


def load_compose(repo_root: Path) -> dict[str, Any]:
    """Wczytuje i parsuje docker-compose.yml."""
    compose_path = repo_root / "docker-compose.yml"
    if not compose_path.is_file():
        raise FileNotFoundError("Brak pliku docker-compose.yml")
    with compose_path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError("docker-compose.yml ma nieprawidłową strukturę")
    return data


def get_service(compose: dict[str, Any], name: str) -> dict[str, Any]:
    """Zwraca konfigurację usługi Compose po nazwie."""
    services = compose.get("services", {})
    if name not in services:
        raise KeyError(f"Brak usługi {name!r} w docker-compose.yml")
    service = services[name]
    if not isinstance(service, dict):
        raise ValueError(f"Usługa {name!r} ma nieprawidłową strukturę")
    return service


def validate_db_service(compose: dict[str, Any]) -> list[str]:
    """Sprawdza konfigurację usługi db; zwraca listę błędów."""
    errors: list[str] = []
    try:
        db = get_service(compose, DB_SERVICE_NAME)
    except (KeyError, ValueError) as exc:
        return [str(exc)]

    if db.get("image") != DB_IMAGE:
        errors.append(f"Oczekiwano obrazu {DB_IMAGE!r}, jest {db.get('image')!r}")

    environment = db.get("environment", {})
    if not isinstance(environment, dict):
        errors.append("Sekcja environment usługi db musi być słownikiem")
    else:
        for var in ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"):
            value = environment.get(var, "")
            if not str(value).startswith("${"):
                errors.append(f"Zmienna {var} powinna pochodzić z pliku .env (${var})")

    volumes = db.get("volumes", [])
    volume_found = any(
        isinstance(entry, str) and entry.startswith(f"{POSTGRES_VOLUME}:")
        for entry in volumes
    )
    if not volume_found:
        errors.append(f"Brak wolumenu {POSTGRES_VOLUME} dla danych PostgreSQL")

    healthcheck = db.get("healthcheck", {})
    if not isinstance(healthcheck, dict):
        errors.append("Brak healthcheck dla usługi db")
    else:
        test_cmd = healthcheck.get("test", [])
        test_text = " ".join(str(part) for part in test_cmd)
        if "pg_isready" not in test_text:
            errors.append("Healthcheck db musi używać pg_isready")

    compose_volumes = compose.get("volumes", {})
    if POSTGRES_VOLUME not in compose_volumes:
        errors.append(f"Brak named volume {POSTGRES_VOLUME!r} w sekcji volumes")

    return errors


def validate_backend_service(compose: dict[str, Any]) -> list[str]:
    """Sprawdza konfigurację usługi backend; zwraca listę błędów."""
    errors: list[str] = []
    try:
        backend = get_service(compose, BACKEND_SERVICE_NAME)
    except (KeyError, ValueError) as exc:
        return [str(exc)]

    build = backend.get("build", {})
    if isinstance(build, dict):
        context = build.get("context", "")
    else:
        context = build
    if context not in ("./backend", "backend"):
        errors.append("Backend powinien budować się z kontekstu ./backend")

    environment = backend.get("environment", {})
    if not isinstance(environment, dict):
        errors.append("Sekcja environment usługi backend musi być słownikiem")
    else:
        database_url = str(environment.get("DATABASE_URL", ""))
        if database_url != "${DATABASE_URL}":
            errors.append("DATABASE_URL backendu powinien pochodzić ze zmiennej ${DATABASE_URL}")

    depends_on = backend.get("depends_on", {})
    if not isinstance(depends_on, dict) or DB_SERVICE_NAME not in depends_on:
        errors.append("Backend musi zależeć od usługi db")
    else:
        db_dep = depends_on[DB_SERVICE_NAME]
        if not isinstance(db_dep, dict) or db_dep.get("condition") != "service_healthy":
            errors.append("Backend powinien czekać na service_healthy usługi db")

    ports = backend.get("ports", [])
    if "8000:8000" not in ports:
        errors.append("Backend powinien wystawiać port 8000:8000")

    return errors


def validate_env_example(repo_root: Path) -> list[str]:
    """Sprawdza wymagane zmienne w .env.example."""
    env_path = repo_root / ".env.example"
    if not env_path.is_file():
        return ["Brak pliku .env.example"]

    content = env_path.read_text(encoding="utf-8")
    errors: list[str] = []
    for required_line in REQUIRED_ENV_EXAMPLE_VARS:
        if required_line not in content:
            errors.append(f"Brak wpisu w .env.example: {required_line}")

    if "localhost" in content and "DATABASE_URL" in content:
        for line in content.splitlines():
            if line.startswith("DATABASE_URL=") and "localhost" in line:
                errors.append("DATABASE_URL nie może używać localhost — tylko host db")

    return errors


def load_dockerfile(repo_root: Path) -> str:
    """Wczytuje zawartość backend/Dockerfile."""
    dockerfile_path = repo_root / "backend" / "Dockerfile"
    if not dockerfile_path.is_file():
        raise FileNotFoundError("Brak pliku backend/Dockerfile")
    return dockerfile_path.read_text(encoding="utf-8")


def validate_dockerfile(content: str) -> list[str]:
    """Sprawdza Dockerfile backendu; zwraca listę błędów."""
    errors: list[str] = []
    if "python:3.13-slim" not in content:
        errors.append("Dockerfile powinien bazować na python:3.13-slim")

    for package in REQUIRED_SYSTEM_PACKAGES:
        if package not in content:
            errors.append(f"Brak pakietu systemowego w Dockerfile: {package}")

    if "COPY requirements.txt" not in content:
        errors.append("Dockerfile powinien kopiować requirements.txt")
    if "COPY app/" not in content:
        errors.append("Dockerfile powinien kopiować katalog app/")

    copy_req_pos = content.find("COPY requirements.txt")
    copy_app_pos = content.find("COPY app/")
    if copy_req_pos == -1 or copy_app_pos == -1 or copy_req_pos > copy_app_pos:
        errors.append("COPY requirements.txt musi występować przed COPY app/")

    if not re.search(r'CMD\s*\[.*uvicorn.*app\.main:app', content, re.IGNORECASE):
        errors.append("CMD powinien uruchamiać uvicorn app.main:app")

    if "0.0.0.0" not in content or "8000" not in content:
        errors.append("CMD uvicorn powinien nasłuchiwać na 0.0.0.0:8000")

    return errors


def parse_requirement_names(requirements_path: Path) -> set[str]:
    """Wyciąga nazwy pakietów z requirements.txt (bez wersji i extras)."""
    if not requirements_path.is_file():
        raise FileNotFoundError("Brak pliku backend/requirements.txt")

    names: set[str] = set()
    for raw_line in requirements_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        package = re.split(r"[<>=!~\[]", line, maxsplit=1)[0].strip()
        names.add(package.lower())
    return names


def missing_requirements(repo_root: Path) -> list[str]:
    """Zwraca brakujące wymagane pakiety w requirements.txt."""
    names = parse_requirement_names(repo_root / "backend" / "requirements.txt")
    missing: list[str] = []
    for package in REQUIRED_PYTHON_PACKAGES:
        if package.lower() not in names:
            missing.append(package)
    return missing
