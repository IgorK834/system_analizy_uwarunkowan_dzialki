#!/usr/bin/env python3
"""Live smoke korpusu referencyjnego (AU-011, Task 21.11).

Przepuszcza wszystkie działki z ``manifest.json`` przez ``POST /analyze`` działającego backendu
(równoległość domyślnie 3, respektuje ``Retry-After`` przy HTTP 429) i zapisuje wynik jako JSON
oraz Markdown: histogram statusów HTTP i ``status`` analizy, kody błędów, czasy p50/p95, liczbę stref
MPZP/POG oraz liczbę analiz ``complete``. Raport porównuje się z poprzednim przebiegiem (sekcja
„Zmiany względem poprzedniego przebiegu”: nowe regresje i naprawy).

Kod wyjścia: ``0`` — brak odpowiedzi 5xx i brak błędów transportu; ``1`` — jakakolwiek odpowiedź 5xx
albo brak odpowiedzi (połączenie, limit czasu); ``2`` — błąd użycia (manifest, argumenty). Opcja
``--fail-on-regression`` dodaje ``1`` także dla nowych regresji względem poprzedniego przebiegu.

Skrypt zależy tylko od ``httpx`` i biblioteki standardowej (nie importuje ``app``), więc działa na
hoście z samym ``pip install httpx`` i w kontenerze ``backend-test``. W pliku wynikowym nie ma
``access_token`` ani pełnych treści odpowiedzi — tylko wartości pochodne.

Przykład::

    python backend/scripts/live_smoke_corpus.py --base-url http://localhost:8000 \\
        --manifest backend/tests/fixtures/reference_corpus/manifest.json --concurrency 3 \\
        --output docs/evaluation/results/live-smoke/$(date +%F).md
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import httpx

SCHEMA_VERSION = "1.0.0"
REPORT_KIND = "live-smoke"
BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
DEFAULT_MANIFEST = BACKEND_DIR / "tests" / "fixtures" / "reference_corpus" / "manifest.json"

DEFAULT_CONCURRENCY = 3
DEFAULT_TIMEOUT_SECONDS = 180.0
DEFAULT_MAX_RETRIES = 5
DEFAULT_MAX_WAIT_SECONDS = 120.0
FALLBACK_RETRY_AFTER_SECONDS = 60.0
"""Okno limitera (60 s), gdy serwer odpowiedział 429 bez poprawnego ``Retry-After``."""

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2

# Porządek jakości wyniku: mniejsza liczba = lepiej. Używany tylko do porównań przebiegów.
_HTTP_RANK = {2: 0, 3: 1, 4: 2, 5: 3}
_TRANSPORT_RANK = 4
_STATUS_RANK = {"complete": 0, "partial": 1, "waiting_for_user_input": 1}
_UNKNOWN_STATUS_RANK = 2


class SmokeError(ValueError):
    """Nieprawidłowe wejście skryptu (manifest, argumenty, poprzedni raport)."""


# --- Manifest ----------------------------------------------------------------


def load_manifest(path: Path) -> tuple[dict[str, Any], str]:
    """Wczytuje manifest i zwraca go razem z SHA-256 pliku."""
    try:
        payload = path.read_bytes()
        manifest = json.loads(payload)
    except (OSError, json.JSONDecodeError) as exc:
        raise SmokeError(f"Nie można wczytać manifestu {path}: {exc}") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("cases"), list):
        raise SmokeError(f"Manifest {path} nie ma listy 'cases'.")
    return manifest, hashlib.sha256(payload).hexdigest()


def manifest_cases(manifest: Mapping[str, Any], limit: int | None = None) -> list[dict[str, str]]:
    """Pary ``case_id`` / ``parcel_identifier`` w kolejności manifestu."""
    cases: list[dict[str, str]] = []
    for index, case in enumerate(manifest["cases"]):
        if not isinstance(case, Mapping) or not case.get("parcel_identifier"):
            raise SmokeError(f"Przypadek #{index} manifestu nie ma pola 'parcel_identifier'.")
        cases.append(
            {
                "case_id": str(case.get("case_id") or case["parcel_identifier"]),
                "parcel_identifier": str(case["parcel_identifier"]),
            }
        )
    return cases[:limit] if limit else cases


# --- Retry-After i limit żądań -------------------------------------------------


def parse_retry_after(value: str | None, *, now: Callable[[], float] = time.time) -> float | None:
    """``Retry-After`` jako sekundy (liczba całkowita albo data HTTP); ``None`` gdy brak/niepoprawny."""
    if value is None or not value.strip():
        return None
    text = value.strip()
    if text.isascii() and text.isdigit():
        return float(text)
    try:
        moment = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return max(0.0, moment.timestamp() - now())


class RateLimitGate:
    """Wspólna bramka: po 429 wstrzymuje wszystkie zadania do końca okna, nie tylko to jedno.

    Bez niej każde z równoległych zadań odczekałoby osobno i ponowiło żądanie jednocześnie, znów
    trafiając na limit.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._open_at = 0.0

    def close_for(self, seconds: float) -> None:
        self._open_at = max(self._open_at, self._clock() + seconds)

    def remaining(self) -> float:
        return max(0.0, self._open_at - self._clock())


