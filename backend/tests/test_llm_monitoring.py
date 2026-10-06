"""Monitoring ścieżki modelu językowego (PV3-19): metryki, progi alarmów, zdrowie, przypięcie w czasie działania.

Bez sieci i bez klucza: model to dostawca skryptowy albo prawdziwy adapter Gemini na zamrożonych
odpowiedziach ``respx``. Zdrowie jest sprawdzane na dwóch poziomach: czysta ocena (``evaluate_llm_health``)
i root kompozycji (``llm_health``) z ustawieniami, wyłącznikiem, rejestrem zużycia i plikiem stanu dryfu.
"""

from __future__ import annotations

import dataclasses
import logging
import typing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.settings import Settings, settings as global_settings
from app.main import app
from app.modules.planning import composition
from app.modules.planning.application import llm_metrics
from app.modules.planning.application import llm_monitoring as monitoring
from app.modules.planning.application.llm_monitoring import (
    AlarmThresholds,
    DriftState,
    HealthInputs,
    LlmHealth,
    evaluate_llm_health,
    parse_drift_state,
)
from app.modules.planning.application.llm_pipeline import MpzpLlmPipeline
from app.modules.planning.application.ports import (
    StructuredExtractionError,
    StructuredExtractionErrorCode as Code,
    StructuredExtractionResult,
)
from tests.test_failure_injection_mpzp_llm import KEY, MODEL, URL, VALID, budgeted, envelope, gemini
from tests.test_mpzp_parser_modes import GOOD_RESPONSE, ScriptedProvider, parse

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean() -> None:
    llm_metrics.metrics.reset()
    composition.shared_circuit_breaker.cache_clear()
    composition._LEDGER_CACHE.clear()


class TimedProvider(ScriptedProvider):
    """Dostawca skryptowy zgłaszający opóźnienie (prawdziwy adapter mierzy je zegarem)."""

    latency_ms = 1500.0

    async def extract_structured(self, request: Any) -> StructuredExtractionResult:
        return dataclasses.replace(await super().extract_structured(request), latency_ms=self.latency_ms)


def make_settings(tmp_path: Path, **values: Any) -> Settings:
    base: dict[str, Any] = {
        "mpzp_llm_kill_switch_file": str(tmp_path / "llm-disabled"),
        "mpzp_llm_drift_state_file": str(tmp_path / "llm-drift.json"),
        "mpzp_llm_health_ledger_ttl_seconds": 0,
    }
    base.update(values)
    return Settings(_env_file=None, **base)


def hybrid_settings(tmp_path: Path, **values: Any) -> Settings:
    return make_settings(
        tmp_path, mpzp_parser_mode="hybrid", mpzp_llm_enabled=True, gemini_api_key=KEY, **values
    )


def ledger_with(cost: float | Exception):
    class Ledger:
        def totals(self, now: datetime) -> Any:
            if isinstance(cost, Exception):
                raise cost
            return SimpleTotals(cost), SimpleTotals(cost)

    return Ledger


@dataclasses.dataclass
class SimpleTotals:
    cost_usd: float
    tokens: int = 0


def health(settings: Settings, *, cost: float | Exception = 0.0, now: datetime = NOW) -> LlmHealth:
    return composition.llm_health(settings, now=now, ledger_factory=ledger_with(cost))


# --- rejestr metryk ------------------------------------------------------------------------------------


def test_latency_is_a_cumulative_histogram_with_sum_count_and_maximum() -> None:
    registry = llm_metrics.LlmMetrics()
    for value in (800.0, 1_500.0, 12_000.0, 45_000.0):
        registry.observe_latency(value)
    counts = registry.snapshot()
    assert counts[llm_metrics.LATENCY_COUNT] == 4 and counts[llm_metrics.LATENCY_SUM] == 800 + 1_500 + 12_000 + 45_000
    assert counts["llm.latency.le_1000ms"] == 1 and counts["llm.latency.le_2500ms"] == 2
    assert counts["llm.latency.le_10000ms"] == 2 and counts["llm.latency.le_30000ms"] == 3
    assert registry.gauges()[llm_metrics.LATENCY_MAX] == 45_000.0
    assert llm_metrics.mean_latency_ms(counts) == 14_825.0
    assert llm_metrics.mean_latency_ms({}) is None  # brak pomiarów to „brak danych”, nie 0 ms


