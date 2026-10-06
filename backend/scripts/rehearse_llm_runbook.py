#!/usr/bin/env python3
"""Próba runbooka ścieżki modelu językowego (PV3-20, PV3-21): włączenie, wyłączenie, rotacja klucza, limity,
przypięcie oraz przełączenie domyślnego trybu parsera i jego wycofanie.

Przeprowadza kroki z ``docs/operations/mpzp-llm.md`` na PRAWDZIWYCH ustawieniach (``Settings`` czytane z
zmiennych środowiskowych, tak jak przy restarcie kontenera), prawdziwym korzeniu kompozycji i prawdziwym
adapterze Gemini — ale dostawca jest zastąpiony zamrożonymi odpowiedziami ``respx`` (żadnego ruchu
sieciowego, żadnego prawdziwego klucza; „klucze” są losowymi tekstami z prefiksem ``AIza``). Dostawca przyjmuje
tylko klucz „nowy” (odpowiada 401 na „stary”), więc rotacja jest widoczna w wyniku. Blok to złoty przypadek
korpusu BK-603 (``scripts/build_llm_replay_fixtures.py``). Wycofanie (sekcja F) porównuje wynik parsera po
powrocie do ``legacy`` z migawką zamrożoną dla dokumentów regresji (``tests/fixtures/mpzp_parser_modes``).

Czego próba NIE obejmuje: unieważnienia klucza w konsoli dostawcy, rzeczywistego ruchu do dostawcy i zachowania
orkiestratora na PostGIS (to pokrywają testy ``test_failure_injection_mpzp_llm`` i ``test_llm_monitoring``).
Próbę kontenerową (``/health`` przez HTTP, ``touch`` kill switcha, restart z nową zmienną) opisano w runbooku.

    cd backend
    python3 scripts/rehearse_llm_runbook.py            # tabela kroków; kod 0 = wszystkie kroki zgodne z oczekiwaniem
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_IMPORT_CWD = Path.cwd()
try:
    os.chdir(BACKEND_DIR)  # app.core.settings czyta względny .env przy imporcie
    import httpx
    import respx

    from app.core.settings import Settings
    from app.modules.planning import composition
    from app.modules.planning.application import llm_metrics
    from app.modules.planning.application.ports import StructuredExtractionRequest
    from app.modules.planning.domain import extraction_contract as contract
    from app.modules.planning.infrastructure.llm.budget import InMemoryUsageLedger
    from app.modules.planning.infrastructure.llm.fake_provider import ReplayStructuredExtractionProvider
    from app.modules.planning.infrastructure.llm.gemini_provider import GEMINI_BASE_URL
    from app.services import cache as analysis_cache
    from app.services.mpzp_parser_options import build_mpzp_parser_options
    from scripts import build_llm_replay_fixtures as golden
    from scripts import check_parser_default_switch as default_switch
    from scripts import freeze_mpzp_parser_modes as frozen_modes
    from scripts import check_llm_drift as drift
    from scripts import check_llm_pin as pin_check
finally:
    os.chdir(_IMPORT_CWD)

MODEL = "gemini-3.8-flash"
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
ENV_NAMES = (
    "MPZP_PARSER_MODE", "MPZP_LLM_ENABLED", "MPZP_LLM_PROVIDER", "MPZP_LLM_MODEL", "GEMINI_API_KEY",
    "MPZP_LLM_KILL_SWITCH_FILE", "MPZP_LLM_DRIFT_STATE_FILE", "MPZP_LLM_DAILY_COST_LIMIT_USD",
    "MPZP_LLM_HEALTH_LEDGER_TTL_SECONDS", "MPZP_LLM_REPLAY_DIR", "MPZP_LLM_CACHE_ENABLED",
)


@dataclass
class Step:
    section: str
    action: str
    expected: str
    observed: str = ""
    passed: bool = False


class Rehearsal:
    def __init__(self, workdir: Path) -> None:
        self.workdir = workdir
        self.steps: list[Step] = []
        self.log_lines: list[str] = []
        self.old_key = "AIza" + secrets.token_urlsafe(30)[:35]
        self.new_key = "AIza" + secrets.token_urlsafe(30)[:35]
        self.requests: list[str] = []
        self.kill_file = workdir / "llm-disabled"
        self.handler = _Capture(self.log_lines)
        self.handler.setLevel(logging.DEBUG)
        self.root_level = logging.getLogger().level
        logging.getLogger().addHandler(self.handler)
        logging.getLogger().setLevel(logging.DEBUG)
        self.case = drift.canary_cases(["L1"])[0]
        self.golden = ReplayStructuredExtractionProvider(golden.DEFAULT_OUTPUT_DIR, model=golden.MODEL)

    # --- środowisko jak przy restarcie kontenera ---------------------------------------------------------

    def settings(self, **env: str) -> Settings:
        """Nowy ``Settings`` ze zmiennych środowiskowych — tak, jak widzi je świeżo uruchomiony proces."""
        saved = {name: os.environ.pop(name, None) for name in ENV_NAMES}
        os.environ.update({
            "MPZP_LLM_KILL_SWITCH_FILE": str(self.kill_file),
            "MPZP_LLM_DRIFT_STATE_FILE": str(self.workdir / "llm-drift.json"),
            "MPZP_LLM_HEALTH_LEDGER_TTL_SECONDS": "0",
            "MPZP_LLM_CACHE_ENABLED": "false",  # próba nie używa bazy: cache i rejestr zużycia są w pamięci
            **env,
        })
        try:
            return Settings(_env_file=None)
        finally:
            for name in ENV_NAMES:
                os.environ.pop(name, None)
                if saved[name] is not None:
                    os.environ[name] = saved[name]  # type: ignore[assignment]

    def health(self, settings: Settings, cost: float = 0.0) -> Any:
        class Ledger:
            def totals(self, now: datetime) -> Any:
                value = type("T", (), {"cost_usd": cost, "tokens": 0})()
                return value, value

        return composition.llm_health(settings, now=NOW, ledger_factory=Ledger)

    # --- dostawca zastąpiony respx -------------------------------------------------------------------

    def provider_route(self, router: respx.MockRouter, accepted_key: str) -> respx.Route:
        """Dostawca przyjmuje tylko ``accepted_key`` (nagłówek ``x-goog-api-key``); inaczej 401."""
        import json

        async def answer(request: httpx.Request) -> httpx.Response:
            self.requests.append(request.headers.get("x-goog-api-key", ""))
            if request.headers.get("x-goog-api-key") != accepted_key:
                return httpx.Response(401, json={"error": {"status": "UNAUTHENTICATED"}})
            content = await self._golden_content(request)
            return httpx.Response(200, json={
                "candidates": [{"content": {"role": "model", "parts": [{"text": json.dumps(content)}]}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 1200, "candidatesTokenCount": 300, "thoughtsTokenCount": 0},
                "modelVersion": MODEL,
            })

        return router.post(f"{GEMINI_BASE_URL}/v1beta/models/{MODEL}:generateContent").mock(side_effect=answer)

    async def _golden_content(self, request: httpx.Request) -> dict[str, Any]:
        import json

        body = json.loads(request.content)
        user = body["contents"][0]["parts"][0]["text"]
        planned = StructuredExtractionRequest(
            system_instruction=body["systemInstruction"]["parts"][0]["text"], user_text=user,
            response_schema=body["generationConfig"]["responseJsonSchema"], temperature=0.0,
            max_output_tokens=body["generationConfig"]["maxOutputTokens"], prompt_version=contract.PROMPT_VERSION,
            prompt_sha256=contract.prompt_sha256(), schema_version=contract.SCHEMA_VERSION,
        )
        return dict((await self.golden.extract_structured(planned)).content)

    async def run_block(self, settings: Settings) -> tuple[str, int]:
        """Jedno przejście TYM SAMYM potokiem co analiza: ``build_mpzp_parser_options`` → potok → bramki."""
        options = build_mpzp_parser_options(settings)
        if settings.mpzp_parser_mode not in ("hybrid_shadow", "hybrid"):
            return "mode_without_model", 0
        if options.llm is None:
            return options.llm_unavailable_reason or "unavailable", 0
        pipeline = options.llm
        try:
            outcome = await pipeline.run([self.case.block], document_sha256=self.case.document_sha256, document=self.case.document)
        finally:
            await pipeline.aclose()
        return ("ok" if outcome.available else ",".join(outcome.unavailable_reasons)), len(outcome.report.accepted)

    # --- kroki ----------------------------------------------------------------------------------------

    def step(self, section: str, action: str, expected: str, check: Callable[[], tuple[str, bool]]) -> None:
        item = Step(section, action, expected)
        try:
            item.observed, item.passed = check()
        except Exception as exc:  # noqa: BLE001 - próba raportuje każdy błąd kroku
            item.observed, item.passed = f"wyjątek {type(exc).__name__}", False
        self.steps.append(item)

    def sync(self, coroutine: Any) -> Any:
        return asyncio.run(coroutine)

    def close(self) -> None:
        logging.getLogger().removeHandler(self.handler)
        logging.getLogger().setLevel(self.root_level)

    def logs_clean(self) -> bool:
        text = "\n".join(self.log_lines)
        return self.old_key not in text and self.new_key not in text and "AIza" not in text.replace("AIza<redacted>", "")


class _Capture(logging.Handler):
    def __init__(self, sink: list[str]) -> None:
        super().__init__()
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        self.sink.append(f"{record.name} {record.getMessage()}")


def rehearse(workdir: Path) -> list[Step]:
    r = Rehearsal(workdir)
    try:
        return _run_steps(r)
    finally:
        r.close()


def _run_steps(r: Rehearsal) -> list[Step]:
    shadow = {"MPZP_PARSER_MODE": "hybrid_shadow", "MPZP_LLM_ENABLED": "true"}

    # A. Włączenie -----------------------------------------------------------------------------------------
    def a1() -> tuple[str, bool]:
        state = r.health(r.settings())
        return f"{state.status}, żądań do dostawcy: {len(r.requests)}", state.status == "disabled" and not r.requests

    def a2() -> tuple[str, bool]:
        state = r.health(r.settings(**shadow))
        return f"{state.status}: {', '.join(state.reasons)} ({state.details.get('config_error')})", state.reasons == ("config_error",)

    def a3() -> tuple[str, bool]:
        with respx.mock(assert_all_called=False) as router:
            r.provider_route(router, r.old_key)
            settings = r.settings(**shadow, GEMINI_API_KEY=r.old_key)
            before = len(r.requests)
            result, values = r.sync(r.run_block(settings))
            state = r.health(settings)
        return (
            f"{state.status} {list(state.warnings)}; pierwszy przebieg: {result}, wartości: {values}, żądań: {len(r.requests) - before}",
            state.status == "ok" and result == "ok" and values > 0,
        )

    r.step("A. Włączenie", "domyślna konfiguracja (tryb legacy, flaga wyłączona)", "komponent `disabled`, brak żądań", a1)
    r.step("A. Włączenie", "`MPZP_PARSER_MODE=hybrid_shadow`, `MPZP_LLM_ENABLED=true`, brak klucza", "`degraded`: `config_error` (api_key)", a2)
    r.step("A. Włączenie", "dodanie klucza, restart (nowe `Settings`), pierwszy przebieg", "`ok`; wartości po bramkach; ostrzeżenia o braku ewaluacji i dryfu", a3)

    # B. Wyłączanie ----------------------------------------------------------------------------------------
    def b1() -> tuple[str, bool]:
        settings = r.settings(**shadow, GEMINI_API_KEY=r.old_key)
        r.kill_file.touch()
        with respx.mock(assert_all_called=False) as router:
            route = r.provider_route(router, r.old_key)
            state = r.health(settings)
            result, _ = r.sync(r.run_block(settings))
            options = build_mpzp_parser_options(r.settings(MPZP_PARSER_MODE="hybrid", MPZP_LLM_ENABLED="true", GEMINI_API_KEY=r.old_key))
        return (
            f"{state.status}: {', '.join(state.reasons)}; przebieg: {result}; opcje parsera: {options.llm_unavailable_reason}; "
            f"żądań: {route.call_count}",
            state.reasons == ("kill_switch",) and result == "kill_switch" and route.call_count == 0
            and options.llm_unavailable_reason == "kill_switch",
        )

    def b2() -> tuple[str, bool]:
        r.kill_file.unlink()
        settings = r.settings(**shadow, GEMINI_API_KEY=r.old_key)
        with respx.mock(assert_all_called=False) as router:
            route = r.provider_route(router, r.old_key)
            state = r.health(settings)
            result, values = r.sync(r.run_block(settings))
        return f"{state.status}; przebieg: {result} ({values}); żądań: {route.call_count}", state.status == "ok" and result == "ok" and route.call_count >= 1

    def b3() -> tuple[str, bool]:
        settings = r.settings(MPZP_PARSER_MODE="hybrid", MPZP_LLM_ENABLED="false", GEMINI_API_KEY=r.old_key)
        with respx.mock(assert_all_called=False) as router:
            route = r.provider_route(router, r.old_key)
            state = r.health(settings)
            result, _ = r.sync(r.run_block(settings))
        return f"{state.status}: {', '.join(state.reasons)}; przebieg: {result}; żądań: {route.call_count}", state.reasons == ("llm_disabled",) and route.call_count == 0

    def b4() -> tuple[str, bool]:
        state = r.health(r.settings(MPZP_PARSER_MODE="legacy", MPZP_LLM_ENABLED="true", GEMINI_API_KEY=r.old_key))
        return f"{state.status}", state.status == "disabled"

    r.step("B. Wyłączanie", "kill switch: `touch` pliku (bez restartu)", "`degraded`: `kill_switch`; brak żądań; powód `kill_switch` w opcjach parsera", b1)
    r.step("B. Wyłączanie", "kill switch: `rm` pliku", "`ok`; żądania wracają", b2)
    r.step("B. Wyłączanie", "`MPZP_LLM_ENABLED=false` w trybie hybrid (restart)", "`degraded`: `llm_disabled`; brak żądań", b3)
    r.step("B. Wyłączanie", "powrót do `MPZP_PARSER_MODE=legacy`", "komponent `disabled`", b4)

    # C. Rotacja klucza ------------------------------------------------------------------------------------
    def c1() -> tuple[str, bool]:
        # Dostawca rozpoznaje już tylko klucz nowy; aplikacja nadal ma stary.
        with respx.mock(assert_all_called=False) as router:
            route = r.provider_route(router, r.new_key)
            settings = r.settings(**shadow, GEMINI_API_KEY=r.old_key)
            result, _ = r.sync(r.run_block(settings))
            state = r.health(settings)
        return f"przebieg: {result}; zdrowie: {state.status} {', '.join(state.reasons)}; żądań: {route.call_count}", "unauthorized" in result and state.reasons == ("model_unavailable",)

    def c2() -> tuple[str, bool]:
        composition.shared_circuit_breaker.cache_clear()  # restart procesu zeruje wyłącznik
        with respx.mock(assert_all_called=False) as router:
            route = r.provider_route(router, r.new_key)
            settings = r.settings(**shadow, GEMINI_API_KEY=r.new_key)
            result, values = r.sync(r.run_block(settings))
            state = r.health(settings)
            keys = sorted({key[:4] + "…" for key in r.requests[-route.call_count:]}) if route.call_count else []
        return f"przebieg: {result} ({values}); zdrowie: {state.status}; żądań: {route.call_count}, klucz w nagłówku: {keys}", result == "ok" and state.status == "ok"

    def c3() -> tuple[str, bool]:
        composition.shared_circuit_breaker.cache_clear()
        with respx.mock(assert_all_called=False) as router:
            r.provider_route(router, r.new_key)
            result, _ = r.sync(r.run_block(r.settings(**shadow, GEMINI_API_KEY=r.old_key)))
        return f"stary klucz po unieważnieniu: {result}", "unauthorized" in result

    def c4() -> tuple[str, bool]:
        return f"wierszy logu: {len(r.log_lines)}; klucz stary/nowy w logach: {not r.logs_clean()}", r.logs_clean() and len(r.log_lines) > 0

    r.step("C. Rotacja klucza", "dostawca zna tylko nowy klucz, aplikacja ma stary", "przebieg `unauthorized`, `degraded`: `model_unavailable`", c1)
    r.step("C. Rotacja klucza", "nowy klucz w środowisku, restart, kontrolny przebieg", "`ok`; w nagłówku nowy klucz", c2)
    r.step("C. Rotacja klucza", "stary klucz po unieważnieniu", "odrzucony przez dostawcę (`unauthorized`)", c3)
    r.step("C. Rotacja klucza", "przegląd logów całej próby", "żaden klucz (stary ani nowy) nie występuje w logach", c4)

    # D. Limity --------------------------------------------------------------------------------------------
    def d1() -> tuple[str, bool]:
        composition.shared_circuit_breaker.cache_clear()
        settings = r.settings(**shadow, GEMINI_API_KEY=r.new_key, MPZP_LLM_DAILY_COST_LIMIT_USD="5")
        with respx.mock(assert_all_called=False) as router:  # udany przebieg z nowym kluczem kończy stan „model niedostępny”
            r.provider_route(router, r.new_key)
            result, _ = r.sync(r.run_block(settings))
        recovered = r.health(settings)
        state = r.health(settings, cost=4.2)
        hard = r.health(settings, cost=5.0)
        return (
            f"po udanym przebiegu ({result}): {recovered.status}; 4,2 USD: {', '.join(state.reasons)}; "
            f"5,0 USD: {', '.join(hard.reasons)}",
            result == "ok" and recovered.status == "ok" and state.reasons == ("daily_cost_alarm",)
            and hard.reasons == ("daily_limit_reached",),
        )

    r.step("D. Limity i alarmy", "koszt doby 4,2 USD (próg alarmu 4) i 5,0 USD (limit twardy)", "`daily_cost_alarm`, potem `daily_limit_reached`", d1)

    # E. Przypięcie ----------------------------------------------------------------------------------------
    def e1() -> tuple[str, bool]:
        settings = r.settings(**shadow, GEMINI_API_KEY=r.new_key, MPZP_LLM_MODEL="gemini-9.9-flash")
        state = r.health(settings)
        with respx.mock(assert_all_called=False) as router:
            route = r.provider_route(router, r.new_key)
            result, _ = r.sync(r.run_block(settings))
        return f"{state.status}: {', '.join(state.reasons)}; przebieg: {result}; żądań: {route.call_count}", state.reasons == ("pin_mismatch",) and result == "pin_mismatch" and route.call_count == 0

    def e2() -> tuple[str, bool]:
        problems = pin_check.check(Path(os.environ.get("REPO_ROOT", BACKEND_DIR.parent)), require_evaluation=True)
        return f"`check_llm_pin.py check --require-evaluation`: {problems}", problems == ["evaluation_not_recorded"]

    r.step("E. Zmiana modelu", "`MPZP_LLM_MODEL` zmieniony bez ponownej ewaluacji", "`degraded`: `pin_mismatch`; ścieżka wstrzymana, brak żądań", e1)
    r.step("E. Zmiana modelu", "bramka wdrożeniowa przy obecnym przypięciu", "jedyna rozbieżność: brak zapisu ewaluacji `--live`", e2)

    # F. Przełączenie domyślnego trybu i wycofanie (PV3-21) ------------------------------------------------
    switched = {"MPZP_PARSER_MODE": "hybrid", "MPZP_LLM_ENABLED": "true", "GEMINI_API_KEY": r.new_key}
    signatures: dict[str, dict[str, object]] = {}

    def f1() -> tuple[str, bool]:
        composition.shared_circuit_breaker.cache_clear()
        settings = r.settings(**switched)
        with respx.mock(assert_all_called=False) as router:
            route = r.provider_route(router, r.new_key)
            result, values = r.sync(r.run_block(settings))
        signatures["hybrid"] = analysis_cache.mpzp_parser_signature(settings)
        return (
            f"przebieg: {result} ({values}); żądań: {route.call_count}; sygnatura: {signatures['hybrid']['mode']}, "
            f"{signatures['hybrid']['model_id']}",
            result == "ok" and values > 0 and signatures["hybrid"]["mode"] == "hybrid",
        )

    def f2() -> tuple[str, bool]:
        settings = r.settings(MPZP_PARSER_MODE="legacy", MPZP_LLM_ENABLED="true", GEMINI_API_KEY=r.new_key)
        options = build_mpzp_parser_options(settings)
        same: list[str] = []
        model_values = 0
        with respx.mock(assert_all_called=False) as router:
            route = r.provider_route(router, r.new_key)
            for fixture_dir in frozen_modes.FIXTURE_DIRS:
                parsed = r.sync(frozen_modes.parse_fixture(fixture_dir, "legacy", **options.kwargs()))
                model_values += sum(
                    1 for zone in parsed["parse_result"]["zones"] for p in zone["parameters"]
                    if p.get("review_status") == "ai_candidate" or p.get("extraction_method") == "llm_verified"
                )
                if parsed == frozen_modes_snapshot()[f"{fixture_dir.name}:legacy"]:
                    same.append(fixture_dir.name)
        signatures["legacy"] = analysis_cache.mpzp_parser_signature(settings)
        total = len(frozen_modes.FIXTURE_DIRS)
        return (
            f"adapter: {options.llm is not None}; dokumenty identyczne z migawką legacy: {len(same)}/{total}; "
            f"wartości modelu: {model_values}; żądań: {route.call_count}; sygnatura cache inna niż hybrid: "
            f"{signatures['legacy'] != signatures.get('hybrid')}",
            options.llm is None and len(same) == total and model_values == 0 and route.call_count == 0
            and signatures["legacy"] != signatures.get("hybrid"),
        )

    def f3() -> tuple[str, bool]:
        state = r.health(r.settings(MPZP_PARSER_MODE="legacy", MPZP_LLM_ENABLED="true", GEMINI_API_KEY=r.new_key))
        record = default_switch.load_record()
        result = default_switch.assess(
            record, settings_default=default_switch.settings_default_mode(),
            pin_problems=pin_check.check(Path(os.environ.get("REPO_ROOT", BACKEND_DIR.parent)), require_evaluation=True),
        )
        return (
            f"zdrowie: {state.status}; tryb wycofania w zapisie: {record['rollback_mode']}; bramka przełączenia: "
            f"{result.status} (kod {result.exit_code})",
            state.status == "disabled" and record["rollback_mode"] == "legacy" and result.exit_code in (0, 3),
        )

    r.step("F. Przełączenie i wycofanie", "`MPZP_PARSER_MODE=hybrid` jako domyślny (restart), przebieg kontrolny",
           "`ok`; kandydaci modelu po bramkach; sygnatura cache trybu `hybrid`", f1)
    r.step("F. Przełączenie i wycofanie", "wycofanie: `MPZP_PARSER_MODE=legacy` (restart), 10 dokumentów regresji",
           "brak adaptera i żądań; wynik identyczny z migawką `legacy`; 0 wartości modelu; inna sygnatura cache", f2)
    r.step("F. Przełączenie i wycofanie", "stan po wycofaniu i bramka przełączenia",
           "komponent `disabled`; zapis wskazuje `legacy` jako tryb wycofania; bramka spójna (bez naruszeń)", f3)
    return r.steps


def frozen_modes_snapshot() -> dict[str, Any]:
    import json

    return json.loads(frozen_modes.OUTPUT.read_text(encoding="utf-8"))


def render(steps: Sequence[Step]) -> str:
    lines = ["| Krok | Działanie | Oczekiwanie | Zaobserwowano | Wynik |", "|---|---|---|---|---|"]
    for index, item in enumerate(steps, start=1):
        lines.append(
            f"| {index}. {item.section} | {item.action} | {item.expected} | {item.observed} | {'zgodne' if item.passed else '**NIEZGODNE**'} |"
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    llm_metrics.metrics.reset()
    # Rejestr zużycia w pamięci zamiast bazy: próba jest offline i niczego nie zapisuje poza katalogiem tymczasowym.
    composition.SqlAlchemyUsageLedger = lambda _session_factory: InMemoryUsageLedger()  # type: ignore[assignment,misc]
    with tempfile.TemporaryDirectory() as tmp:
        steps = rehearse(Path(tmp))
    print(render(steps))
    failed = [item for item in steps if not item.passed]
    print(f"\nKroków: {len(steps)}; zgodnych: {len(steps) - len(failed)}; niezgodnych: {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
