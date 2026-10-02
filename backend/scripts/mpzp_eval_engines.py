"""Engine contract, registry and LLM replay store of the MPZP evaluator (PV3-03).

The module has no dependency on the application package, so the evaluator can
compare engines through one neutral result shape (``EngineResult``) and a
contributed engine (v3, hybrid) only has to register a factory.

Two rules shape the LLM part:

* **Replay is the default.** Model responses are stored under a cache key that
  covers everything that can change an answer (provider, model, prompt and schema
  versions, temperature, SHA-256 of the text sent). A replay never touches the
  network; a missing key is an error, never a silent fallback to a live call.
* **Live is opt-in and never runs in CI.** ``--live`` needs the explicit flag, a
  key in the environment and a registered provider; the key is read from the
  environment, never stored, logged or written into the replay files.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

LLM_API_KEY_ENV = "GEMINI_API_KEY"
CI_ENV_MARKERS = ("CI", "GITHUB_ACTIONS")
REPLAY_SCHEMA_VERSION = "1.0.0"
# An automatic reading is a candidate for manual review; only a human may verify it.
FORBIDDEN_REVIEW_STATUSES = ("verified",)


class EngineUnavailableError(RuntimeError):
    """The requested engine is unknown or its implementation does not exist yet."""


class LiveModeError(RuntimeError):
    """A live model call is not allowed in this environment or configuration."""


class ReplayMissError(LookupError):
    """No stored response for a cache key in replay mode."""


class ReplayIntegrityError(ValueError):
    """A stored response does not match its recorded SHA-256."""


# --- neutral result shape ---------------------------------------------------------


@dataclass(frozen=True)
class SourceSpan:
    """Character range of a returned value in the raw text of one source page.

    Offsets index the page string exactly as stored in ``pages.json``; ``page`` is
    the source page number used by the annotations (not a list index).
    """

    page: int
    start: int
    end: int


@dataclass(frozen=True)
class EngineValue:
    """One value returned for a (zone, catalog parameter)."""

    parameter: str
    value: float | str | None
    confidence: float
    manual_review_required: bool
    page: int | None = None
    source_text: str | None = None
    raw_value: str | None = None
    conflict_group_id: str | None = None
    span: SourceSpan | None = None
    review_status: str | None = None


@dataclass(frozen=True)
class Rejection:
    """A candidate that a deterministic gate refused (hybrid engines)."""

    zone_symbol: str
    gate: str
    parameter: str | None = None
    value: float | None = None
    reason: str | None = None


@dataclass
class EngineUsage:
    """Observed cost of one engine call. ``None`` means "not reported", never 0."""

    calls: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: float | None = None
    cost_usd: float | None = None

    def merged(self, other: EngineUsage) -> EngineUsage:
        def add(left: float | int | None, right: float | int | None) -> float | int | None:
            if left is None and right is None:
                return None
            return (left or 0) + (right or 0)

        return EngineUsage(
            calls=self.calls + other.calls,
            input_tokens=add(self.input_tokens, other.input_tokens),  # type: ignore[arg-type]
            output_tokens=add(self.output_tokens, other.output_tokens),  # type: ignore[arg-type]
            latency_ms=add(self.latency_ms, other.latency_ms),  # type: ignore[arg-type]
            cost_usd=add(self.cost_usd, other.cost_usd),  # type: ignore[arg-type]
        )


@dataclass
class EngineResult:
    """What an engine returned for one document and a set of zone symbols."""

    zones: dict[str, list[EngineValue]]
    status: str = "complete"
    warning_codes: tuple[str, ...] = ()
    rejections: list[Rejection] = field(default_factory=list)
    usage: EngineUsage = field(default_factory=EngineUsage)


class Engine(Protocol):
    name: str
    version: str
    supports_discovery: bool

    def run(self, loaded: Mapping[str, Any], symbols: Sequence[str]) -> EngineResult:
        """Extracts parameters for ``symbols`` (``[]`` = discover the zones)."""


# --- canonical hashing ------------------------------------------------------------


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- LLM requests, responses and the replay store ----------------------------------


@dataclass(frozen=True)
class LlmRequest:
    """Identity of one model call; ``payload`` is sent but never part of the key.

    ``input_sha256`` must be the SHA-256 of the exact public planning text sent. The
    request carries no parcel or user identifier by construction.
    """

    provider: str
    model: str
    prompt_version: str
    schema_version: str
    input_sha256: str
    temperature: float = 0.0
    prompt_sha256: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)

    @property
    def cache_key(self) -> str:
        return sha256_text(
            canonical_json(
                {
                    "provider": self.provider,
                    "model": self.model,
                    "prompt_version": self.prompt_version,
                    "prompt_sha256": self.prompt_sha256,
                    "schema_version": self.schema_version,
                    "temperature": self.temperature,
                    "input_sha256": self.input_sha256,
                }
            )
        )

    def identity(self) -> dict[str, Any]:
        """The key fields, safe to store (no payload text)."""
        return {
            "provider": self.provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "prompt_sha256": self.prompt_sha256,
            "schema_version": self.schema_version,
            "temperature": self.temperature,
            "input_sha256": self.input_sha256,
        }


@dataclass(frozen=True)
class LlmResponse:
    content: Any
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: float | None = None
    cost_usd: float | None = None
    model_returned: str | None = None
    finish_reason: str | None = None

    @property
    def response_sha256(self) -> str:
        return sha256_text(canonical_json(self.content))


class LiveProvider(Protocol):
    def complete(self, request: LlmRequest) -> LlmResponse:
        """One real model call. Implemented by the production adapter (Task 20.10)."""


class ReplayStore:
    """Directory of ``<cache_key>.json`` files with an integrity hash per response."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def path_for(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str) -> LlmResponse | None:
        path = self.path_for(key)
        if not path.is_file():
            return None
        record = json.loads(path.read_text(encoding="utf-8"))
        response = record["response"]
        result = LlmResponse(
            content=response["content"],
            input_tokens=response.get("input_tokens"),
            output_tokens=response.get("output_tokens"),
            latency_ms=response.get("latency_ms"),
            cost_usd=response.get("cost_usd"),
            model_returned=response.get("model_returned"),
            finish_reason=response.get("finish_reason"),
        )
        if record.get("cache_key") != key or record.get("response_sha256") != result.response_sha256:
            raise ReplayIntegrityError(f"Stored response {path.name} does not match its recorded hash.")
        return result

    def put(self, request: LlmRequest, response: LlmResponse, recorded_at: str) -> Path:
        path = self.path_for(request.cache_key)
        if path.exists():
            raise FileExistsError(f"Refusing to overwrite recorded response {path.name}.")
        self.directory.mkdir(parents=True, exist_ok=True)
        record = {
            "schema_version": REPLAY_SCHEMA_VERSION,
            "cache_key": request.cache_key,
            "request": request.identity(),
            "recorded_at": recorded_at,
            "response_sha256": response.response_sha256,
            "response": {
                "content": response.content,
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "latency_ms": response.latency_ms,
                "cost_usd": response.cost_usd,
                "model_returned": response.model_returned,
                "finish_reason": response.finish_reason,
            },
        }
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path

    def digest(self) -> str:
        """SHA-256 over every (cache key, response hash); binds a run to the store content."""
        entries = []
        for key in self.keys():
            record = json.loads(self.path_for(key).read_text(encoding="utf-8"))
            entries.append([key, record["response_sha256"]])
        return sha256_text(canonical_json(entries))

    def keys(self) -> list[str]:
        if not self.directory.is_dir():
            return []
        return sorted(path.stem for path in self.directory.glob("*.json"))