def test_window_totals_forget_old_events_and_follow_the_clock() -> None:
    clock = [1_000.0]
    registry = llm_metrics.LlmMetrics(clock=lambda: clock[0])
    registry.increment("llm.calls", 3)
    clock[0] = 1_000.0 + 7_200
    registry.increment("llm.calls", 2)
    assert registry.snapshot()["llm.calls"] == 5  # liczniki ogólne trzymają całą historię procesu
    assert registry.window_totals(3_600) == {"llm.calls": 2}
    assert registry.window_totals(86_400) == {"llm.calls": 5}
    registry.reset()
    assert registry.window_totals(3_600) == {} and registry.snapshot() == {} and registry.gauges() == {}


def test_rates_are_none_without_samples_and_never_zero_by_default() -> None:
    assert llm_metrics.rejection_rate({}) == (None, 0)
    assert llm_metrics.degradation_rate({}) == (None, 0)
    assert llm_metrics.cache_hit_rate({}) == (None, 0)
    totals = {"llm.verifier.accepted": 6, "llm.verifier.rejected.G3": 3, "llm.verifier.rejected.G4": 1,
              "llm.verifier.rejected.G8": 40, "llm.analyses": 4, "llm.degraded": 1,
              "llm.cache.hit": 3, "llm.cache.miss": 1}
    rate, evaluated = llm_metrics.rejection_rate(totals)
    assert (rate, evaluated) == (0.4, 10)  # G8 (deduplikacja) nie jest odrzuceniem jakościowym
    assert llm_metrics.rejection_rate_by_gate(totals)["G3"] == 0.3
    assert llm_metrics.degradation_rate(totals) == (0.25, 4)
    assert llm_metrics.cache_hit_rate(totals) == (0.75, 4)
    assert llm_metrics.estimated_cost_usd({"llm.cost.estimated_microusd": 3_600}) == 0.0036


