"""Cykliczna kontrola dryfu (PV3-19): bieżące wyjścia kontra zamrożone odtworzenie, próg alarmu, stan dla zdrowia.

Bloki kanarkowe to złote przypadki z korpusu BK-603 (``tests/fixtures/mpzp_evaluation/llm_replay``). „Dostawca na
żywo” jest tu zastąpiony odtwarzaniem albo jego zniekształceniem — bez sieci i bez klucza; sam bieg na żywo jest
ręczny i poza CI.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from app.core.settings import Settings
from app.modules.planning import composition
from app.modules.planning.application import llm_metrics
from app.modules.planning.application.llm_drift import (
    DriftCase,
    CaseDrift,
    compare_values,
    drift_rate,
    run_drift_check,
    summarize,
    value_key,
)
from app.modules.planning.application.llm_monitoring import parse_drift_state
from app.modules.planning.application.ports import (
    StructuredExtractionError,
    StructuredExtractionErrorCode as Code,
    StructuredExtractionRequest,
    StructuredExtractionResult,
)
from app.modules.planning.infrastructure.llm.fake_provider import ReplayStructuredExtractionProvider
from scripts import build_llm_replay_fixtures as golden
from scripts import check_llm_drift as script

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
KEY = "AIzaSyTEST-key_0123456789abcdefghij"


@pytest.fixture(scope="module")
def cases() -> list[DriftCase]:
    return script.canary_cases(["L1", "L3", "N1"])


@pytest.fixture(autouse=True)
def _clean() -> None:
    llm_metrics.metrics.reset()
    composition._LEDGER_CACHE.clear()


def replay() -> ReplayStructuredExtractionProvider:
    return ReplayStructuredExtractionProvider(golden.DEFAULT_OUTPUT_DIR, model=golden.MODEL)


class Perturbed:
    """„Dostawca na żywo”, który zachowuje się inaczej niż zamrożone odtworzenie."""

    provider_name = "gemini"

    def __init__(self, change: Callable[[dict[str, Any]], dict[str, Any]] | None = None, *,
                 error: Exception | None = None, fail_first: int = 0) -> None:
        self.inner = replay()
        self.model = golden.MODEL
        self.change = change
        self.error = error
        self.fail_first = fail_first
        self.calls = 0
        self.closed = False

    async def extract_structured(self, request: StructuredExtractionRequest) -> StructuredExtractionResult:
        self.calls += 1
        if self.error is not None and (self.fail_first == 0 or self.calls <= self.fail_first):
            raise self.error  # fail_first = 0: zawsze; inaczej tylko pierwsze N wywołań
        result = await self.inner.extract_structured(request)
        if self.change is None:
            return result
        return dataclasses.replace(result, content=self.change(json.loads(json.dumps(result.content))))

    async def aclose(self) -> None:
        self.closed = True


def drop(parameter: str | None = None) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def change(content: dict[str, Any]) -> dict[str, Any]:
        content["candidates"] = [c for c in content["candidates"] if parameter is not None and c["parameter"] != parameter]
        return content

    return change


# --- porównanie wartości ---------------------------------------------------------------------------------


def test_the_drift_rate_is_the_share_of_values_present_on_only_one_side() -> None:
    assert drift_rate(8, 1, 1) == 0.2
    assert drift_rate(5, 0, 0) == 0.0
    assert drift_rate(0, 0, 0) is None  # nic po żadnej stronie: brak danych, nie 0 %


def test_the_report_decides_with_the_threshold_and_a_failed_provider_is_not_drift() -> None:
    case = lambda **v: CaseDrift("L1", "b", True, v.get("f", 0), v.get("l", 0), v.get("m", 0),  # noqa: E731
                                 tuple("x" * v.get("of", 0)), tuple("y" * v.get("ol", 0)))
    ok = summarize([case(f=9, l=9, m=9)], threshold=0.2, model_id="m", prompt_version="p", live_calls=1)
    assert (ok.status, ok.drift_rate) == ("ok", 0.0)
    at = summarize([case(f=9, l=9, m=8, of=1, ol=1)], threshold=0.2, model_id="m", prompt_version="p", live_calls=1)
    assert at.status == "alarm" and at.drift_rate == 0.2  # dokładnie próg (2 z 10) alarmuje
    over = summarize([case(f=2, l=3, m=2, ol=1)], threshold=0.3, model_id="m", prompt_version="p", live_calls=1)
    assert over.status == "alarm" and over.drift_rate == 0.3333
    below = summarize([case(f=9, l=9, m=9, ol=1)], threshold=0.2, model_id="m", prompt_version="p", live_calls=1)
    assert below.status == "ok" and below.drift_rate == 0.1
    nothing = summarize([CaseDrift("L1", "b", False, 0, 0, 0, failure="live_unavailable:timeout")],
                        threshold=0.2, model_id="m", prompt_version="p", live_calls=1)
    assert nothing.status == "inconclusive" and nothing.drift_rate is None and nothing.skipped == 1
    empty = summarize([case()], threshold=0.2, model_id="m", prompt_version="p", live_calls=0)
    assert empty.status == "ok" and empty.drift_rate is None  # puste po obu stronach: zgodne
    assert summarize([case(f=1, l=1, m=1)], threshold=0.0, model_id="m", prompt_version="p", live_calls=0).status == "ok"


# --- kontrola na blokach kanarkowych -----------------------------------------------------------------------


async def test_the_replay_compared_with_itself_has_no_drift(cases: list[DriftCase]) -> None:
    report = await run_drift_check(cases, frozen=replay(), live=replay(), threshold=0.2)
    assert report.status == "ok" and report.drift_rate == 0.0
    assert report.compared == len(cases) and report.skipped == 0
    assert report.frozen_values == report.live_values == report.matching > 0
    assert report.live_calls == len(cases) and report.model_id == golden.MODEL


async def test_a_model_that_stops_returning_a_parameter_raises_the_alarm(cases: list[DriftCase]) -> None:
    report = await run_drift_check(cases, frozen=replay(), live=Perturbed(drop("max_building_height_m")), threshold=0.1)
    assert report.status == "alarm" and report.drift_rate is not None and report.drift_rate > 0.1
    assert report.only_frozen > 0 and report.only_live == 0
    assert any("max_building_height_m" in label for case in report.cases for label in case.only_frozen)


async def test_a_model_that_returns_nothing_is_total_drift(cases: list[DriftCase]) -> None:
    report = await run_drift_check(cases, frozen=replay(), live=Perturbed(drop(None)), threshold=0.2)
    assert report.status == "alarm" and report.drift_rate == 1.0 and report.live_values == 0


async def test_a_value_the_gates_reject_does_not_count_as_a_live_value(cases: list[DriftCase]) -> None:
    def forge(content: dict[str, Any]) -> dict[str, Any]:
        for candidate in content["candidates"]:
            candidate["evidence_quote"] = "cytat, którego nie ma w dokumencie"
        return content

    report = await run_drift_check(cases, frozen=replay(), live=Perturbed(forge), threshold=0.2)
    assert report.status == "alarm" and report.live_values == 0  # bramka G3 odrzuca; dryf = utrata wartości


async def test_a_provider_outage_is_inconclusive_not_an_alarm(cases: list[DriftCase]) -> None:
    live = Perturbed(error=StructuredExtractionError(Code.SERVER_ERROR, status=503))
    report = await run_drift_check(cases, frozen=replay(), live=live, threshold=0.2)
    assert report.status == "inconclusive" and report.drift_rate is None and report.compared == 0
    assert all(case.failure and case.failure.startswith("live_unavailable:") for case in report.cases)


async def test_blocks_the_provider_could_not_answer_are_skipped_the_rest_still_compared(cases: list[DriftCase]) -> None:
    live = Perturbed(error=StructuredExtractionError(Code.TIMEOUT), fail_first=1)
    report = await run_drift_check(cases, frozen=replay(), live=live, threshold=0.2)
    assert report.skipped == 1 and report.compared == len(cases) - 1 and report.status == "ok"


async def test_the_live_request_cap_limits_the_cost_of_a_check(cases: list[DriftCase]) -> None:
    report = await run_drift_check(cases, frozen=replay(), live=replay(), threshold=0.2, max_requests=2)
    assert report.live_calls == 2
    assert report.skipped == len(cases) - 2 and report.status == "ok"  # reszta bloków: budżet wyczerpany, nie dryf


def test_a_value_key_ignores_wording_and_rounds_the_number() -> None:
    from app.modules.planning.domain.candidate_verifier import VerifiedCondition

    class Item:
        zone_symbol, parameter, operator = "1MN", "max_building_height_m", "max"

        def __init__(self, value: float, quote: str = "") -> None:
            self.value = value
            self.conditions = (VerifiedCondition("roof_type", "dach", quote),) if quote else ()

    assert value_key(Item(12.0000001)) == value_key(Item(12.0))  # type: ignore[arg-type]
    assert value_key(Item(12.0, "Dach  Płaski")) == value_key(Item(12.0, "dach płaski"))  # type: ignore[arg-type]
    assert compare_values([Item(12.0)], [Item(11.0)]) == (0, ["1MN:max_building_height_m:max=12"], ["1MN:max_building_height_m:max=11"])  # type: ignore[list-item]


# --- skrypt ------------------------------------------------------------------------------------------------


def settings_for(tmp_path: Path, **values: Any) -> Settings:
    return Settings(
        _env_file=None, mpzp_llm_drift_state_file=str(tmp_path / "state" / "llm-drift.json"),
        mpzp_llm_kill_switch_file=str(tmp_path / "kill"), **values,
    )


def run_script(tmp_path: Path, live: Any, *args: str, **kwargs: Any) -> int:
    return script.main(["--confirm-public-text", "--cases", "L1", "N1", *args], live=live, active=settings_for(tmp_path), now=NOW, **kwargs)


def test_the_script_needs_an_explicit_confirmation(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert script.main(["--cases", "L1"], live=replay(), active=settings_for(tmp_path)) == script.EXIT_USAGE
    assert "--confirm-public-text" in capsys.readouterr().err
    assert script.main(["--confirm-public-text", "--cases", "ZZ"], live=replay(), active=settings_for(tmp_path)) == script.EXIT_USAGE
    assert script.main(["--confirm-public-text", "--max-requests", "0"], live=replay(), active=settings_for(tmp_path)) == script.EXIT_USAGE
    assert script.main(["--confirm-public-text", "--threshold", "2"], live=replay(), active=settings_for(tmp_path)) == script.EXIT_USAGE
    assert not (tmp_path / "state").exists()  # odmowa niczego nie zapisuje


def test_no_drift_exits_zero_and_writes_a_state_the_health_check_reads(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_script(tmp_path, replay()) == script.EXIT_OK
    out = capsys.readouterr().out
    assert "ok" in out and "stan zapisany" in out
    state = parse_drift_state((tmp_path / "state" / "llm-drift.json").read_text(encoding="utf-8"))
    assert state is not None and state.status == "ok" and state.checked_at == NOW and state.drift_rate == 0.0
    health = composition.llm_health(
        settings_for(tmp_path, mpzp_parser_mode="hybrid", mpzp_llm_enabled=True, gemini_api_key=KEY,
                     mpzp_llm_health_ledger_ttl_seconds=0),
        now=NOW, ledger_factory=lambda: type("L", (), {"totals": lambda self, now: (type("T", (), {"cost_usd": 0.0})(),) * 2})(),
    )
    assert health.status == "ok" and "drift_never_checked" not in health.warnings


def test_a_drift_alarm_exits_three_and_degrades_the_health_component(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    output = tmp_path / "report.json"
    assert run_script(tmp_path, Perturbed(drop(None)), "--output", str(output)) == script.EXIT_ALARM
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "alarm" and report["drift_rate"] == 1.0 and report["model_id"] == golden.MODEL
    assert report["values"]["live"] == 0 and report["values"]["only_frozen"] > 0
    # Raport nie zawiera cytatów ani tekstu dokumentów — tylko strefa:parametr:operator=wartość.
    assert "cytat" not in output.read_text(encoding="utf-8")
    assert llm_metrics.metrics.snapshot()["llm.drift.alarm"] == 1
    settings = settings_for(tmp_path, mpzp_parser_mode="hybrid", mpzp_llm_enabled=True, gemini_api_key=KEY,
                            mpzp_llm_health_ledger_ttl_seconds=0)
    ledger = type("L", (), {"totals": lambda self, now: (type("T", (), {"cost_usd": 0.0})(),) * 2})
    state = composition.llm_health(settings, now=NOW, ledger_factory=ledger)
    assert state.status == "degraded" and state.reasons == ("drift_alarm",)


def test_an_outage_exits_four_and_is_not_an_alarm(tmp_path: Path) -> None:
    live = Perturbed(error=StructuredExtractionError(Code.SERVER_ERROR, status=503))
    assert run_script(tmp_path, live) == script.EXIT_INCONCLUSIVE
    state = parse_drift_state((tmp_path / "state" / "llm-drift.json").read_text(encoding="utf-8"))
    assert state is not None and state.status == "inconclusive"


def test_the_provider_is_closed_after_the_check(tmp_path: Path) -> None:
    live = Perturbed()
    run_script(tmp_path, live)
    assert live.closed is True


def test_an_unwritable_state_file_does_not_hide_the_result(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    blocked = tmp_path / "file"
    blocked.write_text("x")
    active = Settings(_env_file=None, mpzp_llm_drift_state_file=str(blocked / "nested" / "state.json"))
    code = script.main(["--confirm-public-text", "--cases", "N1"], live=replay(), active=active, now=NOW)
    assert code == script.EXIT_OK and "stan niezapisany" in capsys.readouterr().out


def test_the_live_provider_is_refused_in_ci_with_the_kill_switch_or_without_a_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(script.DriftUsageError, match="klucz|konfiguracja"):
        script.build_live_provider(settings_for(tmp_path))  # brak GEMINI_API_KEY
    (tmp_path / "kill").touch()
    with pytest.raises(script.DriftUsageError, match="kill switch"):
        script.build_live_provider(settings_for(tmp_path, gemini_api_key=KEY))
    (tmp_path / "kill").unlink()
    monkeypatch.setenv("CI", "true")
    with pytest.raises(script.DriftUsageError, match="CI"):
        script.build_live_provider(settings_for(tmp_path, gemini_api_key=KEY))
    assert script.main(["--confirm-public-text"], active=settings_for(tmp_path, gemini_api_key=KEY)) == script.EXIT_USAGE


def test_the_live_provider_is_the_budgeted_production_adapter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    from app.modules.planning.infrastructure.llm.budget import BudgetedStructuredExtractionProvider

    provider = script.build_live_provider(settings_for(tmp_path, gemini_api_key=KEY))
    assert isinstance(provider, BudgetedStructuredExtractionProvider)
    assert KEY not in repr(provider)


def test_the_canary_set_covers_all_golden_blocks() -> None:
    everything = script.canary_cases()
    assert len(everything) == 13 and {case.case_id for case in everything} == {c.case_id for c in golden.CASES}
    assert all(len(case.document_sha256) == 64 for case in everything)
