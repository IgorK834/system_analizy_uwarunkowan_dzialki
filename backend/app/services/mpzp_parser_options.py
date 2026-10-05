"""Opcje parsera MPZP z konfiguracji (PV3-14): tryb i potok modelu dla jednej analizy.

Orkiestrator analizy i wznowienie po ręcznym symbolu używają TEGO SAMEGO potoku: obie ścieżki
wołają ``parse_mpzp_document(..., **build_mpzp_parser_options().kwargs())`` — różni je tylko
wejście (pobrany dokument albo kopia przypięta przy wstrzymaniu analizy, bez ponownego pobrania).

W trybach deterministycznych (``legacy``, ``v3``) nie powstaje żaden adapter ani klient HTTP. W
trybach z modelem brak włączonej ścieżki (``MPZP_LLM_ENABLED=false``) albo błąd konfiguracji
(np. brak klucza) nie przerywa analizy: potok jest ``None``, a parser dokłada ostrzeżenie
``MPZP_LLM_UNAVAILABLE`` z powodem.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.core.settings import Settings, settings
from app.modules.planning.application.llm_pipeline import BudgetTracker, MpzpLlmPipeline
from app.modules.planning.application.ports import StructuredExtractionError
from app.modules.planning.composition import build_mpzp_llm_pipeline, llm_kill_switch_active
from app.services.mpzp_parser_hybrid import LLM_MODES, REASON_KILL_SWITCH, REASON_LLM_DISABLED, ParserMode

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MpzpParserOptions:
    mode: ParserMode
    llm: MpzpLlmPipeline | None = None
    llm_unavailable_reason: str | None = None
    scope_threshold: float = 0.6

    def kwargs(self) -> dict[str, Any]:
        if self.mode not in LLM_MODES:
            return {"mode": self.mode}
        return {
            "mode": self.mode,
            "llm": self.llm,
            "llm_unavailable_reason": self.llm_unavailable_reason,
            "scope_threshold": self.scope_threshold,
        }


def build_mpzp_parser_options(
    app_settings: Settings | None = None, *, budget: BudgetTracker | None = None
) -> MpzpParserOptions:
    """Opcje dla jednego dokumentu; ``budget`` — wspólny licznik budżetu całej analizy (kilka aktów)."""
    active = app_settings or settings
    mode = active.mpzp_parser_mode
    threshold = active.mpzp_llm_scope_confidence_threshold
    if mode not in LLM_MODES:
        return MpzpParserOptions(mode=mode, scope_threshold=threshold)
    if not active.mpzp_llm_enabled:
        return MpzpParserOptions(mode=mode, llm_unavailable_reason=REASON_LLM_DISABLED, scope_threshold=threshold)
    if llm_kill_switch_active(active):
        # Kill switch (PV3-16): wyłączenie ścieżki bez restartu i bez wdrożenia kodu.
        return MpzpParserOptions(mode=mode, llm_unavailable_reason=REASON_KILL_SWITCH, scope_threshold=threshold)
    try:
        pipeline = build_mpzp_llm_pipeline(active, tracker=budget)
    except StructuredExtractionError as error:
        logger.warning("mpzp_llm configuration error: %s", error.code.value)
        return MpzpParserOptions(mode=mode, llm_unavailable_reason=error.code.value, scope_threshold=threshold)
    except Exception as exc:  # noqa: BLE001 - zła konfiguracja modelu nie może zatrzymać analizy
        logger.warning("mpzp_llm pipeline build failed: %s", type(exc).__name__)
        return MpzpParserOptions(mode=mode, llm_unavailable_reason="configuration", scope_threshold=threshold)
    return MpzpParserOptions(mode=mode, llm=pipeline, scope_threshold=threshold)
