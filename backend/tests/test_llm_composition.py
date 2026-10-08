"""Wyłączona domyślnie ścieżka modelu (PV3-10): brak adaptera, brak ruchu, brak wycieku klucza w konfiguracji."""

from __future__ import annotations

import re
import socket
from pathlib import Path

import pytest
import respx
import yaml
from pydantic import ValidationError

from app.core.settings import Settings
from app.modules.planning import composition
from app.modules.planning.application.llm_extraction import LlmExtractionService
from app.modules.planning.application.ports import StructuredExtractionError, StructuredExtractionErrorCode as Code
from app.modules.planning.infrastructure.llm import gemini_provider
from app.modules.planning.infrastructure.llm.fake_provider import ReplayStructuredExtractionProvider
from app.modules.planning.infrastructure.llm.gemini_provider import GeminiStructuredExtractionProvider
from tests.parcel_fixtures_config import find_repo_root

KEY = "AIzaSyTEST-key_0123456789abcdefghij"
LLM_ENV = [
    "MPZP_LLM_ENABLED", "MPZP_LLM_PROVIDER", "MPZP_LLM_MODEL", "GEMINI_API_KEY", "MPZP_LLM_REPLAY_DIR",
    "MPZP_LLM_TIMEOUT_SECONDS", "MPZP_LLM_TEMPERATURE", "MPZP_LLM_BLOCK_CHAR_LIMIT",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in LLM_ENV:
        monkeypatch.delenv(name, raising=False)


def make_settings(**values: object) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    attempts: list[str] = []

    def refuse(*args: object, **kwargs: object) -> None:
        attempts.append("socket")
        raise AssertionError("połączenie sieciowe przy wyłączonej ścieżce modelu")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)

    def no_adapter(*args: object, **kwargs: object) -> None:
        attempts.append("adapter")
        raise AssertionError("adapter nie powinien powstać")

    monkeypatch.setattr(GeminiStructuredExtractionProvider, "__init__", no_adapter)
    monkeypatch.setattr(gemini_provider.httpx.AsyncClient, "__init__", no_adapter)
    return attempts


# --- domyślnie wyłączone ----------------------------------------------------------------------------------


def test_defaults_keep_the_model_path_off_and_deterministic() -> None:
    settings = make_settings()
    assert settings.mpzp_llm_enabled is False and settings.mpzp_llm_provider == "gemini"
    assert settings.mpzp_llm_model == "gemini-3.8-flash" and settings.gemini_api_key is None
    assert settings.mpzp_llm_temperature == 0.0 and settings.mpzp_llm_thinking_level == "low"
    assert settings.mpzp_llm_max_retries == 2 and settings.mpzp_llm_breaker_failure_threshold == 5
    assert settings.mpzp_llm_block_char_limit == 6000 and settings.mpzp_llm_max_chunks_per_block == 6
    assert settings.mpzp_llm_timeout_seconds > 0 and settings.mpzp_llm_max_output_tokens >= 256


def test_the_flag_is_off_even_when_a_key_is_present(no_network: list[str]) -> None:
    settings = make_settings(gemini_api_key=KEY)
    assert composition.build_structured_extraction_provider(settings) is None
    assert composition.build_llm_extraction_service(settings) is None
    assert no_network == []


@respx.mock(assert_all_called=False)
def test_disabled_creates_no_adapter_and_produces_no_network_traffic(respx_mock: respx.MockRouter, no_network: list[str]) -> None:
    catch_all = respx_mock.route().respond(200)
    for settings in (make_settings(), make_settings(gemini_api_key=KEY), make_settings(mpzp_llm_provider="fake")):
        assert composition.build_structured_extraction_provider(settings) is None
        assert composition.build_llm_extraction_service(settings) is None
    assert catch_all.call_count == 0 and respx_mock.calls.call_count == 0 and no_network == []


def test_the_global_settings_object_is_disabled_in_a_clean_environment() -> None:
    reloaded = Settings(_env_file=None)
    assert composition.build_structured_extraction_provider(reloaded) is None


