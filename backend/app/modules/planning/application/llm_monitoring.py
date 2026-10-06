"""Stan zdrowia i alarmy ścieżki modelu językowego (PV3-19, ADR-012 aneks PV3-18–20).

Moduł jest czystą logiką (bez sieci, bazy, plików i ustawień): wywołujący — root kompozycji —
zbiera wejścia (liczniki w oknie czasowym, stan wyłącznika, wynik sprawdzenia przypięcia, zużycie
dobowe z rejestru, wynik ostatniej kontroli dryfu) i dostaje ``LlmHealth``.

Zasady:

* stan to ``ok`` / ``degraded`` / ``disabled`` — **nigdy „failed”**: ścieżka modelu jest opcjonalna
  (rdzeń deterministyczny odpowiada bez niej), więc jej awaria nie może zmieniać gotowości aplikacji;
* ``disabled`` oznacza stan zamierzony (tryb parsera bez modelu), a nie błąd;
* każdy powód ``degraded`` jest stałym kodem (``REASON_*``) — bez treści żądań, odpowiedzi i kluczy;
* progi odsetków działają dopiero od minimalnej liczby prób (jeden odrzucony kandydat z jednego nie
  jest alarmem), a „brak prób” to ``None``, nigdy 0 %.

Progi domyślne (``AlarmThresholds``) są propozycją z ADR-012 (aneks PV3-18–20): nie skalibrowano ich
na ruchu produkcyjnym, bo go jeszcze nie było.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Final, Literal

from app.modules.planning.application import llm_metrics

HealthStatus = Literal["ok", "degraded", "disabled"]
DriftStatus = Literal["ok", "alarm", "inconclusive"]

REASON_MODE_WITHOUT_MODEL: Final[str] = "mode_without_model"
REASON_LLM_DISABLED: Final[str] = "llm_disabled"
REASON_KILL_SWITCH: Final[str] = "kill_switch"
REASON_CONFIG_ERROR: Final[str] = "config_error"
REASON_BREAKER_OPEN: Final[str] = "breaker_open"
REASON_MODEL_UNAVAILABLE: Final[str] = "model_unavailable"
REASON_MODEL_MISMATCH: Final[str] = "model_mismatch"
REASON_PIN_MISMATCH: Final[str] = "pin_mismatch"
REASON_REJECTION_RATE: Final[str] = "rejection_rate_alarm"
REASON_DEGRADATION_RATE: Final[str] = "degradation_rate_alarm"
REASON_DAILY_COST: Final[str] = "daily_cost_alarm"
REASON_DAILY_LIMIT: Final[str] = "daily_limit_reached"
REASON_DRIFT: Final[str] = "drift_alarm"
REASON_HEALTH_CHECK_ERROR: Final[str] = "health_check_error"

WARNING_EVALUATION_PENDING: Final[str] = "evaluation_pending"
WARNING_DRIFT_NOT_CHECKED: Final[str] = "drift_never_checked"
WARNING_DRIFT_STALE: Final[str] = "drift_check_stale"
WARNING_COST_UNKNOWN: Final[str] = "daily_cost_unknown"

# Parsery z modelem (``hybrid_shadow`` liczy model w tle, ``hybrid`` dokłada kandydatów).
MODEL_MODES: Final[frozenset[str]] = frozenset({"hybrid_shadow", "hybrid"})
DRIFT_STATE_SCHEMA: Final[str] = "mpzp-llm-drift-state/1"


@dataclass(frozen=True)
class AlarmThresholds:
    """Progi alarmów (ADR-012, aneks PV3-18–20): odsetek odrzuceń, degradacja, koszt dobowy, dryf.

    Odsetki liczone w oknie ``window_seconds`` i dopiero od ``min_candidates`` ocenionych kandydatów
    albo ``min_analyses`` analiz z modelem. ``daily_cost_usd`` to próg ostrzegawczy poniżej twardego
    limitu dobowego (domyślnie 80 % z 5 USD); ``None`` = bez alarmu kosztu.
    """

    window_seconds: float = 3600.0
    min_candidates: int = 20
    min_analyses: int = 10
    rejection_rate: float = 0.30
    degradation_rate: float = 0.20
    daily_cost_usd: float | None = 4.0
    drift_rate: float = 0.20
    drift_max_age_days: int = 14

    def __post_init__(self) -> None:
        if self.window_seconds <= 0 or self.min_candidates < 1 or self.min_analyses < 1:
            raise ValueError("Okno i minimalne próby alarmów muszą być dodatnie.")
        for value in (self.rejection_rate, self.degradation_rate, self.drift_rate):
            if not 0.0 <= value <= 1.0:
                raise ValueError("Progi odsetków muszą mieścić się w 0–1.")
        if self.daily_cost_usd is not None and self.daily_cost_usd < 0:
            raise ValueError("Próg kosztu dobowego nie może być ujemny.")


@dataclass(frozen=True)
class DriftState:
    """Wynik ostatniej kontroli dryfu (zapisany przez ``scripts/check_llm_drift.py``)."""

    status: DriftStatus
    checked_at: datetime
    drift_rate: float | None
    threshold: float
    model_id: str
    prompt_version: str

    def to_json(self) -> str:
        return json.dumps(
            {
                "schema": DRIFT_STATE_SCHEMA,
                "status": self.status,
                "checked_at": self.checked_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "drift_rate": self.drift_rate,
                "threshold": self.threshold,
                "model_id": self.model_id,
                "prompt_version": self.prompt_version,
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ) + "\n"


def parse_drift_state(text: str) -> DriftState | None:
    """Odczyt zapisu stanu dryfu; uszkodzony albo obcy plik to ``None`` (nie błąd, nie alarm)."""
    try:
        data = json.loads(text)
        if not isinstance(data, dict) or data.get("schema") != DRIFT_STATE_SCHEMA:
            return None
        status = data["status"]
        if status not in ("ok", "alarm", "inconclusive"):
            return None
        checked = datetime.fromisoformat(str(data["checked_at"]).replace("Z", "+00:00"))
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=timezone.utc)
        rate = data.get("drift_rate")
        return DriftState(
            status=status,
            checked_at=checked,
            drift_rate=None if rate is None else float(rate),
            threshold=float(data["threshold"]),
            model_id=str(data["model_id"]),
            prompt_version=str(data["prompt_version"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


@dataclass(frozen=True)
class HealthInputs:
    """Wszystko, czego ocena potrzebuje; zbiera to root kompozycji (bez I/O w tym module)."""

    now: datetime
    mode: str
    enabled: bool
    model_id: str
    prompt_version: str
    kill_switch: bool = False
    config_error: str | None = None
    breaker_state: str | None = None
    # Przypięcie: ``None`` = nie sprawdzano; krotka problemów (pusta = zgodne).
    pin_problems: tuple[str, ...] | None = None
    evaluation_recorded: bool | None = None
    window: Mapping[str, int] = field(default_factory=dict)
    last_run_ok: bool | None = None
    last_run_age_seconds: float | None = None
    daily_cost_usd: float | None = None
    daily_cost_limit_usd: float | None = None
    # Czy zużycie dobowe jest w ogóle śledzone (dostawca płatny z rejestrem); odtwarzanie nic nie kosztuje.
    cost_tracked: bool = True
    drift: DriftState | None = None
    thresholds: AlarmThresholds = field(default_factory=AlarmThresholds)


@dataclass(frozen=True)
class LlmHealth:
    status: HealthStatus
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    details: Mapping[str, Any] = field(default_factory=dict)

    def component(self) -> dict[str, Any]:
        """Postać publiczna (``/health``): stan, powody i tryb — bez liczb kosztu, limitów i wersji."""
        return {"status": self.status, "reasons": list(self.reasons), "mode": self.details.get("mode")}


def _rate(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def evaluate_llm_health(inputs: HealthInputs) -> LlmHealth:
    """Ocena stanu komponentu LLM; nigdy nie zwraca „failed” i nie zależy od sieci."""
    thresholds = inputs.thresholds
    totals = inputs.window
    rejection, evaluated = llm_metrics.rejection_rate(totals)
    degradation, analyses = llm_metrics.degradation_rate(totals)
    cache_rate, cache_samples = llm_metrics.cache_hit_rate(totals)
    details: dict[str, Any] = {
        "mode": inputs.mode,
        "enabled": inputs.enabled,
        "model_id": inputs.model_id,
        "prompt_version": inputs.prompt_version,
        "breaker": inputs.breaker_state,
        "window_seconds": thresholds.window_seconds,
        "rates": {
            "rejection": _rate(rejection),
            "rejection_samples": evaluated,
            "rejection_by_gate": {k: _rate(v) for k, v in llm_metrics.rejection_rate_by_gate(totals).items()},
            "degradation": _rate(degradation),
            "degradation_samples": analyses,
            "cache_hit": _rate(cache_rate),
            "cache_samples": cache_samples,
        },
        "usage": {
            "calls": totals.get("llm.calls", 0),
            "tokens_input": totals.get("llm.tokens.input", 0),
            "tokens_output": totals.get("llm.tokens.output", 0),
            "cost_estimated_usd": llm_metrics.estimated_cost_usd(totals),
            "latency_mean_ms": llm_metrics.mean_latency_ms(totals),
            "daily_cost_usd": inputs.daily_cost_usd,
            "daily_cost_limit_usd": inputs.daily_cost_limit_usd,
        },
        "thresholds": {
            "rejection_rate": thresholds.rejection_rate,
            "degradation_rate": thresholds.degradation_rate,
            "daily_cost_usd": thresholds.daily_cost_usd,
            "drift_rate": thresholds.drift_rate,
            "min_candidates": thresholds.min_candidates,
            "min_analyses": thresholds.min_analyses,
        },
        "pin": {"problems": list(inputs.pin_problems or ()), "evaluation_recorded": inputs.evaluation_recorded},
        "drift": None
        if inputs.drift is None
        else {
            "status": inputs.drift.status,
            "checked_at": inputs.drift.checked_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "drift_rate": inputs.drift.drift_rate,
            "threshold": inputs.drift.threshold,
        },
    }
    if inputs.mode not in MODEL_MODES:
        return LlmHealth("disabled", (), (), {**details, "note": REASON_MODE_WITHOUT_MODEL})
    if not inputs.enabled:
        # Tryb z modelem przy wyłączonej fladze: analizy kończą się ostrzeżeniem MPZP_LLM_UNAVAILABLE.
        return LlmHealth("degraded", (REASON_LLM_DISABLED,), (), details)

    reasons: list[str] = []
    warnings: list[str] = []
    if inputs.kill_switch:
        reasons.append(REASON_KILL_SWITCH)
    if inputs.config_error:
        reasons.append(REASON_CONFIG_ERROR)
        details["config_error"] = inputs.config_error
    if inputs.breaker_state == "open":
        reasons.append(REASON_BREAKER_OPEN)
    if (
        inputs.last_run_ok is False
        and inputs.last_run_age_seconds is not None
        and inputs.last_run_age_seconds <= thresholds.window_seconds
    ):
        reasons.append(REASON_MODEL_UNAVAILABLE)
    if totals.get("llm.unavailable.model_mismatch", 0) > 0:
        reasons.append(REASON_MODEL_MISMATCH)
    if inputs.pin_problems:
        reasons.append(REASON_PIN_MISMATCH)
    if rejection is not None and evaluated >= thresholds.min_candidates and rejection >= thresholds.rejection_rate:
        reasons.append(REASON_REJECTION_RATE)
    if degradation is not None and analyses >= thresholds.min_analyses and degradation >= thresholds.degradation_rate:
        reasons.append(REASON_DEGRADATION_RATE)
    cost = inputs.daily_cost_usd
    if cost is None:
        if inputs.cost_tracked and (inputs.daily_cost_limit_usd is not None or thresholds.daily_cost_usd is not None):
            warnings.append(WARNING_COST_UNKNOWN)
    else:
        if inputs.daily_cost_limit_usd is not None and cost >= inputs.daily_cost_limit_usd:
            reasons.append(REASON_DAILY_LIMIT)
        elif thresholds.daily_cost_usd is not None and cost >= thresholds.daily_cost_usd:
            reasons.append(REASON_DAILY_COST)
    if inputs.evaluation_recorded is False:
        warnings.append(WARNING_EVALUATION_PENDING)
    drift = inputs.drift
    if drift is None:
        warnings.append(WARNING_DRIFT_NOT_CHECKED)
    else:
        age = inputs.now - drift.checked_at
        if age > timedelta(days=thresholds.drift_max_age_days):
            warnings.append(WARNING_DRIFT_STALE)
        elif drift.status == "alarm":
            reasons.append(REASON_DRIFT)
    status: HealthStatus = "degraded" if reasons else "ok"
    return LlmHealth(status, tuple(dict.fromkeys(reasons)), tuple(dict.fromkeys(warnings)), details)