# --- Pojedynczy przypadek ------------------------------------------------------


def classify_http(status_code: int | None) -> str:
    """Klasa wyniku: ``2xx`` … ``5xx`` albo ``transport`` (brak odpowiedzi)."""
    return "transport" if status_code is None else f"{status_code // 100}xx"


def _count(value: object) -> int | None:
    """Długość listy albo ``None`` — brak sekcji to nie to samo co zero stref (``null`` ≠ 0)."""
    return len(value) if isinstance(value, list) else None


def summarize_response(status_code: int, body: object) -> dict[str, Any]:
    """Pola pochodne odpowiedzi: nigdy ``access_token`` ani pełna treść."""
    summary: dict[str, Any] = {
        "analysis_status": None,
        "error_code": None,
        "analysis_id": None,
        "mpzp_zone_count": None,
        "pog_zone_count": None,
        "pog_legal_status": None,
        "pog_coverage_status": None,
        "mpzp_discovery_status": None,
        "manual_zone_required": None,
        "warning_codes": [],
        "body_valid": isinstance(body, dict),
    }
    if not isinstance(body, dict):
        return summary
    if status_code >= 400:
        error = body.get("error")
        summary["error_code"] = error if isinstance(error, str) else None
        return summary
    status = body.get("status")
    summary["analysis_status"] = status if isinstance(status, str) else None
    analysis_id = body.get("analysis_id")
    summary["analysis_id"] = analysis_id if isinstance(analysis_id, int) else None
    summary["mpzp_zone_count"] = _count(body.get("mpzp_zones"))
    pog = body.get("pog")
    if isinstance(pog, dict):
        summary["pog_zone_count"] = _count(pog.get("zones"))
        legal, coverage = pog.get("legal_status"), pog.get("coverage_status")
        summary["pog_legal_status"] = legal if isinstance(legal, str) else None
        summary["pog_coverage_status"] = coverage if isinstance(coverage, str) else None
    discovery = body.get("mpzp_discovery")
    if isinstance(discovery, dict) and isinstance(discovery.get("status"), str):
        summary["mpzp_discovery_status"] = discovery["status"]
    required = body.get("manual_zone_required")
    summary["manual_zone_required"] = required if isinstance(required, bool) else None
    warnings = body.get("warnings")
    if isinstance(warnings, list):
        summary["warning_codes"] = sorted(
            {str(item["code"]) for item in warnings if isinstance(item, dict) and item.get("code")}
        )
    return summary


