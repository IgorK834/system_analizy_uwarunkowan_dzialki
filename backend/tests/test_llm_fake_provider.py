"""Dostawca odtwarzający zapisane odpowiedzi (PV3-10): klucze, integralność, schemat, brak sieci."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

import pytest

from app.modules.planning.application.ports import (
    StructuredExtractionError,
    StructuredExtractionErrorCode as Code,
    StructuredExtractionProvider,
    StructuredExtractionRequest,
)
from app.modules.planning.domain import extraction_contract as contract
from app.modules.planning.infrastructure.llm.fake_provider import (
    PROVIDER_NAME,
    ReplayStructuredExtractionProvider,
    canonical_json,
)
from scripts.mpzp_eval_engines import LlmRequest, LlmResponse, ReplayStore, sha256_text

MODEL = "gemini-3.8-flash"
CONTENT = {"candidates": [], "not_found": [{"zone_symbol": "1MN", "parameter": "setback_m"}]}


def make_request(text: str = "Zone symbols: 1MN\n...") -> StructuredExtractionRequest:
    return StructuredExtractionRequest(
        system_instruction=contract.system_instruction(), user_text=text, response_schema=contract.RESPONSE_SCHEMA,
        prompt_version=contract.PROMPT_VERSION, prompt_sha256=contract.prompt_sha256(), schema_version=contract.SCHEMA_VERSION,
    )


def record_with_evaluator_store(directory: Path, request: StructuredExtractionRequest, content: Any = CONTENT, **response: Any) -> Path:
    """Zapis tym samym kodem, którym ewaluator nagrywa odpowiedzi (``ReplayStore.put``)."""
    llm_request = LlmRequest(
        provider="gemini", model=MODEL, prompt_version=request.prompt_version, prompt_sha256=request.prompt_sha256,
        schema_version=request.schema_version, temperature=request.temperature, input_sha256=request.input_sha256,
    )
    fields: dict[str, Any] = {"input_tokens": 410, "output_tokens": 55, "model_returned": MODEL, "finish_reason": "STOP"}
    fields.update(response)
    return ReplayStore(directory).put(llm_request, LlmResponse(content=content, **fields), "2026-10-03T00:00:00Z")


async def test_replays_a_response_recorded_by_the_evaluator_store(tmp_path: Path) -> None:
    request = make_request()
    record_with_evaluator_store(tmp_path, request)
    provider = ReplayStructuredExtractionProvider(tmp_path, model=MODEL)
    assert isinstance(provider, StructuredExtractionProvider) and provider.provider_name == PROVIDER_NAME == "fake"
    result = await provider.extract_structured(request)
    assert result.content == CONTENT and provider.calls == 1 and provider.has_response(request)
    assert (result.provider, result.model_requested, result.model_returned, result.model_mismatch) == ("fake", MODEL, MODEL, False)
    assert (result.input_tokens, result.output_tokens, result.finish_reason, result.attempts) == (410, 55, "STOP", 1)
    assert result.response_sha256 == sha256_text(canonical_json(CONTENT))
    assert result.request_sha256 == request.request_sha256 and result.input_sha256 == request.input_sha256


async def test_the_cache_key_matches_the_evaluator_and_the_contract_helper(tmp_path: Path) -> None:
    request = make_request()
    provider = ReplayStructuredExtractionProvider(tmp_path, model=MODEL)
    llm_request = LlmRequest(
        provider="gemini", model=MODEL, prompt_version=request.prompt_version, prompt_sha256=request.prompt_sha256,
        schema_version=request.schema_version, temperature=request.temperature, input_sha256=request.input_sha256,
    )
    assert provider.cache_key(request) == llm_request.cache_key
    assert provider.cache_key(request) == contract.extraction_cache_key(
        provider="gemini", model=MODEL, temperature=0.0, user_text=request.user_text)


@pytest.mark.parametrize(
    "change",
    [{"user_text": "other"}, {"prompt_sha256": "0" * 64}, {"prompt_version": "mpzp-extraction/2"},
     {"schema_version": "mpzp-extraction-schema/2"}, {"temperature": 0.3}],
)
async def test_any_change_of_the_request_identity_is_a_miss_not_a_fallback(tmp_path: Path, change: dict[str, Any]) -> None:
    request = make_request()
    record_with_evaluator_store(tmp_path, request)
    provider = ReplayStructuredExtractionProvider(tmp_path, model=MODEL)
    other = StructuredExtractionRequest(**{**request.__dict__, **change})
    with pytest.raises(StructuredExtractionError) as caught:
        await provider.extract_structured(other)
    assert caught.value.code is Code.REPLAY_MISS and caught.value.retryable is False and not provider.has_response(other)


async def test_a_different_model_or_recorded_provider_is_a_miss(tmp_path: Path) -> None:
    request = make_request()
    record_with_evaluator_store(tmp_path, request)
    for provider in (
        ReplayStructuredExtractionProvider(tmp_path, model="gemini-3.7-flash"),
        ReplayStructuredExtractionProvider(tmp_path, model=MODEL, recorded_provider="other"),
    ):
        with pytest.raises(StructuredExtractionError) as caught:
            await provider.extract_structured(request)
        assert caught.value.code is Code.REPLAY_MISS


async def test_an_empty_or_missing_directory_is_a_miss(tmp_path: Path) -> None:
    for directory in (tmp_path, tmp_path / "nope"):
        with pytest.raises(StructuredExtractionError) as caught:
            await ReplayStructuredExtractionProvider(directory, model=MODEL).extract_structured(make_request())
        assert caught.value.code is Code.REPLAY_MISS


async def test_a_tampered_response_is_an_integrity_error(tmp_path: Path) -> None:
    request = make_request()
    path = record_with_evaluator_store(tmp_path, request)
    record = json.loads(path.read_text("utf-8"))
    record["response"]["content"] = {"candidates": [], "not_found": []}  # skrót już się nie zgadza
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(StructuredExtractionError) as caught:
        await ReplayStructuredExtractionProvider(tmp_path, model=MODEL).extract_structured(request)
    assert caught.value.code is Code.REPLAY_INTEGRITY


@pytest.mark.parametrize("corrupt", ["{not json", "[]", '{"cache_key": "x"}', '{"response": {"content": 1}}'])
async def test_unreadable_records_are_integrity_errors(tmp_path: Path, corrupt: str) -> None:
    request = make_request()
    path = record_with_evaluator_store(tmp_path, request)
    path.write_text(corrupt, encoding="utf-8")
    with pytest.raises(StructuredExtractionError) as caught:
        await ReplayStructuredExtractionProvider(tmp_path, model=MODEL).extract_structured(request)
    assert caught.value.code is Code.REPLAY_INTEGRITY


async def test_a_recorded_answer_that_breaks_the_schema_is_rejected_like_a_live_one(tmp_path: Path) -> None:
    request = make_request()
    record_with_evaluator_store(tmp_path, request, content={"candidates": "x", "not_found": []})
    with pytest.raises(StructuredExtractionError) as caught:
        await ReplayStructuredExtractionProvider(tmp_path, model=MODEL).extract_structured(request)
    assert caught.value.code is Code.SCHEMA_VIOLATION and "$.candidates:type" in caught.value.violations


async def test_a_recorded_other_model_id_is_reported_as_a_mismatch(tmp_path: Path) -> None:
    request = make_request()
    record_with_evaluator_store(tmp_path, request, model_returned="gemini-3.8-flash-lite")
    result = await ReplayStructuredExtractionProvider(tmp_path, model=MODEL).extract_structured(request)
    assert result.model_mismatch is True and result.model_returned == "gemini-3.8-flash-lite"
    path = next(tmp_path.glob("*.json"))
    record = json.loads(path.read_text("utf-8"))
    record["response"]["model_returned"] = None
    path.write_text(json.dumps(record), encoding="utf-8")
    again = await ReplayStructuredExtractionProvider(tmp_path, model=MODEL).extract_structured(request)
    assert again.model_returned is None and again.model_mismatch is False


async def test_the_provider_never_opens_a_socket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("odtwarzanie nie może używać sieci")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    request = make_request()
    record_with_evaluator_store(tmp_path, request)
    provider = ReplayStructuredExtractionProvider(tmp_path, model=MODEL)
    assert (await provider.extract_structured(request)).content == CONTENT
    with pytest.raises(StructuredExtractionError):
        await provider.extract_structured(make_request("missing"))