def test_log_events_carry_only_short_codes_and_numbers(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.mpzp_llm")
    secret_text = "maksymalna wysokość zabudowy: 12 m " * 6
    llm_metrics.log_event("probe", calls=2, text=secret_text, multi="a\nb", key=KEY[:10], nested={"x": 1})
    message = caplog.records[-1].getMessage()
    assert "calls=2" in message and "key=" in message
    assert "maksymalna" not in message and "multi=" not in message and "nested=" not in message


# --- emisja metryk przez potok -------------------------------------------------------------------------


async def test_a_hybrid_run_emits_calls_latency_tokens_cost_and_gate_counters() -> None:
    provider = TimedProvider()
    result = await parse("hybrid", MpzpLlmPipeline(provider))
    assert result.status == "partial"  # zmyślony cytat odrzucony przez G3 → ostrzeżenie
    counts = llm_metrics.metrics.snapshot()
    assert counts["llm.calls"] == 1 and counts["llm.analyses"] == 1
    assert counts[llm_metrics.LATENCY_COUNT] == 1 and counts[llm_metrics.LATENCY_SUM] == 1_500
    assert counts["llm.latency.le_2500ms"] == 1 and "llm.latency.le_1000ms" not in counts
    assert counts["llm.tokens.input"] == 900 and counts["llm.tokens.output"] == 300
    assert counts["llm.cost.estimated_microusd"] == 3_600  # 900 × 1,50 + 300 × 7,50 µUSD
    assert counts["llm.verifier.accepted"] == 1 and counts["llm.verifier.rejected.G3"] == 1
    assert "llm.degraded" not in counts
    rate, evaluated = llm_metrics.rejection_rate(llm_metrics.metrics.window_totals(3_600))
    assert (rate, evaluated) == (0.5, 2)
    ok, age = llm_metrics.last_run()
    assert ok is True and age is not None and age < 5


async def test_a_provider_without_latency_leaves_no_latency_sample() -> None:
    await parse("hybrid", MpzpLlmPipeline(ScriptedProvider()))  # 0,0 ms = „brak pomiaru”
    assert llm_metrics.LATENCY_COUNT not in llm_metrics.metrics.snapshot()


class DictCache:
    def __init__(self) -> None:
        self.records: dict[str, Any] = {}

    def get(self, cache_key: str) -> Any:
        return self.records.get(cache_key)

    def save(self, record: Any) -> Any:
        self.records[record.cache_key] = record
        return record


async def test_cache_hits_and_misses_are_counted_and_a_hit_costs_nothing() -> None:
    cache = DictCache()
    first = await parse("hybrid", MpzpLlmPipeline(TimedProvider(), cache=cache))
    second_provider = TimedProvider()
    second = await parse("hybrid", MpzpLlmPipeline(second_provider, cache=cache))
    assert second_provider.calls == 0  # trafienie cache: bez wywołania dostawcy
    counts = llm_metrics.metrics.snapshot()
    assert counts["llm.cache.miss"] == 1 and counts["llm.cache.hit"] == 1
    assert counts["llm.calls"] == 1 and counts["llm.tokens.input"] == 900  # tylko pierwsze wywołanie kosztowało
    assert llm_metrics.cache_hit_rate(counts) == (0.5, 2)
    assert [p.name for z in first.zones for p in z.parameters if p.review_status] == [
        p.name for z in second.zones for p in z.parameters if p.review_status
    ]


async def test_a_failing_model_counts_degradation_and_flips_the_last_run() -> None:
    error = StructuredExtractionError(Code.SERVER_ERROR, status=503)
    result = await parse("hybrid", MpzpLlmPipeline(ScriptedProvider(error=error)))
    assert result.status == "partial" and any(w.code == "MPZP_LLM_UNAVAILABLE" for w in result.warnings)
    counts = llm_metrics.metrics.snapshot()
    assert counts["llm.analyses"] == 1 and counts["llm.degraded"] == 1
    assert llm_metrics.degradation_rate(counts) == (1.0, 1)
    assert llm_metrics.last_run()[0] is False


async def test_a_deterministic_mode_leaves_the_model_counters_empty() -> None:
    await parse("v3")
    assert llm_metrics.metrics.snapshot() == {}


async def test_the_real_adapter_path_measures_latency_with_its_own_clock() -> None:
    with respx.mock(assert_all_called=True) as router:
        router.post(URL).mock(return_value=httpx.Response(200, json=envelope(VALID)))
        await parse("hybrid", MpzpLlmPipeline(budgeted(gemini())))
    counts = llm_metrics.metrics.snapshot()
    assert counts["llm.calls"] == 1 and counts[llm_metrics.LATENCY_COUNT] == 1
    assert counts["llm.tokens.input"] == 1800 and llm_metrics.metrics.gauges()[llm_metrics.LATENCY_MAX] >= 0


# --- czysta ocena zdrowia ------------------------------------------------------------------------------


def inputs(**values: Any) -> HealthInputs:
    base: dict[str, Any] = {
        "now": NOW, "mode": "hybrid", "enabled": True, "model_id": MODEL, "prompt_version": "mpzp-extraction/1",
        "breaker_state": "closed", "pin_problems": (), "evaluation_recorded": True, "daily_cost_usd": 0.1,
        "daily_cost_limit_usd": 5.0, "drift": DriftState("ok", NOW - timedelta(days=1), 0.0, 0.2, MODEL, "mpzp-extraction/1"),
    }
    base.update(values)
    return HealthInputs(**base)


def test_the_health_status_vocabulary_has_no_failed() -> None:
    assert set(typing.get_args(monitoring.HealthStatus)) == {"ok", "degraded", "disabled"}


def test_deterministic_modes_are_disabled_not_degraded() -> None:
    for mode in ("legacy", "v3"):
        state = evaluate_llm_health(inputs(mode=mode, enabled=False))
        assert (state.status, state.reasons) == ("disabled", ())
    assert evaluate_llm_health(inputs(mode="hybrid", enabled=False)).reasons == ("llm_disabled",)


def test_a_healthy_model_path_is_ok() -> None:
    state = evaluate_llm_health(inputs())
    assert (state.status, state.reasons, state.warnings) == ("ok", (), ())
    assert state.component() == {"status": "ok", "reasons": [], "mode": "hybrid"}


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"kill_switch": True}, "kill_switch"),
        ({"config_error": "api_key"}, "config_error"),
        ({"breaker_state": "open"}, "breaker_open"),
        ({"last_run_ok": False, "last_run_age_seconds": 30.0}, "model_unavailable"),
        ({"window": {"llm.unavailable.model_mismatch": 1}}, "model_mismatch"),
        ({"pin_problems": ("model_id_differs",)}, "pin_mismatch"),
        ({"daily_cost_usd": 4.0}, "daily_cost_alarm"),
        ({"daily_cost_usd": 5.0}, "daily_limit_reached"),
        ({"drift": DriftState("alarm", NOW, 0.4, 0.2, MODEL, "mpzp-extraction/1")}, "drift_alarm"),
    ],
)
def test_each_failure_mode_gives_degraded_with_a_stable_code(overrides: dict[str, Any], reason: str) -> None:
    state = evaluate_llm_health(inputs(**overrides))
    assert state.status == "degraded" and reason in state.reasons


