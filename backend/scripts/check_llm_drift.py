#!/usr/bin/env python3
"""Cykliczna kontrola dryfu ścieżki modelu językowego (PV3-19, ADR-012).

Przepuszcza bloki kanarkowe (7 przypadków złotych, 13 bloków stref z korpusu BK-603) przez ten sam potok
(kontrakt → bramki G1–G8) z **zamrożonym odtworzeniem** i z **dostawcą na żywo** i porównuje przyjęte
wartości. Rozbieżność powyżej progu (domyślnie 0,20) to alarm: dostawca mógł zmienić zachowanie modelu
pod tym samym identyfikatorem. Wynik zapisuje stan (``MPZP_LLM_DRIFT_STATE_FILE``), który czyta
``/health`` (komponent ``llm``: powód ``drift_alarm``).

Narzędzie jest **ręczne albo z harmonogramu, poza CI i poza pytest**: wysyła do dostawcy publiczne teksty
aktów planistycznych (nigdy identyfikatorów działki ani użytkownika), więc wymaga jawnego
``--confirm-public-text``, klucza ``GEMINI_API_KEY`` w środowisku, wyłączonego kill switcha, i liczy
żądania do dziennych/miesięcznych limitów (rejestr ``mpzp_llm_usage``). Koszt jednej kontroli: najwyżej 13
żądań (rząd 0,2 USD wg ceny od 2027, pomiar spike: ok. 0,014 USD na żądanie).

    cd backend
    python3 scripts/check_llm_drift.py --confirm-public-text            # pełna kontrola, zapis stanu
    python3 scripts/check_llm_drift.py --confirm-public-text --cases L1 L3 --max-requests 4

Kody wyjścia: 0 — brak dryfu; 3 — alarm dryfu; 4 — nierozstrzygnięte (awaria dostawcy, brak porównania);
2 — błąd użycia lub konfiguracji (brak zgody, klucza, kill switch, CI). Szczegóły: ``docs/operations/mpzp-llm.md``.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_IMPORT_CWD = Path.cwd()
try:
    os.chdir(BACKEND_DIR)  # app.core.settings czyta względny .env przy imporcie
    from app.core.settings import Settings, settings as app_settings
    from app.modules.planning import composition
    from app.modules.planning.application import llm_metrics
    from app.modules.planning.application.llm_drift import DriftCase, DriftReport, run_drift_check
    from app.modules.planning.application.llm_extraction import ExtractionLimits
    from app.modules.planning.application.llm_monitoring import DriftState
    from app.modules.planning.application.ports import StructuredExtractionError, StructuredExtractionProvider
    from app.modules.planning.domain.candidate_verifier import DocumentContext
    from app.modules.planning.infrastructure.llm.fake_provider import ReplayStructuredExtractionProvider
    from scripts import build_llm_replay_fixtures as golden
finally:
    os.chdir(_IMPORT_CWD)

EXIT_OK, EXIT_USAGE, EXIT_ALARM, EXIT_INCONCLUSIVE = 0, 2, 3, 4
KEY_ENV = "GEMINI_API_KEY"


class DriftUsageError(ValueError):
    """Kontrola nie może ani nie powinna się uruchomić (zgoda, klucz, kill switch, CI)."""


def canary_cases(case_ids: Sequence[str] | None = None) -> list[DriftCase]:
    """Bloki kanarkowe z przypadków złotych; ``case_ids`` zawęża zestaw (np. ``L1``, ``N1``)."""
    wanted = {item.upper() for item in case_ids} if case_ids else None
    cases: list[DriftCase] = []
    for case in golden.CASES:
        if wanted is not None and case.case_id not in wanted:
            continue
        for block in golden.case_blocks(case):
            digest = hashlib.sha256(f"{case.sample_id}:{block.block_id}".encode()).hexdigest()
            cases.append(
                DriftCase(
                    case_id=case.case_id,
                    block=block,
                    document_sha256=digest,
                    document=DocumentContext(zone_symbols=tuple(case.symbols)),
                )
            )
    if wanted is not None:
        unknown = wanted - {case.case_id for case in golden.CASES}
        if unknown:
            raise DriftUsageError(f"nieznane przypadki: {', '.join(sorted(unknown))}")
    return cases


def build_live_provider(active: Settings) -> StructuredExtractionProvider:
    """Produkcyjny adapter z limitami między analizami (rejestr zużycia); ``DriftUsageError`` przy braku warunków."""
    if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
        raise DriftUsageError("kontrola dryfu nie działa w CI (wysyła żądania do dostawcy)")
    live = active.model_copy(update={"mpzp_llm_enabled": True, "mpzp_llm_provider": "gemini"})
    if composition.llm_kill_switch_active(live):
        raise DriftUsageError("kill switch jest aktywny: ścieżka modelu jest wyłączona")
    try:
        provider = composition.build_structured_extraction_provider(live)
    except StructuredExtractionError as error:
        raise DriftUsageError(f"konfiguracja dostawcy: {error.code.value}") from error
    assert provider is not None
    return composition.build_budgeted_provider(provider, live)


def write_state(path: str, state: DriftState) -> bool:
    if not path.strip():
        return False
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(state.to_json(), encoding="utf-8")
    except OSError:
        return False
    return True


def render_report(report: DriftReport) -> dict[str, Any]:
    return {
        "status": report.status,
        "drift_rate": report.drift_rate,
        "threshold": report.threshold,
        "compared_blocks": report.compared,
        "skipped_blocks": report.skipped,
        "values": {
            "frozen": report.frozen_values,
            "live": report.live_values,
            "matching": report.matching,
            "only_frozen": report.only_frozen,
            "only_live": report.only_live,
        },
        "model_id": report.model_id,
        "prompt_version": report.prompt_version,
        "live_calls": report.live_calls,
        "cases": [
            {
                "case": item.case_id,
                "block": item.block_id,
                "compared": item.compared,
                "only_frozen": list(item.only_frozen),
                "only_live": list(item.only_live),
                "failure": item.failure,
            }
            for item in report.cases
        ],
    }


async def check(
    cases: Sequence[DriftCase],
    live: StructuredExtractionProvider,
    *,
    threshold: float,
    max_requests: int,
    replay_dir: Path = golden.DEFAULT_OUTPUT_DIR,
) -> DriftReport:
    frozen = ReplayStructuredExtractionProvider(replay_dir, model=golden.MODEL)
    try:
        return await run_drift_check(
            cases, frozen=frozen, live=live, limits=ExtractionLimits(), threshold=threshold, max_requests=max_requests
        )
    finally:
        close = getattr(live, "aclose", None)
        if close is not None:
            await close()


def main(
    argv: Sequence[str] | None = None,
    *,
    live: StructuredExtractionProvider | None = None,
    active: Settings | None = None,
    replay_dir: Path = golden.DEFAULT_OUTPUT_DIR,
    now: datetime | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--confirm-public-text", action="store_true",
                        help="potwierdzam wysłanie publicznych tekstów aktów do dostawcy (wymagane)")
    parser.add_argument("--cases", nargs="+", metavar="ID", help="przypadki kanarkowe (domyślnie wszystkie)")
    parser.add_argument("--threshold", type=float, default=None, help="próg dryfu (domyślnie MPZP_LLM_DRIFT_ALARM_RATE)")
    parser.add_argument("--max-requests", type=int, default=20, help="limit żądań na żywo w jednej kontroli")
    parser.add_argument("--state-file", default=None, help="gdzie zapisać stan dla /health (domyślnie MPZP_LLM_DRIFT_STATE_FILE)")
    parser.add_argument("--output", type=Path, default=None, help="zapisz raport JSON")
    args = parser.parse_args(argv)
    settings = active or app_settings
    try:
        if not args.confirm_public_text:
            raise DriftUsageError("wymagane --confirm-public-text (żądania do dostawcy, koszt)")
        if args.max_requests < 1:
            raise DriftUsageError("--max-requests musi być dodatnie")
        threshold = settings.mpzp_llm_drift_alarm_rate if args.threshold is None else args.threshold
        if not 0.0 <= threshold <= 1.0:
            raise DriftUsageError("--threshold musi mieścić się w 0–1")
        cases = canary_cases(args.cases)
        provider = live if live is not None else build_live_provider(settings)
    except DriftUsageError as error:
        print(f"błąd: {error}", file=sys.stderr)
        return EXIT_USAGE
    report = asyncio.run(check(cases, provider, threshold=threshold, max_requests=args.max_requests, replay_dir=replay_dir))
    moment = now or datetime.now(timezone.utc)
    state = DriftState(report.status, moment, report.drift_rate, threshold, report.model_id, report.prompt_version)
    stored = write_state(args.state_file if args.state_file is not None else settings.mpzp_llm_drift_state_file, state)
    llm_metrics.metrics.increment(f"llm.drift.{report.status}")
    llm_metrics.log_event(
        "drift_check", status=report.status, drift_rate=report.drift_rate if report.drift_rate is not None else "none",
        compared=report.compared, skipped=report.skipped, live_calls=report.live_calls,
    )
    document = render_report(report)
    if args.output is not None:
        args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"dryf {report.model_id} + {report.prompt_version}: {report.status}; "
        f"rozbieżność {report.drift_rate if report.drift_rate is not None else 'brak danych'} "
        f"(próg {threshold}); porównano {report.compared} bloków, pominięto {report.skipped}; "
        f"żądań na żywo {report.live_calls}; stan {'zapisany' if stored else 'niezapisany'}"
    )
    if report.status == "alarm":
        return EXIT_ALARM
    return EXIT_INCONCLUSIVE if report.status == "inconclusive" else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
