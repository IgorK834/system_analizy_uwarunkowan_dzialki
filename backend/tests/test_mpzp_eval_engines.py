"""Engine registry, LLM cache key, replay store and live gating of the MPZP evaluator (PV3-03)."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

import pytest

from scripts import mpzp_eval_engines as engines
from scripts.mpzp_eval_engines import (
    EngineContext,
    EngineSpec,
    EngineUnavailableError,
    EngineUsage,
    LiveModeError,
    LlmGateway,
    LlmRequest,
    LlmResponse,
    ReplayIntegrityError,
    ReplayMissError,
    ReplayStore,
)


def _request(**changes: Any) -> LlmRequest:
    base = {
        "provider": "gemini", "model": "model-x", "prompt_version": "p1", "schema_version": "s1",
        "input_sha256": "a" * 64, "temperature": 0.0, "prompt_sha256": "b" * 64,
        "payload": {"text": "dosłowny tekst bloku"},
    }
    return LlmRequest(**{**base, **changes})


def _response(**changes: Any) -> LlmResponse:
    base = {"content": {"candidates": [{"parameter": "max_building_height_m", "raw_value": "10 m"}]},
            "input_tokens": 120, "output_tokens": 30, "latency_ms": 840.5, "cost_usd": 0.0004,
            "model_returned": "model-x", "finish_reason": "STOP"}
    return LlmResponse(**{**base, **changes})


# --- cache key -----------------------------------------------------------------------------


def test_cache_key_covers_every_field_that_can_change_an_answer() -> None:
    key = _request().cache_key
    assert len(key) == 64 and key == _request().cache_key
    for change in (
        {"provider": "other"}, {"model": "model-y"}, {"prompt_version": "p2"}, {"schema_version": "s2"},
        {"input_sha256": "c" * 64}, {"temperature": 0.7}, {"prompt_sha256": "d" * 64},
    ):
        assert _request(**change).cache_key != key, change
    # the payload is what is sent; only its hash (input_sha256) identifies it
    assert _request(payload={"text": "inny"}).cache_key == key


def test_request_identity_never_carries_the_payload_text() -> None:
    identity = _request().identity()
    assert "payload" not in identity and "dosłowny" not in json.dumps(identity, ensure_ascii=False)
    assert set(identity) == {"provider", "model", "prompt_version", "prompt_sha256", "schema_version",
                             "temperature", "input_sha256"}


# --- replay store ------------------------------------------------------------------------------


def test_store_round_trip_keeps_usage_and_never_overwrites(tmp_path: Path) -> None:
    store = ReplayStore(tmp_path / "replay")
    request, response = _request(), _response()
    assert store.get(request.cache_key) is None and store.keys() == []
    path = store.put(request, response, "2026-10-01T10:00:00Z")
    assert path.name == f"{request.cache_key}.json"
    loaded = store.get(request.cache_key)
    assert loaded == response and loaded.response_sha256 == response.response_sha256
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["request"] == request.identity() and record["recorded_at"] == "2026-10-01T10:00:00Z"
    assert "dosłowny" not in path.read_text(encoding="utf-8")  # no document text in the recording
    with pytest.raises(FileExistsError):
        store.put(request, _response(content={"x": 1}), "2026-10-01T11:00:00Z")
    assert store.keys() == [request.cache_key]


def test_store_detects_a_tampered_or_misplaced_recording(tmp_path: Path) -> None:
    store = ReplayStore(tmp_path)
    request = _request()
    path = store.put(request, _response(), "2026-10-01T10:00:00Z")
    record = json.loads(path.read_text(encoding="utf-8"))
    record["response"]["content"]["candidates"][0]["raw_value"] = "99 m"
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ReplayIntegrityError):
        store.get(request.cache_key)
    other = tmp_path / ("0" * 64 + ".json")
    other.write_text(json.dumps({**record, "cache_key": request.cache_key}), encoding="utf-8")
    with pytest.raises(ReplayIntegrityError):
        store.get("0" * 64)


def test_store_digest_binds_a_run_to_the_recorded_content(tmp_path: Path) -> None:
    store = ReplayStore(tmp_path)
    empty = store.digest()
    store.put(_request(), _response(), "t")
    one = store.digest()
    store.put(_request(input_sha256="e" * 64), _response(content={"candidates": []}), "t")
    assert len({empty, one, store.digest()}) == 3
    assert ReplayStore(tmp_path).digest() == store.digest()  # same content, same digest


# --- gateway -------------------------------------------------------------------------------------


class _Provider:
    def __init__(self) -> None:
        self.calls: list[LlmRequest] = []

    def complete(self, request: LlmRequest) -> LlmResponse:
        self.calls.append(request)
        return _response()


def test_replay_returns_recordings_offline_and_a_miss_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ReplayStore(tmp_path)
    store.put(_request(), _response(), "t")

    def blocked(*_a: object, **_k: object) -> None:
        raise AssertionError("replay must not touch the network")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    gateway = LlmGateway("replay", store)
    assert gateway.complete(_request()) == _response() and gateway.replayed == 1
    with pytest.raises(ReplayMissError) as miss:
        gateway.complete(_request(input_sha256="f" * 64))
    assert "dosłowny" not in str(miss.value)  # the message names the key, not the text
    assert gateway.recorded == 0


def test_live_gateway_records_once_then_replays(tmp_path: Path) -> None:
    provider = _Provider()
    gateway = LlmGateway("live", ReplayStore(tmp_path), provider=provider, clock=lambda: "2026-10-01T12:00:00Z")
    first = gateway.complete(_request())
    second = gateway.complete(_request())
    assert first == second and len(provider.calls) == 1
    assert (gateway.recorded, gateway.replayed) == (1, 1)
    with pytest.raises(LiveModeError):
        LlmGateway("live", ReplayStore(tmp_path))


def test_build_gateway_refuses_every_unsafe_live_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = engines.LLM_API_KEY_ENV
    with pytest.raises(LiveModeError, match="--llm-replay"):
        engines.build_gateway(replay_dir=None, live=False)
    replay = engines.build_gateway(replay_dir=tmp_path, live=False, environ={})
    assert replay.mode == "replay"
    for marker in engines.CI_ENV_MARKERS:
        with pytest.raises(LiveModeError, match="CI"):
            engines.build_gateway(replay_dir=tmp_path, live=True, environ={marker: "true", key: "k"})
    with pytest.raises(LiveModeError, match=key):
        engines.build_gateway(replay_dir=tmp_path, live=True, environ={})
    monkeypatch.setattr(engines, "_LIVE_PROVIDER_FACTORY", None)
    with pytest.raises(LiveModeError, match="no live provider"):
        engines.build_gateway(replay_dir=tmp_path, live=True, environ={key: "secret-key-value"})
    received: list[str] = []
    provider = _Provider()
    engines.register_live_provider(lambda api_key: (received.append(api_key), provider)[1])
    try:
        live = engines.build_gateway(replay_dir=tmp_path, live=True, environ={key: "secret-key-value"})
        assert live.mode == "live" and received == ["secret-key-value"]
        live.complete(_request())
        # the key reaches the provider only; it is never written next to the recordings
        written = "".join(path.read_text(encoding="utf-8") for path in tmp_path.glob("*.json"))
        assert "secret-key-value" not in written
    finally:
        engines.register_live_provider(None)


# --- registry ----------------------------------------------------------------------------------


def test_registry_registers_replaces_and_creates_engines() -> None:
    class Engine:
        name, version, supports_discovery = "tmp_engine", "t/1", False

        def run(self, loaded: Any, symbols: Any) -> Any:
            raise NotImplementedError

    spec = EngineSpec("tmp_engine", "test", factory=lambda ctx: Engine())
    engines.register_engine(spec)
    try:
        assert "tmp_engine" in engines.engine_names()
        with pytest.raises(ValueError, match="already registered"):
            engines.register_engine(spec)
        engines.register_engine(spec, replace=True)
        assert engines.create_engine("tmp_engine").version == "t/1"
        assert engines.create_engine("tmp_engine", EngineContext()).name == "tmp_engine"
    finally:
        engines.unregister_engine("tmp_engine")
    assert "tmp_engine" not in engines.engine_names()
    with pytest.raises(EngineUnavailableError, match="unknown engine"):
        engines.get_engine_spec("tmp_engine")


def test_planned_engines_are_registered_but_unavailable_until_implemented() -> None:
    from scripts import evaluate_mpzp_parser  # noqa: F401  (registers legacy, v3, hybrid)

    assert {"legacy", "v3", "hybrid"} <= set(engines.engine_names())
    assert engines.get_engine_spec("hybrid").uses_llm and not engines.get_engine_spec("legacy").uses_llm
    assert engines.create_engine("legacy").name == "legacy"
    assert engines.create_engine("v3").name == "v3"  # bloki stref i źródło wartości (PV3-04–06)
    # Hybryda (PV3-14) jest zaimplementowana, ale bez bramy odpowiedzi (odtwarzanie/--live) nie powstaje.
    with pytest.raises(EngineUnavailableError, match="needs --llm-replay"):
        engines.create_engine("hybrid")


def test_usage_sums_keep_not_reported_distinct_from_zero() -> None:
    nothing = EngineUsage().merged(EngineUsage())
    assert (nothing.input_tokens, nothing.cost_usd, nothing.latency_ms) == (None, None, None) and nothing.calls == 0
    partial = EngineUsage(calls=1, input_tokens=10, cost_usd=0.5).merged(EngineUsage(calls=2, output_tokens=4))
    assert partial.calls == 3 and partial.input_tokens == 10 and partial.output_tokens == 4
    assert partial.cost_usd == 0.5 and partial.latency_ms is None
    zero = EngineUsage(calls=1, input_tokens=0).merged(EngineUsage(calls=1, input_tokens=0))
    assert zero.input_tokens == 0  # a reported zero stays a zero
