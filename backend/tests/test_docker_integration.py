"""Testy integracyjne wymagające uruchomionego Dockera — pomijane domyślnie."""

import subprocess
import shutil
from pathlib import Path

import pytest
from sqlalchemy import text

from tests.docker_compose_config import find_repo_root

pytestmark = pytest.mark.integration


def _run(command: list[str], repo_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )


def _use_direct_container_checks() -> bool:
    return Path("/app/app").is_dir() and shutil.which("docker") is None


@pytest.fixture
def repo_root() -> Path:
    if _use_direct_container_checks():
        return Path("/app")
    return find_repo_root()


def test_postgis_version_is_34x(repo_root: Path) -> None:
    if _use_direct_container_checks():
        from app.db.session import SessionLocal

        with SessionLocal() as db:
            version = db.execute(text("SELECT PostGIS_Version();")).scalar_one()

        assert "3.4" in version
        return

    result = _run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "db",
            "psql",
            "-U",
            "app",
            "-d",
            "dzialki",
            "-tAc",
            "SELECT PostGIS_Version();",
        ],
        repo_root,
    )
    assert result.returncode == 0, result.stderr
    assert "3.4" in result.stdout


def test_spatial_ref_sys_has_srid_2180(repo_root: Path) -> None:
    if _use_direct_container_checks():
        from app.db.session import SessionLocal

        with SessionLocal() as db:
            count = db.execute(
                text("SELECT count(*) FROM spatial_ref_sys WHERE srid=2180;")
            ).scalar_one()

        assert count == 1
        return

    result = _run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "db",
            "psql",
            "-U",
            "app",
            "-d",
            "dzialki",
            "-tAc",
            "SELECT count(*) FROM spatial_ref_sys WHERE srid=2180;",
        ],
        repo_root,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1"


def test_backend_imports_gis_and_pdf_libraries(repo_root: Path) -> None:
    if _use_direct_container_checks():
        import fastapi  # noqa: F401
        import pyproj  # noqa: F401
        import shapely  # noqa: F401
        import sqlalchemy  # noqa: F401
        import weasyprint  # noqa: F401

        assert True
        return

    result = _run(
        [
            "docker",
            "compose",
            "run",
            "--rm",
            "backend",
            "python",
            "-c",
            "import shapely, pyproj, weasyprint, sqlalchemy, fastapi; print('OK')",
        ],
        repo_root,
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
