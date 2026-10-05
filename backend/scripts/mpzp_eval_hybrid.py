"""Most ewaluatora do potoku hybrydowego (PV3-14): port ekstrakcji nad bramą odpowiedzi i adapter na żywo.

* ``GatewayStructuredExtractionProvider`` — port ``StructuredExtractionProvider`` nad ``LlmGateway``
  ewaluatora: odtwarzanie (domyślne) albo nagrywanie na żywo pod tym samym kluczem co złote odpowiedzi
  i produkcyjny cache (``extraction_cache_key``). Brak zapisanej odpowiedzi jest liczony i raportowany
  — silnik ``hybrid`` kończy wtedy przebieg błędem zamiast cicho mieszać wynik deterministyczny.
* ``LiveGeminiBridge`` — ``LiveProvider`` dla ``--live`` (ręcznie, poza CI): tłumaczy ``LlmRequest`` z
  ładunkiem (``payload``) na żądanie portu i woła produkcyjny adapter Gemini w osobnym wątku z własną
  pętlą zdarzeń. Ładunek nie trafia do zapisu odpowiedzi (``ReplayStore`` zapisuje tylko klucz).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from pydantic import SecretStr

from app.modules.planning.application.llm_pipeline import LlmPricing
from app.modules.planning.application.ports import (
    StructuredExtractionError,
    StructuredExtractionErrorCode,
    StructuredExtractionRequest,
    StructuredExtractionResult,
)
from app.modules.planning.infrastructure.llm.gemini_provider import GeminiConfig, GeminiStructuredExtractionProvider
from scripts.mpzp_eval_engines import (
    LlmGateway,
    LlmRequest,
    LlmResponse,
    ReplayIntegrityError,
    ReplayMissError,
    register_live_provider,
)

RECORDED_PROVIDER = "gemini"
DEFAULT_MODEL = "gemini-3.8-flash"


def request_payload(request: StructuredExtractionRequest) -> dict[str, Any]:
    """Ładunek ``LlmRequest`` (wysyłany, nigdy zapisywany): wszystko, czego potrzebuje adapter."""
    return {
        "system_instruction": request.system_instruction,
        "user_text": request.user_text,
        "response_schema": dict(request.response_schema),
        "temperature": request.temperature,
        "max_output_tokens": request.max_output_tokens,
        "prompt_version": request.prompt_version,
        "prompt_sha256": request.prompt_sha256,
        "schema_version": request.schema_version,
    }


def request_from_payload(payload: Mapping[str, Any]) -> StructuredExtractionRequest:
    return StructuredExtractionRequest(
        system_instruction=str(payload["system_instruction"]),
        user_text=str(payload["user_text"]),
        response_schema=payload["response_schema"],
        temperature=float(payload.get("temperature", 0.0)),
        max_output_tokens=payload.get("max_output_tokens"),
        prompt_version=str(payload.get("prompt_version", "")),
        prompt_sha256=str(payload.get("prompt_sha256", "")),
        schema_version=str(payload.get("schema_version", "")),
    )


class GatewayStructuredExtractionProvider:
    """Port ekstrakcji nad bramą ewaluatora (odtwarzanie albo nagrywanie)."""

    provider_name = RECORDED_PROVIDER

    def __init__(self, gateway: LlmGateway, model: str = DEFAULT_MODEL) -> None:
        self.gateway = gateway
        self.model = model
        self.calls = 0
        self.misses = 0
        self.input_tokens: int | None = None
        self.output_tokens: int | None = None
        self.latency_ms: float | None = None
        self.cost_usd: float | None = None

    def _add(self, name: str, value: float | int | None) -> None:
        if value is not None:
            setattr(self, name, (getattr(self, name) or 0) + value)

    async def extract_structured(self, request: StructuredExtractionRequest) -> StructuredExtractionResult:
        llm_request = LlmRequest(
            provider=RECORDED_PROVIDER,
            model=self.model,
            prompt_version=request.prompt_version,
            schema_version=request.schema_version,
            input_sha256=request.input_sha256,
            temperature=request.temperature,
            prompt_sha256=request.prompt_sha256,
            payload=request_payload(request),
        )
        self.calls += 1
        try:
            response = self.gateway.complete(llm_request)
        except ReplayMissError:
            self.misses += 1
            raise StructuredExtractionError(StructuredExtractionErrorCode.REPLAY_MISS) from None
        except ReplayIntegrityError:
            raise StructuredExtractionError(StructuredExtractionErrorCode.REPLAY_INTEGRITY) from None
        if not isinstance(response.content, Mapping):
            raise StructuredExtractionError(StructuredExtractionErrorCode.BAD_RESPONSE)
        self._add("input_tokens", response.input_tokens)
        self._add("output_tokens", response.output_tokens)
        self._add("latency_ms", response.latency_ms)
        self._add("cost_usd", response.cost_usd)
        returned = response.model_returned
        return StructuredExtractionResult(
            content=response.content,
            provider=RECORDED_PROVIDER,
            model_requested=self.model,
            model_returned=returned,
            model_mismatch=returned is not None and returned != self.model,
            response_sha256=response.response_sha256,
            request_sha256=request.request_sha256,
            input_sha256=request.input_sha256,
            finish_reason=response.finish_reason or "STOP",
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            latency_ms=response.latency_ms or 0.0,
        )


def _run_in_thread(factory: Callable[[], Any]) -> Any:
    """Uruchamia korutynę w osobnym wątku z własną pętlą (silnik ewaluatora jest synchroniczny)."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(factory())).result()


class LiveGeminiBridge:
    """``LiveProvider`` ewaluatora nad produkcyjnym adapterem Gemini (tylko ``--live``, ręcznie, poza CI)."""

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        *,
        provider_factory: Callable[[SecretStr, GeminiConfig], Any] = GeminiStructuredExtractionProvider,
        pricing: LlmPricing | None = None,
    ) -> None:
        self._api_key = SecretStr(api_key)
        self.model = model
        self._factory = provider_factory
        self._pricing = pricing or LlmPricing()

    def __repr__(self) -> str:  # klucz nigdy w repr
        return f"LiveGeminiBridge(model={self.model!r})"

    def complete(self, request: LlmRequest) -> LlmResponse:
        structured = request_from_payload(request.payload)
        if structured.input_sha256 != request.input_sha256:
            raise ValueError("payload does not match the request key (input_sha256)")

        async def call() -> StructuredExtractionResult:
            provider = self._factory(self._api_key, GeminiConfig(model=self.model))
            try:
                return await provider.extract_structured(structured)
            finally:
                await provider.aclose()

        result: StructuredExtractionResult = _run_in_thread(call)
        output = None if result.output_tokens is None and result.thinking_tokens is None else (
            (result.output_tokens or 0) + (result.thinking_tokens or 0)
        )
        return LlmResponse(
            content=dict(result.content),
            input_tokens=result.input_tokens,
            output_tokens=output,
            latency_ms=result.latency_ms,
            cost_usd=self._pricing.estimate(result.input_tokens, output),
            model_returned=result.model_returned,
            finish_reason=result.finish_reason,
        )


def register_live_gemini(model: str = DEFAULT_MODEL) -> None:
    """Rejestruje most na żywo (wywoływane wyłącznie przy ``--live`` z silnikiem ``hybrid``)."""
    register_live_provider(lambda api_key: LiveGeminiBridge(api_key, model))
