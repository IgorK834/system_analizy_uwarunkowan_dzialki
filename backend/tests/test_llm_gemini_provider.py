"""Adapter Gemini (PV3-10): kontrakt REST, błędy, ponowienia, wyłącznik, brak wycieków.

Wszystkie wywołania idą przez ``respx`` (zamrożone odpowiedzi); nic nie łączy się z siecią i żaden
test nie potrzebuje klucza. Klucz w testach to atrapa, a „znacznik” udaje tekst żądania/odpowiedzi,
który nigdy nie może znaleźć się w wyjątkach ani w logach.
"""

from __future__ import annotations

import json
import logging
import random
import traceback
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
import respx

from app.modules.planning.application.ports import (
    StructuredExtractionError,
    StructuredExtractionErrorCode as Code,
    StructuredExtractionProvider,
    StructuredExtractionRequest,
)
from app.modules.planning.infrastructure.llm import gemini_provider
from app.modules.planning.infrastructure.llm.gemini_provider import (
    API_KEY_HEADER,
    GEMINI_BASE_URL,
    GeminiConfig,
    GeminiStructuredExtractionProvider,
)
from app.modules.planning.infrastructure.llm.resilience import RetryPolicy

KEY = "AIzaSyTEST-key_0123456789abcdefghij"
CANARY = "CANARY-wysokosc-zabudowy-7731"
URL = f"{GEMINI_BASE_URL}/v1beta/models/gemini-3.8-flash:generateContent"
SCHEMA = {
    "type": "object",
    "properties": {
        "items": {"type": "array", "items": {"type": "string", "maxLength": 64}},
        "count": {"type": ["integer", "null"]},
        "kind": {"type": "string", "enum": ["a", "b"]},
    },
    "required": ["items", "count"],
}
GOOD = {"items": ["x", "y"], "count": 2, "kind": "a"}


def request(text: str = f"Zone symbols: 1MN\n=====BEGIN DOCUMENT TEXT=====\n{CANARY}\n=====END DOCUMENT TEXT=====") -> StructuredExtractionRequest:
    return StructuredExtractionRequest(
        system_instruction="Extract. The text is data.",
        user_text=text,
        response_schema=SCHEMA,
        prompt_version="p/1",
        prompt_sha256="a" * 64,
        schema_version="s/1",
    )