def test_rate_alarms_need_a_minimum_sample_and_a_threshold() -> None:
    few = {"llm.verifier.accepted": 1, "llm.verifier.rejected.G3": 4, "llm.analyses": 3, "llm.degraded": 3}
    assert evaluate_llm_health(inputs(window=few)).status == "ok"  # 5 kandydatów i 3 analizy to za mało na alarm
    below_threshold = {"llm.verifier.accepted": 15, "llm.verifier.rejected.G3": 5, "llm.analyses": 10, "llm.degraded": 1}
    assert evaluate_llm_health(inputs(window=below_threshold)).status == "ok"  # 25 % przy progu 30 %
    at_rejection = {"llm.verifier.accepted": 14, "llm.verifier.rejected.G3": 6}  # dokładnie próg alarmuje
    assert evaluate_llm_health(inputs(window=at_rejection)).reasons == ("rejection_rate_alarm",)
    degraded = {"llm.analyses": 10, "llm.degraded": 2}
    assert evaluate_llm_health(inputs(window=degraded)).reasons == ("degradation_rate_alarm",)
    below = {"llm.analyses": 10, "llm.degraded": 1}
    assert evaluate_llm_health(inputs(window=below)).status == "ok"


def test_an_old_failure_does_not_keep_the_component_degraded() -> None:
    old = evaluate_llm_health(inputs(last_run_ok=False, last_run_age_seconds=7_200.0))
    assert old.status == "ok"
    recovered = evaluate_llm_health(inputs(last_run_ok=True, last_run_age_seconds=5.0))
    assert recovered.status == "ok"


def test_warnings_do_not_change_the_status() -> None:
    state = evaluate_llm_health(inputs(evaluation_recorded=False, drift=None, daily_cost_usd=None))
    assert state.status == "ok"
    assert set(state.warnings) == {"evaluation_pending", "drift_never_checked", "daily_cost_unknown"}
    stale = DriftState("alarm", NOW - timedelta(days=30), 0.9, 0.2, MODEL, "mpzp-extraction/1")
    state = evaluate_llm_health(inputs(drift=stale))
    assert state.status == "ok" and "drift_check_stale" in state.warnings  # stary alarm to ostrzeżenie o braku kontroli


def test_a_replay_provider_has_no_cost_to_track_and_so_no_unknown_cost_warning() -> None:
    state = evaluate_llm_health(inputs(daily_cost_usd=None, cost_tracked=False))
    assert state.status == "ok" and "daily_cost_unknown" not in state.warnings


def test_the_public_component_exposes_neither_numbers_nor_versions() -> None:
    state = evaluate_llm_health(inputs(daily_cost_usd=4.5, pin_problems=("model_id_differs",)))
    public = state.component()
    assert set(public) == {"status", "reasons", "mode"}
    assert "4.5" not in str(public) and MODEL not in str(public)
    assert state.details["usage"]["daily_cost_usd"] == 4.5  # szczegóły tylko dla operatora