async def test_the_adapter_alone_does_not_connect_until_it_is_called(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("sieć")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    provider = GeminiStructuredExtractionProvider(KEY)
    assert provider._client is None  # klient HTTP powstaje dopiero przy pierwszym wywołaniu
    await provider.aclose()


# --- włączone: jawna konfiguracja ------------------------------------------------------------------------------


def test_enabled_without_a_key_is_a_configuration_error_not_a_silent_disable() -> None:
    for settings in (make_settings(mpzp_llm_enabled=True), make_settings(mpzp_llm_enabled=True, gemini_api_key="   ")):
        with pytest.raises(StructuredExtractionError) as caught:
            composition.build_structured_extraction_provider(settings)
        assert caught.value.code is Code.CONFIGURATION and caught.value.detail == "api_key"


def test_enabled_with_a_key_builds_the_gemini_adapter_with_the_configuration() -> None:
    settings = make_settings(
        mpzp_llm_enabled=True, gemini_api_key=KEY, mpzp_llm_model="gemini-3.7-flash", mpzp_llm_timeout_seconds=12,
        mpzp_llm_max_output_tokens=2048, mpzp_llm_thinking_level="medium", mpzp_llm_max_retries=4,
        mpzp_llm_breaker_failure_threshold=7, mpzp_llm_breaker_cooldown_seconds=90,
    )
    provider = composition.build_structured_extraction_provider(settings)
    assert isinstance(provider, GeminiStructuredExtractionProvider)
    config = provider.config
    assert (provider.model, config.timeout_seconds, config.max_output_tokens, config.thinking_level) == ("gemini-3.7-flash", 12, 2048, "medium")
    assert config.retry.max_retries == 4 and config.breaker_failure_threshold == 7 and config.breaker_cooldown_seconds == 90
    assert KEY not in repr(provider)


def test_the_replay_provider_needs_a_directory_and_needs_no_key(tmp_path: Path) -> None:
    with pytest.raises(StructuredExtractionError) as caught:
        composition.build_structured_extraction_provider(make_settings(mpzp_llm_enabled=True, mpzp_llm_provider="fake"))
    assert caught.value.code is Code.CONFIGURATION and caught.value.detail == "replay_dir"
    provider = composition.build_structured_extraction_provider(
        make_settings(mpzp_llm_enabled=True, mpzp_llm_provider="fake", mpzp_llm_replay_dir=str(tmp_path)))
    assert isinstance(provider, ReplayStructuredExtractionProvider) and provider.model == "gemini-3.8-flash"


def test_the_service_gets_its_limits_from_the_settings(tmp_path: Path) -> None:
    service = composition.build_llm_extraction_service(make_settings(
        mpzp_llm_enabled=True, mpzp_llm_provider="fake", mpzp_llm_replay_dir=str(tmp_path),
        mpzp_llm_block_char_limit=4000, mpzp_llm_chunk_overlap_chars=200, mpzp_llm_max_chunks_per_block=3,
        mpzp_llm_max_output_tokens=1024))
    assert isinstance(service, LlmExtractionService)
    limits = service.limits
    assert (limits.block_char_limit, limits.chunk_overlap_chars, limits.max_chunks) == (4000, 200, 3)
    assert limits.temperature == 0.0 and limits.max_output_tokens == 1024


# --- ustawienia --------------------------------------------------------------------------------------------------


def test_the_settings_read_the_documented_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MPZP_LLM_ENABLED", "true")
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    monkeypatch.setenv("MPZP_LLM_MODEL", "gemini-3.7-flash")
    settings = Settings(_env_file=None)
    assert settings.mpzp_llm_enabled is True and settings.mpzp_llm_model == "gemini-3.7-flash"
    assert settings.gemini_api_key is not None and settings.gemini_api_key.get_secret_value() == KEY


def test_the_key_is_a_secret_in_every_rendering_of_the_settings() -> None:
    settings = make_settings(gemini_api_key=KEY, mpzp_llm_enabled=True)
    for rendering in (repr(settings), str(settings), repr(settings.gemini_api_key), str(settings.model_dump()), settings.model_dump_json(),
                      repr(settings.model_dump())):
        assert KEY not in rendering and "AIzaSy" not in rendering


@pytest.mark.parametrize(
    "values",
    [
        {"mpzp_llm_temperature": 0.5}, {"mpzp_llm_model": "../etc"}, {"mpzp_llm_model": "a b"}, {"mpzp_llm_provider": "inny-dostawca"},
        {"mpzp_llm_thinking_level": "minimal"}, {"mpzp_llm_timeout_seconds": 0}, {"mpzp_llm_max_output_tokens": 10},
        {"mpzp_llm_max_retries": 99}, {"mpzp_llm_block_char_limit": 10}, {"mpzp_llm_max_chunks_per_block": 0},
    ],
)
def test_invalid_llm_settings_are_rejected(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        make_settings(**values)


# --- repozytorium bez sekretów ----------------------------------------------------------------------------------------


def test_env_example_has_only_an_empty_key_placeholder_and_the_flag_off() -> None:
    lines = (find_repo_root() / ".env.example").read_text("utf-8").splitlines()
    assignments = {line.split("=", 1)[0]: line.split("=", 1)[1] for line in lines if "=" in line and not line.startswith("#")}
    assert assignments["GEMINI_API_KEY"] == ""
    assert assignments["MPZP_LLM_ENABLED"] == "false" and assignments["MPZP_LLM_PROVIDER"] == "gemini"
    assert assignments["MPZP_LLM_MODEL"] == "gemini-3.8-flash"
    assert not re.search(r"AIza[0-9A-Za-z_-]{20,}", "\n".join(lines))


def test_compose_passes_the_key_through_without_a_value() -> None:
    compose = yaml.safe_load((find_repo_root() / "docker-compose.yml").read_text("utf-8"))
    environment = compose["services"]["backend"]["environment"]
    assert environment["GEMINI_API_KEY"] == "${GEMINI_API_KEY:-}"  # przekazanie zmiennej, domyślnie pusta
    assert environment["MPZP_LLM_ENABLED"] == "${MPZP_LLM_ENABLED:-false}"
    assert environment["MPZP_LLM_MODEL"] == "${MPZP_LLM_MODEL:-gemini-3.8-flash}"
    for service, body in compose["services"].items():
        if service not in {"backend", "backend-test"}:  # backend-test dzieli środowisko z backendem (AU-010)
            assert "GEMINI_API_KEY" not in (body.get("environment") or {})  # klucz tylko dla backendu


def test_no_api_key_is_present_in_the_repository_text_files() -> None:
    root = find_repo_root()
    pattern = re.compile(r"AIza[0-9A-Za-z_-]{35}")
    skipped = {".git", "node_modules", ".next", "__pycache__", ".pytest_cache", "results", "coverage"}
    suffixes = {".py", ".md", ".yml", ".yaml", ".json", ".txt", ".example", ".toml", ".ts", ".tsx", ".sh", ".html"}
    hits: list[str] = []
    for base in ("backend", "docs", ".github", "frontend/lib", "frontend/components", "shared"):
        directory = root / base
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if any(part in skipped for part in path.parts) or not path.is_file() or path.suffix not in suffixes:
                continue
            if pattern.search(path.read_text("utf-8", errors="ignore")):
                hits.append(str(path.relative_to(root)))
    for name in (".env.example", "docker-compose.yml", "README.md"):
        if pattern.search((root / name).read_text("utf-8")):
            hits.append(name)
    assert hits == []


def test_the_provider_host_is_a_constant_not_a_setting() -> None:
    assert gemini_provider.GEMINI_BASE_URL == "https://generativelanguage.googleapis.com"
    url_like = [name for name in Settings.model_fields if ("llm" in name or "gemini" in name) and ("url" in name or "host" in name)]
    assert url_like == []  # adres dostawcy nie jest konfiguracją, więc klucz nie może trafić pod obcy host