async def run_case(
    client: httpx.AsyncClient,
    case: Mapping[str, str],
    *,
    request_id: str,
    force_refresh: bool,
    gate: RateLimitGate,
    max_retries: int,
    max_wait_seconds: float,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, Any]:
    """Jedno ``POST /analyze``; 429 ponawia po ``Retry-After`` (do ``max_retries`` razy)."""
    payload = {"method": "parcel_id", "parcel_identifier": case["parcel_identifier"]}
    params = {"force_refresh": "true"} if force_refresh else None
    attempts = 0
    waited = 0.0
    result: dict[str, Any] = {
        "case_id": case["case_id"],
        "parcel_identifier": case["parcel_identifier"],
        "http_status": None,
        "http_class": "transport",
        "request_id": request_id,
        "duration_ms": None,
        "attempts": 0,
        "waited_seconds": 0.0,
        "retry_after_seconds": None,
        "transport_error": None,
        **summarize_response(0, None),
    }
    while True:
        pause = gate.remaining()
        if pause > 0:
            waited += pause
            await sleep(pause)
        attempts += 1
        started = clock()
        try:
            response = await client.post(
                "/analyze",
                json=payload,
                params=params,
                headers={"X-Request-ID": request_id},
            )
        except httpx.HTTPError as exc:
            result["duration_ms"] = round((clock() - started) * 1000.0, 1)
            result["transport_error"] = f"{type(exc).__name__}: {exc}"[:300]
            break
        result["duration_ms"] = round((clock() - started) * 1000.0, 1)
        result["http_status"] = response.status_code
        result["http_class"] = classify_http(response.status_code)
        result["request_id"] = response.headers.get("x-request-id", request_id)
        try:
            body: object = response.json()
        except ValueError:
            body = None
        result.update(summarize_response(response.status_code, body))
        if response.status_code != 429:
            break
        wait = parse_retry_after(response.headers.get("retry-after"))
        result["retry_after_seconds"] = wait
        if attempts > max_retries:
            break
        wait = min(FALLBACK_RETRY_AFTER_SECONDS if wait is None else wait, max_wait_seconds)
        gate.close_for(wait)
    result["attempts"] = attempts
    result["waited_seconds"] = round(waited, 3)
    return result


