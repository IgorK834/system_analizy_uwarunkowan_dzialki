#!/usr/bin/env python3
"""Bramka przełączenia domyślnego trybu parsera MPZP (PV3-21, ADR-012).

Domyślny tryb (``MPZP_PARSER_MODE``) wolno zmienić z ``legacy`` na tryb z modelem WYŁĄCZNIE po
udokumentowanej decyzji ``GO`` z bramki jakości (Task 20.17), po uzgodnionym okresie obserwacji w trybie
cienia bez regresji metryk i po decyzji właściciela. Zapis tych przesłanek jest w
``app/core/mpzp_parser_rollout.json`` (śledzony w repozytorium, więc CI sprawdza go offline); narzędzie
ocenia go i wskazuje braki. ``tests/test_mpzp_parser_rollout.py`` wymaga, żeby domyślny tryb w ustawieniach
był równy zapisowi i — jeśli różni się od ``legacy`` — żeby ocena była ``READY``.

    cd backend
    python3 scripts/check_parser_default_switch.py check                 # ocena zapisu (offline)
    python3 scripts/check_parser_default_switch.py check --verify-files  # + skróty raportów w docs/evaluation
    python3 scripts/check_parser_default_switch.py record-gate --report ../docs/evaluation/results/parser-v3/gate_report.json
    python3 scripts/check_parser_default_switch.py record-shadow --report ../docs/evaluation/results/parser-v3/shadow_observation.json

Decyzję właściciela (``owner_decision``) i potwierdzenie progów okresu cienia
(``shadow_requirements.status = "confirmed"``) wpisuje człowiek — razem z wpisem z datą w ADR-012.

Kody wyjścia: 0 — ``READY`` (przełączenie dozwolone); 3 — ``NOT_READY`` (zapis spójny, przesłanki niespełnione;
domyślny tryb musi zostać ``legacy``); 1 — naruszenie (zapis niespójny z ustawieniami albo tryb przełączony bez
spełnionych przesłanek); 2 — błąd użycia lub nieczytelny zapis.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_IMPORT_CWD = Path.cwd()
try:
    os.chdir(BACKEND_DIR)  # app.core.settings czyta względny .env przy imporcie
    from app.core.settings import Settings
    from app.services.mpzp_parser_hybrid import LLM_MODES, PARSER_MODES
    from scripts import check_llm_pin
finally:
    os.chdir(_IMPORT_CWD)

ROLLOUT_PATH = BACKEND_DIR / "app" / "core" / "mpzp_parser_rollout.json"
SCHEMA = "mpzp-parser-rollout/1"
SHADOW_SCHEMA = "mpzp-shadow-observation/1"
DETERMINISTIC_MODES = frozenset(PARSER_MODES) - LLM_MODES

# Kody braków (kolejność = kolejność kroków w runbooku, §10).
GATE_NOT_GO = "gate_not_go"
GATE_REPORT_CHANGED = "gate_report_changed"
PIN_NOT_READY = "pin_not_ready"
SHADOW_REQUIREMENTS_UNCONFIRMED = "shadow_requirements_unconfirmed"
SHADOW_MISSING = "shadow_observation_missing"
SHADOW_REPORT_CHANGED = "shadow_report_changed"
SHADOW_TOO_SHORT = "shadow_too_short"
SHADOW_TOO_FEW_RUNS = "shadow_too_few_runs"
SHADOW_DISAGREEMENT = "shadow_disagreement_above_limit"
SHADOW_ERRORS = "shadow_errors_above_limit"
SHADOW_DEGRADATION = "shadow_degradation_above_limit"
SHADOW_REJECTIONS = "shadow_rejections_above_limit"
DRIFT_NOT_CHECKED = "drift_not_checked_in_window"
DRIFT_NOT_OK = "drift_not_ok"
OWNER_DECISION_MISSING = "owner_decision_missing"
OWNER_DECISION_TOO_EARLY = "owner_decision_before_shadow_end"
# Naruszenia (spójność zapisu z ustawieniami i z zasadą wycofania).
DEFAULT_MODE_DIFFERS = "default_mode_differs_from_settings"
ROLLBACK_NOT_DETERMINISTIC = "rollback_mode_not_deterministic"
UNKNOWN_MODE = "unknown_mode"
SWITCHED_WITHOUT_DECISION = "switched_without_decision"


class RolloutError(ValueError):
    """Nieczytelny albo niekompletny zapis."""


@dataclass(frozen=True)
class Assessment:
    status: str  # READY | NOT_READY
    missing: tuple[str, ...]
    violations: tuple[str, ...]
    default_mode: str

    @property
    def exit_code(self) -> int:
        if self.violations:
            return 1
        return 0 if self.status == "READY" else 3


def load_record(path: Path = ROLLOUT_PATH) -> dict[str, Any]:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RolloutError(f"nieczytelny zapis {path.name}: {type(exc).__name__}") from exc
    if not isinstance(record, dict) or record.get("schema") != SCHEMA:
        raise RolloutError(f"zapis {path.name} nie ma schematu {SCHEMA}")
    for key in ("default_mode", "target_mode", "rollback_mode", "gate", "shadow_requirements"):
        if key not in record:
            raise RolloutError(f"zapis {path.name} nie ma pola {key}")
    return record


def settings_default_mode() -> str:
    return str(Settings.model_fields["mpzp_parser_mode"].default)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _day(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).date()


def _shadow_missing(record: dict[str, Any], pin_model: str | None, pin_prompt: str | None) -> list[str]:
    requirements = record["shadow_requirements"]
    observation = record.get("shadow_observation")
    if not observation:
        return [SHADOW_MISSING]
    missing: list[str] = []
    window = observation.get("window") or {}
    shadow = observation.get("shadow") or {}
    pipeline = observation.get("pipeline") or {}
    if (window.get("days") or 0) < requirements["min_days"]:
        missing.append(SHADOW_TOO_SHORT)
    if (shadow.get("runs") or 0) < requirements["min_shadow_runs"]:
        missing.append(SHADOW_TOO_FEW_RUNS)
    # Brak odsetka (mianownik 0) nie jest „zerową rozbieżnością” — to brak pomiaru.
    for key, limit, code in (
        ("disagree_rate", requirements["max_disagree_rate"], SHADOW_DISAGREEMENT),
        ("error_rate", requirements["max_shadow_error_rate"], SHADOW_ERRORS),
    ):
        if shadow.get(key) is None or shadow[key] > limit:
            missing.append(code)
    for key, limit, code in (
        ("degradation_rate", requirements["max_degradation_rate"], SHADOW_DEGRADATION),
        ("rejection_rate", requirements["max_rejection_rate"], SHADOW_REJECTIONS),
    ):
        if pipeline.get(key) is None or pipeline[key] > limit:
            missing.append(code)
    if requirements.get("drift_check_required", True):
        drift = observation.get("drift")
        start, end = _day(window.get("start")), _day(window.get("end"))
        checked = _day(drift.get("checked_at")) if drift else None
        if drift is None or checked is None or start is None or end is None or not start <= checked <= end:
            missing.append(DRIFT_NOT_CHECKED)
        elif drift.get("status") != "ok" or (
            pin_model is not None and (drift.get("model_id"), drift.get("prompt_version")) != (pin_model, pin_prompt)
        ):
            missing.append(DRIFT_NOT_OK)
    return missing


def assess(
    record: dict[str, Any],
    *,
    settings_default: str,
    pin_problems: Sequence[str] = (),
    pin_model: str | None = None,
    pin_prompt: str | None = None,
    file_hashes: dict[str, str | None] | None = None,
) -> Assessment:
    """Ocena zapisu: braki przesłanek (``missing``) i naruszenia spójności (``violations``).

    ``file_hashes`` — skróty plików raportów obecnych na dysku (``None`` = brak pliku); bez nich ocena
    opiera się na skrótach zapisanych w rekordzie (CI nie ma ``docs/evaluation``).
    """
    violations: list[str] = []
    modes = (record["default_mode"], record["target_mode"], record["rollback_mode"])
    if any(mode not in PARSER_MODES for mode in modes):
        violations.append(UNKNOWN_MODE)
    if record["rollback_mode"] not in DETERMINISTIC_MODES:
        violations.append(ROLLBACK_NOT_DETERMINISTIC)
    if record["default_mode"] != settings_default:
        violations.append(DEFAULT_MODE_DIFFERS)

    missing: list[str] = []
    gate = record["gate"]
    if gate.get("decision") != "GO":
        missing.append(GATE_NOT_GO)
    hashes = file_hashes or {}
    if gate.get("report") in hashes and hashes[gate["report"]] != gate.get("report_sha256"):
        missing.append(GATE_REPORT_CHANGED)
    if pin_problems:
        missing.append(PIN_NOT_READY)
    if record["shadow_requirements"].get("status") != "confirmed" or not record["shadow_requirements"].get(
        "confirmed_by_owner_on"
    ):
        missing.append(SHADOW_REQUIREMENTS_UNCONFIRMED)
    missing.extend(_shadow_missing(record, pin_model, pin_prompt))
    observation = record.get("shadow_observation") or {}
    if observation.get("report") in hashes and hashes[observation["report"]] != observation.get("report_sha256"):
        missing.append(SHADOW_REPORT_CHANGED)
    owner = record.get("owner_decision")
    if not owner or owner.get("decision") != "GO" or not owner.get("date") or not owner.get("reference"):
        missing.append(OWNER_DECISION_MISSING)
    else:
        shadow_end = _day((observation.get("window") or {}).get("end"))
        if shadow_end is not None and _day(owner["date"]) < shadow_end:  # type: ignore[operator]
            missing.append(OWNER_DECISION_TOO_EARLY)

    status = "NOT_READY" if missing else "READY"
    # Każde odejście od ``legacy`` (także na ``v3``) jest przełączeniem domyślnego trybu i wymaga przesłanek.
    switched = settings_default != "legacy" or record["default_mode"] != "legacy"
    if switched and missing:
        violations.append(SWITCHED_WITHOUT_DECISION)
    return Assessment(status, tuple(dict.fromkeys(missing)), tuple(dict.fromkeys(violations)), record["default_mode"])


def _relative(path: Path, repo_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo_root.resolve()))
    except ValueError as exc:
        raise RolloutError("raport musi leżeć w repozytorium (docs/evaluation/…)") from exc


def record_gate(record: dict[str, Any], report_path: Path, repo_root: Path) -> dict[str, Any]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("decision") not in ("GO", "NO_GO", "NOT_DECIDABLE"):
        raise RolloutError("raport bramki bez decyzji GO/NO_GO/NOT_DECIDABLE")
    record["gate"] = {
        "decision": report["decision"],
        "evaluated_at": report.get("evaluated_at"),
        "report": _relative(report_path, repo_root),
        "report_sha256": sha256_file(report_path),
    }
    return record


def record_shadow(record: dict[str, Any], report_path: Path, repo_root: Path) -> dict[str, Any]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("schema") != SHADOW_SCHEMA:
        raise RolloutError(f"raport okresu cienia bez schematu {SHADOW_SCHEMA} (scripts/mpzp_shadow_report.py)")
    record["shadow_observation"] = {
        "report": _relative(report_path, repo_root),
        "report_sha256": sha256_file(report_path),
        **{key: report.get(key) for key in ("window", "shadow", "pipeline", "drift")},
    }
    return record


def write_record(record: dict[str, Any], path: Path = ROLLOUT_PATH) -> None:
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def evaluate(repo_root: Path, *, verify_files: bool, path: Path = ROLLOUT_PATH) -> Assessment:
    record = load_record(path)
    pin_problems = check_llm_pin.check(repo_root, require_evaluation=True)
    pin = check_llm_pin.model_pin.load_pin()
    hashes: dict[str, str | None] = {}
    if verify_files:
        for ref in (record["gate"].get("report"), (record.get("shadow_observation") or {}).get("report")):
            if ref:
                target = repo_root / ref
                hashes[ref] = sha256_file(target) if target.is_file() else None
    return assess(
        record,
        settings_default=settings_default_mode(),
        pin_problems=pin_problems,
        pin_model=pin.model_id,
        pin_prompt=pin.prompt_version,
        file_hashes=hashes,
    )


def main(argv: Sequence[str] | None = None, *, repo_root: Path | None = None, path: Path = ROLLOUT_PATH) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    check_parser = commands.add_parser("check", help="oceń przesłanki przełączenia domyślnego trybu")
    check_parser.add_argument("--verify-files", action="store_true", help="porównaj skróty raportów obecnych na dysku")
    for name, help_text in (("record-gate", "zapisz decyzję bramki 20.17"), ("record-shadow", "zapisz raport okresu cienia")):
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument("--report", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=None)
    args = parser.parse_args(argv)
    root = repo_root or args.repo_root or check_llm_pin.default_repo_root()
    try:
        if args.command == "check":
            result = evaluate(root, verify_files=args.verify_files, path=path)
            for code in result.missing:
                print(f"brak: {code}", file=sys.stderr)
            for code in result.violations:
                print(f"NARUSZENIE: {code}", file=sys.stderr)
            print(f"domyślny tryb {result.default_mode}: {result.status}" + (" + naruszenia" if result.violations else ""))
            return result.exit_code
        record = load_record(path)
        updated = (record_gate if args.command == "record-gate" else record_shadow)(record, args.report, root)
        write_record(updated, path)
        print(f"zapisano {args.command.removeprefix('record-')} w {path.name}; uruchom `check`")
        return 0
    except (RolloutError, OSError, json.JSONDecodeError, check_llm_pin.model_pin.PinError) as exc:
        print(f"błąd: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
