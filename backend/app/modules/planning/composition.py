"""Root kompozycji modułu planowania: reguły, kafle POG, inspektor i agregaty."""

from __future__ import annotations

import time
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.settings import Settings, settings
from app.modules.planning.application.llm_extraction import ExtractionLimits, LlmExtractionService
from app.modules.planning.application.llm_pipeline import BudgetTracker, LlmBudget, LlmPricing, MpzpLlmPipeline
from app.modules.planning.application.ports import (
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
from app.modules.planning.infrastructure.llm.resilience import CircuitBreaker, RetryPolicy
from app.modules.planning.domain.candidate_verifier import VerifierPolicy
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

PARSER_VERSION = "mpzp-rules/1.0"


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
    provider = build_structured_extraction_provider(active)
    if provider is None:
        return None
    if active.mpzp_llm_provider == "gemini":
        if session_factory is None:
            from app.db.session import SessionLocal

            session_factory = SessionLocal
        # Limity między analizami (PV3-15): częstotliwość i współbieżność procesu, doba i miesiąc w bazie.
        provider = BudgetedStructuredExtractionProvider(
            provider,
            ledger=SqlAlchemyUsageLedger(session_factory),
            limits=usage_limits(active),
            pricing=TokenPricing(active.mpzp_llm_price_input_usd_per_mtok, active.mpzp_llm_price_output_usd_per_mtok),
            max_output_tokens=active.mpzp_llm_max_output_tokens,
            concurrency=shared_concurrency_limiter(active.mpzp_llm_max_concurrency),
            rate=shared_rate_limiter(active.mpzp_llm_max_requests_per_minute),
            concurrency_wait_seconds=active.mpzp_llm_concurrency_wait_seconds,
        )
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