LlmMode = Literal["replay", "live"]


class LlmGateway:
    """Single entry point for model calls made by an evaluated engine."""

    def __init__(
        self,
        mode: LlmMode,
        store: ReplayStore,
        provider: LiveProvider | None = None,
        clock: Callable[[], str] | None = None,
    ) -> None:
        if mode == "live" and provider is None:
            raise LiveModeError("live mode needs a registered provider")
        self.mode: LlmMode = mode
        self.store = store
        self.provider = provider
        self._clock = clock or _utc_now
        self.replayed = 0
        self.recorded = 0

    def complete(self, request: LlmRequest) -> LlmResponse:
        key = request.cache_key
        stored = self.store.get(key)
        if stored is not None:
            self.replayed += 1
            return stored
        if self.mode == "replay":
            raise ReplayMissError(
                f"No recorded response for cache key {key[:16]}… "
                f"(model {request.model}, prompt {request.prompt_version}, input {request.input_sha256[:12]}…)."
            )
        assert self.provider is not None  # guaranteed by the constructor
        response = self.provider.complete(request)
        self.store.put(request, response, self._clock())
        self.recorded += 1
        return response


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# The production adapter (Task 20.10) registers itself here; until then ``--live``
# fails with a clear message instead of guessing a provider.
_LIVE_PROVIDER_FACTORY: Callable[[str], LiveProvider] | None = None


