"""Root kompozycji modułu planowania: reguły, kafle POG, inspektor i agregaty."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.settings import Settings, settings
from app.modules.planning.application import llm_metrics
from app.modules.planning.application.llm_extraction import ExtractionLimits, LlmExtractionService
from app.modules.planning.application.llm_monitoring import (
    AlarmThresholds,
    HealthInputs,
    LlmHealth,
    REASON_HEALTH_CHECK_ERROR,
    evaluate_llm_health,
    parse_drift_state,
)
from app.modules.planning.application.llm_pipeline import BudgetTracker, LlmBudget, LlmPricing, MpzpLlmPipeline
from app.modules.planning.application.ports import (
    KimpzpFeatureInfoParser,
    StructuredExtractionError,
    StructuredExtractionErrorCode,
    StructuredExtractionProvider,
)
from app.modules.planning.application.pog_release_queries import PogReleaseQueryService
from app.modules.planning.application.pog_tiles import PogTileLimits, PogTileService
from app.modules.planning.application.service import PlanningRuleService
from app.modules.planning.infrastructure.llm.fake_provider import ReplayStructuredExtractionProvider
from app.modules.planning.infrastructure.llm.gemini_provider import (
    GeminiConfig,
    GeminiStructuredExtractionProvider,
)
from app.modules.planning.infrastructure.llm.repository import SqlAlchemyLlmExtractionCache
from app.modules.planning.infrastructure.llm.budget import (
    BudgetedStructuredExtractionProvider,
    ConcurrencyLimiter,
    LocalRateLimiter,
    SqlAlchemyUsageLedger,
    TokenPricing,
    UsageLimits,
)
from app.modules.planning.infrastructure.llm import pin as model_pin
from app.modules.planning.infrastructure.llm.resilience import CircuitBreaker, RetryPolicy
from app.modules.planning.domain import extraction_contract as contract
from app.modules.planning.domain.candidate_verifier import VerifierPolicy
from app.modules.planning.infrastructure.kimpzp_feature_info import parse_kimpzp_feature_info
from app.modules.planning.infrastructure.mvt import (
    InMemoryPogTileCache,
    SqlAlchemyPogTileRepository,
)
from app.modules.planning.infrastructure.pog_release_queries import (
    SqlAlchemyPogReleaseQueryRepository,
)
from app.modules.planning.infrastructure.repository import (
    SqlAlchemyPlanningRuleRepository,
)

# ``2.0`` (PV3-21): reguły z silników wspólnych z parserem MPZP (ilości PV3-07, zapisy opisowe PV3-21).
PARSER_VERSION = "mpzp-rules/2.0"

logger = logging.getLogger(__name__)


def kimpzp_feature_info_parser() -> KimpzpFeatureInfoParser:
    """Adapter odpowiedzi GetFeatureInfo KIMPZP (AU-004) za portem ``KimpzpFeatureInfoParser``."""
    return parse_kimpzp_feature_info


def build_planning_rule_service(session: Session) -> PlanningRuleService:
    return PlanningRuleService(
        SqlAlchemyPlanningRuleRepository(session),
        parser_version=PARSER_VERSION,
    )


@lru_cache(maxsize=1)
def pog_tile_cache() -> InMemoryPogTileCache:
    """Cache procesu API; kafle są niezmienne dla ``release_id`` i schematu."""
    return InMemoryPogTileCache(settings.pog_tile_cache_max_bytes)


def build_pog_tile_service(session: Session) -> PogTileService:
    return PogTileService(
        SqlAlchemyPogTileRepository(
            session, statement_timeout_ms=settings.pog_tile_statement_timeout_ms
        ),
        pog_tile_cache(),
        PogTileLimits(
            min_zoom=settings.pog_tile_min_zoom,
            max_zoom=settings.pog_tile_max_zoom,
            max_features=settings.pog_tile_max_features,
            max_bytes=settings.pog_tile_max_bytes,
        ),
        source_id=settings.pog_tile_source_id,
    )


def build_pog_release_query_service(session: Session) -> PogReleaseQueryService:
    """Inspektor obiektu (BK-404) i agregaty stref (BK-405) przypięte do wydania."""
    return PogReleaseQueryService(SqlAlchemyPogReleaseQueryRepository(session))


def build_structured_extraction_provider(
    app_settings: Settings | None = None,
) -> StructuredExtractionProvider | None:
    """Dostawca ekstrakcji modelem językowym albo ``None`` (PV3-10).

    Przy ``mpzp_llm_enabled=false`` (stan domyślny) nic nie jest tworzone: ani adapter, ani klient
    HTTP, więc nie ma żadnego ruchu sieciowego. Włączony dostawca ``gemini`` wymaga klucza
    ``GEMINI_API_KEY``, ``fake`` — katalogu zapisanych odpowiedzi; brak któregokolwiek to błąd
    konfiguracji (bez cichego wyłączania i bez odpadania na inny dostawca).
    """
    active = app_settings or settings
    if not active.mpzp_llm_enabled:
        return None
    if active.mpzp_llm_provider == "fake":
        if not active.mpzp_llm_replay_dir.strip():
            raise StructuredExtractionError(StructuredExtractionErrorCode.CONFIGURATION, detail="replay_dir")
        return ReplayStructuredExtractionProvider(active.mpzp_llm_replay_dir, model=active.mpzp_llm_model)
    if active.gemini_api_key is None or not active.gemini_api_key.get_secret_value().strip():
        raise StructuredExtractionError(StructuredExtractionErrorCode.CONFIGURATION, detail="api_key")
    return GeminiStructuredExtractionProvider(
        active.gemini_api_key,
        GeminiConfig(
            model=active.mpzp_llm_model,
            timeout_seconds=active.mpzp_llm_timeout_seconds,
            connect_timeout_seconds=active.mpzp_llm_connect_timeout_seconds,
            max_output_tokens=active.mpzp_llm_max_output_tokens,
            thinking_level=active.mpzp_llm_thinking_level,
            max_request_bytes=active.mpzp_llm_max_request_bytes,
            max_response_bytes=active.mpzp_llm_max_response_bytes,
            retry=RetryPolicy(
                max_retries=active.mpzp_llm_max_retries,
                base_delay_seconds=active.mpzp_llm_retry_base_delay_seconds,
                max_delay_seconds=active.mpzp_llm_retry_max_delay_seconds,
                max_retry_after_seconds=active.mpzp_llm_max_retry_after_seconds,
            ),
            breaker_failure_threshold=active.mpzp_llm_breaker_failure_threshold,
            breaker_cooldown_seconds=active.mpzp_llm_breaker_cooldown_seconds,
        ),
        breaker=shared_circuit_breaker(
            active.mpzp_llm_breaker_failure_threshold, active.mpzp_llm_breaker_cooldown_seconds
        ),
    )


# --- stan wspólny procesu (PV3-15): wyłącznik, współbieżność i częstotliwość przeżywają analizę ---------


@lru_cache(maxsize=8)
def shared_circuit_breaker(failure_threshold: int, cooldown_seconds: float) -> CircuitBreaker:
    return CircuitBreaker(failure_threshold, cooldown_seconds)


@lru_cache(maxsize=8)
def shared_concurrency_limiter(limit: int) -> ConcurrencyLimiter:
    return ConcurrencyLimiter(limit)


@lru_cache(maxsize=8)
def shared_rate_limiter(per_minute: int) -> LocalRateLimiter:
    return LocalRateLimiter(per_minute)


def usage_limits(app_settings: Settings | None = None) -> UsageLimits:
    active = app_settings or settings
    return UsageLimits(
        daily_tokens=active.mpzp_llm_daily_token_limit,
        daily_cost_usd=active.mpzp_llm_daily_cost_limit_usd,
        monthly_tokens=active.mpzp_llm_monthly_token_limit,
        monthly_cost_usd=active.mpzp_llm_monthly_cost_limit_usd,
    )


def llm_kill_switch_active(app_settings: Settings | None = None) -> bool:
    """Kill switch (PV3-16): plik ``MPZP_LLM_KILL_SWITCH_FILE`` istnieje → ścieżka modelu wyłączona."""
    active = app_settings or settings
    path = active.mpzp_llm_kill_switch_file.strip()
    return bool(path) and Path(path).exists()


def build_llm_extraction_service(app_settings: Settings | None = None) -> LlmExtractionService | None:
    """Usługa ekstrakcji parametrów strefy (PV3-11) albo ``None`` przy wyłączonej ścieżce modelu."""
    active = app_settings or settings
    provider = build_structured_extraction_provider(active)
    if provider is None:
        return None
    return LlmExtractionService(
        provider,
        ExtractionLimits(
            block_char_limit=active.mpzp_llm_block_char_limit,
            chunk_overlap_chars=active.mpzp_llm_chunk_overlap_chars,
            max_chunks=active.mpzp_llm_max_chunks_per_block,
            temperature=active.mpzp_llm_temperature,
            max_output_tokens=active.mpzp_llm_max_output_tokens,
        ),
    )


def _extraction_limits(active: Settings) -> ExtractionLimits:
    return ExtractionLimits(
        block_char_limit=active.mpzp_llm_block_char_limit,
        chunk_overlap_chars=active.mpzp_llm_chunk_overlap_chars,
        max_chunks=active.mpzp_llm_max_chunks_per_block,
        temperature=active.mpzp_llm_temperature,
        max_output_tokens=active.mpzp_llm_max_output_tokens,
    )


def build_llm_extraction_cache(
    app_settings: Settings | None = None,
    session_factory: Callable[[], Session] | None = None,
) -> SqlAlchemyLlmExtractionCache:
    """Cache i provenance wywołań modelu (PV3-13); własne sesje, niezależne od transakcji analizy."""
    active = app_settings or settings
    if session_factory is None:
        from app.db.session import SessionLocal

        session_factory = SessionLocal
    return SqlAlchemyLlmExtractionCache(session_factory, retention_days=active.mpzp_llm_cache_retention_days)


def llm_budget(app_settings: Settings | None = None) -> LlmBudget:
    active = app_settings or settings
    return LlmBudget(
        max_requests=active.mpzp_llm_max_requests_per_analysis,
        max_input_tokens=active.mpzp_llm_max_input_tokens_per_analysis,
        max_input_tokens_per_document=active.mpzp_llm_max_input_tokens_per_document,
        max_input_tokens_per_request=active.mpzp_llm_max_input_tokens_per_request,
        time_budget_seconds=active.mpzp_llm_time_budget_seconds,
    )


def new_analysis_llm_budget(
    app_settings: Settings | None = None, *, started: float | None = None
) -> BudgetTracker:
    """Licznik budżetu jednej analizy, wspólny dla potoków wszystkich jej dokumentów.

    ``started`` (zegar monotoniczny) to start analizy: termin ``MPZP_LLM_ANALYSIS_DEADLINE_SECONDS`` jest
    od niego liczony i propagowany do każdego żądania modelu.
    """
    active = app_settings or settings
    begin = time.monotonic() if started is None else started
    return BudgetTracker(llm_budget(active), deadline=begin + active.mpzp_llm_analysis_deadline_seconds)


def build_budgeted_provider(
    provider: StructuredExtractionProvider,
    app_settings: Settings | None = None,
    session_factory: Callable[[], Session] | None = None,
) -> BudgetedStructuredExtractionProvider:
    """Dostawca z limitami między analizami (PV3-15): częstotliwość i współbieżność procesu, doba i miesiąc w bazie."""
    active = app_settings or settings
    if session_factory is None:
        from app.db.session import SessionLocal

        session_factory = SessionLocal
    return BudgetedStructuredExtractionProvider(
        provider,
        ledger=SqlAlchemyUsageLedger(session_factory),
        limits=usage_limits(active),
        pricing=TokenPricing(active.mpzp_llm_price_input_usd_per_mtok, active.mpzp_llm_price_output_usd_per_mtok),
        max_output_tokens=active.mpzp_llm_max_output_tokens,
        concurrency=shared_concurrency_limiter(active.mpzp_llm_max_concurrency),
        rate=shared_rate_limiter(active.mpzp_llm_max_requests_per_minute),
        concurrency_wait_seconds=active.mpzp_llm_concurrency_wait_seconds,
    )


def build_mpzp_llm_pipeline(
    app_settings: Settings | None = None,
    *,
    session_factory: Callable[[], Session] | None = None,
    tracker: BudgetTracker | None = None,
) -> MpzpLlmPipeline | None:
    """Potok cache → budżet → model → weryfikacja (PV3-12–14) albo ``None`` przy wyłączonej ścieżce.

    Dostawca jest tworzony per analiza (klient HTTP zamykany po analizie przez ``MpzpLlmPipeline.aclose``).
    """
    active = app_settings or settings
    if active.mpzp_llm_enabled and active.mpzp_llm_enforce_pin and active.mpzp_llm_provider == "gemini":
        # Przypięcie (PV3-19): nieoceniony model/prompt nie wchodzi do analiz — przed utworzeniem adaptera.
        problems = llm_pin_problems(active)
        if problems:
            llm_metrics.metrics.increment("llm.pin.mismatch")
            raise StructuredExtractionError(StructuredExtractionErrorCode.PIN_MISMATCH, detail=problems[0])
    provider = build_structured_extraction_provider(active)
    if provider is None:
        return None
    if active.mpzp_llm_provider == "gemini":
        provider = build_budgeted_provider(provider, active, session_factory)
    return MpzpLlmPipeline(
        provider,
        limits=_extraction_limits(active),
        cache=build_llm_extraction_cache(active, session_factory) if active.mpzp_llm_cache_enabled else None,
        budget=llm_budget(active),
        tracker=tracker,
        policy=VerifierPolicy(scope_confidence_threshold=active.mpzp_llm_scope_confidence_threshold),
        pricing=LlmPricing(
            input_usd_per_mtok=active.mpzp_llm_price_input_usd_per_mtok,
            output_usd_per_mtok=active.mpzp_llm_price_output_usd_per_mtok,
        ),
        params={"thinking_level": active.mpzp_llm_thinking_level} if active.mpzp_llm_provider == "gemini" else {},
    )


def purge_llm_extraction_cache(app_settings: Settings | None = None) -> int:
    """Usuwa zapisy cache modelu poza okresem retencji (``MPZP_LLM_CACHE_RETENTION_DAYS``)."""
    return build_llm_extraction_cache(app_settings).purge_expired()


# --- przypięcie wersji i stan zdrowia (PV3-19) --------------------------------------------------------------


def llm_pin_problems(app_settings: Settings | None = None) -> tuple[str, ...]:
    """Rozbieżności ustawień i kodu względem ``model_pin.json``; pusta krotka = zgodne."""
    active = app_settings or settings
    try:
        pin = model_pin.load_pin()
    except model_pin.PinError as error:
        return (str(error),)
    problems = list(
        model_pin.verify_pin(
            pin,
            model_id=active.mpzp_llm_model,
            prompt_version=contract.PROMPT_VERSION,
            prompt_sha256=contract.prompt_sha256(),
            schema_version=contract.SCHEMA_VERSION,
            schema_sha256=contract.SCHEMA_SHA256,
            thinking_level=active.mpzp_llm_thinking_level,
        )
    )
    if active.mpzp_llm_prompt_version != contract.PROMPT_VERSION:
        # Deklaracja operatora (MPZP_LLM_PROMPT_VERSION) nie zgadza się z promptem, który faktycznie działa.
        problems.append(model_pin.PROMPT_VERSION_DIFFERS)
    return tuple(dict.fromkeys(problems))


def llm_evaluation_recorded() -> bool | None:
    """Czy przypięcie ma zapis ponownej ewaluacji ``--live``; ``None`` — brak czytelnego przypięcia."""
    try:
        pin = model_pin.load_pin()
    except model_pin.PinError:
        return None
    return pin.evaluation_recorded and not model_pin.evaluation_problems(pin)


def alarm_thresholds(app_settings: Settings | None = None) -> AlarmThresholds:
    active = app_settings or settings
    return AlarmThresholds(
        window_seconds=active.mpzp_llm_alarm_window_seconds,
        min_candidates=active.mpzp_llm_alarm_min_candidates,
        min_analyses=active.mpzp_llm_alarm_min_analyses,
        rejection_rate=active.mpzp_llm_alarm_rejection_rate,
        degradation_rate=active.mpzp_llm_alarm_degradation_rate,
        daily_cost_usd=active.mpzp_llm_alarm_daily_cost_usd,
        drift_rate=active.mpzp_llm_drift_alarm_rate,
        drift_max_age_days=active.mpzp_llm_drift_max_age_days,
    )


def llm_config_error(active: Settings) -> str | None:
    """Błąd konfiguracji włączonej ścieżki (brak klucza, brak katalogu odtwarzania) bez tworzenia adaptera."""
    if not active.mpzp_llm_enabled:
        return None
    if active.mpzp_llm_provider == "fake":
        return None if active.mpzp_llm_replay_dir.strip() else "replay_dir"
    if active.gemini_api_key is None or not active.gemini_api_key.get_secret_value().strip():
        return "api_key"
    return None


_LEDGER_CACHE: dict[str, tuple[float, float | None]] = {}


def daily_cost_from_ledger(
    active: Settings,
    *,
    now: datetime,
    ledger_factory: Callable[[], object] | None = None,
) -> float | None:
    """Szacowany koszt doby (UTC) z rejestru ``mpzp_llm_usage`` z krótkim buforem; ``None`` = nieznany.

    Zdrowie nie może zależeć od bazy: błąd odczytu daje ``None`` (ostrzeżenie ``daily_cost_unknown``), a
    gotowość aplikacji sprawdza osobno ``/health/ready``.
    """
    key = active.mpzp_llm_provider
    cached = _LEDGER_CACHE.get(key)
    moment = time.monotonic()
    if cached is not None and moment - cached[0] < active.mpzp_llm_health_ledger_ttl_seconds:
        return cached[1]
    value: float | None
    try:
        if ledger_factory is None:
            from app.db.session import SessionLocal

            ledger: object = SqlAlchemyUsageLedger(SessionLocal)
        else:
            ledger = ledger_factory()
        day, _ = ledger.totals(now)  # type: ignore[attr-defined]
        value = round(float(day.cost_usd), 6)
    except Exception as exc:  # noqa: BLE001 - zdrowie nie może zawodzić z powodu rejestru
        logger.warning("mpzp_llm health ledger read failed: %s", type(exc).__name__)
        value = None
    _LEDGER_CACHE[key] = (moment, value)
    return value


def read_drift_state(active: Settings):  # noqa: ANN201 - DriftState | None
    path = active.mpzp_llm_drift_state_file.strip()
    if not path:
        return None
    try:
        return parse_drift_state(Path(path).read_text(encoding="utf-8"))
    except OSError:
        return None


def llm_health(
    app_settings: Settings | None = None,
    *,
    now: datetime | None = None,
    ledger_factory: Callable[[], object] | None = None,
) -> LlmHealth:
    """Stan komponentu LLM dla ``/health``: ``ok`` / ``degraded`` / ``disabled`` — nigdy „failed”.

    Nie wykonuje żadnego wywołania sieciowego do dostawcy; nie zgłasza wyjątków (błąd samej oceny to
    ``degraded`` z kodem ``health_check_error``).
    """
    active = app_settings or settings
    moment = now or datetime.now(timezone.utc)
    mode = active.mpzp_parser_mode
    try:
        thresholds = alarm_thresholds(active)
        uses_model = mode in ("hybrid_shadow", "hybrid") and active.mpzp_llm_enabled
        gemini = uses_model and active.mpzp_llm_provider == "gemini"
        last_ok, last_age = llm_metrics.last_run()
        problems = llm_pin_problems(active) if gemini else None
        inputs = HealthInputs(
            now=moment,
            mode=mode,
            enabled=active.mpzp_llm_enabled,
            model_id=active.mpzp_llm_model,
            prompt_version=contract.PROMPT_VERSION,
            kill_switch=uses_model and llm_kill_switch_active(active),
            config_error=llm_config_error(active) if uses_model else None,
            breaker_state=(
                shared_circuit_breaker(
                    active.mpzp_llm_breaker_failure_threshold, active.mpzp_llm_breaker_cooldown_seconds
                ).state
                if gemini
                else None
            ),
            pin_problems=problems,
            evaluation_recorded=llm_evaluation_recorded() if gemini else None,
            window=llm_metrics.metrics.window_totals(thresholds.window_seconds),
            last_run_ok=last_ok,
            last_run_age_seconds=last_age,
            daily_cost_usd=daily_cost_from_ledger(active, now=moment, ledger_factory=ledger_factory) if gemini else None,
            daily_cost_limit_usd=active.mpzp_llm_daily_cost_limit_usd if gemini else None,
            cost_tracked=gemini,
            drift=read_drift_state(active) if uses_model else None,
            thresholds=thresholds,
        )
        return evaluate_llm_health(inputs)
    except Exception as exc:  # noqa: BLE001 - zdrowie nigdy nie zgłasza wyjątku ani „failed”
        logger.warning("mpzp_llm health evaluation failed: %s", type(exc).__name__)
        return LlmHealth("degraded", (REASON_HEALTH_CHECK_ERROR,), (), {"mode": mode})
