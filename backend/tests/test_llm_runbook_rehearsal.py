"""Próba runbooka ``docs/operations/mpzp-llm.md`` jest powtarzalna offline (PV3-20, wycofanie — PV3-21).

Skrypt ``scripts/rehearse_llm_runbook.py`` przechodzi włączenie, wyłączenie, rotację klucza, limity i zmianę
modelu na prawdziwych ustawieniach, kompozycji i adapterze (dostawca = zamrożone odpowiedzi ``respx``). Test
pilnuje, by polecenia z runbooka dalej odtwarzały opisany wynik.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.modules.planning import composition
from app.modules.planning.application import llm_metrics
from app.modules.planning.infrastructure.llm.budget import InMemoryUsageLedger
from scripts import rehearse_llm_runbook as rehearsal


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(composition, "SqlAlchemyUsageLedger", lambda _factory: InMemoryUsageLedger())
    llm_metrics.metrics.reset()
    composition.shared_circuit_breaker.cache_clear()
    composition._LEDGER_CACHE.clear()


def test_every_runbook_step_gives_the_documented_result(tmp_path: Path) -> None:
    steps = rehearsal.rehearse(tmp_path)
    failed = [(step.section, step.action, step.observed) for step in steps if not step.passed]
    assert failed == []
    assert [step.section.split(".")[0] for step in steps] == list("AAABBBBCCCCDEEFFF")
    table = rehearsal.render(steps)
    assert table.count("zgodne") == len(steps) and "NIEZGODNE" not in table


def test_the_rehearsal_leaves_no_trace_in_the_environment_or_logging(tmp_path: Path) -> None:
    import logging
    import os

    before_env = dict(os.environ)
    before_handlers = list(logging.getLogger().handlers)
    rehearsal.rehearse(tmp_path)
    assert dict(os.environ) == before_env
    assert logging.getLogger().handlers == before_handlers
    assert not any(name.startswith("AIza") for name in os.environ.values())


def test_the_main_entry_point_returns_zero_and_prints_the_table(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    assert rehearsal.main([]) == 0
    out = capsys.readouterr().out
    assert "| Krok | Działanie |" in out and "Kroków: 17; zgodnych: 17; niezgodnych: 0" in out
