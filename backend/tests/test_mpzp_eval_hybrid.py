"""Silnik ``hybrid`` ewaluatora i most do adaptera (PV3-14) — bez sieci i bez klucza.

Złote odpowiedzi (``tests/fixtures/mpzp_evaluation/llm_replay``) są składane z adnotacji, nie nagrane z
modelu: sprawdzają potok (klucz, kontrakt, bramki, scalanie), nie jakość modelu.
"""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from app.modules.planning.application.ports import (
    StructuredExtractionError,
    StructuredExtractionErrorCode,
    StructuredExtractionRequest,
    StructuredExtractionResult,
)
from scripts import build_llm_replay_fixtures as golden
from scripts import evaluate_mpzp_parser as ev
from scripts import mpzp_eval_engines as engines
from scripts import mpzp_eval_hybrid as bridge


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("ewaluacja z odtwarzania nie może używać sieci")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


def _loaded(case: golden.GoldenCase) -> dict[str, Any]:
    manifest, base = golden.corpus()
    sample = next(item for item in manifest["samples"] if item["sample_id"] == case.sample_id)
    return ev.load_document(base, manifest["documents"][sample["document_id"]])


@pytest.mark.parametrize("case", golden.CASES, ids=lambda case: case.case_id)
def test_the_hybrid_engine_replays_the_golden_responses_without_network(case: golden.GoldenCase, no_network: None) -> None:
    gateway = engines.build_gateway(replay_dir=golden.DEFAULT_OUTPUT_DIR, live=False)
    engine = engines.create_engine("hybrid", engines.EngineContext(gateway=gateway))
    result = engine.run(_loaded(case), list(case.symbols))
    assert result.usage.calls == gateway.replayed and gateway.recorded == 0
    assert "MPZP_LLM_UNAVAILABLE" not in result.warning_codes
    assert all(value.review_status in (None, "ai_candidate") for values in result.zones.values() for value in values)


def test_a_missing_recorded_response_fails_the_run_instead_of_degrading(tmp_path: Path) -> None:
    gateway = engines.build_gateway(replay_dir=tmp_path, live=False)
    engine = engines.create_engine("hybrid", engines.EngineContext(gateway=gateway))
    case = golden.CASES[0]
    with pytest.raises(engines.ReplayMissError):
        engine.run(_loaded(case), list(case.symbols))
    with pytest.raises(engines.EngineUnavailableError):
        ev._hybrid_factory(engines.EngineContext(gateway=None))


def _request() -> StructuredExtractionRequest:
    return StructuredExtractionRequest(
        system_instruction="Extract.", user_text="Zone symbols: 1MN\ntekst", response_schema={"type": "object"},
        prompt_version="p/1", prompt_sha256="a" * 64, schema_version="s/1",
    )


class _StubGateway:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response, self.error, self.requests = response, error, []

    def complete(self, request: engines.LlmRequest) -> engines.LlmResponse:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.response


async def test_the_gateway_provider_maps_errors_and_keeps_the_payload_out_of_the_key() -> None:
    ok = _StubGateway(engines.LlmResponse(content={"candidates": [], "not_found": []}, input_tokens=10, output_tokens=5,
                                          latency_ms=3.0, cost_usd=0.001, model_returned="gemini-3.8-flash"))
    provider = bridge.GatewayStructuredExtractionProvider(ok)  # type: ignore[arg-type]
    result = await provider.extract_structured(_request())
    assert result.content == {"candidates": [], "not_found": []} and not result.model_mismatch
    sent = ok.requests[0]
    assert sent.payload["user_text"] == "Zone symbols: 1MN\ntekst" and "user_text" not in sent.identity()
    assert sent.cache_key == engines.LlmRequest("gemini", "gemini-3.8-flash", "p/1", "s/1", _request().input_sha256,
                                                0.0, "a" * 64).cache_key
    assert (provider.input_tokens, provider.cost_usd) == (10, 0.001)
    for error, code in ((engines.ReplayMissError("x"), StructuredExtractionErrorCode.REPLAY_MISS),
                        (engines.ReplayIntegrityError("x"), StructuredExtractionErrorCode.REPLAY_INTEGRITY)):
        with pytest.raises(StructuredExtractionError) as caught:
            await bridge.GatewayStructuredExtractionProvider(_StubGateway(error=error)).extract_structured(_request())  # type: ignore[arg-type]
        assert caught.value.code == code
    with pytest.raises(StructuredExtractionError) as caught:
        await bridge.GatewayStructuredExtractionProvider(
            _StubGateway(engines.LlmResponse(content=["x"]))).extract_structured(_request())  # type: ignore[arg-type]
    assert caught.value.code == StructuredExtractionErrorCode.BAD_RESPONSE


def test_the_live_bridge_calls_the_production_adapter_in_its_own_loop_and_never_shows_the_key() -> None:
    seen: list[tuple[str, str]] = []

    class FakeAdapter:
        def __init__(self, key: SecretStr, config: Any) -> None:
            seen.append((key.get_secret_value(), config.model))
            self.closed = False

        async def extract_structured(self, request: StructuredExtractionRequest) -> StructuredExtractionResult:
            return StructuredExtractionResult(
                content={"candidates": [], "not_found": []}, provider="gemini", model_requested="gemini-3.8-flash",
                model_returned="gemini-3.8-flash", model_mismatch=False, response_sha256="r" * 64,
                request_sha256=request.request_sha256, input_sha256=request.input_sha256, finish_reason="STOP",
                input_tokens=1000, output_tokens=200, thinking_tokens=50, latency_ms=900.0,
            )

        async def aclose(self) -> None:
            self.closed = True

    live = bridge.LiveGeminiBridge("AIzaSyFAKE-not-a-key", provider_factory=FakeAdapter)
    request = _request()
    response = live.complete(engines.LlmRequest("gemini", "gemini-3.8-flash", "p/1", "s/1", request.input_sha256,
                                                payload=bridge.request_payload(request)))
    assert seen == [("AIzaSyFAKE-not-a-key", "gemini-3.8-flash")]
    assert (response.input_tokens, response.output_tokens) == (1000, 250)
    assert response.cost_usd == pytest.approx((1000 * 1.5 + 250 * 7.5) / 1e6)
    assert "AIza" not in repr(live)
    with pytest.raises(ValueError):
        live.complete(engines.LlmRequest("gemini", "gemini-3.8-flash", "p/1", "s/1", "0" * 64,
                                         payload=bridge.request_payload(request)))
    assert bridge.request_from_payload(bridge.request_payload(request)) == request


def test_live_hybrid_registers_the_production_bridge_only_when_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setenv(engines.LLM_API_KEY_ENV, "AIzaSyFAKE-not-a-key")
    monkeypatch.setattr(engines, "_LIVE_PROVIDER_FACTORY", None)
    args = ev.build_parser().parse_args(
        ["--engine", "hybrid", "--live", "--llm-replay", str(tmp_path), "--output-dir", str(tmp_path / "o")]
    )
    try:
        _names, gateway = ev._resolve_engines(args)
        assert gateway.mode == "live" and isinstance(gateway.provider, bridge.LiveGeminiBridge)
    finally:
        engines.register_live_provider(None)