def test_thresholds_are_validated() -> None:
    for bad in ({"rejection_rate": 1.5}, {"window_seconds": 0}, {"min_candidates": 0}, {"daily_cost_usd": -1.0}):
        with pytest.raises(ValueError):
            AlarmThresholds(**bad)


def test_the_drift_state_roundtrips_and_garbage_is_ignored() -> None:
    state = DriftState("alarm", NOW, 0.35, 0.2, MODEL, "mpzp-extraction/1")
    assert parse_drift_state(state.to_json()) == state
    for garbage in ("", "{", "[]", '{"schema": "other"}', '{"schema": "mpzp-llm-drift-state/1", "status": "x"}'):
        assert parse_drift_state(garbage) is None


# --- zdrowie z korzenia kompozycji ---------------------------------------------------------------------


def test_the_default_configuration_is_disabled_and_touches_neither_database_nor_network(tmp_path: Path) -> None:
    def explode() -> Any:
        raise AssertionError("rejestr nie powinien być czytany przy trybie bez modelu")

    state = composition.llm_health(make_settings(tmp_path), now=NOW, ledger_factory=explode)
    assert (state.status, state.reasons) == ("disabled", ())


def test_a_configured_hybrid_path_is_ok_with_honest_warnings(tmp_path: Path) -> None:
    state = health(hybrid_settings(tmp_path))
    assert state.status == "ok" and state.reasons == ()
    # Brak ewaluacji --live i brak kontroli dryfu są jawne, ale nie psują zdrowia.
    assert {"evaluation_pending", "drift_never_checked"} <= set(state.warnings)
    assert state.details["pin"]["problems"] == [] and state.details["breaker"] == "closed"


def test_hybrid_mode_with_the_flag_off_is_degraded(tmp_path: Path) -> None:
    assert health(make_settings(tmp_path, mpzp_parser_mode="hybrid")).reasons == ("llm_disabled",)


def test_the_kill_switch_file_degrades_the_component_without_a_restart(tmp_path: Path) -> None:
    settings = hybrid_settings(tmp_path)
    assert health(settings).status == "ok"
    (tmp_path / "llm-disabled").touch()
    assert health(settings).reasons == ("kill_switch",)
    (tmp_path / "llm-disabled").unlink()
    assert health(settings).status == "ok"


def test_the_replay_provider_is_ok_without_a_ledger_read_or_a_cost_warning(tmp_path: Path) -> None:
    def explode() -> Any:
        raise AssertionError("odtwarzanie nie używa rejestru zużycia")

    settings = make_settings(
        tmp_path, mpzp_parser_mode="hybrid_shadow", mpzp_llm_enabled=True, mpzp_llm_provider="fake",
        mpzp_llm_replay_dir=str(tmp_path),
    )
    state = composition.llm_health(settings, now=NOW, ledger_factory=explode)
    assert state.status == "ok" and "daily_cost_unknown" not in state.warnings and state.details["breaker"] is None


def test_a_missing_key_is_a_configuration_degradation_not_an_exception(tmp_path: Path) -> None:
    state = health(make_settings(tmp_path, mpzp_parser_mode="hybrid", mpzp_llm_enabled=True))
    assert state.status == "degraded" and state.reasons == ("config_error",)
    assert state.details["config_error"] == "api_key"
    fake = health(make_settings(tmp_path, mpzp_parser_mode="hybrid", mpzp_llm_enabled=True, mpzp_llm_provider="fake"))
    assert fake.details["config_error"] == "replay_dir"


def test_an_open_circuit_breaker_degrades_the_component(tmp_path: Path) -> None:
    settings = hybrid_settings(tmp_path)
    breaker = composition.shared_circuit_breaker(
        settings.mpzp_llm_breaker_failure_threshold, settings.mpzp_llm_breaker_cooldown_seconds
    )
    for _ in range(settings.mpzp_llm_breaker_failure_threshold):
        breaker.record_failure()
    assert health(settings).reasons == ("breaker_open",)
    breaker.record_success()
    assert health(settings).status == "ok"