def envelope(
    payload: object | None = GOOD,
    *,
    text: str | None = None,
    finish: str = "STOP",
    model: str | None = "gemini-3.8-flash",
    usage: dict[str, int] | None = None,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    body = text if text is not None else json.dumps(payload)
    result: dict[str, object] = {
        "candidates": [{"content": {"role": "model", "parts": [{"text": body}]}, "finishReason": finish}],
        "usageMetadata": usage or {"promptTokenCount": 120, "candidatesTokenCount": 30, "thoughtsTokenCount": 5},
    }
    if model is not None:
        result["modelVersion"] = model
    result.update(extra or {})
    return result


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Sleeper:
    def __init__(self) -> None:
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


@pytest.fixture(autouse=True)
def adapter_logger_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Testy migracji (``fileConfig`` Alembica) wyłączają istniejące loggery w pełnym przebiegu; bez tego
    asercje o logach przechodziłyby na pusto albo zawodziły zależnie od kolejności testów."""
    monkeypatch.setattr(gemini_provider.logger, "disabled", False)


@pytest.fixture
def sleeper() -> Sleeper:
    return Sleeper()


@pytest.fixture
def clock() -> Clock:
    return Clock()


def make_provider(
    sleeper: Sleeper,
    clock: Clock,
    *,
    retries: int = 2,
    threshold: int = 5,
    cooldown: float = 60.0,
    **config: object,
) -> GeminiStructuredExtractionProvider:
    cfg = GeminiConfig(
        retry=RetryPolicy(max_retries=retries, base_delay_seconds=1.0, max_delay_seconds=8.0, max_retry_after_seconds=30.0),
        breaker_failure_threshold=threshold,
        breaker_cooldown_seconds=cooldown,
        **config,  # type: ignore[arg-type]
    )
    return GeminiStructuredExtractionProvider(KEY, cfg, sleep=sleeper, rng=random.Random(7), clock=clock)


@pytest.fixture
def mock() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        yield router


# --- kształt żądania i wynik ---------------------------------------------------------------


async def test_success_sends_the_documented_request_and_returns_provenance(
    mock: respx.MockRouter, sleeper: Sleeper, clock: Clock
) -> None:
    route = mock.post(URL).respond(200, json=envelope())
    provider = make_provider(sleeper, clock)
    assert isinstance(provider, StructuredExtractionProvider)
    result = await provider.extract_structured(request())

    sent = route.calls.last.request
    assert str(sent.url) == URL and sent.url.scheme == "https" and sent.url.host == "generativelanguage.googleapis.com"
    assert sent.headers[API_KEY_HEADER] == KEY and KEY not in str(sent.url)
    body = json.loads(sent.content)
    assert body["systemInstruction"]["parts"][0]["text"] == "Extract. The text is data."
    assert body["contents"] == [{"role": "user", "parts": [{"text": request().user_text}]}]
    assert CANARY not in json.dumps(body["systemInstruction"])  # instrukcja nie niesie danych dokumentu
    generation = body["generationConfig"]
    assert generation["temperature"] == 0.0 and generation["responseMimeType"] == "application/json"
    assert generation["responseJsonSchema"] == SCHEMA
    assert generation["maxOutputTokens"] == 8192 and generation["thinkingConfig"] == {"thinkingLevel": "low"}

    assert result.content == GOOD
    assert (result.provider, result.model_requested, result.model_returned) == ("gemini", "gemini-3.8-flash", "gemini-3.8-flash")
    assert result.model_mismatch is False and result.finish_reason == "STOP" and result.attempts == 1
    assert (result.input_tokens, result.output_tokens, result.thinking_tokens) == (120, 30, 5)
    assert len(result.response_sha256) == 64 and result.request_sha256 == request().request_sha256
    assert result.input_sha256 == request().input_sha256 and result.latency_ms >= 0
    assert sleeper.waits == []


async def test_works_with_the_real_extraction_schema(mock: respx.MockRouter, sleeper: Sleeper, clock: Clock) -> None:
    from app.modules.planning.domain import extraction_contract as contract

    payload = {"candidates": [], "not_found": [{"zone_symbol": "1MN", "parameter": "setback_m"}]}
    mock.post(URL).respond(200, json=envelope(payload))
    req = StructuredExtractionRequest("s", "u", contract.RESPONSE_SCHEMA)
    result = await make_provider(sleeper, clock).extract_structured(req)
    assert result.content == payload


async def test_model_id_with_models_prefix_is_not_a_mismatch(mock: respx.MockRouter, sleeper: Sleeper, clock: Clock) -> None:
    mock.post(URL).respond(200, json=envelope(model="models/gemini-3.8-flash"))
    result = await make_provider(sleeper, clock).extract_structured(request())
    assert result.model_mismatch is False and result.model_returned == "gemini-3.8-flash"


async def test_different_returned_model_is_detected_and_reported(
    mock: respx.MockRouter, sleeper: Sleeper, clock: Clock, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    mock.post(URL).respond(200, json=envelope(model="gemini-3.8-flash-lite"))
    result = await make_provider(sleeper, clock).extract_structured(request())
    assert result.model_mismatch is True
    assert (result.model_requested, result.model_returned) == ("gemini-3.8-flash", "gemini-3.8-flash-lite")
    assert any("model_mismatch" in record.getMessage() and "gemini-3.8-flash-lite" in record.getMessage() for record in caplog.records)


async def test_missing_model_version_is_unverifiable_not_a_mismatch(mock: respx.MockRouter, sleeper: Sleeper, clock: Clock) -> None:
    mock.post(URL).respond(200, json=envelope(model=None))
    result = await make_provider(sleeper, clock).extract_structured(request())
    assert result.model_returned is None and result.model_mismatch is False


async def test_thought_parts_are_not_part_of_the_answer(mock: respx.MockRouter, sleeper: Sleeper, clock: Clock) -> None:
    body = envelope()
    body["candidates"][0]["content"]["parts"].insert(0, {"text": "reasoning that is not JSON", "thought": True})  # type: ignore[index]
    mock.post(URL).respond(200, json=body)
    assert (await make_provider(sleeper, clock).extract_structured(request())).content == GOOD


# --- statusy HTTP -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, Code.BAD_REQUEST),
        (401, Code.UNAUTHORIZED),
        (403, Code.FORBIDDEN),
        (404, Code.MODEL_NOT_FOUND),
        (413, Code.REQUEST_TOO_LARGE),
        (418, Code.BAD_RESPONSE),
    ],
)
async def test_non_retryable_statuses_fail_at_once(
    mock: respx.MockRouter, sleeper: Sleeper, clock: Clock, status: int, code: Code
) -> None:
    route = mock.post(URL).respond(status, json={"error": {"code": status, "status": "INVALID_ARGUMENT", "message": CANARY}})
    with pytest.raises(StructuredExtractionError) as caught:
        await make_provider(sleeper, clock).extract_structured(request())
    error = caught.value
    assert error.code is code and error.status == status and error.retryable is False and error.attempts == 1
    assert route.call_count == 1 and sleeper.waits == []
    assert error.detail == "INVALID_ARGUMENT"  # tylko znacznik statusu dostawcy, nie komunikat
    assert CANARY not in str(error)


@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_server_errors_are_retried_with_growing_jittered_delays(
    mock: respx.MockRouter, sleeper: Sleeper, clock: Clock, status: int
) -> None:
    route = mock.post(URL).respond(status)
    with pytest.raises(StructuredExtractionError) as caught:
        await make_provider(sleeper, clock, retries=2).extract_structured(request())
    error = caught.value
    assert error.code is Code.SERVER_ERROR and error.retryable is True and error.attempts == 3
    assert route.call_count == 3 and len(sleeper.waits) == 2
    first, second = sleeper.waits
    assert 0.5 <= first <= 1.0 and 1.0 <= second <= 2.0  # base 1 s, 2 s; jitter odejmuje do 50%


async def test_recovers_after_a_transient_server_error(mock: respx.MockRouter, sleeper: Sleeper, clock: Clock) -> None:
    mock.post(URL).mock(side_effect=[httpx.Response(503), httpx.Response(200, json=envelope())])
    result = await make_provider(sleeper, clock).extract_structured(request())
    assert result.content == GOOD and result.attempts == 2 and len(sleeper.waits) == 1


async def test_rate_limit_honours_retry_after(mock: respx.MockRouter, sleeper: Sleeper, clock: Clock) -> None:
    mock.post(URL).mock(
        side_effect=[httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, json=envelope())]
    )
    result = await make_provider(sleeper, clock).extract_structured(request())
    assert result.attempts == 2 and sleeper.waits and sleeper.waits[0] >= 7.0  # Retry-After jest dolną granicą


async def test_rate_limit_with_excessive_retry_after_is_not_waited_for(
    mock: respx.MockRouter, sleeper: Sleeper, clock: Clock
) -> None:
    route = mock.post(URL).respond(429, headers={"Retry-After": "3600"})
    with pytest.raises(StructuredExtractionError) as caught:
        await make_provider(sleeper, clock).extract_structured(request())
    assert caught.value.code is Code.RATE_LIMITED and caught.value.retry_after == 3600.0
    assert route.call_count == 1 and sleeper.waits == []


async def test_rate_limit_without_header_uses_backoff_and_then_fails(
    mock: respx.MockRouter, sleeper: Sleeper, clock: Clock
) -> None:
    route = mock.post(URL).respond(429)
    with pytest.raises(StructuredExtractionError) as caught:
        await make_provider(sleeper, clock, retries=1).extract_structured(request())
    assert caught.value.code is Code.RATE_LIMITED and caught.value.attempts == 2 and route.call_count == 2


async def test_zero_retries_means_one_attempt(mock: respx.MockRouter, sleeper: Sleeper, clock: Clock) -> None:
    route = mock.post(URL).respond(500)
    with pytest.raises(StructuredExtractionError):
        await make_provider(sleeper, clock, retries=0).extract_structured(request())
    assert route.call_count == 1 and sleeper.waits == []


async def test_http_408_is_retryable(mock: respx.MockRouter, sleeper: Sleeper, clock: Clock) -> None:
    mock.post(URL).mock(side_effect=[httpx.Response(408), httpx.Response(200, json=envelope())])
    assert (await make_provider(sleeper, clock).extract_structured(request())).attempts == 2


# --- transport ----------------------------------------------------------------------------------


async def test_timeout_is_classified_and_retried(mock: respx.MockRouter, sleeper: Sleeper, clock: Clock) -> None:
    route = mock.post(URL).mock(side_effect=httpx.ReadTimeout("timed out"))
    with pytest.raises(StructuredExtractionError) as caught:
        await make_provider(sleeper, clock).extract_structured(request())
    assert caught.value.code is Code.TIMEOUT and caught.value.retryable and caught.value.attempts == 3
    assert route.call_count == 3


async def test_connection_errors_are_classified_as_network(mock: respx.MockRouter, sleeper: Sleeper, clock: Clock) -> None:
    mock.post(URL).mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(StructuredExtractionError) as caught:
        await make_provider(sleeper, clock, retries=1).extract_structured(request())
    assert caught.value.code is Code.NETWORK and caught.value.attempts == 2


async def test_non_transport_http_errors_are_not_retried(mock: respx.MockRouter, sleeper: Sleeper, clock: Clock) -> None:
    route = mock.post(URL).mock(side_effect=httpx.TooManyRedirects("bad"))
    with pytest.raises(StructuredExtractionError) as caught:
        await make_provider(sleeper, clock).extract_structured(request())
    assert caught.value.code is Code.NETWORK and caught.value.retryable is False and route.call_count == 1


async def test_redirect_is_never_followed(mock: respx.MockRouter, sleeper: Sleeper, clock: Clock) -> None:
    evil = mock.get("https://evil.example/collect").respond(200)
    evil_post = mock.post("https://evil.example/collect").respond(200)
    route = mock.post(URL).respond(302, headers={"Location": "https://evil.example/collect"})
    with pytest.raises(StructuredExtractionError) as caught:
        await make_provider(sleeper, clock).extract_structured(request())
    assert caught.value.code is Code.UNEXPECTED_REDIRECT and caught.value.status == 302
    assert route.call_count == 1 and evil.call_count == 0 and evil_post.call_count == 0


async def test_declared_oversized_response_is_rejected(mock: respx.MockRouter, sleeper: Sleeper, clock: Clock) -> None:
    mock.post(URL).respond(200, content=b"x" * 5000)
    with pytest.raises(StructuredExtractionError) as caught:
        await make_provider(sleeper, clock, max_response_bytes=2048).extract_structured(request())
    assert caught.value.code is Code.RESPONSE_TOO_LARGE and caught.value.retryable is False


async def test_streamed_oversized_response_is_cut_off(mock: respx.MockRouter, sleeper: Sleeper, clock: Clock) -> None:
    async def body() -> AsyncIterator[bytes]:
        for _ in range(5):
            yield b"x" * 600

    mock.post(URL).mock(return_value=httpx.Response(200, content=body()))
    with pytest.raises(StructuredExtractionError) as caught:
        await make_provider(sleeper, clock, max_response_bytes=2048).extract_structured(request())
    assert caught.value.code is Code.RESPONSE_TOO_LARGE


async def test_oversized_request_is_not_sent(mock: respx.MockRouter, sleeper: Sleeper, clock: Clock) -> None:
    route = mock.post(URL).respond(200, json=envelope())
    with pytest.raises(StructuredExtractionError) as caught:
        await make_provider(sleeper, clock, max_request_bytes=2048).extract_structured(request("x" * 5000))
    assert caught.value.code is Code.REQUEST_TOO_LARGE and route.call_count == 0


# --- treść odpowiedzi -------------------------------------------------------------------------------


async def test_invalid_json_text(mock: respx.MockRouter, sleeper: Sleeper, clock: Clock) -> None:
    mock.post(URL).respond(200, json=envelope(text=f'{{"items": ["{CANARY}"'))
    with pytest.raises(StructuredExtractionError) as caught:
        await make_provider(sleeper, clock).extract_structured(request())
    assert caught.value.code is Code.INVALID_JSON and caught.value.retryable is False and caught.value.attempts == 1


async def test_empty_answer_is_invalid_json(mock: respx.MockRouter, sleeper: Sleeper, clock: Clock) -> None:
    mock.post(URL).respond(200, json=envelope(text=""))
    with pytest.raises(StructuredExtractionError) as caught:
        await make_provider(sleeper, clock).extract_structured(request())
    assert caught.value.code is Code.INVALID_JSON


async def test_invalid_envelope(mock: respx.MockRouter, sleeper: Sleeper, clock: Clock) -> None:
    mock.post(URL).respond(200, content=b"<html>not json</html>")
    with pytest.raises(StructuredExtractionError) as caught:
        await make_provider(sleeper, clock).extract_structured(request())
    assert caught.value.code is Code.INVALID_JSON
    mock.post(URL).respond(200, json=[1, 2, 3])
    with pytest.raises(StructuredExtractionError) as caught:
        await make_provider(sleeper, clock).extract_structured(request())
    assert caught.value.code is Code.BAD_RESPONSE


@pytest.mark.parametrize(
    ("payload", "path"),
    [
        ({"items": ["x"]}, "$.count:required"),
        ({"items": "x", "count": 1}, "$.items:type"),
        ({"items": ["x"], "count": "1"}, "$.count:type"),
        ({"items": ["x", 3], "count": 1}, "$.items[1]:type"),
        ({"items": ["x"], "count": 1, "kind": "zzz"}, "$.kind:enum"),
        ({"items": ["x" * 80], "count": 1}, "$.items[0]:maxLength"),
        ([], "$:type"),
    ],
)
async def test_schema_violation_is_reported_with_paths_only(
    mock: respx.MockRouter, sleeper: Sleeper, clock: Clock, payload: object, path: str
) -> None:
    mock.post(URL).respond(200, json=envelope(payload))
    with pytest.raises(StructuredExtractionError) as caught:
        await make_provider(sleeper, clock).extract_structured(request())
    error = caught.value
    assert error.code is Code.SCHEMA_VIOLATION and error.retryable is False
    assert path in error.violations
    assert "x" * 80 not in str(error) and "x" * 80 not in repr(error.violations)


async def test_truncated_output_is_reported_even_when_the_json_is_cut(
    mock: respx.MockRouter, sleeper: Sleeper, clock: Clock
) -> None:
    mock.post(URL).respond(200, json=envelope(text='{"items": ["a", "b', finish="MAX_TOKENS"))
    with pytest.raises(StructuredExtractionError) as caught:
        await make_provider(sleeper, clock).extract_structured(request())
    assert caught.value.code is Code.TRUNCATED and caught.value.detail == "MAX_TOKENS" and caught.value.retryable is False


async def test_truncation_wins_over_a_json_that_happens_to_parse(mock: respx.MockRouter, sleeper: Sleeper, clock: Clock) -> None:
    mock.post(URL).respond(200, json=envelope(GOOD, finish="MAX_TOKENS"))
    with pytest.raises(StructuredExtractionError) as caught:
        await make_provider(sleeper, clock).extract_structured(request())
    assert caught.value.code is Code.TRUNCATED


@pytest.mark.parametrize("reason", ["SAFETY", "RECITATION", "BLOCKLIST", "OTHER", "weird reason"])
async def test_non_stop_finish_reasons_are_blocked(mock: respx.MockRouter, sleeper: Sleeper, clock: Clock, reason: str) -> None:
    mock.post(URL).respond(200, json=envelope(GOOD, finish=reason))
    with pytest.raises(StructuredExtractionError) as caught:
        await make_provider(sleeper, clock).extract_structured(request())
    assert caught.value.code is Code.BLOCKED


async def test_answer_without_candidates_is_blocked(mock: respx.MockRouter, sleeper: Sleeper, clock: Clock) -> None:
    mock.post(URL).respond(200, json={"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}, "modelVersion": "gemini-3.8-flash"})
    with pytest.raises(StructuredExtractionError) as caught:
        await make_provider(sleeper, clock).extract_structured(request())
    assert caught.value.code is Code.BLOCKED and caught.value.detail == "no_candidates"


async def test_usage_metadata_is_optional_and_untrusted(mock: respx.MockRouter, sleeper: Sleeper, clock: Clock) -> None:
    body = envelope()
    body["usageMetadata"] = {"promptTokenCount": "many", "candidatesTokenCount": -3}
    mock.post(URL).respond(200, json=body)
    result = await make_provider(sleeper, clock).extract_structured(request())
    assert (result.input_tokens, result.output_tokens, result.thinking_tokens) == (None, None, None)


# --- wyłącznik awaryjny ----------------------------------------------------------------------------------


async def test_circuit_breaker_opens_after_consecutive_failures_and_recovers(
    mock: respx.MockRouter, sleeper: Sleeper, clock: Clock
) -> None:
    route = mock.post(URL).respond(503)
    provider = make_provider(sleeper, clock, retries=0, threshold=3, cooldown=60.0)
    for _ in range(3):
        with pytest.raises(StructuredExtractionError) as caught:
            await provider.extract_structured(request())
        assert caught.value.code is Code.SERVER_ERROR
    assert provider.breaker.state == "open" and route.call_count == 3

    with pytest.raises(StructuredExtractionError) as caught:
        await provider.extract_structured(request())
    assert caught.value.code is Code.CIRCUIT_OPEN and caught.value.attempts == 0
    assert route.call_count == 3  # żadnego ruchu przy otwartym wyłączniku

    clock.now += 61.0
    assert provider.breaker.state == "half_open"
    route.respond(200, json=envelope())
    result = await provider.extract_structured(request())  # jedna próba testowa
    assert result.content == GOOD and provider.breaker.state == "closed"


async def test_failed_probe_reopens_the_breaker(mock: respx.MockRouter, sleeper: Sleeper, clock: Clock) -> None:
    route = mock.post(URL).respond(500)
    provider = make_provider(sleeper, clock, retries=0, threshold=2, cooldown=30.0)
    for _ in range(2):
        with pytest.raises(StructuredExtractionError):
            await provider.extract_structured(request())
    clock.now += 31.0
    with pytest.raises(StructuredExtractionError) as caught:
        await provider.extract_structured(request())
    assert caught.value.code is Code.SERVER_ERROR  # próba poszła i zawiodła
    assert provider.breaker.state == "open"
    with pytest.raises(StructuredExtractionError) as caught:
        await provider.extract_structured(request())
    assert caught.value.code is Code.CIRCUIT_OPEN and route.call_count == 3


async def test_content_and_request_errors_do_not_trip_the_breaker(mock: respx.MockRouter, sleeper: Sleeper, clock: Clock) -> None:
    route = mock.post(URL)
    provider = make_provider(sleeper, clock, retries=0, threshold=2)
    for response in (
        httpx.Response(400),
        httpx.Response(200, json=envelope(text="not json")),
        httpx.Response(200, json=envelope(GOOD, finish="MAX_TOKENS")),
        httpx.Response(200, json=envelope({"items": 1})),
        httpx.Response(400),
    ):
        route.mock(return_value=response)
        with pytest.raises(StructuredExtractionError):
            await provider.extract_structured(request())
    assert provider.breaker.state == "closed" and provider.breaker.consecutive_failures == 0


async def test_success_resets_the_failure_streak(mock: respx.MockRouter, sleeper: Sleeper, clock: Clock) -> None:
    route = mock.post(URL)
    provider = make_provider(sleeper, clock, retries=0, threshold=3)
    for response in (httpx.Response(500), httpx.Response(500), httpx.Response(200, json=envelope()), httpx.Response(500), httpx.Response(500)):
        route.mock(return_value=response)
        try:
            await provider.extract_structured(request())
        except StructuredExtractionError:
            pass
    assert provider.breaker.state == "closed" and provider.breaker.consecutive_failures == 2


async def test_auth_errors_count_as_availability_failures(mock: respx.MockRouter, sleeper: Sleeper, clock: Clock) -> None:
    route = mock.post(URL).respond(401)
    provider = make_provider(sleeper, clock, retries=0, threshold=2)
    for _ in range(2):
        with pytest.raises(StructuredExtractionError):
            await provider.extract_structured(request())
    with pytest.raises(StructuredExtractionError) as caught:
        await provider.extract_structured(request())
    assert caught.value.code is Code.CIRCUIT_OPEN and route.call_count == 2


# --- sekrety i treść żądania ------------------------------------------------------------------------------


def _scenarios(mock: respx.MockRouter) -> list[tuple[str, object]]:
    echo = {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": f"bad value {CANARY} for key {KEY}"}}
    return [
        ("400 echo", lambda: mock.post(URL).respond(400, json=echo)),
        ("401 echo", lambda: mock.post(URL).respond(401, json=echo)),
        ("403 echo", lambda: mock.post(URL).respond(403, text=f"{KEY} {CANARY}")),
        ("429", lambda: mock.post(URL).respond(429, headers={"Retry-After": "1"}, text=CANARY)),
        ("500 echo", lambda: mock.post(URL).respond(500, text=f"{CANARY} {KEY}")),
        ("timeout", lambda: mock.post(URL).mock(side_effect=httpx.ReadTimeout(f"{CANARY} {KEY}"))),
        ("connect", lambda: mock.post(URL).mock(side_effect=httpx.ConnectError(f"{CANARY} {KEY}"))),
        ("invalid json", lambda: mock.post(URL).respond(200, json=envelope(text=f'{{"items": ["{CANARY} {KEY}"'))),
        ("schema", lambda: mock.post(URL).respond(200, json=envelope({"items": [CANARY * 3], "count": CANARY}))),
        ("truncated", lambda: mock.post(URL).respond(200, json=envelope(text=f'{{"items": ["{CANARY}', finish="MAX_TOKENS"))),
        ("blocked", lambda: mock.post(URL).respond(200, json=envelope(GOOD, finish="SAFETY"))),
        ("mismatch ok", lambda: mock.post(URL).respond(200, json=envelope({"items": [CANARY], "count": 1}, model="other-model"))),
        ("success echo", lambda: mock.post(URL).respond(200, json=envelope({"items": [CANARY], "count": 1}))),
    ]


@pytest.mark.parametrize("index", range(13))
async def test_neither_the_key_nor_the_request_text_leaks_into_errors_or_logs(
    mock: respx.MockRouter, sleeper: Sleeper, clock: Clock, caplog: pytest.LogCaptureFixture, index: int
) -> None:
    caplog.set_level(logging.DEBUG)
    name, arm = _scenarios(mock)[index]
    arm()  # type: ignore[operator]
    provider = make_provider(sleeper, clock, retries=1)
    surfaces: list[str] = []
    try:
        result = await provider.extract_structured(request())
        surfaces.append(repr(result.model_returned))
    except StructuredExtractionError as exc:
        surfaces += [str(exc), repr(exc), repr(exc.__dict__), "".join(traceback.format_exception(exc))]
    surfaces += [caplog.text, repr(provider)]
    surfaces += [json.dumps(record.__dict__, default=str) for record in caplog.records]
    for surface in surfaces:
        assert KEY not in surface, name
        assert "AIzaSy" not in surface, name
        assert CANARY not in surface, name


async def test_logs_carry_hashes_tokens_finish_reason_and_returned_model(
    mock: respx.MockRouter, sleeper: Sleeper, clock: Clock, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    mock.post(URL).respond(200, json=envelope())
    result = await make_provider(sleeper, clock).extract_structured(request())
    line = next(record.getMessage() for record in caplog.records if "outcome=ok" in record.getMessage())
    assert "input_tokens=120" in line and "output_tokens=30" in line and "finish=STOP" in line
    assert "returned=gemini-3.8-flash" in line
    assert result.response_sha256[:16] in line and result.request_sha256[:16] in line


async def test_the_key_goes_only_to_the_fixed_host_in_a_header(mock: respx.MockRouter, sleeper: Sleeper, clock: Clock) -> None:
    route = mock.post(URL).respond(200, json=envelope())
    await make_provider(sleeper, clock).extract_structured(request())
    assert route.call_count == 1 and mock.calls.call_count == 1
    for call in mock.calls:
        assert call.request.url.host == "generativelanguage.googleapis.com"
        assert KEY not in str(call.request.url)


# --- konfiguracja i cykl życia -------------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["", "short", "has space in it 123", "line\nbreak-key-123456", "ąęśćżźół-klucz-123456"])
def test_malformed_keys_are_rejected_without_echoing_them(key: str) -> None:
    with pytest.raises(StructuredExtractionError) as caught:
        GeminiStructuredExtractionProvider(key)
    assert caught.value.code is Code.CONFIGURATION
    if key.strip():
        assert key not in str(caught.value) and key not in repr(caught.value)


@pytest.mark.parametrize("model", ["", "../x", "a/b", "m?key=1", "a" * 65, "name with space", "-lead"])
def test_unsafe_model_ids_are_rejected(model: str) -> None:
    with pytest.raises(StructuredExtractionError) as caught:
        GeminiConfig(model=model)
    assert caught.value.code is Code.CONFIGURATION


@pytest.mark.parametrize(
    "overrides",
    [{"thinking_level": "minimal"}, {"timeout_seconds": 0}, {"max_output_tokens": 8}, {"max_request_bytes": 10}],
)
def test_invalid_configuration_is_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(StructuredExtractionError):
        GeminiConfig(**overrides)  # type: ignore[arg-type]


async def test_construction_opens_no_client_and_sends_nothing(mock: respx.MockRouter) -> None:
    provider = GeminiStructuredExtractionProvider(KEY)
    assert provider._client is None  # klient HTTP powstaje dopiero przy pierwszym wywołaniu
    await provider.aclose()
    assert mock.calls.call_count == 0


async def test_client_is_created_lazily_reused_and_closed(mock: respx.MockRouter, sleeper: Sleeper, clock: Clock) -> None:
    mock.post(URL).respond(200, json=envelope())
    provider = make_provider(sleeper, clock)
    await provider.extract_structured(request())
    client = provider._client
    assert client is not None and client.follow_redirects is False
    await provider.extract_structured(request())
    assert provider._client is client
    await provider.aclose()
    assert provider._client is None and client.is_closed


async def test_an_injected_client_is_not_closed_by_the_adapter(mock: respx.MockRouter, sleeper: Sleeper, clock: Clock) -> None:
    mock.post(URL).respond(200, json=envelope())
    async with httpx.AsyncClient(follow_redirects=False) as client:
        provider = GeminiStructuredExtractionProvider(KEY, GeminiConfig(), client=client, sleep=sleeper, clock=clock)
        await provider.extract_structured(request())
        await provider.aclose()
        assert not client.is_closed


async def test_request_without_token_limit_uses_the_configured_default(mock: respx.MockRouter, sleeper: Sleeper, clock: Clock) -> None:
    route = mock.post(URL).respond(200, json=envelope())
    await make_provider(sleeper, clock, max_output_tokens=4096, thinking_level="medium").extract_structured(request())
    body = json.loads(route.calls.last.request.content)["generationConfig"]
    assert body["maxOutputTokens"] == 4096 and body["thinkingConfig"]["thinkingLevel"] == "medium"
    custom = StructuredExtractionRequest("s", "u", SCHEMA, max_output_tokens=1234)
    await make_provider(sleeper, clock).extract_structured(custom)
    assert json.loads(route.calls.last.request.content)["generationConfig"]["maxOutputTokens"] == 1234