async def run_smoke(
    base_url: str,
    cases: Sequence[Mapping[str, str]],
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    force_refresh: bool = False,
    max_retries: int = DEFAULT_MAX_RETRIES,
    max_wait_seconds: float = DEFAULT_MAX_WAIT_SECONDS,
    run_id: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    on_result: Callable[[dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    """Wszystkie przypadki z ograniczoną równoległością; wynik w kolejności manifestu."""
    if concurrency < 1:
        raise SmokeError("--concurrency musi być co najmniej 1.")
    run_id = run_id or datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    gate = RateLimitGate()
    semaphore = asyncio.Semaphore(concurrency)
    timeout = httpx.Timeout(timeout_seconds, connect=min(10.0, timeout_seconds))

    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout, transport=transport) as client:

        async def one(index: int, case: Mapping[str, str]) -> dict[str, Any]:
            async with semaphore:
                result = await run_case(
                    client,
                    case,
                    request_id=f"live-smoke-{run_id}-{index + 1:03d}",
                    force_refresh=force_refresh,
                    gate=gate,
                    max_retries=max_retries,
                    max_wait_seconds=max_wait_seconds,
                    sleep=sleep,
                )
            if on_result is not None:
                on_result(result)
            return result

        return list(await asyncio.gather(*(one(i, case) for i, case in enumerate(cases))))


# --- Statystyki ----------------------------------------------------------------


def percentile(values: Sequence[float], quantile: float) -> float | None:
    """Percentyl z interpolacją liniową (ranga ``(n - 1) * quantile``), jak w ewaluatorze korpusu."""
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    rank = (len(ordered) - 1) * quantile
    lower, upper = math.floor(rank), math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)


def _sorted_counts(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def _zone_stats(results: Sequence[Mapping[str, Any]], key: str) -> dict[str, int]:
    counts = [result[key] for result in results if isinstance(result.get(key), int)]
    return {
        "cases_with_zones": sum(1 for count in counts if count > 0),
        "zones_total": sum(counts),
        "cases_with_section": len(counts),
        "cases_without_section": sum(1 for result in results if result.get("http_class") == "2xx" and result.get(key) is None),
    }


def build_summary(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    http_hist = Counter(
        "brak odpowiedzi" if result["http_status"] is None else str(result["http_status"])
        for result in results
    )
    ok = [result for result in results if result["http_class"] == "2xx"]
    status_hist = Counter(str(result.get("analysis_status") or "brak") for result in ok)
    error_hist = Counter(str(result["error_code"]) for result in results if result.get("error_code"))
    warning_hist: Counter[str] = Counter()
    for result in ok:
        warning_hist.update(result.get("warning_codes") or [])
    durations = [float(result["duration_ms"]) for result in results if result.get("duration_ms") is not None]
    five_xx = sum(1 for result in results if result["http_class"] == "5xx")
    transport = sum(1 for result in results if result["http_class"] == "transport")
    return {
        "cases": len(results),
        "http_status_histogram": _sorted_counts(http_hist),
        "analysis_status_histogram": _sorted_counts(status_hist),
        "error_code_histogram": _sorted_counts(error_hist),
        "warning_code_histogram": _sorted_counts(warning_hist),
        "five_xx": five_xx,
        "transport_errors": transport,
        "client_errors": sum(1 for result in results if result["http_class"] == "4xx"),
        "successful": len(ok),
        "complete": sum(1 for result in ok if result.get("analysis_status") == "complete"),
        "rate_limited_retries": sum(max(0, int(result.get("attempts") or 1) - 1) for result in results),
        "mpzp": _zone_stats(results, "mpzp_zone_count"),
        "pog": _zone_stats(results, "pog_zone_count"),
        "latency_ms": {
            "count": len(durations),
            "p50": percentile(durations, 0.5),
            "p95": percentile(durations, 0.95),
            "max": max(durations) if durations else None,
        },
        "passed": five_xx == 0 and transport == 0,
    }


# --- Porównanie z poprzednim przebiegiem --------------------------------------


def _http_rank(result: Mapping[str, Any]) -> int:
    status = result.get("http_status")
    return _TRANSPORT_RANK if status is None else _HTTP_RANK.get(int(status) // 100, _TRANSPORT_RANK)


def _status_rank(status: object) -> int:
    return _STATUS_RANK.get(str(status), _UNKNOWN_STATUS_RANK)


def _change(case_id: str, field: str, before: object, after: object) -> dict[str, Any]:
    return {"case_id": case_id, "field": field, "before": before, "after": after}


def compare_runs(previous: Mapping[str, Any] | None, current: Mapping[str, Any]) -> dict[str, Any]:
    """Nowe regresje, naprawy i inne zmiany per przypadek (``previous=None`` — brak porównania)."""
    if previous is None:
        return {"available": False}
    before = {case["case_id"]: case for case in previous.get("cases", []) if isinstance(case, Mapping)}
    after = {case["case_id"]: case for case in current.get("cases", [])}
    regressions: list[dict[str, Any]] = []
    fixes: list[dict[str, Any]] = []
    other: list[dict[str, Any]] = []
    unchanged = 0
    for case_id, now in after.items():
        was = before.get(case_id)
        if was is None:
            continue
        changes: list[tuple[str, dict[str, Any]]] = []  # ("regression" | "fix" | "other", wpis)
        old_rank, new_rank = _http_rank(was), _http_rank(now)
        if old_rank != new_rank:
            changes.append(
                (
                    "regression" if new_rank > old_rank else "fix",
                    _change(case_id, "http_status", was.get("http_status"), now.get("http_status")),
                )
            )
        elif was.get("http_status") != now.get("http_status"):
            changes.append(("other", _change(case_id, "http_status", was.get("http_status"), now.get("http_status"))))
        elif was.get("error_code") != now.get("error_code"):
            changes.append(("other", _change(case_id, "error_code", was.get("error_code"), now.get("error_code"))))
        if old_rank == new_rank == _HTTP_RANK[2]:
            old_status, new_status = _status_rank(was.get("analysis_status")), _status_rank(now.get("analysis_status"))
            if old_status != new_status:
                changes.append(
                    (
                        "regression" if new_status > old_status else "fix",
                        _change(case_id, "analysis_status", was.get("analysis_status"), now.get("analysis_status")),
                    )
                )
            elif was.get("analysis_status") != now.get("analysis_status"):
                changes.append(
                    ("other", _change(case_id, "analysis_status", was.get("analysis_status"), now.get("analysis_status")))
                )
            for field in ("mpzp_zone_count", "pog_zone_count"):
                old_count, new_count = was.get(field), now.get(field)
                if isinstance(old_count, int) and isinstance(new_count, int) and old_count != new_count:
                    changes.append(("regression" if new_count < old_count else "fix", _change(case_id, field, old_count, new_count)))
                elif (old_count is None) != (new_count is None):
                    changes.append(("other", _change(case_id, field, old_count, new_count)))
        if not changes:
            unchanged += 1
        for kind, entry in changes:
            {"regression": regressions, "fix": fixes, "other": other}[kind].append(entry)

    old_summary, new_summary = previous.get("summary", {}), current.get("summary", {})

    def delta(path: Sequence[str]) -> dict[str, object]:
        def read(summary: Mapping[str, Any]) -> object:
            value: object = summary
            for key in path:
                value = value.get(key) if isinstance(value, Mapping) else None
            return value

        return {"before": read(old_summary), "after": read(new_summary)}

    return {
        "available": True,
        "previous": {
            "generated_at": previous.get("metadata", {}).get("generated_at"),
            "commit_sha": previous.get("metadata", {}).get("commit_sha"),
            "base_url": previous.get("metadata", {}).get("base_url"),
        },
        "regressions": regressions,
        "fixes": fixes,
        "other_changes": other,
        "unchanged_cases": unchanged,
        "added_cases": sorted(set(after) - set(before)),
        "removed_cases": sorted(set(before) - set(after)),
        "deltas": {
            "five_xx": delta(["five_xx"]),
            "transport_errors": delta(["transport_errors"]),
            "complete": delta(["complete"]),
            "successful": delta(["successful"]),
            "mpzp_cases_with_zones": delta(["mpzp", "cases_with_zones"]),
            "pog_cases_with_zones": delta(["pog", "cases_with_zones"]),
            "latency_p50_ms": delta(["latency_ms", "p50"]),
            "latency_p95_ms": delta(["latency_ms", "p95"]),
        },
    }


def find_previous_report(directory: Path, exclude: Path | None = None) -> tuple[Path, dict[str, Any]] | None:
    """Najnowszy poprzedni raport JSON z katalogu wyników (po ``generated_at``); pomija bieżący plik."""
    candidates: list[tuple[str, Path, dict[str, Any]]] = []
    if not directory.is_dir():
        return None
    skip = exclude.resolve() if exclude is not None else None
    for path in sorted(directory.glob("*.json")):
        if skip is not None and path.resolve() == skip:
            continue
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(report, dict) or report.get("kind") != REPORT_KIND or not isinstance(report.get("cases"), list):
            continue
        candidates.append((str(report.get("metadata", {}).get("generated_at") or path.stem), path, report))
    if not candidates:
        return None
    _, path, report = max(candidates, key=lambda item: item[0])
    return path, report


def load_report(path: Path) -> dict[str, Any]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SmokeError(f"Nie można wczytać poprzedniego raportu {path}: {exc}") from exc
    if not isinstance(report, dict) or report.get("kind") != REPORT_KIND:
        raise SmokeError(f"{path} nie jest raportem live smoke.")
    return report


# --- Raport --------------------------------------------------------------------


def _git(*args: str) -> str | None:
    try:
        return subprocess.run(
            ["git", *args], cwd=REPO_ROOT, check=True, capture_output=True, text=True, timeout=10
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _git_commit() -> str:
    env = os.environ.get("GITHUB_SHA")
    return env if env else (_git("rev-parse", "HEAD") or "unknown")


def _worktree_dirty() -> bool | None:
    """``True``, gdy drzewo robocze ma niezacommitowane zmiany; ``None``, gdy nie da się ustalić (brak git)."""
    status = _git("status", "--porcelain", "--untracked-files=no")
    return None if status is None else bool(status)


def build_report(
    results: Sequence[Mapping[str, Any]],
    *,
    metadata: Mapping[str, Any],
    previous: Mapping[str, Any] | None,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "kind": REPORT_KIND,
        "schema_version": SCHEMA_VERSION,
        "metadata": dict(metadata),
        "summary": build_summary(results),
        "cases": [dict(result) for result in results],
    }
    report["comparison"] = compare_runs(previous, report)
    return report


def _fmt_ms(value: object) -> str:
    return "—" if value is None else f"{float(value):,.0f}".replace(",", " ")  # type: ignore[arg-type]


def _cell(value: object) -> str:
    if value is None or value == "":
        return "—"
    return str(value).replace("|", "\\|").replace("\n", " ")


def _histogram_table(title_columns: tuple[str, str], histogram: Mapping[str, int]) -> list[str]:
    if not histogram:
        return ["_brak danych_", ""]
    lines = [f"| {title_columns[0]} | {title_columns[1]} |", "|---|---:|"]
    lines.extend(f"| `{key}` | {count} |" for key, count in histogram.items())
    lines.append("")
    return lines


def _change_rows(items: Sequence[Mapping[str, Any]]) -> list[str]:
    if not items:
        return ["_brak_", ""]
    lines = ["| Przypadek | Pole | Poprzednio | Teraz |", "|---|---|---|---|"]
    lines.extend(
        f"| `{item['case_id']}` | `{item['field']}` | {_cell(item['before'])} | {_cell(item['after'])} |" for item in items
    )
    lines.append("")
    return lines


def render_markdown(report: Mapping[str, Any]) -> str:
    meta, summary, comparison = report["metadata"], report["summary"], report["comparison"]
    verdict = "PASS" if summary["passed"] else "FAIL"
    lines = [
        f"# Live smoke korpusu referencyjnego — {meta['run_date']}",
        "",
        f"Przebieg korpusu referencyjnego ({summary['cases']} działek) na żywych usługach (`POST /analyze`), bez zamrożonych obserwacji — "
        "różnica względem ewaluacji offline (`evaluate_reference_corpus.py`, tryb `offline`) jest wynikiem tej pracy. "
        "W raporcie nie ma tokenów dostępu ani pełnych odpowiedzi.",
        "",
        f"**Wynik: {verdict}** — próg: 0 odpowiedzi 5xx i 0 braków odpowiedzi. "
        f"5xx: **{summary['five_xx']}**, brak odpowiedzi: **{summary['transport_errors']}**, "
        f"`complete`: **{summary['complete']}** z {summary['successful']} udanych analiz (HTTP 2xx).",
        "",
        "## Metadane",
        "",
        "| Pole | Wartość |",
        "|---|---|",
        f"| Adres API | `{meta['base_url']}` |",
        f"| Korpus | `{_cell(meta.get('corpus_id'))}` (`sha256:{str(meta['manifest_sha256'])[:16]}…`) |",
        f"| Commit | `{meta['commit_sha']}`{' + niezacommitowane zmiany' if meta.get('worktree_dirty') else ''} |",
        f"| Start / koniec (UTC) | {meta['started_at']} / {meta['finished_at']} ({meta['duration_seconds']:.1f} s) |",
        f"| Równoległość / limit czasu żądania | {meta['concurrency']} / {meta['timeout_seconds']:g} s |",
        f"| `force_refresh` | {str(meta['force_refresh']).lower()} |",
        f"| Ponowienia po HTTP 429 (`Retry-After`) | {summary['rate_limited_retries']} |",
        "",
        "## Podsumowanie",
        "",
        "### Statusy HTTP",
        "",
        *_histogram_table(("HTTP", "Przypadki"), summary["http_status_histogram"]),
        "### Status analizy (`status`, tylko HTTP 2xx)",
        "",
        *_histogram_table(("status", "Przypadki"), summary["analysis_status_histogram"]),
        "### Kody błędów (`ErrorResponse.error`)",
        "",
        *_histogram_table(("kod", "Przypadki"), summary["error_code_histogram"]),
        "### Czasy odpowiedzi",
        "",
        "| Miara | ms |",
        "|---|---:|",
        f"| p50 | {_fmt_ms(summary['latency_ms']['p50'])} |",
        f"| p95 | {_fmt_ms(summary['latency_ms']['p95'])} |",
        f"| maksimum | {_fmt_ms(summary['latency_ms']['max'])} |",
        "",
        "### Strefy planistyczne",
        "",
        "| Sekcja | Działki z ≥ 1 strefą | Suma stref | HTTP 2xx bez sekcji (`null`) |",
        "|---|---:|---:|---:|",
        f"| MPZP (`mpzp_zones`) | {summary['mpzp']['cases_with_zones']} | {summary['mpzp']['zones_total']} | "
        f"{summary['mpzp']['cases_without_section']} |",
        f"| POG (`pog.zones`) | {summary['pog']['cases_with_zones']} | {summary['pog']['zones_total']} | "
        f"{summary['pog']['cases_without_section']} |",
        "",
        "Brak sekcji (`null`) nie jest zerem stref, a brak stref nie dowodzi braku ograniczenia — zob. statusy źródeł "
        "w odpowiedzi API.",
        "",
        "### Ostrzeżenia (`warnings[].code`, HTTP 2xx)",
        "",
        *_histogram_table(("kod", "Przypadki"), summary["warning_code_histogram"]),
        "## Zmiany względem poprzedniego przebiegu",
        "",
    ]
    if not comparison["available"]:
        lines += ["Brak poprzedniego przebiegu do porównania — ten raport jest punktem odniesienia.", ""]
    else:
        previous = comparison["previous"]
        lines += [
            f"Poprzedni przebieg: {_cell(previous['generated_at'])} (commit `{_cell(previous['commit_sha'])}`). "
            f"Bez zmian: {comparison['unchanged_cases']} przypadków.",
            "",
            "| Wskaźnik | Poprzednio | Teraz |",
            "|---|---:|---:|",
            *(
                f"| {label} | {_cell(comparison['deltas'][key]['before'])} | {_cell(comparison['deltas'][key]['after'])} |"
                for label, key in (
                    ("Odpowiedzi 5xx", "five_xx"),
                    ("Brak odpowiedzi", "transport_errors"),
                    ("Udane analizy (2xx)", "successful"),
                    ("Analizy `complete`", "complete"),
                    ("Działki z ≥ 1 strefą MPZP", "mpzp_cases_with_zones"),
                    ("Działki z ≥ 1 strefą POG", "pog_cases_with_zones"),
                    ("Czas p50 [ms]", "latency_p50_ms"),
                    ("Czas p95 [ms]", "latency_p95_ms"),
                )
            ),
            "",
            f"### Nowe regresje ({len(comparison['regressions'])})",
            "",
            *_change_rows(comparison["regressions"]),
            f"### Naprawy ({len(comparison['fixes'])})",
            "",
            *_change_rows(comparison["fixes"]),
        ]
        if comparison["other_changes"]:
            lines += [f"### Inne zmiany ({len(comparison['other_changes'])})", "", *_change_rows(comparison["other_changes"])]
        if comparison["added_cases"] or comparison["removed_cases"]:
            lines += [
                f"Przypadki dodane do korpusu: {', '.join(f'`{c}`' for c in comparison['added_cases']) or '—'}; "
                f"usunięte: {', '.join(f'`{c}`' for c in comparison['removed_cases']) or '—'}.",
                "",
            ]
    lines += [
        "## Wyniki per działka",
        "",
        "| # | case_id | Identyfikator działki | HTTP | status | kod błędu | MPZP | POG | czas [ms] | próby | request_id |",
        "|---:|---|---|---:|---|---|---:|---:|---:|---:|---|",
    ]
    for index, case in enumerate(report["cases"], start=1):
        http = "brak" if case["http_status"] is None else case["http_status"]
        lines.append(
            f"| {index} | `{case['case_id']}` | `{case['parcel_identifier']}` | {http} | {_cell(case.get('analysis_status'))} | "
            f"{_cell(case.get('error_code') or case.get('transport_error'))} | {_cell(case.get('mpzp_zone_count'))} | "
            f"{_cell(case.get('pog_zone_count'))} | {_fmt_ms(case.get('duration_ms'))} | {case['attempts']} | "
            f"`{_cell(case.get('request_id'))}` |"
        )
    lines += [
        "",
        "`MPZP`/`POG` — liczba stref w odpowiedzi; `—` oznacza brak sekcji albo błąd (nie zero). "
        "Czas dotyczy ostatniej próby, bez oczekiwania na `Retry-After`.",
        "",
    ]
    return "\n".join(lines)


def write_report(report: Mapping[str, Any], markdown_path: Path, json_path: Path) -> None:
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")


# --- CLI ---------------------------------------------------------------------


def default_output_path(today: str) -> Path:
    docs = REPO_ROOT / "docs"
    directory = docs / "evaluation" / "results" / "live-smoke" if docs.is_dir() else Path.cwd()
    return directory / f"{today}.md"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Live smoke korpusu referencyjnego przez POST /analyze (AU-011).")
    parser.add_argument("--base-url", default="http://localhost:8000", help="Adres API, np. http://localhost:8000.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--output", type=Path, default=None, help="Raport Markdown (domyślnie docs/…/live-smoke/<data>.md).")
    parser.add_argument("--json-output", type=Path, default=None, help="Raport JSON (domyślnie obok Markdown, rozszerzenie .json).")
    parser.add_argument(
        "--previous",
        default=None,
        help="Poprzedni raport JSON do porównania; 'none' wyłącza porównanie (domyślnie: najnowszy w katalogu wyników).",
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS, help="Limit czasu jednego żądania [s].")
    parser.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES, help="Ponowienia po HTTP 429.")
    parser.add_argument("--max-wait", type=float, default=DEFAULT_MAX_WAIT_SECONDS, help="Górny limit oczekiwania na Retry-After [s].")
    parser.add_argument("--limit", type=int, default=None, help="Tylko pierwsze N działek (próba).")
    parser.add_argument("--force-refresh", action="store_true", help="Dodaj force_refresh=true (limit 5/min — wolniej).")
    parser.add_argument("--fail-on-regression", action="store_true", help="Kod 1 także przy nowych regresjach.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest, manifest_sha256 = load_manifest(args.manifest)
        cases = manifest_cases(manifest, args.limit)
        if not cases:
            raise SmokeError("Manifest nie zawiera żadnych działek.")
        started = datetime.now(UTC)
        markdown_path = args.output or default_output_path(started.strftime("%Y-%m-%d"))
        json_path = args.json_output or markdown_path.with_suffix(".json")
        previous_path: Path | None
        previous: dict[str, Any] | None
        if args.previous == "none":
            previous_path, previous = None, None
        elif args.previous:
            previous_path = Path(args.previous)
            previous = load_report(previous_path)
        else:
            found = find_previous_report(json_path.parent, exclude=json_path)
            previous_path, previous = found if found else (None, None)
    except SmokeError as exc:
        print(f"live smoke: {exc}", file=sys.stderr)
        return EXIT_USAGE

    wall = time.monotonic()
    results = asyncio.run(
        run_smoke(
            args.base_url,
            cases,
            concurrency=args.concurrency,
            timeout_seconds=args.timeout,
            force_refresh=args.force_refresh,
            max_retries=args.max_retries,
            max_wait_seconds=args.max_wait,
            run_id=started.strftime("%Y%m%d%H%M%S"),
            on_result=lambda r: print(
                f"[{r['case_id']}] HTTP {r['http_status'] if r['http_status'] is not None else 'brak'} "
                f"{r.get('analysis_status') or r.get('error_code') or r.get('transport_error') or ''} "
                f"({_fmt_ms(r['duration_ms'])} ms)",
                flush=True,
            ),
        )
    )
    finished = datetime.now(UTC)
    metadata = {
        "generated_at": finished.isoformat().replace("+00:00", "Z"),
        "started_at": started.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "finished_at": finished.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "run_date": started.strftime("%Y-%m-%d"),
        "duration_seconds": round(time.monotonic() - wall, 3),
        "base_url": args.base_url,
        "corpus_id": manifest.get("corpus_id"),
        "manifest_sha256": manifest_sha256,
        "commit_sha": _git_commit(),
        "worktree_dirty": _worktree_dirty(),
        "concurrency": args.concurrency,
        "timeout_seconds": args.timeout,
        "force_refresh": args.force_refresh,
        "previous_report": previous_path.name if previous_path else None,
    }
    report = build_report(results, metadata=metadata, previous=previous)
    write_report(report, markdown_path, json_path)

    summary, comparison = report["summary"], report["comparison"]
    print(
        f"live smoke: {summary['cases']} działek, 5xx={summary['five_xx']}, brak odpowiedzi={summary['transport_errors']}, "
        f"complete={summary['complete']}/{summary['successful']} -> {markdown_path}"
    )
    if not summary["passed"]:
        print("live smoke FAIL: odpowiedź 5xx lub brak odpowiedzi (próg: 0).", file=sys.stderr)
        return EXIT_FAILED
    if args.fail_on_regression and comparison.get("regressions"):
        print(f"live smoke FAIL: {len(comparison['regressions'])} nowych regresji względem poprzedniego przebiegu.", file=sys.stderr)
        return EXIT_FAILED
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