async def test_a_model_failure_degrades_and_a_later_success_recovers(tmp_path: Path) -> None:
    settings = hybrid_settings(tmp_path)
    with respx.mock(assert_all_called=False) as router:
        router.post(URL).mock(return_value=httpx.Response(503))
        await parse("hybrid", MpzpLlmPipeline(budgeted(gemini())))
    assert health(settings).reasons == ("model_unavailable",)
    with respx.mock(assert_all_called=True) as router:
        router.post(URL).mock(return_value=httpx.Response(200, json=envelope(VALID)))
        await parse("hybrid", MpzpLlmPipeline(budgeted(gemini())))
    assert health(settings).status == "ok"


def test_the_pin_mismatch_is_reported_for_a_changed_model(tmp_path: Path) -> None:
    state = health(hybrid_settings(tmp_path, mpzp_llm_model="gemini-9.9-flash"))
    assert state.status == "degraded" and state.reasons == ("pin_mismatch",)
    assert state.details["pin"]["problems"] == ["model_id_differs"]
    declared = health(hybrid_settings(tmp_path, mpzp_llm_prompt_version="mpzp-extraction/2"))
    assert declared.details["pin"]["problems"] == ["prompt_version_differs"]
    assert health(hybrid_settings(tmp_path, mpzp_llm_thinking_level="high")).details["pin"]["problems"] == [
        "thinking_level_differs"
    ]


def test_the_daily_cost_comes_from_the_ledger_and_an_unreadable_ledger_is_only_a_warning(tmp_path: Path) -> None:
    settings = hybrid_settings(tmp_path)
    assert health(settings, cost=4.2).reasons == ("daily_cost_alarm",)
    assert health(settings, cost=5.0).reasons == ("daily_limit_reached",)
    unreadable = health(settings, cost=RuntimeError("baza"))
    assert unreadable.status == "ok" and "daily_cost_unknown" in unreadable.warnings
    unlimited = hybrid_settings(tmp_path, mpzp_llm_alarm_daily_cost_usd="", mpzp_llm_daily_cost_limit_usd="")
    assert health(unlimited, cost=1_000.0).status == "ok"


def test_the_ledger_is_read_through_a_short_cache(tmp_path: Path) -> None:
    settings = hybrid_settings(tmp_path, mpzp_llm_health_ledger_ttl_seconds=60)
    reads = []

    def factory() -> Any:
        reads.append(1)
        return ledger_with(0.5)()

    for _ in range(3):
        composition.llm_health(settings, now=NOW, ledger_factory=factory)
    assert len(reads) == 1


def test_a_drift_alarm_file_degrades_and_a_stale_one_only_warns(tmp_path: Path) -> None:
    settings = hybrid_settings(tmp_path)
    path = tmp_path / "llm-drift.json"
    path.write_text(DriftState("alarm", NOW - timedelta(hours=2), 0.4, 0.2, MODEL, "mpzp-extraction/1").to_json())
    assert health(settings).reasons == ("drift_alarm",)
    path.write_text(DriftState("alarm", NOW - timedelta(days=40), 0.4, 0.2, MODEL, "mpzp-extraction/1").to_json())
    stale = health(settings)
    assert stale.status == "ok" and "drift_check_stale" in stale.warnings
    path.write_text("nie json")
    assert "drift_never_checked" in health(settings).warnings


def test_the_rate_alarms_use_the_configured_window_and_samples(tmp_path: Path) -> None:
    settings = hybrid_settings(tmp_path)
    llm_metrics.metrics.increment("llm.verifier.accepted", 10)
    llm_metrics.metrics.increment("llm.verifier.rejected.G3", 12)
    state = health(settings)
    assert state.reasons == ("rejection_rate_alarm",)
    assert state.details["rates"]["rejection"] == pytest.approx(0.5455, abs=1e-4)
    assert state.details["rates"]["rejection_by_gate"]["G3"] == pytest.approx(0.5455, abs=1e-4)
    llm_metrics.metrics.reset()
    llm_metrics.metrics.increment("llm.analyses", 12)
    llm_metrics.metrics.increment("llm.degraded", 4)
    assert health(settings).reasons == ("degradation_rate_alarm",)