def register_live_provider(factory: Callable[[str], LiveProvider] | None) -> None:
    """Registers (or clears) the factory that builds a live provider from the API key."""
    global _LIVE_PROVIDER_FACTORY
    _LIVE_PROVIDER_FACTORY = factory


def build_gateway(
    *,
    replay_dir: Path | None,
    live: bool,
    environ: Mapping[str, str] | None = None,
) -> LlmGateway:
    """Builds the gateway for a run; every refusal is explicit."""
    env = os.environ if environ is None else environ
    if replay_dir is None:
        raise LiveModeError("pass --llm-replay DIR (offline replay) or --live with --llm-replay DIR")
    store = ReplayStore(replay_dir)
    if not live:
        return LlmGateway("replay", store)
    if any(env.get(marker) for marker in CI_ENV_MARKERS):
        raise LiveModeError("--live is disabled in CI")
    api_key = env.get(LLM_API_KEY_ENV)
    if not api_key:
        raise LiveModeError(f"--live needs the API key in the {LLM_API_KEY_ENV} environment variable")
    if _LIVE_PROVIDER_FACTORY is None:
        raise LiveModeError("no live provider is registered (the production adapter is Task 20.10)")
    return LlmGateway("live", store, provider=_LIVE_PROVIDER_FACTORY(api_key))


# --- registry ---------------------------------------------------------------------


@dataclass(frozen=True)
class EngineContext:
    """What a factory may use; ``gateway`` is set only for engines that use a model."""

    gateway: LlmGateway | None = None


@dataclass(frozen=True)
class EngineSpec:
    name: str
    description: str
    factory: Callable[[EngineContext], Engine] | None = None
    uses_llm: bool = False
    unavailable_reason: str | None = None


_REGISTRY: dict[str, EngineSpec] = {}


def register_engine(spec: EngineSpec, *, replace: bool = False) -> None:
    if spec.name in _REGISTRY and not replace:
        raise ValueError(f"engine {spec.name!r} is already registered")
    _REGISTRY[spec.name] = spec


def unregister_engine(name: str) -> None:
    _REGISTRY.pop(name, None)


def engine_names() -> list[str]:
    return list(_REGISTRY)


def get_engine_spec(name: str) -> EngineSpec:
    spec = _REGISTRY.get(name)
    if spec is None:
        raise EngineUnavailableError(f"unknown engine {name!r}; registered: {', '.join(_REGISTRY) or 'none'}")
    return spec


def create_engine(name: str, context: EngineContext | None = None) -> Engine:
    spec = get_engine_spec(name)
    if spec.factory is None:
        raise EngineUnavailableError(f"engine {name!r} is not available: {spec.unavailable_reason or 'no implementation'}")
    return spec.factory(context or EngineContext())
