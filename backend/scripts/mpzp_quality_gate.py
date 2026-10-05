"""Bramka jakości ścieżki hybrydowej (PV3-17, Task 20.17): raport decyzyjny wobec zamrożonych kryteriów.

Narzędzie NIE uruchamia silników — czyta wyniki ewaluatora (``evaluate_mpzp_parser.py``) dla silników
``legacy``, ``v3`` i ``hybrid`` i liczy wynik wobec kryteriów zamrożonych w ADR-012 (2026-10-05, przed
pierwszym biegiem na zbiorze końcowym). Polecenia:

    # 1) przegląd ręczny: arkusz ≥ 100 przyjętych wartości ``ai_candidate`` (losowanie z ziarnem)
    python3 scripts/mpzp_quality_gate.py review-sheet --results RESULTS/hybrid --output review_sheet.csv
    # 2) człowiek wypełnia kolumny ``verdict`` (correct/incorrect/unclear), ``reviewer``, ``reviewed_at``
    python3 scripts/mpzp_quality_gate.py review-score --sheet review_sheet.csv --output review.json
    # 3) zmienność modelu: trzy biegi ``--live`` do OSOBNYCH katalogów odpowiedzi
    python3 scripts/mpzp_quality_gate.py variance --runs RUN1/hybrid RUN2/hybrid RUN3/hybrid --output variance.json
    # 4) raport i decyzja
    python3 scripts/mpzp_quality_gate.py gate --results RESULTS --review review.json --variance variance.json \\
        --output-dir ../docs/evaluation/results/parser-v3

Decyzja: ``GO`` wyłącznie, gdy zbiór jest niezależnym zbiorem końcowym (profil ``final-v2`` z Task 20.2),
każde kryterium jest zmierzone i spełnione, a przegląd ręczny ma ≥ 100 ocenionych wartości; przy brakach —
``NOT_DECIDABLE`` z listą braków; przy niespełnionym kryterium — ``NO_GO``. Raport jest rekomendacją
opartą na pomiarze z ograniczeniami, nie gwarancją jakości.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

GATE_VERSION = "mpzp-quality-gate/1"
FROZEN_ON = "2026-10-05"  # ADR-012, „Decyzja właściciela”, pkt 4 — zmiana wymaga nowego wpisu w ADR
# (klucz, opis, operator, próg). Wartości są kopią ADR-012; test pilnuje, że nikt ich nie poluzował.
CRITERIA: tuple[tuple[str, str, str, float], ...] = (
    ("precision", "precision", ">=", 0.95),
    ("recall", "recall", ">=", 0.80),
    ("source_consistent", "source_consistent (Task 20.3)", ">=", 0.98),
    ("unverified_quotes", "przyjęte wartości bez zweryfikowanego cytatu", "==", 0),
    ("zone_assignment_error_rate", "błędy przypisania do strefy / znalezione", "<=", 0.02),
    ("ece", "ECE (kalibracja pewności)", "<=", 0.10),
    ("cost_per_analysis_usd", "koszt na analizę, cena od 2027 [USD]", "<=", 0.05),
    ("additional_latency_p95_s", "dodatkowe opóźnienie p95 względem v3 [s]", "<=", 30.0),
    ("manual_review_count", "ocenione ręcznie wartości ai_candidate", ">=", 100),
)
REVIEW_COLUMNS = (
    "review_id", "sample_id", "document_id", "document_url", "zone_symbol", "parameter", "value", "raw_value",
    "page", "quote", "verdict", "reviewer", "reviewed_at", "note",
)
VERDICTS = ("correct", "incorrect", "unclear")
WILSON_Z = 1.959964


def wilson(successes: int, total: int) -> list[float] | None:
    if total <= 0:
        return None
    p = successes / total
    denominator = 1 + WILSON_Z**2 / total
    centre = (p + WILSON_Z**2 / (2 * total)) / denominator
    margin = WILSON_Z * math.sqrt(p * (1 - p) / total + WILSON_Z**2 / (4 * total**2)) / denominator
    return [round(max(0.0, centre - margin), 4), round(min(1.0, centre + margin), 4)]


def p95(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]  # metoda najbliższej rangi


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def ai_candidates(results_dir: Path) -> list[dict[str, Any]]:
    """Przyjęte wartości modelu (``ai_candidate``) z ``parameter_results.json`` silnika."""
    rows = _read(results_dir / "parameter_results.json")["rows"]
    found = []
    for row in rows:
        for output in row.get("parser_outputs") or []:
            if output.get("review_status") == "ai_candidate":
                found.append({
                    "sample_id": row["sample_id"], "document_id": row.get("document_id"),
                    "document_url": row.get("document_url"), "zone_symbol": row["zone_symbol"],
                    "parameter": row["parameter"], "value": output.get("value"), "raw_value": output.get("raw_value"),
                    "page": output.get("page"), "quote": output.get("source_text"),
                })
    return found


# --- przegląd ręczny ---------------------------------------------------------------------------------


def review_sheet(results_dir: Path, output: Path, n: int = 100, seed: int = 20261005) -> int:
    candidates = ai_candidates(results_dir)
    rng = random.Random(seed)
    chosen = candidates if len(candidates) <= n else rng.sample(candidates, n)
    chosen.sort(key=lambda item: (item["sample_id"], item["zone_symbol"], item["parameter"], str(item["value"])))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        for index, item in enumerate(chosen, start=1):
            writer.writerow({**item, "review_id": f"R{index:04d}", "verdict": "", "reviewer": "", "reviewed_at": "", "note": ""})
    return len(chosen)


def review_score(sheet: Path) -> dict[str, Any]:
    """Precyzja wg przeglądu ręcznego; wiersz liczy się tylko z werdyktem i podpisem recenzenta."""
    with sheet.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    problems = []
    counts = {verdict: 0 for verdict in VERDICTS}
    reviewers: set[str] = set()
    for row in rows:
        verdict = (row.get("verdict") or "").strip().lower()
        reviewer = (row.get("reviewer") or "").strip()
        if not verdict:
            continue
        if verdict not in VERDICTS:
            problems.append(f"{row.get('review_id')}: unknown verdict {verdict!r}")
            continue
        if not reviewer:
            problems.append(f"{row.get('review_id')}: verdict without reviewer")
            continue
        counts[verdict] += 1
        reviewers.add(reviewer)
    decided = counts["correct"] + counts["incorrect"]
    return {
        "sheet_rows": len(rows),
        "reviewed": sum(counts.values()),
        "counts": counts,
        "precision": round(counts["correct"] / decided, 4) if decided else None,
        "precision_ci95": wilson(counts["correct"], decided),
        "reviewers": sorted(reviewers),
        "problems": problems,
        "note": "Recenzent jest deklarowany w arkuszu; narzędzie nie weryfikuje, że jest człowiekiem.",
    }


# --- zmienność bieg-do-biegu ----------------------------------------------------------------------------


def variance(run_dirs: Sequence[Path]) -> dict[str, Any]:
    """Zgodność przyjętych wartości modelu między biegami na żywo (te same pary i manifest)."""
    if len(run_dirs) < 2:
        raise ValueError("variance needs at least two runs")
    per_run: list[dict[tuple[str, str, str], tuple[str, ...]]] = []
    metrics = []
    manifests = set()
    for run in run_dirs:
        values: dict[tuple[str, str, str], tuple[str, ...]] = {}
        for item in ai_candidates(run):
            key = (item["sample_id"], item["zone_symbol"], item["parameter"])
            values[key] = tuple(sorted({*values.get(key, ()), repr(item["value"])}))
        per_run.append(values)
        overall = _read(run / "metrics.json")["overall"]["detection"]
        metrics.append({"run": str(run), "precision": overall["precision"]["value"], "recall": overall["recall"]["value"]})
        manifests.add(_read(run / "run_manifest.json").get("manifest_sha256"))
    keys = set().union(*per_run)
    identical = sum(1 for key in keys if len({run.get(key, ()) for run in per_run}) == 1)
    return {
        "runs": len(run_dirs),
        "same_manifest": len(manifests) == 1,
        "pairs_with_model_values": len(keys),
        "identical_across_runs": identical,
        "agreement": round(identical / len(keys), 4) if keys else None,
        "agreement_ci95": wilson(identical, len(keys)),
        "per_run_metrics": metrics,
    }


# --- weryfikacja cytatów niezależnie od silnika ------------------------------------------------------------


def unverified_quotes(results_dir: Path, corpus: Path) -> dict[str, Any]:
    """Przyjęte wartości modelu, których cytatu nie ma dosłownie w tekście dokumentu korpusu."""
    from scripts import evaluate_mpzp_parser as ev

    manifest, _ = ev.load_corpus(corpus)
    texts: dict[str, str] = {}
    missing: list[dict[str, Any]] = []
    candidates = ai_candidates(results_dir)
    for item in candidates:
        document_id = item["document_id"]
        if document_id not in texts:
            loaded = ev.load_document(corpus.parent, manifest["documents"][document_id])
            texts[document_id] = ev.normalize_text("\n".join(loaded["pages"]))
        quote = ev.normalize_text(item["quote"] or "")
        if not quote or quote not in texts[document_id]:
            missing.append({key: item[key] for key in ("sample_id", "zone_symbol", "parameter", "value")})
    return {"checked": len(candidates), "unverified": len(missing), "examples": missing[:20]}


# --- bramka ------------------------------------------------------------------------------------------------


def _passes(value: float | None, operator: str, threshold: float) -> bool | None:
    if value is None:
        return None
    return {">=": value >= threshold, "<=": value <= threshold, "==": value == threshold}[operator]


def _engine_summary(directory: Path) -> dict[str, Any] | None:
    if not (directory / "metrics.json").is_file():
        return None
    metrics = _read(directory / "metrics.json")
    detection = metrics["overall"]["detection"]
    observations = _read(directory / "observations.json")
    return {
        "precision": detection["precision"],
        "recall": detection["recall"],
        "source_consistent": detection["source_consistent"],
        "counts": detection["counts"],
        "cross_zone_errors": detection["cross_zone_errors"],
        "ece": metrics["overall"]["calibration"].get("expected_calibration_error"),
        "by_format": {name: {k: block["detection"][k]["value"] for k in ("precision", "recall", "source_consistent")}
                      for name, block in metrics.get("by_format", {}).items()},
        "by_gmina": {name: {k: block["detection"][k]["value"] for k in ("precision", "recall", "source_consistent")}
                     for name, block in metrics.get("by_gmina", {}).items()},
        "rejections": metrics.get("rejections", {}),
        "observations": observations["summary"],
        "per_sample_wall_ms": {item["sample_id"]: item.get("wall_ms") for item in observations.get("per_sample", [])},
        "determinism": _read(directory / "determinism.json") if (directory / "determinism.json").is_file() else None,
        "manifest_sha256": _read(directory / "run_manifest.json").get("manifest_sha256"),
    }


def corpus_problems(corpus: Path) -> list[str]:
    """Zbiór musi być niezależnym zbiorem końcowym (profil ``final-v2``, Task 20.2)."""
    from scripts import evaluate_mpzp_parser as ev

    manifest = json.loads(corpus.read_bytes())
    return ev.validate_corpus_profile(manifest, corpus.parent, "final-v2")


def evaluate_gate(
    results: Path,
    corpus: Path,
    review: Mapping[str, Any] | None,
    variance_report: Mapping[str, Any] | None,
) -> dict[str, Any]:
    engines = {name: _engine_summary(results / name) for name in ("legacy", "v3", "hybrid")}
    hybrid, v3 = engines["hybrid"], engines["v3"]
    blockers: list[str] = []
    problems = corpus_problems(corpus)
    if problems:
        blockers.append(f"zbiór nie jest niezależnym zbiorem końcowym (profil final-v2): {len(problems)} problemów, "
                        f"np. {problems[0]}")
    if hybrid is None:
        blockers.append("brak wyników silnika hybrid (bieg --live albo odtworzenie zapisanych odpowiedzi)")
    if v3 is None:
        blockers.append("brak wyników silnika v3 (punkt odniesienia opóźnienia i porównania)")
    if review is None or review.get("reviewed", 0) == 0:
        blockers.append("brak przeglądu ręcznego (arkusz review-sheet wypełniony przez człowieka)")
    if variance_report is None:
        blockers.append("brak badania zmienności (3 biegi na żywo)")

    measured: dict[str, Any] = {}
    counts: dict[str, Any] = {}
    if hybrid is not None:
        detection_counts = hybrid["counts"]
        found = sum(detection_counts.get(k, 0) for k in ("tp_exact", "tp_partial", "tp_wrong", "fp"))
        measured["precision"] = hybrid["precision"]["value"]
        counts["precision"] = f"{hybrid['precision']['numerator']}/{hybrid['precision']['denominator']}"
        measured["recall"] = hybrid["recall"]["value"]
        counts["recall"] = f"{hybrid['recall']['numerator']}/{hybrid['recall']['denominator']}"
        measured["source_consistent"] = hybrid["source_consistent"]["value"]
        counts["source_consistent"] = f"{hybrid['source_consistent']['numerator']}/{hybrid['source_consistent']['denominator']}"
        quotes = unverified_quotes(results / "hybrid", corpus)
        measured["unverified_quotes"] = quotes["unverified"]
        counts["unverified_quotes"] = f"{quotes['unverified']}/{quotes['checked']}"
        measured["zone_assignment_error_rate"] = round(hybrid["cross_zone_errors"] / found, 4) if found else None
        counts["zone_assignment_error_rate"] = f"{hybrid['cross_zone_errors']}/{found}"
        measured["ece"] = hybrid["ece"]
        counts["ece"] = f"n={_read(results / 'hybrid' / 'metrics.json')['overall']['calibration'].get('n_values')}"
        measured["cost_per_analysis_usd"] = hybrid["observations"].get("cost_usd_per_sample")
        counts["cost_per_analysis_usd"] = f"{hybrid['observations'].get('calls')} wywołań"
        if v3 is not None:
            deltas = [
                (wall - v3["per_sample_wall_ms"][sample]) / 1000.0
                for sample, wall in hybrid["per_sample_wall_ms"].items()
                if wall is not None and v3["per_sample_wall_ms"].get(sample) is not None
            ]
            value = p95(deltas)
            measured["additional_latency_p95_s"] = round(value, 3) if value is not None else None
            counts["additional_latency_p95_s"] = f"n={len(deltas)}"
    if review is not None:
        measured["manual_review_count"] = review.get("reviewed", 0)
        counts["manual_review_count"] = f"precyzja przeglądu {review.get('precision')} (CI95 {review.get('precision_ci95')})"

    rows = []
    for key, label, operator, threshold in CRITERIA:
        value = measured.get(key)
        rows.append({"criterion": key, "label": label, "operator": operator, "threshold": threshold,
                     "value": value, "counts": counts.get(key), "passed": _passes(value, operator, threshold)})
    unmeasured = [row["criterion"] for row in rows if row["passed"] is None]
    failed = [row["criterion"] for row in rows if row["passed"] is False]
    if blockers or unmeasured:
        decision = "NOT_DECIDABLE"
    elif failed:
        decision = "NO_GO"
    else:
        decision = "GO"
    return {
        "gate_version": GATE_VERSION,
        "criteria_frozen_on": FROZEN_ON,
        "evaluated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "corpus": str(corpus),
        "decision": decision,
        "blockers": blockers,
        "unmeasured": unmeasured,
        "failed": failed,
        "criteria": rows,
        "engines": {name: _without_samples(summary) for name, summary in engines.items()},
        "manual_review": review,
        "variance": variance_report,
        "disclaimer": ("Wynik jest rekomendacją opartą na pomiarze z jawnymi ograniczeniami (liczba gmin, formaty, "
                       "zmienność modelu, anotacje), a nie gwarancją jakości."),
    }


def _without_samples(summary: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if summary is None:
        return None
    return {key: value for key, value in summary.items() if key != "per_sample_wall_ms"}


def render_markdown(report: Mapping[str, Any]) -> str:
    def fmt(value: Any) -> str:
        return "—" if value is None else (f"{value:.4g}" if isinstance(value, float) else str(value))

    def ci(metric: Mapping[str, Any]) -> str:
        interval = metric.get("ci95") or {}
        low, high = interval.get("low"), interval.get("high")
        return f"[{low:.3f}; {high:.3f}]" if low is not None and high is not None else ""

    lines = [
        "# Bramka jakości parsera MPZP v3 (PV3-17)",
        "",
        f"Decyzja: **{report['decision']}** · kryteria zamrożone {report['criteria_frozen_on']} (ADR-012) · "
        f"{report['gate_version']} · {report['evaluated_at']}",
        "",
        f"> {report['disclaimer']}",
        "",
    ]
    if report["blockers"]:
        lines += ["## Braki uniemożliwiające decyzję", ""] + [f"- {item}" for item in report["blockers"]] + [""]
    lines += ["## Kryteria", "", "| Kryterium | Próg | Wynik | Liczniki | Spełnione |", "|---|---|---|---|---|"]
    for row in report["criteria"]:
        status = {True: "tak", False: "**nie**", None: "nie zmierzono"}[row["passed"]]
        lines.append(f"| {row['label']} | {row['operator']} {row['threshold']} | {fmt(row['value'])} | "
                     f"{row['counts'] or '—'} | {status} |")
    lines += ["", "## Silniki (całość)", "", "| Silnik | Precision | Recall | source_consistent | ECE |", "|---|---|---|---|---|"]
    for name, summary in report["engines"].items():
        if summary is None:
            lines.append(f"| {name} | — | — | — | — |")
            continue
        lines.append(
            f"| {name} | {fmt(summary['precision']['value'])} {ci(summary['precision'])} | "
            f"{fmt(summary['recall']['value'])} {ci(summary['recall'])} | "
            f"{fmt(summary['source_consistent']['value'])} | {fmt(summary['ece'])} |"
        )
    hybrid = report["engines"].get("hybrid")
    if hybrid:
        lines += ["", "## Odrzucenia według bramek (hybrid)", "", "```json",
                  json.dumps(hybrid.get("rejections"), ensure_ascii=False, indent=1), "```"]
    if report.get("variance"):
        variance_report = report["variance"]
        lines += ["", "## Zmienność modelu", "",
                  f"Pary z wartością modelu: {variance_report['pairs_with_model_values']}, identyczne we wszystkich "
                  f"{variance_report['runs']} biegach: {variance_report['identical_across_runs']} "
                  f"(zgodność {fmt(variance_report['agreement'])}, CI95 {variance_report['agreement_ci95']})."]
    return "\n".join(lines) + "\n"


def write_gate(report: Mapping[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "gate_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                                                 encoding="utf-8")
    (output_dir / "gate_report.md").write_text(render_markdown(report), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    sheet = commands.add_parser("review-sheet")
    sheet.add_argument("--results", type=Path, required=True)
    sheet.add_argument("--output", type=Path, required=True)
    sheet.add_argument("--n", type=int, default=100)
    sheet.add_argument("--seed", type=int, default=20261005)
    score = commands.add_parser("review-score")
    score.add_argument("--sheet", type=Path, required=True)
    score.add_argument("--output", type=Path)
    var = commands.add_parser("variance")
    var.add_argument("--runs", type=Path, nargs="+", required=True)
    var.add_argument("--output", type=Path)
    gate = commands.add_parser("gate")
    gate.add_argument("--results", type=Path, required=True, help="katalog z podkatalogami legacy/, v3/, hybrid/")
    gate.add_argument("--corpus", type=Path, default=BACKEND / "tests" / "fixtures" / "mpzp_evaluation" / "manifest.json")
    gate.add_argument("--review", type=Path)
    gate.add_argument("--variance", type=Path)
    gate.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "review-sheet":
        written = review_sheet(args.results, args.output, args.n, args.seed)
        print(f"review sheet: {written} ai_candidate values -> {args.output}")
        return 0 if written else 1
    if args.command == "review-score":
        result = review_score(args.sheet)
        _emit(result, args.output)
        return 0 if not result["problems"] else 1
    if args.command == "variance":
        _emit(variance(args.runs), args.output)
        return 0
    report = evaluate_gate(
        args.results, args.corpus,
        _read(args.review) if args.review else None,
        _read(args.variance) if args.variance else None,
    )
    write_gate(report, args.output_dir)
    print(f"gate decision: {report['decision']} -> {args.output_dir}")
    for blocker in report["blockers"]:
        print(f"  blocker: {blocker}")
    return {"GO": 0, "NO_GO": 2, "NOT_DECIDABLE": 3}[report["decision"]]


def _emit(payload: Mapping[str, Any], output: Path | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    sys.exit(main())