def test_an_error_in_the_evaluation_itself_is_degraded_never_an_exception(tmp_path: Path) -> None:
    with patch.object(composition, "llm_pin_problems", side_effect=RuntimeError("boom")):
        state = health(hybrid_settings(tmp_path))
    assert state.status == "degraded" and state.reasons == ("health_check_error",)


# --- przypięcie w czasie działania ---------------------------------------------------------------------


def test_an_unpinned_model_never_gets_an_adapter_or_a_request(tmp_path: Path) -> None:
    settings = hybrid_settings(tmp_path, mpzp_llm_model="gemini-9.9-flash")
    with patch.object(composition, "build_structured_extraction_provider", side_effect=AssertionError("adapter")):
        with pytest.raises(StructuredExtractionError) as caught:
            composition.build_mpzp_llm_pipeline(settings, session_factory=MagicMock())
    assert caught.value.code is Code.PIN_MISMATCH and "model_id_differs" in str(caught.value)
    assert llm_metrics.metrics.snapshot()["llm.pin.mismatch"] == 1


def test_the_pin_can_be_released_explicitly_for_an_evaluation_run(tmp_path: Path) -> None:
    settings = hybrid_settings(tmp_path, mpzp_llm_model="gemini-9.9-flash", mpzp_llm_enforce_pin=False)
    pipeline = composition.build_mpzp_llm_pipeline(settings, session_factory=MagicMock())
    assert pipeline is not None and pipeline.model_id == "gemini-9.9-flash"


def test_the_replay_provider_is_not_subject_to_the_model_pin(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path, mpzp_parser_mode="hybrid", mpzp_llm_enabled=True, mpzp_llm_provider="fake",
        mpzp_llm_replay_dir=str(tmp_path), mpzp_llm_model="other-model",
    )
    assert composition.build_mpzp_llm_pipeline(settings) is not None


async def test_an_unpinned_model_degrades_the_analysis_with_a_clear_reason(tmp_path: Path) -> None:
    from app.services.mpzp_parser_options import build_mpzp_parser_options

    settings = hybrid_settings(tmp_path, mpzp_llm_model="gemini-9.9-flash")
    options = build_mpzp_parser_options(settings)
    assert options.llm is None and options.llm_unavailable_reason == "pin_mismatch"
    result = await parse("hybrid", None, **{"llm_unavailable_reason": options.llm_unavailable_reason})
    assert result.status == "partial"
    warning = next(w for w in result.warnings if w.code == "MPZP_LLM_UNAVAILABLE")
    assert "pin_mismatch" in warning.message
    assert llm_metrics.metrics.snapshot()["llm.unavailable.pin_mismatch"] == 1


# --- endpointy zdrowia ----------------------------------------------------------------------------------


def patch_health(state: LlmHealth):
    return patch("app.routers.health.planning_composition.llm_health", return_value=state)


def ready_session() -> MagicMock:
    session = MagicMock()
    session.__enter__.return_value = session
    session.__exit__.return_value = False
    return session


def test_health_reports_the_llm_component_without_changing_the_service_status() -> None:
    with patch_health(LlmHealth("degraded", ("breaker_open",), (), {"mode": "hybrid"})):
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "backend",
        "components": {"llm": {"status": "degraded", "reasons": ["breaker_open"], "mode": "hybrid"}},
    }


def test_health_by_default_reports_the_disabled_component() -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["components"]["llm"] == {"status": "disabled", "reasons": [], "mode": "legacy"}


def test_a_degraded_model_does_not_block_readiness_or_liveness() -> None:
    state = LlmHealth("degraded", ("breaker_open", "model_unavailable"), (), {"mode": "hybrid"})
    with patch_health(state), patch("app.routers.health.SessionLocal", return_value=ready_session()):
        ready = client.get("/health/ready")
        live = client.get("/health/live")
    assert ready.status_code == 200 and ready.json() == {"status": "ok", "service": "backend"}
    assert live.status_code == 200


