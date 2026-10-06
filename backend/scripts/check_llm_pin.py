#!/usr/bin/env python3
"""Kontrola przypięcia modelu i promptu ścieżki modelu językowego (PV3-19, ADR-012).

Zmiana ``model_id`` albo ``prompt_version`` (także treści promptu i schematu) nie może wejść bez
ponownej ewaluacji na zbiorze złotym i wpisu w dzienniku zmian ADR-012. Narzędzie wykrywa taką zmianę
i — na żądanie — sprawdza, że ponowna ewaluacja została zapisana. Działa offline, bez klucza i bez sieci.

    # CI i przegląd zmian: czy ustawienia, kod, Compose, .env.example i ADR zgadzają się z przypięciem
    python3 scripts/check_llm_pin.py check
    # przed włączeniem ścieżki na produkcji (bramka wdrożeniowa): dodatkowo wymaga zapisu ewaluacji --live
    python3 scripts/check_llm_pin.py check --require-evaluation
    # po ręcznym biegu `--live` ewaluatora hybrid: zapisuje wynik w przypięciu (model, prompt, skróty)
    python3 scripts/check_llm_pin.py record-evaluation --run-manifest ../docs/evaluation/results/<bieg>/hybrid/run_manifest.json

Kody wyjścia: 0 — zgodne; 1 — wykryto rozbieżność (lista kodów na stderr); 2 — błąd użycia lub
nieczytelne przypięcie. Procedura zmiany modelu: ``docs/operations/mpzp-llm.md``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_IMPORT_CWD = Path.cwd()
try:
    os.chdir(BACKEND_DIR)  # app.core.settings czyta względny .env przy imporcie
    from app.core.settings import Settings
    from app.modules.planning.domain import extraction_contract as contract
    from app.modules.planning.infrastructure.llm import pin as model_pin
finally:
    os.chdir(_IMPORT_CWD)

ADR_PATH = Path("docs") / "adr" / "ADR-012-mpzp-llm-extraction.md"
COMPOSE_MODEL = re.compile(r"MPZP_LLM_MODEL:\s*\$\{MPZP_LLM_MODEL:-([^}]+)\}")
ENV_MODEL = re.compile(r"^MPZP_LLM_MODEL=(\S*)\s*$", re.MULTILINE)
COMPOSE_PROMPT = re.compile(r"MPZP_LLM_PROMPT_VERSION:\s*\$\{MPZP_LLM_PROMPT_VERSION:-([^}]+)\}")
ENV_PROMPT = re.compile(r"^MPZP_LLM_PROMPT_VERSION=(\S*)\s*$", re.MULTILINE)
GOLDEN_MODEL = re.compile(r'^MODEL = "([^"]+)"', re.MULTILINE)

# Dodatkowe kody (miejsca, w których model jest zapisany obok ustawień).
COMPOSE_MODEL_DIFFERS = "compose_model_differs"
ENV_EXAMPLE_MODEL_DIFFERS = "env_example_model_differs"
COMPOSE_PROMPT_DIFFERS = "compose_prompt_version_differs"
ENV_EXAMPLE_PROMPT_DIFFERS = "env_example_prompt_version_differs"
SETTINGS_PROMPT_VERSION_DIFFERS = "settings_prompt_version_differs"
GOLDEN_REPLAY_MODEL_DIFFERS = "golden_replay_model_differs"


def default_repo_root() -> Path:
    configured = os.environ.get("REPO_ROOT")
    return Path(configured) if configured else BACKEND_DIR.parent


def _field_default(name: str) -> object:
    return Settings.model_fields[name].default


def check(repo_root: Path, *, require_evaluation: bool, pin_path: Path = model_pin.PIN_PATH) -> list[str]:
    """Lista kodów rozbieżności (pusta = zgodne)."""
    pin = model_pin.load_pin(pin_path)
    problems = list(
        model_pin.verify_pin(
            pin,
            model_id=str(_field_default("mpzp_llm_model")),
            prompt_version=contract.PROMPT_VERSION,
            prompt_sha256=contract.prompt_sha256(),
            schema_version=contract.SCHEMA_VERSION,
            schema_sha256=contract.SCHEMA_SHA256,
            thinking_level=str(_field_default("mpzp_llm_thinking_level")),
        )
    )
    if _field_default("mpzp_llm_prompt_version") != contract.PROMPT_VERSION:
        problems.append(SETTINGS_PROMPT_VERSION_DIFFERS)
    compose = repo_root / "docker-compose.yml"
    if compose.is_file():
        text = compose.read_text(encoding="utf-8")
        match = COMPOSE_MODEL.search(text)
        if match is None or match.group(1) != pin.model_id:
            problems.append(COMPOSE_MODEL_DIFFERS)
        match = COMPOSE_PROMPT.search(text)
        if match is None or match.group(1) != pin.prompt_version:
            problems.append(COMPOSE_PROMPT_DIFFERS)
    env_example = repo_root / ".env.example"
    if env_example.is_file():
        text = env_example.read_text(encoding="utf-8")
        match = ENV_MODEL.search(text)
        if match is None or match.group(1) != pin.model_id:
            problems.append(ENV_EXAMPLE_MODEL_DIFFERS)
        match = ENV_PROMPT.search(text)
        if match is None or match.group(1) != pin.prompt_version:
            problems.append(ENV_EXAMPLE_PROMPT_DIFFERS)
    # Złote odpowiedzi są kluczowane modelem: po zmianie modelu trzeba je odtworzyć (build_llm_replay_fixtures.py).
    golden_source = BACKEND_DIR / "scripts" / "build_llm_replay_fixtures.py"
    if golden_source.is_file():
        match = GOLDEN_MODEL.search(golden_source.read_text(encoding="utf-8"))
        if match is None or match.group(1) != pin.model_id:
            problems.append(GOLDEN_REPLAY_MODEL_DIFFERS)
    adr = repo_root / ADR_PATH
    problems.extend(
        model_pin.adr_log_problems(adr.read_text(encoding="utf-8"), pin) if adr.is_file() else (model_pin.ADR_LOG_ENTRY_MISSING,)
    )
    if require_evaluation:
        problems.extend(model_pin.evaluation_problems(pin, repo_root))
    return list(dict.fromkeys(problems))


def record_evaluation(
    run_manifest: Path, repo_root: Path, *, pin_path: Path = model_pin.PIN_PATH, recorded_at: str | None = None
) -> dict[str, object]:
    pin = model_pin.load_pin(pin_path)
    raw = run_manifest.read_bytes()
    manifest = json.loads(raw)
    try:
        relative = str(run_manifest.resolve().relative_to(repo_root.resolve()))
    except ValueError as exc:
        raise model_pin.PinError("run_manifest_outside_repo") from exc
    stamp = recorded_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    record = model_pin.build_evaluation_record(pin, manifest, raw, relative, stamp)
    updated = model_pin.ModelPin(
        pin.provider, pin.model_id, pin.prompt_version, pin.prompt_sha256, pin.schema_version, pin.schema_sha256,
        pin.thinking_level, pin.adr_log, record,
    )
    pin_path.write_text(model_pin.render_pin(updated), encoding="utf-8")
    return record


def main(argv: Sequence[str] | None = None, *, repo_root: Path | None = None, pin_path: Path = model_pin.PIN_PATH) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    check_parser = commands.add_parser("check", help="porównaj przypięcie z ustawieniami, kodem, Compose, .env.example i ADR")
    check_parser.add_argument("--require-evaluation", action="store_true", help="żądaj zapisu ponownej ewaluacji --live")
    record_parser = commands.add_parser("record-evaluation", help="zapisz wynik biegu --live ewaluatora hybrid w przypięciu")
    record_parser.add_argument("--run-manifest", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=None)
    args = parser.parse_args(argv)
    root = repo_root or args.repo_root or default_repo_root()
    try:
        if args.command == "check":
            problems = check(root, require_evaluation=args.require_evaluation, pin_path=pin_path)
            for code in problems:
                print(f"przypięcie: {code}", file=sys.stderr)
            pin = model_pin.load_pin(pin_path)
            state = "zapisana" if pin.evaluation_recorded else "oczekuje"
            print(
                f"przypięcie {pin.model_id} + {pin.prompt_version}: "
                f"{'zgodne' if not problems else 'ROZBIEŻNE'}; ewaluacja --live: {state}"
            )
            return 1 if problems else 0
        record = record_evaluation(args.run_manifest, root, pin_path=pin_path)
        print(f"zapisano ewaluację: {record['run_manifest']} (sha256 {str(record['run_manifest_sha256'])[:16]}…)")
        print("Uzupełnij wiersz w dzienniku zmian ADR-012 (kolumna oceny) i uruchom `check --require-evaluation`.")
        return 0
    except (model_pin.PinError, OSError, json.JSONDecodeError) as exc:
        print(f"błąd: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
