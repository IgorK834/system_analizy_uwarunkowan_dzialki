"""Testy statycznej konfiguracji workflow GitHub Actions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from tests.repo_structure import find_repo_root

CI_WORKFLOW_PATH = ".github/workflows/ci.yml"


def _load_ci_workflow(repo_root: Path) -> dict[str, Any]:
    workflow_path = repo_root / CI_WORKFLOW_PATH
    with workflow_path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError("Plik ci.yml ma nieprawidłową strukturę")
    return data


def _workflow_text(repo_root: Path) -> str:
    return (repo_root / CI_WORKFLOW_PATH).read_text(encoding="utf-8")


def _collect_step_commands(workflow: dict[str, Any]) -> str:
    commands: list[str] = []
    jobs = workflow.get("jobs", {})
    if not isinstance(jobs, dict):
        return ""

    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        for step in job.get("steps", []):
            if not isinstance(step, dict):
                continue
            if "run" in step:
                commands.append(str(step["run"]))
            if "uses" in step:
                commands.append(str(step["uses"]))
    return "\n".join(commands)


def test_ci_workflow_file_exists() -> None:
    repo_root = find_repo_root()
    assert (repo_root / CI_WORKFLOW_PATH).is_file()


def test_ci_workflow_is_valid_yaml() -> None:
    workflow = _load_ci_workflow(find_repo_root())
    assert "jobs" in workflow


def _workflow_triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    # Klucz 'on' w YAML bywa parsowany jako True — obsługujemy oba warianty.
    triggers = workflow.get("on") or workflow.get(True) or {}
    if not isinstance(triggers, dict):
        return {}
    return triggers


def test_ci_triggers_on_push_to_main() -> None:
    workflow = _load_ci_workflow(find_repo_root())
    push = _workflow_triggers(workflow).get("push", {})
    branches = push.get("branches", [])
    assert "main" in branches


def test_ci_triggers_on_pull_request() -> None:
    workflow = _load_ci_workflow(find_repo_root())
    pull_request = _workflow_triggers(workflow).get("pull_request", {})
    branches = pull_request.get("branches", [])
    assert "main" in branches


def test_ci_has_checkout_step() -> None:
    commands = _collect_step_commands(_load_ci_workflow(find_repo_root()))
    assert "actions/checkout" in commands


def test_ci_runs_docker_compose_config() -> None:
    commands = _collect_step_commands(_load_ci_workflow(find_repo_root()))
    assert "docker compose --profile test config" in commands


def test_ci_builds_backend_image() -> None:
    commands = _collect_step_commands(_load_ci_workflow(find_repo_root()))
    # AU-010: obraz produkcyjny (`runtime`) i testowy (`test`) budowane razem.
    assert "docker compose --profile test build backend backend-test" in commands


def test_ci_runs_pytest_inside_container() -> None:
    commands = _collect_step_commands(_load_ci_workflow(find_repo_root()))
    assert "docker compose --profile test run" in commands
    assert "backend-test" in commands
    assert "pytest" in commands


def test_ci_runs_on_ubuntu_latest() -> None:
    workflow = _load_ci_workflow(find_repo_root())
    jobs = workflow.get("jobs", {})
    for job in jobs.values():
        if isinstance(job, dict):
            assert job.get("runs-on") == "ubuntu-latest"


def test_ci_uses_env_example() -> None:
    commands = _collect_step_commands(_load_ci_workflow(find_repo_root()))
    assert ".env.example" in commands


def test_ci_has_cleanup_step() -> None:
    commands = _collect_step_commands(_load_ci_workflow(find_repo_root()))
    assert "docker compose --profile test down" in commands


def test_ci_does_not_contain_hardcoded_password() -> None:
    content = _workflow_text(find_repo_root())
    assert "POSTGRES_PASSWORD=app" not in content


def test_ci_does_not_require_secrets() -> None:
    content = _workflow_text(find_repo_root())
    assert "secrets." not in content