async def test_a_real_model_outage_reaches_the_health_endpoint_and_leaves_readiness_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name, value in (("mpzp_parser_mode", "hybrid"), ("mpzp_llm_enabled", True), ("gemini_api_key", SecretStr(KEY)),
                        ("mpzp_llm_kill_switch_file", str(tmp_path / "k")),
                        ("mpzp_llm_drift_state_file", str(tmp_path / "d.json"))):
        monkeypatch.setattr(global_settings, name, value)
    monkeypatch.setattr(composition, "daily_cost_from_ledger", lambda *a, **k: 0.0)
    with respx.mock(assert_all_called=False) as router:
        router.post(URL).mock(return_value=httpx.Response(503))
        await parse("hybrid", MpzpLlmPipeline(budgeted(gemini())))
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["components"]["llm"]["status"] == "degraded"
    assert body["components"]["llm"]["reasons"] == ["model_unavailable"]
    with patch("app.routers.health.SessionLocal", return_value=ready_session()):
        assert client.get("/health/ready").status_code == 200


def test_the_operator_endpoint_needs_the_admin_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(global_settings, "admin_api_keys", "")
    assert client.get("/health/llm").status_code == 403
    monkeypatch.setattr(global_settings, "admin_api_keys", "ops:klucz-ops")
    assert client.get("/health/llm").status_code == 401
    assert client.get("/health/llm", headers={"X-Admin-Key": "zly"}).status_code == 401


def test_the_operator_endpoint_returns_details_but_no_text_or_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(global_settings, "admin_api_keys", "ops:klucz-ops")
    llm_metrics.metrics.increment("llm.calls", 2)
    llm_metrics.metrics.observe_latency(1_500)
    details = {"mode": "hybrid", "rates": {"rejection": 0.5}, "thresholds": {"rejection_rate": 0.3}}
    with patch_health(LlmHealth("degraded", ("rejection_rate_alarm",), ("drift_never_checked",), details)):
        response = client.get("/health/llm", headers={"X-Admin-Key": "klucz-ops"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded" and body["reasons"] == ["rejection_rate_alarm"]
    assert body["warnings"] == ["drift_never_checked"] and body["details"]["thresholds"]["rejection_rate"] == 0.3
    assert body["counters"]["llm.calls"] == 2 and body["window_counters"]["llm.calls"] == 2
    assert body["gauges"][llm_metrics.LATENCY_MAX] == 1_500.0
    assert "klucz-ops" not in response.text and KEY not in response.text


def test_openapi_documents_the_health_components() -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "/health/llm" in paths
    schema = client.get("/openapi.json").json()["components"]["schemas"]["LlmComponentHealth"]
    assert schema["properties"]["status"]["enum"] == ["ok", "degraded", "disabled"]


# --- logi ------------------------------------------------------------------------------------------------


async def test_logs_contain_neither_request_text_nor_the_key(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    payload = {"candidates": [GOOD_RESPONSE["candidates"][0]], "not_found": []}
    with respx.mock(assert_all_called=True) as router:
        router.post(URL).mock(return_value=httpx.Response(200, json=envelope(payload)))
        await parse("hybrid", MpzpLlmPipeline(budgeted(gemini())))
    text = "\n".join(f"{r.name} {r.getMessage()}" for r in caplog.records)
    assert "event=pipeline" in text  # zdarzenia faktycznie powstały
    assert KEY not in text and "AIza" not in text
    for fragment in ("nie wyższa aniżeli", "maksymalna wysokość zabudowy", "Dla terenu 1MN", "BEGIN DOCUMENT TEXT"):
        assert fragment not in text


async def test_a_failure_log_names_the_error_code_not_the_provider_message(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    echo = {"error": {"message": f"API key not valid: {KEY}", "status": "INVALID_ARGUMENT"}}
    with respx.mock(assert_all_called=True) as router:
        router.post(URL).mock(return_value=httpx.Response(400, json=echo))
        result = await parse("hybrid", MpzpLlmPipeline(budgeted(gemini())))
    assert result.status == "partial"
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert KEY not in text and "API key not valid" not in text
