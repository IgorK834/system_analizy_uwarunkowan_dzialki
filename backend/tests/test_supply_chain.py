"""Łańcuch dostaw (AU-010): lockfile z hashami, obrazy wieloetapowe, przypięte obrazy bazowe, bramki CI.

Testy czytają pliki repozytorium (``REPO_ROOT``), nie uruchamiają Dockera ani sieci — pilnują, żeby
powtarzalność buildu i bramki bezpieczeństwa nie zniknęły po cichu przy kolejnej zmianie.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from tests.docker_compose_config import find_repo_root, parse_requirement_names

DIGEST = re.compile(r"@sha256:[0-9a-f]{64}")
TEST_ONLY = {"pytest", "pytest-asyncio", "pytest-cov", "respx", "coverage", "ruff", "mypy", "pip-audit"}


@pytest.fixture(scope="module")
def repo() -> Path:
    return find_repo_root()


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _locked(path: Path) -> dict[str, tuple[str, int]]:
    """Pakiet → (wersja, liczba skrótów) z pliku wygenerowanego przez ``pip-compile --generate-hashes``."""
    entries: dict[str, tuple[str, int]] = {}
    current: str | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        match = re.match(r"^([A-Za-z0-9_.-]+)==([^\s\;]+)", line)
        if match and not raw.startswith((" ", "#")):
            current = _normalize(match.group(1))
            entries[current] = (match.group(2), 0)
        elif line.startswith("--hash=sha256:") and current:
            version, count = entries[current]
            entries[current] = (version, count + 1)
    return entries


# --- Zależności Pythona -----------------------------------------------------------------------------


def test_old_unpinned_requirements_file_is_gone(repo: Path) -> None:
    assert not (repo / "backend" / "requirements.txt").exists()


def test_every_locked_package_is_pinned_and_hashed(repo: Path) -> None:
    for name in ("requirements.lock", "requirements-dev.txt"):
        locked = _locked(repo / "backend" / name)
        assert len(locked) > 20, name
        unhashed = [package for package, (_, hashes) in locked.items() if hashes == 0]
        assert unhashed == [], f"{name}: pakiety bez skrótów SHA-256: {unhashed}"


def test_lock_contains_every_direct_runtime_dependency(repo: Path) -> None:
    direct = {_normalize(name) for name in parse_requirement_names(repo / "backend" / "requirements.in")}
    locked = set(_locked(repo / "backend" / "requirements.lock"))

    assert direct <= locked, sorted(direct - locked)


def test_production_lock_has_no_test_tooling(repo: Path) -> None:
    locked = set(_locked(repo / "backend" / "requirements.lock"))

    assert locked & TEST_ONLY == set()


def test_dev_lock_has_the_tooling_and_agrees_with_the_production_versions(repo: Path) -> None:
    production = _locked(repo / "backend" / "requirements.lock")
    dev = _locked(repo / "backend" / "requirements-dev.txt")

    assert {"pytest", "pytest-asyncio", "pytest-cov", "respx", "ruff", "mypy", "pip-audit"} <= set(dev)
    disagreements = {
        package: (production[package][0], dev[package][0])
        for package in set(production) & set(dev)
        if production[package][0] != dev[package][0]
    }
    assert disagreements == {}


# --- Dockerfile'e ---------------------------------------------------------------------------------------


def _stages(dockerfile: str) -> dict[str, str]:
    """Etap → treść instrukcji (od ``FROM`` do następnego ``FROM``), w kolejności pliku."""
    stages: dict[str, str] = {}
    current: str | None = None
    for line in dockerfile.splitlines():
        match = re.match(r"^FROM\s+\S+\s+AS\s+(\S+)", line)
        if match:
            current = match.group(1)
            stages[current] = ""
        if current:
            stages[current] += line + "\n"
    return stages


@pytest.mark.parametrize("path", ["backend/Dockerfile", "frontend/Dockerfile"])
def test_base_images_are_pinned_by_digest(repo: Path, path: str) -> None:
    from_lines = [
        line for line in (repo / path).read_text(encoding="utf-8").splitlines() if re.match(r"^FROM\s", line)
    ]
    external = [line for line in from_lines if not re.match(r"^FROM\s+(deps|app-base|source|dependencies|build)\b", line)]

    assert external
    assert all(DIGEST.search(line) for line in external), external


def test_backend_runtime_stage_is_last_and_free_of_tests_scripts_and_dev_tooling(repo: Path) -> None:
    stages = _stages((repo / "backend" / "Dockerfile").read_text(encoding="utf-8"))

    assert list(stages)[-1] == "runtime"  # domyślny cel `docker build` to obraz produkcyjny
    assert {"deps", "app-base", "test", "runtime"} <= set(stages)
    instructions = "\n".join(
        line
        for stage in ("deps", "app-base", "runtime")
        for line in stages[stage].splitlines()
        if not line.lstrip().startswith("#")
    )
    runtime_chain = instructions
    for forbidden in ("COPY tests/", "COPY scripts/", "requirements-dev", "pytest"):
        assert forbidden not in runtime_chain, forbidden


def test_backend_test_stage_adds_the_tooling_with_hash_verification(repo: Path) -> None:
    test_stage = _stages((repo / "backend" / "Dockerfile").read_text(encoding="utf-8"))["test"]

    assert "requirements-dev.txt" in test_stage and "COPY tests/" in test_stage and "COPY scripts/" in test_stage
    assert "COPY mypy-baseline.txt" in test_stage  # bramka mypy w CI czyta bazę z obrazu


def test_every_pip_install_in_the_backend_dockerfile_requires_hashes(repo: Path) -> None:
    installs = re.findall(r"^RUN pip install[^\n]*", (repo / "backend" / "Dockerfile").read_text(encoding="utf-8"), re.M)

    assert len(installs) == 2
    assert all("--require-hashes" in line and "--no-deps" in line for line in installs), installs


def test_compose_builds_runtime_for_the_stack_and_test_target_only_under_a_profile(repo: Path) -> None:
    services = yaml.safe_load((repo / "docker-compose.yml").read_text(encoding="utf-8"))["services"]

    assert services["backend"]["build"]["target"] == "runtime"
    assert services["address-index-sync"]["build"]["target"] == "runtime"
    assert services["backend-test"]["build"]["target"] == "test"
    assert services["backend-test"]["profiles"] == ["test"]
    assert services["backend-test"]["environment"] == services["backend"]["environment"]
    assert DIGEST.search(services["db"]["image"])


# --- Bramki w CI ------------------------------------------------------------------------------------------


def _workflow(repo: Path) -> dict:
    return yaml.safe_load((repo / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))


def _commands(job: dict) -> str:
    return "\n".join(str(step.get("run", "")) for step in job["steps"])


def test_ci_gates_fail_on_known_vulnerabilities_lint_and_types(repo: Path) -> None:
    jobs = _workflow(repo)["jobs"]
    backend = _commands(jobs["backend"])
    frontend = _commands(jobs["frontend"])

    assert "pip-audit --require-hashes" in backend and "requirements.lock" in backend
    assert "requirements-dev.txt" in backend
    assert "ruff check" in backend and "scripts/check_mypy_baseline.py" in backend
    assert "--cov-fail-under=80" in backend and "backend-test" in backend
    assert "npm audit --omit=dev --audit-level=high" in frontend
    assert "npm run lint" in frontend and "npm run typecheck" in frontend and "npm run test:coverage" in frontend


def test_ci_checks_that_the_runtime_image_has_no_tests_and_that_the_build_is_reproducible(repo: Path) -> None:
    jobs = _workflow(repo)["jobs"]

    assert "dzialki-backend-runtime" in _commands(jobs["backend"])
    assert "/app/tests" in _commands(jobs["backend"])
    reproducible = _commands(jobs["backend-reproducible"])
    assert "--no-cache" in reproducible and "diff freeze-1.txt freeze-2.txt" in reproducible


def test_no_automatic_dependency_update_bot_is_configured(repo: Path) -> None:
    """Aktualizacje są ręczne: konfiguracja botów otwierających PR-y nie wraca bez uzgodnienia."""
    assert not (repo / ".github" / "dependabot.yml").exists()
    assert not (repo / ".github" / "dependabot.yaml").exists()
    assert not (repo / "renovate.json").exists()


# --- Frontend ---------------------------------------------------------------------------------------------


def _version(raw: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", raw.split("-")[0])[:3])


def test_frontend_dependencies_are_past_the_audited_vulnerabilities(repo: Path) -> None:
    package = json.loads((repo / "frontend" / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((repo / "frontend" / "package-lock.json").read_text(encoding="utf-8"))["packages"]

    assert _version(package["dependencies"]["next"]) >= (16, 3, 8)
    assert _version(package["dependencies"]["maplibre-gl"]) >= (6, 12, 0)
    assert _version(lock["node_modules/next"]["version"]) >= (16, 3, 8)
    assert _version(lock["node_modules/maplibre-gl"]["version"]) >= (6, 12, 0)
    assert _version(lock["node_modules/postcss"]["version"]) >= (8, 5, 23)
    assert _version(lock["node_modules/nanoid"]["version"]) >= (3, 3, 18)
    assert _version(lock["node_modules/sharp"]["version"]) >= (0, 35, 5)


def test_frontend_lockfile_matches_package_json(repo: Path) -> None:
    package = json.loads((repo / "frontend" / "package.json").read_text(encoding="utf-8"))
    root = json.loads((repo / "frontend" / "package-lock.json").read_text(encoding="utf-8"))["packages"][""]

    assert root["dependencies"] == package["dependencies"]
    assert root["devDependencies"] == package["devDependencies"]
    assert root.get("overrides", package.get("overrides")) == package.get("overrides")


def test_frontend_has_a_lint_script_and_config(repo: Path) -> None:
    package = json.loads((repo / "frontend" / "package.json").read_text(encoding="utf-8"))

    assert package["scripts"]["lint"] == "eslint ."
    assert (repo / "frontend" / "eslint.config.mjs").is_file()
    assert "eslint" in package["devDependencies"]
