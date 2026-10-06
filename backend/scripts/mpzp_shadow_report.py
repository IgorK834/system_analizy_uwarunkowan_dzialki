#!/usr/bin/env python3
"""Raport okresu obserwacji w trybie cienia (PV3-21): wejście do decyzji o przełączeniu domyślnego trybu.

W trybie ``hybrid_shadow`` odpowiedź API jest wynikiem ``v3``, a model liczony w tle zapisuje porównanie
do logu ``app.mpzp_llm`` (``event=shadow_compare``: zgodne / rozbieżne / tylko model / tylko rdzeń) i przebieg
potoku (``event=pipeline``: przyjęte, odrzucone, degradacja). Liczniki procesu zerują się przy restarcie, więc
okres obserwacji liczy się z LOGÓW (np. ``docker compose logs --no-color backend > shadow.log`` codziennie albo
z zbieracza logów). Raport zawiera wyłącznie liczby i skróty plików wejściowych — log nie zawiera treści
żądań, kluczy ani identyfikatorów działki (``llm_metrics.log_event`` przepuszcza tylko liczby i krótkie kody).

    cd backend
    python3 scripts/mpzp_shadow_report.py --log shadow-2026-10.log [--log …] \
        --drift-state /var/lib/dzialki/llm-drift.json --output ../docs/evaluation/results/parser-v3/shadow_observation.json

Kody wyjścia: 0 — raport zapisany; 2 — brak zdarzeń trybu cienia w logach albo błąd wejścia.
Ocenę raportu względem uzgodnionych progów robi ``scripts/check_parser_default_switch.py``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.modules.planning.application.llm_monitoring import parse_drift_state  # noqa: E402

SCHEMA = "mpzp-shadow-observation/1"
# Format wiersza z ``app.core.logging``: ``%(asctime)s %(levelname)s %(name)s %(message)s``; przedrostek
# ``docker compose logs`` (``backend-1  | ``) i znacznik czasu ``-t`` są dopuszczone przed nim.
_EVENT = re.compile(
    r"(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d{3} \w+ app\.mpzp_llm mpzp_llm event=(?P<event>\w+)(?P<fields>(?: \S+=\S*)*)\s*$"
)
_SHADOW_FAILED = re.compile(r"(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d{3} \w+ \S+ mpzp_llm shadow failed: ")
_FIELD = re.compile(r"(\w+)=(\S*)")


@dataclass
class ShadowTotals:
    runs: int = 0
    errors: int = 0
    agree: int = 0
    disagree: int = 0
    llm_only: int = 0
    det_only: int = 0
    pipeline_runs: int = 0
    degraded: int = 0
    accepted: int = 0
    rejected: int = 0
    unavailable: dict[str, int] = field(default_factory=dict)
    first: datetime | None = None
    last: datetime | None = None

    def seen(self, stamp: datetime) -> None:
        self.first = stamp if self.first is None or stamp < self.first else self.first
        self.last = stamp if self.last is None or stamp > self.last else self.last


def _int(fields: dict[str, str], key: str) -> int:
    try:
        return int(fields.get(key, "0"))
    except ValueError:
        return 0


def _stamp(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def aggregate(lines: Iterable[str]) -> ShadowTotals:
    """Sumuje zdarzenia trybu cienia; wiersze spoza formatu są pomijane (log zawiera też inne zdarzenia)."""
    totals = ShadowTotals()
    for line in lines:
        failed = _SHADOW_FAILED.search(line)
        if failed:
            totals.errors += 1
            totals.seen(_stamp(failed.group("ts")))
            continue
        match = _EVENT.search(line)
        if match is None:
            continue
        fields = dict(_FIELD.findall(match.group("fields")))
        event = match.group("event")
        if event == "shadow_compare":
            totals.runs += 1
            for key in ("agree", "disagree", "llm_only", "det_only"):
                setattr(totals, key, getattr(totals, key) + _int(fields, key))
        elif event == "pipeline":
            totals.pipeline_runs += 1
            totals.accepted += _int(fields, "accepted")
            totals.rejected += _int(fields, "rejected")
            reasons = [r for r in fields.get("unavailable", "none").split(",") if r and r != "none"]
            if reasons:
                totals.degraded += 1
                for reason in reasons:
                    totals.unavailable[reason] = totals.unavailable.get(reason, 0) + 1
        else:
            continue
        totals.seen(_stamp(match.group("ts")))
    return totals


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def build_report(
    totals: ShadowTotals,
    sources: Sequence[tuple[str, str]],
    drift_state_text: str | None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Raport okresu cienia: okno czasu, porównania, przebiegi potoku i ostatnia kontrola dryfu."""
    days = None
    if totals.first is not None and totals.last is not None:
        days = round((totals.last - totals.first).total_seconds() / 86400, 3)
    compared = totals.agree + totals.disagree
    drift = parse_drift_state(drift_state_text) if drift_state_text else None
    return {
        "schema": SCHEMA,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "window": {
            "start": totals.first.isoformat().replace("+00:00", "Z") if totals.first else None,
            "end": totals.last.isoformat().replace("+00:00", "Z") if totals.last else None,
            "days": days,
        },
        "shadow": {
            "runs": totals.runs,
            "errors": totals.errors,
            "agree": totals.agree,
            "disagree": totals.disagree,
            "llm_only": totals.llm_only,
            "det_only": totals.det_only,
            # Rozbieżność wśród par, w których oba silniki dały wartość (para tylko modelu to luka rdzenia).
            "disagree_rate": _rate(totals.disagree, compared),
            "error_rate": _rate(totals.errors, totals.runs + totals.errors),
        },
        "pipeline": {
            "runs": totals.pipeline_runs,
            "degraded": totals.degraded,
            "degradation_rate": _rate(totals.degraded, totals.pipeline_runs),
            "accepted": totals.accepted,
            "rejected": totals.rejected,
            "rejection_rate": _rate(totals.rejected, totals.accepted + totals.rejected),
            "unavailable": dict(sorted(totals.unavailable.items())),
        },
        "drift": None if drift is None else {
            "status": drift.status,
            "checked_at": drift.checked_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "drift_rate": drift.drift_rate,
            "threshold": drift.threshold,
            "model_id": drift.model_id,
            "prompt_version": drift.prompt_version,
        },
        "sources": [{"file": name, "sha256": digest} for name, digest in sources],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--log", action="append", required=True, type=Path, help="plik logu backendu (można podać wiele)")
    parser.add_argument("--drift-state", type=Path, default=None, help="stan kontroli dryfu (MPZP_LLM_DRIFT_STATE_FILE)")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    lines: list[str] = []
    sources: list[tuple[str, str]] = []
    for path in args.log:
        try:
            raw = path.read_bytes()
        except OSError as exc:
            print(f"nie można odczytać {path}: {type(exc).__name__}", file=sys.stderr)
            return 2
        sources.append((path.name, hashlib.sha256(raw).hexdigest()))
        lines.extend(raw.decode("utf-8", errors="replace").splitlines())
    totals = aggregate(lines)
    if totals.runs == 0 and totals.errors == 0:
        print("brak zdarzeń trybu cienia (event=shadow_compare) w logach", file=sys.stderr)
        return 2
    drift_text = None
    if args.drift_state is not None and args.drift_state.is_file():
        drift_text = args.drift_state.read_text(encoding="utf-8")
    report = build_report(totals, sources, drift_text)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    shadow = report["shadow"]
    print(
        f"okno {report['window']['days']} d, porównań {shadow['runs']} (błędów {shadow['errors']}), "
        f"rozbieżność {shadow['disagree_rate']}, degradacja {report['pipeline']['degradation_rate']} → {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
