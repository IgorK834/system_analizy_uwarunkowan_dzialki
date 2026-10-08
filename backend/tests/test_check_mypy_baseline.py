"""Bramka mypy z zapisaną bazą (AU-010): logika porównania bez uruchamiania mypy."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import check_mypy_baseline as gate

KNOWN = "app/modules/a.py: Incompatible types in assignment [assignment]"
OTHER = 'app/modules/b.py: Name "x" already defined on line 3 [no-redef]'


def test_normalize_drops_line_numbers_and_keeps_code() -> None:
    lines = [
        "app/modules/a.py:10: error: Incompatible types in assignment  [assignment]",
        "app/modules/a.py:99:5: error: Incompatible types in assignment  [assignment]",
        "app/modules/b.py:3: note: to tylko notatka",
        "Success: coś innego",
    ]

    assert gate.normalize(lines) == [KNOWN, KNOWN]


@pytest.fixture
def baseline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "mypy-baseline.txt"
    monkeypatch.setattr(gate, "BASELINE", path)
    return path


def test_no_new_findings_passes_and_reports_fixed_ones(
    baseline: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline.write_text(f"# nagłówek\n{KNOWN}\n{OTHER}\n", encoding="utf-8")
    monkeypatch.setattr(gate, "run_mypy", lambda: [KNOWN])

    assert gate.main([]) == 0
    output = capsys.readouterr().out
    assert "do usunięcia z bazy" in output and "b.py" in output


def test_a_new_finding_fails_even_when_the_count_of_known_ones_drops(
    baseline: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline.write_text(f"{KNOWN}\n{OTHER}\n", encoding="utf-8")
    new = "app/modules/c.py: Cannot find implementation [no-redef]"
    monkeypatch.setattr(gate, "run_mypy", lambda: [KNOWN, new])

    assert gate.main([]) == 1
    assert "NOWE zgłoszenia" in capsys.readouterr().out


def test_a_repeated_known_finding_counts_as_new(baseline: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    baseline.write_text(f"{KNOWN}\n", encoding="utf-8")
    monkeypatch.setattr(gate, "run_mypy", lambda: [KNOWN, KNOWN])

    assert gate.main([]) == 1


def test_update_rewrites_the_baseline_sorted(baseline: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gate, "run_mypy", lambda: [KNOWN, OTHER])

    assert gate.main(["--update"]) == 0
    entries = [line for line in baseline.read_text(encoding="utf-8").splitlines() if not line.startswith("#")]
    assert entries == [KNOWN, OTHER]
    assert gate.main([]) == 0


def test_missing_baseline_means_every_finding_is_new(baseline: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gate, "run_mypy", lambda: [KNOWN])

    assert gate.main([]) == 1


def test_repository_baseline_is_well_formed() -> None:
    entries = [
        line for line in gate.BASELINE.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")
    ]

    assert entries and all(line.startswith("app/modules/") and line.endswith("]") for line in entries)
