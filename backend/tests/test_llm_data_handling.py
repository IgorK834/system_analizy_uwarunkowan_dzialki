"""Dane wysyłane do modelu, sekrety i kill switch (PV3-16, ADR-014).

Bez sieci i bez prawdziwego klucza: żądania do dostawcy przechwytuje ``respx``, a klucz to atrapa.
Sprawdzane jest PRAWDZIWE żądanie HTTP wychodzące z adaptera: wyłącznie dozwolone pola, żadnego
identyfikatora działki, adresu, danych użytkownika, adresu IP ani tokenu dostępu; klucz tylko w nagłówku
dostawcy i nigdzie indziej (logi, wyjątki, zapisy, odpowiedź API).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from app.core.logging import SecretRedactionFilter, redact_secrets
from app.core.settings import Settings
from app.modules.planning import composition
from app.modules.planning.application.llm_pipeline import MpzpLlmPipeline
from app.modules.planning.domain import extraction_contract as contract
from app.modules.planning.infrastructure.llm import gemini_provider
from app.modules.planning.infrastructure.llm.budget import InMemoryUsageLedger
from app.modules.planning.infrastructure.llm.gemini_provider import GEMINI_BASE_URL
from app.services.mpzp_parser_options import build_mpzp_parser_options
from scripts import secret_scan
from tests.test_failure_injection_mpzp_llm import KEY, URL, budgeted, envelope, gemini
from tests.test_llm_repository import MemoryCache
from tests.test_mpzp_parser_modes import TEXT, parse

ALLOWED_BODY = {"systemInstruction", "contents", "generationConfig"}
ALLOWED_GENERATION = {"temperature", "maxOutputTokens", "responseMimeType", "responseJsonSchema", "thinkingConfig"}
ALLOWED_HEADERS = {"host", "accept", "accept-encoding", "connection", "user-agent", "content-length", "content-type",
                   gemini_provider.API_KEY_HEADER}


def assert_allowed_request(request: httpx.Request) -> dict[str, Any]:
    """Żądanie zawiera wyłącznie dozwolone pola (ADR-014 §2); zwraca treść do dalszych sprawdzeń."""
    assert str(request.url).startswith("https://generativelanguage.googleapis.com/")
    assert {name.lower() for name in request.headers} <= ALLOWED_HEADERS
    body = json.loads(request.content)
    assert set(body) == ALLOWED_BODY
    assert set(body["generationConfig"]) == ALLOWED_GENERATION
    assert body["systemInstruction"] == {"parts": [{"text": contract.system_instruction()}]}
    assert len(body["contents"]) == 1 and body["contents"][0]["role"] == "user"
    parts = body["contents"][0]["parts"]
    assert len(parts) == 1 and set(parts[0]) == {"text"}
    contract.validate_user_text(parts[0]["text"])  # nagłówek tylko z dozwolonych pól, tekst między znacznikami
    return body


async def test_the_request_contains_only_allowed_fields_and_the_key_only_in_its_header() -> None:
    with respx.mock(assert_all_called=True) as router:
        route = router.post(URL).mock(return_value=httpx.Response(200, json=envelope()))
        await parse("hybrid", MpzpLlmPipeline(budgeted(gemini())))
    request = route.calls.last.request
    body = assert_allowed_request(request)
    assert request.headers[gemini_provider.API_KEY_HEADER] == KEY
    assert KEY not in request.content.decode("utf-8") and KEY not in str(request.url)
    assert "Zone symbols: 1MN" in body["contents"][0]["parts"][0]["text"]


@pytest.mark.integration
def test_a_full_api_analysis_never_sends_parcel_user_address_ip_or_token_data() -> None:
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services.mpzp_parser_options import MpzpParserOptions
    from tests import test_analysis_orchestrator as orchestrator
    from tests.test_failure_injection_mpzp_llm import _kiut
    from tests.test_mpzp_parser_modes import extraction

    identifier = f"{orchestrator._PREFIX}PII_146101_1.0001.4242"
    secret_markers = [identifier, "146101_1.0001.4242", "203.0.113.77", "jan.kowalski@example.com",
                      "Bearer user-session-token-123", "ul. Testowa 7"]
    options = MpzpParserOptions(mode="hybrid", llm=MpzpLlmPipeline(budgeted(gemini())))
    orchestrator._cleanup()
    try:
        with (
            respx.mock(assert_all_called=True) as router,
            patch("app.services.analysis_orchestrator.resolve_parcel",
                  new=AsyncMock(return_value=orchestrator._lookup(identifier))),
            patch("app.services.analysis_orchestrator.analyze_context",
                  new=AsyncMock(return_value=orchestrator._empty_context())),
            patch("app.services.analysis_orchestrator.discover_mpzp",
                  new=AsyncMock(return_value=orchestrator._discovery_1mn())),
            patch("app.services.analysis_orchestrator.discover_pog",
                  new=AsyncMock(return_value=orchestrator._unknown_pog_discovery())),
            patch("app.services.analysis_orchestrator.check_kiut_coverage_for_geometry",
                  new=AsyncMock(return_value=_kiut())),
            patch("app.services.analysis_orchestrator.fetch_mpzp_document",
                  new=AsyncMock(return_value=orchestrator._document())),
            patch("app.services.mpzp_parser.extract_document_text", new=AsyncMock(return_value=extraction(TEXT))),
            patch("app.services.analysis_orchestrator.build_mpzp_parser_options", new=lambda **_kwargs: options),
        ):
            route = router.post(URL).mock(return_value=httpx.Response(200, json=envelope()))
            response = TestClient(app).post(
                "/analyze",
                json={"method": "parcel_id", "parcel_identifier": identifier},
                headers={"X-Forwarded-For": "203.0.113.77", "Authorization": "Bearer user-session-token-123",
                         "User-Agent": "jan.kowalski@example.com", "X-Address": "ul. Testowa 7"},
            )
        assert response.status_code == 200, response.text
        access_token = response.json().get("access_token")
        assert route.call_count == 1
        request = route.calls.last.request
        assert_allowed_request(request)
        sent = request.content.decode("utf-8") + json.dumps(dict(request.headers)) + str(request.url)
        for marker in [*secret_markers, *([access_token] if access_token else [])]:
            assert marker not in sent, marker
    finally:
        orchestrator._cleanup()


async def test_the_key_never_appears_in_logs_exceptions_records_or_results(caplog: pytest.LogCaptureFixture) -> None:
    for name in ("app", "app.mpzp_llm", "app.modules.planning.infrastructure.llm.gemini_provider"):
        logging.getLogger(name).disabled = False  # Alembic ``fileConfig`` bywa wyłącza w pełnym biegu
    caplog.set_level(logging.DEBUG)
    cache, ledger = MemoryCache(), InMemoryUsageLedger()
    echo = {"error": {"code": 400, "message": f"API key {KEY} invalid; request was: {TEXT}"}}
    with respx.mock(assert_all_called=True) as router:
        router.post(URL).mock(side_effect=[httpx.Response(400, json=echo), httpx.Response(200, json=envelope())])
        failed = await parse("hybrid", MpzpLlmPipeline(budgeted(gemini(), ledger), cache=cache))
        ok = await parse("hybrid", MpzpLlmPipeline(budgeted(gemini(), ledger), cache=cache))
    artefacts = [
        caplog.text,
        json.dumps(failed.model_dump(mode="json"), ensure_ascii=False),
        json.dumps(ok.model_dump(mode="json"), ensure_ascii=False),
        json.dumps([record.__dict__ for record in cache.rows.values()], default=str, ensure_ascii=False),
        json.dumps([entry.__dict__ for entry in ledger.entries], default=str),
        repr(gemini()), repr(budgeted(gemini())),
    ]
    assert all(KEY not in artefact for artefact in artefacts)
    assert "bad_request" in failed.warnings[-1].message  # błąd opisany kodem, bez echa dostawcy


def test_log_redaction_hides_keys_tokens_and_signed_urls() -> None:
    text = (f"key={KEY} x-goog-api-key: AIzaSyREAL0123456789012345 "
            "Authorization: Bearer abc.def.ghi https://x/report?token=secret-1&page=2 "
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.c2lnbmF0dXJlLXZhbHVl " + "GEMINI_API_KEY" + "=AIzaX")
    redacted = redact_secrets(text)
    for secret in (KEY, "AIzaSyREAL", "abc.def.ghi", "secret-1", "eyJhbGciOiJIUzI1NiJ9", "AIzaX"):
        assert secret not in redacted
    assert "page=2" in redacted and redacted.count("<redacted>") >= 6
    record = logging.LogRecord("t", logging.ERROR, __file__, 1, "call failed with %s", (KEY,), None)
    try:
        raise RuntimeError(f"echo {KEY}")
    except RuntimeError:
        import sys

        record.exc_info = sys.exc_info()
    assert SecretRedactionFilter().filter(record) is True
    assert KEY not in record.getMessage() and KEY not in (record.exc_text or "") and record.exc_info is None


def test_configured_logging_redacts_through_the_root_handler(capsys: pytest.CaptureFixture[str]) -> None:
    from app.core.logging import configure_logging

    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    try:
        configure_logging()
        logging.getLogger("app.test.redaction").error("upstream echoed %s", KEY)
        assert KEY not in capsys.readouterr().out
    finally:
        root.handlers[:], root.level = saved


async def test_the_provider_host_is_pinned_https_without_redirects_and_with_a_response_limit() -> None:
    assert GEMINI_BASE_URL == "https://generativelanguage.googleapis.com"
    assert not any("base_url" in name or "host" in name for name in Settings.model_fields if name.startswith("mpzp_llm"))
    with respx.mock(assert_all_called=False) as router:
        router.post(URL).mock(return_value=httpx.Response(302, headers={"Location": "https://evil.example/steal"}))
        stolen = router.route(host="evil.example").mock(return_value=httpx.Response(200))
        redirected = await parse("hybrid", MpzpLlmPipeline(budgeted(gemini())))
    assert not stolen.called and "unexpected_redirect" in redirected.warnings[-1].message
    huge = {"candidates": [{"content": {"parts": [{"text": "x" * 2_000_000}]}, "finishReason": "STOP"}]}
    with respx.mock(assert_all_called=True) as router:
        router.post(URL).mock(return_value=httpx.Response(200, json=huge))
        too_large = await parse("hybrid", MpzpLlmPipeline(budgeted(gemini())))
    assert "response_too_large" in too_large.warnings[-1].message


# --- kill switch ---------------------------------------------------------------------------------------------


async def test_the_kill_switch_file_disables_the_model_path_without_a_deploy(tmp_path: Path) -> None:
    switch = tmp_path / "llm-disabled"
    active = Settings(_env_file=None, mpzp_parser_mode="hybrid", mpzp_llm_enabled=True, mpzp_llm_provider="fake",  # type: ignore[call-arg]
                      mpzp_llm_replay_dir=str(tmp_path), mpzp_llm_kill_switch_file=str(switch))
    assert build_mpzp_parser_options(active).llm is not None and not composition.llm_kill_switch_active(active)
    switch.touch()  # operator: ``touch`` w kontenerze — bez restartu i bez wdrożenia kodu
    options = build_mpzp_parser_options(active)
    assert options.llm is None and options.llm_unavailable_reason == "kill_switch"
    result = await parse("hybrid", None, llm_unavailable_reason=options.llm_unavailable_reason)
    assert result.status == "partial" and "kill_switch" in result.warnings[-1].message
    assert result.zones == (await parse("v3")).zones
    switch.unlink()
    assert build_mpzp_parser_options(active).llm is not None
    disabled_path = Settings(_env_file=None, mpzp_llm_kill_switch_file="")  # type: ignore[call-arg]
    assert composition.llm_kill_switch_active(disabled_path) is False


# --- skanowanie sekretów (krok CI) ------------------------------------------------------------------------------


def test_the_secret_scanner_finds_planted_secrets_and_env_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "ok.py").write_text(f'KEY = "{KEY}"  # atrapa testowa krótsza niż prawdziwy klucz\n', encoding="utf-8")
    (tmp_path / ".env.example").write_text("GEMINI_API_KEY=\n", encoding="utf-8")
    assert secret_scan.main([str(tmp_path)]) == 0
    real_like = "AIza" + "B" * 35
    (tmp_path / "leak.md").write_text(f"klucz: {real_like}\n", encoding="utf-8")
    (tmp_path / ".env").write_text("A=1\n", encoding="utf-8")
    (tmp_path / "key.pem").write_text("-----BEGIN " + "PRIVATE KEY-----\n", encoding="utf-8")
    (tmp_path / "blob.bin").write_bytes(b"\x00" + real_like.encode())
    assert secret_scan.main([str(tmp_path)]) == 1
    output = capsys.readouterr().out
    assert "leak.md:1: google_api_key" in output and ".env: env_file" in output and "key.pem:1: private_key" in output
    assert real_like not in output and "blob.bin" not in output  # wartość sekretu nigdy nie jest drukowana


def test_the_repository_has_no_secrets() -> None:
    from tests.parcel_fixtures_config import find_repo_root

    assert secret_scan.scan(find_repo_root()) == []


def test_ci_runs_the_secret_scan_before_creating_the_env_file() -> None:
    from tests.parcel_fixtures_config import find_repo_root

    workflow = (find_repo_root() / ".github" / "workflows" / "ci.yml").read_text("utf-8")
    assert workflow.index("python3 backend/scripts/secret_scan.py .") < workflow.index("cp .env.example .env")
