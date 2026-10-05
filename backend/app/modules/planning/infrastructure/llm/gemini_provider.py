"""Adapter Gemini Flash (Gemini Developer API, REST ``generateContent``) za portem ekstrakcji (PV3-10).

Adapter zna tylko kontrakt „instrukcja + tekst + schemat JSON → obiekt JSON”; nie zna domeny MPZP.

Gwarancje (testowane offline, ``respx``):

* **Wyłączony domyślnie** — adaptera nie tworzy nikt, kto nie ustawił ``mpzp_llm_enabled``
  (fabryka w ``planning.composition``); sam konstruktor nie otwiera połączenia (klient HTTP
  powstaje przy pierwszym wywołaniu).
* **Stały host, HTTPS, bez przekierowań** — klucz trafia wyłącznie do ``generativelanguage.googleapis.com``
  w nagłówku ``x-goog-api-key`` (nigdy w adresie); odpowiedź 3xx kończy się błędem, bez podążania.
* **Instrukcja osobno od danych** — ``systemInstruction`` i ``contents`` to osobne pola; schemat
  odpowiedzi w ``responseJsonSchema``; temperatura i limity tokenów jawne.
* **Limity** — czas (połączenie i całość), rozmiar żądania i odpowiedzi (odpowiedź czytana
  strumieniowo, przerwana po przekroczeniu), ponowienia z wykładniczym opóźnieniem i losowaniem
  (z poszanowaniem ``Retry-After``), wyłącznik awaryjny.
* **Nic wrażliwego w logach i wyjątkach** — ani klucza, ani treści żądania, ani treści odpowiedzi
  (zawiera cytaty z dokumentu); w wyniku i w logach są: skróty SHA-256, liczba tokenów, powód
  zakończenia i identyfikator modelu zwrócony przez API.
* **Identyfikator modelu** — zwrócony ``modelVersion`` różny od skonfigurowanego jest wykrywany,
  logowany i zgłaszany w wyniku (``model_mismatch``).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

import httpx
from pydantic import SecretStr

from app.modules.planning.application.ports import (
    StructuredExtractionError,
    StructuredExtractionErrorCode,
    StructuredExtractionRequest,
    StructuredExtractionResult,
)
from app.modules.planning.infrastructure.llm.json_schema import validate_json_schema
from app.modules.planning.infrastructure.llm.resilience import (
    CircuitBreaker,
    RetryPolicy,
    parse_retry_after,
    retry_wait,
)

logger = logging.getLogger(__name__)

PROVIDER_NAME: Final[str] = "gemini"
GEMINI_HOST: Final[str] = "generativelanguage.googleapis.com"
GEMINI_BASE_URL: Final[str] = f"https://{GEMINI_HOST}"
API_VERSION: Final[str] = "v1beta"
API_KEY_HEADER: Final[str] = "x-goog-api-key"

_MODEL_PATTERN: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_KEY_PATTERN: Final[re.Pattern[str]] = re.compile(r"[\x21-\x7e]{8,512}")
_STATUS_PATTERN: Final[re.Pattern[str]] = re.compile(r"[A-Z][A-Z_]{2,40}")
_THINKING_LEVELS: Final[tuple[str, ...]] = ("low", "medium", "high")
# Powody zakończenia, które oznaczają kompletną odpowiedź; MAX_TOKENS to ucięcie, reszta to blokada.
_COMPLETE_REASONS: Final[frozenset[str]] = frozenset({"STOP"})
_RETRYABLE_STATUS: Final[frozenset[int]] = frozenset({408, 429})
# Błędy dostępności liczone przez wyłącznik awaryjny (błędy treści i żądania — nie).
_BREAKER_CODES: Final[frozenset[StructuredExtractionErrorCode]] = frozenset(
    {
        StructuredExtractionErrorCode.UNAUTHORIZED,
        StructuredExtractionErrorCode.FORBIDDEN,
        StructuredExtractionErrorCode.MODEL_NOT_FOUND,
        StructuredExtractionErrorCode.RATE_LIMITED,
        StructuredExtractionErrorCode.SERVER_ERROR,
        StructuredExtractionErrorCode.TIMEOUT,
        StructuredExtractionErrorCode.NETWORK,
    }
)

Sleeper = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class GeminiConfig:
    """Konfiguracja adaptera; wartości domyślne pochodzą z ADR-012 i pomiaru spike’u."""

    model: str = "gemini-3.8-flash"
    timeout_seconds: float = 30.0
    connect_timeout_seconds: float = 5.0
    max_output_tokens: int = 8192
    thinking_level: str = "low"
    max_request_bytes: int = 200_000
    max_response_bytes: int = 1_048_576
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    breaker_failure_threshold: int = 5
    breaker_cooldown_seconds: float = 60.0

    def __post_init__(self) -> None:
        if not _MODEL_PATTERN.fullmatch(self.model):
            raise _configuration("model")
        if self.thinking_level not in _THINKING_LEVELS:
            raise _configuration("thinking_level")
        if self.timeout_seconds <= 0 or self.connect_timeout_seconds <= 0:
            raise _configuration("timeout")
        if self.max_output_tokens < 64 or self.max_request_bytes < 1024 or self.max_response_bytes < 1024:
            raise _configuration("limits")


def _configuration(detail: str) -> StructuredExtractionError:
    return StructuredExtractionError(StructuredExtractionErrorCode.CONFIGURATION, detail=detail)


def _sha256_json(value: object) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _bare_model(identifier: str) -> str:
    return identifier.removeprefix("models/").strip()


@dataclass(frozen=True)
class _HttpOutcome:
    status: int
    headers: Mapping[str, str]
    body: bytes


class GeminiStructuredExtractionProvider:
    """Port ``StructuredExtractionProvider`` na REST Gemini; klient HTTP powstaje leniwie."""

    provider_name = PROVIDER_NAME

    def __init__(
        self,
        api_key: SecretStr | str,
        config: GeminiConfig | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        sleep: Sleeper | None = None,
        rng: random.Random | None = None,
        clock: Callable[[], float] | None = None,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        secret = api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key
        if not _KEY_PATTERN.fullmatch(secret or ""):
            raise _configuration("api_key")
        self._key = SecretStr(secret)
        self.config = config or GeminiConfig()
        self.model = self.config.model
        self._client = client
        self._owns_client = client is None
        self._sleep: Sleeper = sleep or asyncio.sleep
        self._rng = rng or random.Random()
        self._clock = clock or time.monotonic
        # Wyłącznik może być wspólny dla procesu (PV3-15): adapter powstaje per analiza, a stan awarii
        # dostawcy musi przetrwać między analizami.
        self.breaker = breaker or CircuitBreaker(
            self.config.breaker_failure_threshold, self.config.breaker_cooldown_seconds, self._clock
        )

    def __repr__(self) -> str:
        return f"GeminiStructuredExtractionProvider(model={self.model!r}, key=<redacted>)"

    @property
    def endpoint(self) -> str:
        return f"{GEMINI_BASE_URL}/{API_VERSION}/models/{self.model}:generateContent"

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
        self._client = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.config.timeout_seconds, connect=self.config.connect_timeout_seconds),
                follow_redirects=False,
                limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
            )
        return self._client

    # --- żądanie ----------------------------------------------------------------------------

    def build_body(self, request: StructuredExtractionRequest) -> dict[str, Any]:
        """Treść żądania REST: instrukcja systemowa i dane w osobnych polach, schemat wyjścia."""
        return {
            "systemInstruction": {"parts": [{"text": request.system_instruction}]},
            "contents": [{"role": "user", "parts": [{"text": request.user_text}]}],
            "generationConfig": {
                "temperature": request.temperature,
                "maxOutputTokens": request.max_output_tokens or self.config.max_output_tokens,
                "responseMimeType": "application/json",
                "responseJsonSchema": dict(request.response_schema),
                "thinkingConfig": {"thinkingLevel": self.config.thinking_level},
            },
        }

    async def _send(self, body: bytes) -> _HttpOutcome:
        headers = {API_KEY_HEADER: self._key.get_secret_value(), "content-type": "application/json"}
        limit = self.config.max_response_bytes
        async with self._http().stream("POST", self.endpoint, headers=headers, content=body) as response:
            declared = response.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > limit:
                raise StructuredExtractionError(
                    StructuredExtractionErrorCode.RESPONSE_TOO_LARGE, status=response.status_code
                )
            chunks: list[bytes] = []
            received = 0
            async for chunk in response.aiter_bytes():
                received += len(chunk)
                if received > limit:
                    raise StructuredExtractionError(
                        StructuredExtractionErrorCode.RESPONSE_TOO_LARGE, status=response.status_code
                    )
                chunks.append(chunk)
            return _HttpOutcome(response.status_code, dict(response.headers), b"".join(chunks))

    # --- klasyfikacja -------------------------------------------------------------------------

    @staticmethod
    def _provider_status(body: bytes) -> str:
        """Tylko znacznik ``error.status`` (np. INVALID_ARGUMENT); treść komunikatu może echować dane."""
        try:
            payload = json.loads(body)
            status = payload["error"]["status"]
        except (ValueError, KeyError, TypeError):
            return ""
        return status if isinstance(status, str) and _STATUS_PATTERN.fullmatch(status) else ""

    def _classify_http(self, outcome: _HttpOutcome, attempts: int) -> StructuredExtractionError:
        status = outcome.status
        detail = self._provider_status(outcome.body)
        code: StructuredExtractionErrorCode
        retryable = False
        if status == 400:
            code = StructuredExtractionErrorCode.BAD_REQUEST
        elif status == 401:
            code = StructuredExtractionErrorCode.UNAUTHORIZED
        elif status == 403:
            code = StructuredExtractionErrorCode.FORBIDDEN
        elif status == 404:
            code = StructuredExtractionErrorCode.MODEL_NOT_FOUND
        elif status == 413:
            code = StructuredExtractionErrorCode.REQUEST_TOO_LARGE
        elif status == 429:
            code, retryable = StructuredExtractionErrorCode.RATE_LIMITED, True
        elif status == 408:
            code, retryable = StructuredExtractionErrorCode.TIMEOUT, True
        elif status >= 500:
            code, retryable = StructuredExtractionErrorCode.SERVER_ERROR, True
        elif 300 <= status < 400:
            code = StructuredExtractionErrorCode.UNEXPECTED_REDIRECT
        else:
            code = StructuredExtractionErrorCode.BAD_RESPONSE
        retry_after = parse_retry_after(outcome.headers.get("retry-after")) if retryable else None
        return StructuredExtractionError(
            code,
            detail=detail,
            retryable=retryable,
            status=status,
            attempts=attempts,
            retry_after=retry_after,
        )

    # --- wywołanie -----------------------------------------------------------------------------

    async def extract_structured(self, request: StructuredExtractionRequest) -> StructuredExtractionResult:
        started = time.perf_counter()
        body = json.dumps(self.build_body(request), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(body) > self.config.max_request_bytes:
            raise StructuredExtractionError(StructuredExtractionErrorCode.REQUEST_TOO_LARGE, detail="request")
        if not self.breaker.allow():
            logger.warning("llm_call provider=%s model=%s outcome=circuit_open", PROVIDER_NAME, self.model)
            raise StructuredExtractionError(StructuredExtractionErrorCode.CIRCUIT_OPEN, attempts=0)

        attempts = 0
        policy = self.config.retry
        while True:
            attempts += 1
            error: StructuredExtractionError
            try:
                outcome = await self._send(body)
            except StructuredExtractionError as exc:
                error = StructuredExtractionError(
                    exc.code, detail=exc.detail, retryable=False, status=exc.status, attempts=attempts
                )
            except httpx.TimeoutException:
                error = StructuredExtractionError(
                    StructuredExtractionErrorCode.TIMEOUT, retryable=True, attempts=attempts
                )
            except httpx.TransportError:
                error = StructuredExtractionError(
                    StructuredExtractionErrorCode.NETWORK, detail="transport", retryable=True, attempts=attempts
                )
            except httpx.HTTPError:
                error = StructuredExtractionError(
                    StructuredExtractionErrorCode.NETWORK, detail="http", retryable=False, attempts=attempts
                )
            else:
                if outcome.status == 200:
                    return self._finish(request, outcome, attempts, started)
                error = self._classify_http(outcome, attempts)

            wait = retry_wait(attempts - 1, policy, self._rng, error.retry_after) if error.retryable else None
            if error.retryable and attempts <= policy.max_retries and wait is not None:
                logger.warning(
                    "llm_call provider=%s model=%s outcome=retry code=%s status=%s attempt=%d wait=%.2f",
                    PROVIDER_NAME, self.model, error.code.value, error.status, attempts, wait,
                )
                await self._sleep(wait)
                continue
            self._record_failure(error)
            raise error

    def _record_failure(self, error: StructuredExtractionError) -> None:
        if error.code in _BREAKER_CODES:
            self.breaker.record_failure()
        else:
            self.breaker.release_probe()
        logger.error(
            "llm_call provider=%s model=%s outcome=error code=%s status=%s attempts=%d breaker=%s",
            PROVIDER_NAME, self.model, error.code.value, error.status, error.attempts, self.breaker.state,
        )

    # --- odpowiedź -------------------------------------------------------------------------------

    def _finish(
        self, request: StructuredExtractionRequest, outcome: _HttpOutcome, attempts: int, started: float
    ) -> StructuredExtractionResult:
        # Odpowiedź 200 oznacza, że usługa działa — niezależnie od jakości treści.
        self.breaker.record_success()
        try:
            envelope = json.loads(outcome.body)
        except ValueError:
            raise self._content_error(StructuredExtractionErrorCode.INVALID_JSON, attempts, detail="envelope") from None
        if not isinstance(envelope, dict):
            raise self._content_error(StructuredExtractionErrorCode.BAD_RESPONSE, attempts, detail="envelope")

        returned_raw = envelope.get("modelVersion")
        returned = _bare_model(returned_raw) if isinstance(returned_raw, str) and returned_raw.strip() else None
        mismatch = returned is not None and returned != _bare_model(self.model)
        if mismatch:
            logger.warning(
                "llm_call provider=%s model=%s outcome=model_mismatch returned=%s",
                PROVIDER_NAME, self.model, returned if returned and _MODEL_PATTERN.fullmatch(returned) else "<invalid>",
            )

        candidates = envelope.get("candidates")
        if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
            raise self._content_error(StructuredExtractionErrorCode.BLOCKED, attempts, detail="no_candidates")
        first = candidates[0]
        reason_raw = first.get("finishReason")
        reason = reason_raw if isinstance(reason_raw, str) and _STATUS_PATTERN.fullmatch(reason_raw) else "UNKNOWN"
        parts = (first.get("content") or {}).get("parts") or []
        text = "".join(
            part["text"] for part in parts if isinstance(part, dict) and isinstance(part.get("text"), str) and not part.get("thought")
        )
        if reason == "MAX_TOKENS":
            raise self._content_error(StructuredExtractionErrorCode.TRUNCATED, attempts, detail=reason)
        if reason not in _COMPLETE_REASONS:
            raise self._content_error(StructuredExtractionErrorCode.BLOCKED, attempts, detail=reason)
        try:
            content = json.loads(text)
        except ValueError:
            raise self._content_error(StructuredExtractionErrorCode.INVALID_JSON, attempts, detail=reason) from None
        violations = validate_json_schema(content, request.response_schema)
        if violations:
            raise self._content_error(
                StructuredExtractionErrorCode.SCHEMA_VIOLATION,
                attempts,
                violations=tuple(str(item) for item in violations),
            )

        usage = envelope.get("usageMetadata") if isinstance(envelope.get("usageMetadata"), dict) else {}
        result = StructuredExtractionResult(
            content=content,
            provider=PROVIDER_NAME,
            model_requested=self.model,
            model_returned=returned,
            model_mismatch=mismatch,
            response_sha256=_sha256_json(content),
            request_sha256=request.request_sha256,
            input_sha256=request.input_sha256,
            finish_reason=reason,
            input_tokens=_count(usage.get("promptTokenCount")),
            output_tokens=_count(usage.get("candidatesTokenCount")),
            thinking_tokens=_count(usage.get("thoughtsTokenCount")),
            latency_ms=round((time.perf_counter() - started) * 1000.0, 1),
            attempts=attempts,
        )
        logger.info(
            "llm_call provider=%s model=%s outcome=ok returned=%s attempts=%d input_tokens=%s output_tokens=%s "
            "finish=%s request_sha256=%s response_sha256=%s",
            PROVIDER_NAME, self.model, returned if returned and _MODEL_PATTERN.fullmatch(returned) else None,
            attempts, result.input_tokens, result.output_tokens, reason, result.request_sha256[:16],
            result.response_sha256[:16],
        )
        return result

    def _content_error(
        self,
        code: StructuredExtractionErrorCode,
        attempts: int,
        *,
        detail: str = "",
        violations: tuple[str, ...] = (),
    ) -> StructuredExtractionError:
        error = StructuredExtractionError(code, detail=detail, attempts=attempts, status=200, violations=violations)
        logger.error(
            "llm_call provider=%s model=%s outcome=error code=%s attempts=%d detail=%s violations=%d",
            PROVIDER_NAME, self.model, code.value, attempts, error.detail, len(error.violations),
        )
        return error


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
