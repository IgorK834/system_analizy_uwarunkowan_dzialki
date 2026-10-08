"""Workflow ``live-smoke.yml`` (AU-011): ręczny i tygodniowy, nie blokuje PR, nie zapisuje w repozytorium."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.docker_compose_config import find_repo_root

WORKFLOW = ".github/workflows/live-smoke.yml"


@pytest.fixture(scope="module")
def repo() -> Path:
    return find_repo_root()


@pytest.fixture(scope="module")
def workflow(repo: Path) -> dict:
    # PyYAML czyta klucz ``on`` jako ``True`` (YAML 1.1) — normalizujemy.
    raw = yaml.safe_load((repo / WORKFLOW).read_text(encoding="utf-8"))
    return {("on" if key is True else key): value for key, value in raw.items()}


def _commands(workflow: dict) -> str:
    steps = workflow["jobs"]["live-smoke"]["steps"]
    return "\n".join(str(step.get("run", "")) for step in steps)


def test_it_runs_on_demand_and_weekly_but_never_on_push_or_pull_request(workflow: dict) -> None:
    triggers = workflow["on"]

    assert set(triggers) == {"workflow_dispatch", "schedule"}  # nie blokuje PR i nie startuje po każdym pushu
    (schedule,) = triggers["schedule"]
    minute, hour, day_of_month, month, day_of_week = schedule["cron"].split()
    assert day_of_month == month == "*" and day_of_week.isdigit()  # raz w tygodniu, w konkretny dzień
    assert minute.isdigit() and hour.isdigit()


def test_it_has_read_only_permissions_and_does_not_touch_git(workflow: dict) -> None:
    assert workflow["permissions"] == {"contents": "read"}
    commands = _commands(workflow)
    for forbidden in ("git push", "git commit", "git config", "gh pr", "gh api"):
        assert forbidden not in commands, forbidden


def test_it_runs_the_smoke_script_on_the_reference_manifest_with_concurrency_three_by_default(workflow: dict) -> None:
    steps = workflow["jobs"]["live-smoke"]["steps"]
    smoke = next(step for step in steps if "live_smoke_corpus.py" in str(step.get("run", "")))

    assert "scripts/live_smoke_corpus.py" in smoke["run"]
    assert "tests/fixtures/reference_corpus/manifest.json" in smoke["run"]
    assert "--base-url http://backend:8000" in smoke["run"]
    assert "--output" in smoke["run"] and "${RUN_DATE}.md" in smoke["run"]
    assert smoke["env"]["CONCURRENCY"].endswith("|| '3' }}")  # domyślnie 3
    # Dane z workflow_dispatch nie są wklejane do polecenia powłoki.
    assert "${{ inputs." not in smoke["run"]


def test_it_also_runs_the_live_mode_of_the_evaluator_and_always_publishes_the_result(workflow: dict) -> None:
    steps = workflow["jobs"]["live-smoke"]["steps"]
    evaluation = next(step for step in steps if "evaluate_reference_corpus.py" in str(step.get("run", "")))
    assert "--mode live" in evaluation["run"]
    assert "always()" in evaluation["if"]

    upload = next(step for step in steps if str(step.get("uses", "")).startswith("actions/upload-artifact"))
    assert upload["if"] == "always()"  # raport powstaje także (zwłaszcza) przy awarii 5xx
    assert "docs/evaluation/results/live-smoke/" in upload["with"]["path"]
    assert any(step.get("if") == "always()" and "down -v" in str(step.get("run", "")) for step in steps)


def test_two_runs_never_overlap(workflow: dict) -> None:
    assert workflow["concurrency"] == {"group": "live-smoke", "cancel-in-progress": False}
    assert workflow["jobs"]["live-smoke"]["timeout-minutes"] <= 120


def test_the_results_directory_is_tracked_not_ignored(repo: Path) -> None:
    ignore = (repo / ".gitignore").read_text(encoding="utf-8")

    assert "live-smoke" not in ignore and "docs/evaluation" not in ignore
