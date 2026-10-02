"""Paired comparison of MPZP engines on the same (sample, zone, parameter) pairs (PV3-03).

Substance (outcomes, counts, p-values, bootstrap intervals) is deterministic: the
bootstrap resamples whole samples (clusters) with a seeded generator that uses only
``random.random``. Time, tokens and cost are observations and live in a separate
block that is never part of the determinism digest.
"""

from __future__ import annotations

import html
import math
import random
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

DEFAULT_RESAMPLES = 2000
DEFAULT_SEED = 603
MIN_CLUSTERS_FOR_BOOTSTRAP = 5
OUTCOMES = ("exact", "strict", "false_alarm")
OUTCOME_LABELS = {
    "exact": "wartość dokładna (tp_exact) wśród par z wartością w adnotacji",
    "strict": "wartość dokładna i z właściwego źródła (source-aware) wśród tych samych par",
    "false_alarm": "fałszywy alarm (fp) wśród par bez wartości w adnotacji",
}
HEADLINE_METRICS = (
    ("precision", "Precision"),
    ("recall", "Recall"),
    ("exact_value_accuracy_among_found", "Dokładność wartości (znalezione)"),
    ("zone_assignment_accuracy", "Przypisanie do strefy (wg wartości)"),
    ("source_consistent", "`source_consistent`"),
    ("strict_end_to_end", "Dokładność end-to-end ze źródłem"),
)


class ComparisonError(ValueError):
    """Engines were not evaluated on the same pairs."""


# --- statistics ---------------------------------------------------------------------


def percentile(values: Sequence[float], q: float) -> float | None:
    """Nearest-rank percentile (``q`` in 0..100); ``None`` for no data."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def mcnemar_exact(only_baseline: int, only_candidate: int) -> float | None:
    """Two-sided exact McNemar p from the discordant pairs; ``None`` without any."""
    n = only_baseline + only_candidate
    if n == 0:
        return None
    k = min(only_baseline, only_candidate)
    tail = sum(math.comb(n, i) for i in range(k + 1))
    return min(1.0, 2 * tail / 2**n)


def cluster_bootstrap_difference(
    clusters: Sequence[tuple[int, int, int]], resamples: int, seed: int
) -> dict[str, float | None]:
    """95% percentile interval of the rate difference (candidate − baseline).

    ``clusters`` holds ``(n_pairs, baseline_successes, candidate_successes)`` per sample.
    """
    total = sum(item[0] for item in clusters)
    if total == 0:
        return {"low": None, "high": None}
    rng = random.Random(seed)
    count = len(clusters)
    differences: list[float] = []
    for _ in range(resamples):
        n = base = cand = 0
        for _ in range(count):
            item = clusters[int(rng.random() * count)]
            n += item[0]
            base += item[1]
            cand += item[2]
        if n:
            differences.append((cand - base) / n)
    if not differences:
        return {"low": None, "high": None}
    return {"low": percentile(differences, 2.5), "high": percentile(differences, 97.5)}


# --- paired outcomes -----------------------------------------------------------------


def _key(row: Mapping[str, Any]) -> tuple[str, str, str]:
    return (row["sample_id"], row["zone_symbol"], row["parameter"])


def _outcomes(row: Mapping[str, Any]) -> dict[str, bool | None]:
    """``None`` = the pair is not eligible for that outcome."""
    has_required = bool(row["required_values"])
    exact = row["verdict"] == "tp_exact"
    return {
        "exact": exact if has_required else None,
        "strict": (exact and row.get("source_assignment", row.get("assignment")) != "wrong_source")
        if has_required
        else None,
        "false_alarm": (row["verdict"] == "fp") if not has_required else None,
    }


def paired_table(
    baseline_rows: Sequence[Mapping[str, Any]], candidate_rows: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    base = {_key(row): row for row in baseline_rows}
    cand = {_key(row): row for row in candidate_rows}
    if base.keys() != cand.keys():
        missing = sorted(set(base) ^ set(cand))[:3]
        raise ComparisonError(f"engines were not evaluated on the same pairs (e.g. {missing})")
    pairs = []
    for key in sorted(base):
        left, right = base[key], cand[key]
        pairs.append(
            {
                "sample_id": key[0],
                "zone_symbol": key[1],
                "parameter": key[2],
                "format": left["format"],
                "split": left["split"],
                "gmina": left.get("gmina"),
                "baseline": _outcomes(left),
                "candidate": _outcomes(right),
            }
        )
    return pairs


def _slice_stats(pairs: Sequence[Mapping[str, Any]], outcome: str, resamples: int, seed: int) -> dict[str, Any]:
    eligible = [p for p in pairs if p["baseline"][outcome] is not None]
    n = len(eligible)
    base_success = sum(bool(p["baseline"][outcome]) for p in eligible)
    cand_success = sum(bool(p["candidate"][outcome]) for p in eligible)
    only_base = sum(bool(p["baseline"][outcome]) and not p["candidate"][outcome] for p in eligible)
    only_cand = sum(bool(p["candidate"][outcome]) and not p["baseline"][outcome] for p in eligible)
    per_sample: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for p in eligible:
        cell = per_sample[p["sample_id"]]
        cell[0] += 1
        cell[1] += bool(p["baseline"][outcome])
        cell[2] += bool(p["candidate"][outcome])
    clusters = [tuple(cell) for _, cell in sorted(per_sample.items())]
    ci: dict[str, Any]
    if len(clusters) >= MIN_CLUSTERS_FOR_BOOTSTRAP:
        ci = {**cluster_bootstrap_difference(clusters, resamples, seed), "reason": None}  # type: ignore[arg-type]
    else:
        ci = {"low": None, "high": None, "reason": f"fewer than {MIN_CLUSTERS_FOR_BOOTSTRAP} samples"}
    return {
        "n_pairs": n,
        "n_samples": len(clusters),
        "baseline_successes": base_success,
        "candidate_successes": cand_success,
        "baseline_rate": base_success / n if n else None,
        "candidate_rate": cand_success / n if n else None,
        "difference": (cand_success - base_success) / n if n else None,
        "only_baseline": only_base,
        "only_candidate": only_cand,
        "mcnemar_exact_p": mcnemar_exact(only_base, only_cand),
        "bootstrap_ci95": ci,
    }


def _slices(pairs: Sequence[Mapping[str, Any]], resamples: int, seed: int) -> dict[str, Any]:
    return {outcome: _slice_stats(pairs, outcome, resamples, seed) for outcome in OUTCOMES}


def compare_pair(
    baseline_rows: Sequence[Mapping[str, Any]],
    candidate_rows: Sequence[Mapping[str, Any]],
    resamples: int = DEFAULT_RESAMPLES,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    pairs = paired_table(baseline_rows, candidate_rows)
    result: dict[str, Any] = {"n_pairs_total": len(pairs), "overall": _slices(pairs, resamples, seed)}
    for dimension in ("format", "gmina", "split"):
        values = sorted({str(p[dimension]) for p in pairs})
        result[f"by_{dimension}"] = {
            value: _slices([p for p in pairs if str(p[dimension]) == value], resamples, seed) for value in values
        }
    return result


# --- observations (time and cost; never part of the substantive digest) ---------------


def summarize_observations(observations: Mapping[str, Any]) -> dict[str, Any]:
    per_sample = observations["per_sample"]

    def total(field: str) -> float | int | None:
        values = [item[field] for item in per_sample if item.get(field) is not None]
        return sum(values) if values else None

    wall = [item["wall_ms"] for item in per_sample if item.get("wall_ms") is not None]
    model = [item["latency_ms"] for item in per_sample if item.get("latency_ms") is not None]
    cost = total("cost_usd")
    zones = sum(item["zones"] for item in per_sample)
    documents = len({item["document_id"] for item in per_sample})
    return {
        "samples": len(per_sample),
        "documents": documents,
        "zones": zones,
        "calls": total("calls"),
        "input_tokens": total("input_tokens"),
        "output_tokens": total("output_tokens"),
        "wall_ms_p50": percentile(wall, 50),
        "wall_ms_p95": percentile(wall, 95),
        "model_latency_ms_p50": percentile(model, 50),
        "model_latency_ms_p95": percentile(model, 95),
        "cost_usd_total": cost,
        "cost_usd_per_sample": cost / len(per_sample) if cost is not None and per_sample else None,
        "cost_usd_per_zone": cost / zones if cost is not None and zones else None,
        "cost_usd_per_document": cost / documents if cost is not None and documents else None,
    }


def rejection_gate_table(records: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    """Gate → how many candidates were rejected, split by whether the value was right."""
    table: dict[str, Counter[str]] = defaultdict(Counter)
    for record in records:
        gate = str(record["gate"])
        table[gate]["rejected"] += 1
        correct = record.get("value_was_correct")
        table[gate]["value_correct" if correct is True else "value_wrong" if correct is False else "value_unknown"] += 1
    return {gate: dict(sorted(counter.items())) for gate, counter in sorted(table.items())}


# --- assembling and rendering ----------------------------------------------------------


def build_comparison(
    engine_results: Mapping[str, Mapping[str, Any]],
    baseline: str,
    resamples: int = DEFAULT_RESAMPLES,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """``engine_results[name]`` needs ``rows``, ``metrics``, ``observations``, ``rejection_records``."""
    if baseline not in engine_results:
        raise ComparisonError(f"baseline {baseline!r} was not evaluated")
    headline: dict[str, Any] = {}
    for name, result in engine_results.items():
        metrics = result["metrics"]
        headline[name] = {
            "overall": metrics["overall"]["detection"],
            "by_format": {k: v["detection"] for k, v in metrics["by_format"].items()},
            "by_gmina": {k: v["detection"] for k, v in metrics.get("by_gmina", {}).items()},
        }
    paired = {
        name: compare_pair(engine_results[baseline]["rows"], result["rows"], resamples, seed)
        for name, result in engine_results.items()
        if name != baseline
    }
    return {
        "baseline": baseline,
        "engines": list(engine_results),
        "bootstrap": {"method": "cluster (sample) percentile", "resamples": resamples, "seed": seed},
        "headline": headline,
        "paired": paired,
        "rejection_gates": {
            name: rejection_gate_table(result.get("rejection_records", [])) for name, result in engine_results.items()
        },
    }


def build_observation_summary(engine_results: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    return {name: summarize_observations(result["observations"]) for name, result in engine_results.items()}


def _num(metric: Mapping[str, Any] | None) -> str:
    if metric is None or metric.get("value") is None:
        reason = (metric or {}).get("reason") or "brak"
        return f"null ({reason})"
    return f"{metric['value']:.3f} ({metric['numerator']}/{metric['denominator']})"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.3f}"


def _p(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4f}"


def _interval(ci: Mapping[str, Any]) -> str:
    return "n/a" if ci["low"] is None else f"[{ci['low']:+.3f}; {ci['high']:+.3f}]"


def _fmt(value: float | int | None, digits: int = 0) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}" if digits else f"{value:,.0f}".replace(",", " ")


def render_comparison_report(
    comparison: Mapping[str, Any], observations: Mapping[str, Mapping[str, Any]], run_info: Mapping[str, Any]
) -> str:
    base = comparison["baseline"]
    boot = comparison["bootstrap"]
    lines = [
        "# Porównanie silników parsera MPZP (PV3-03)", "",
        "Plik jest generowany z wyników silników; nie jest edytowany ręcznie. Porównanie jest sparowane: "
        "każdy silnik oceniono na tych samych parach (próbka, strefa, parametr) tego samego manifestu.", "",
        f"- silnik odniesienia: `{base}`; silniki: {', '.join(f'`{n}`' for n in comparison['engines'])};",
        f"- `annotations_sha256`: `{run_info['annotations_sha256']}`; `corpus_sha256`: `{run_info['corpus_sha256']}`;",
        f"- przedziały: bootstrap po próbkach (klastrach), {boot['resamples']} losowań, ziarno {boot['seed']}; "
        f"test McNemara dokładny, dwustronny; przy mniej niż {MIN_CLUSTERS_FOR_BOOTSTRAP} próbkach w wycinku przedział nie jest podawany.", "",
        "## Metryki każdego silnika", "",
        "| Silnik | " + " | ".join(label for _, label in HEADLINE_METRICS) + " |",
        "|---|" + "---|" * len(HEADLINE_METRICS),
    ]
    for name in comparison["engines"]:
        overall = comparison["headline"][name]["overall"]
        lines.append(f"| `{name}` | " + " | ".join(_num(overall.get(key)) for key, _ in HEADLINE_METRICS) + " |")
    lines.append("")
    for dimension, title in (("format", "formatu"), ("gmina", "gminy")):
        lines.extend([f"### Metryki według {title}", ""])
        key = f"by_{dimension}"
        slice_names = sorted({s for n in comparison["engines"] for s in comparison["headline"][n][key]})
        lines.append("| Wycinek | Silnik | Precision | Recall | `source_consistent` | End-to-end ze źródłem |")
        lines.append("|---|---|---|---|---|---|")
        for slice_name in slice_names:
            for name in comparison["engines"]:
                detection = comparison["headline"][name][key].get(slice_name)
                if detection is None:
                    continue
                lines.append(
                    f"| `{slice_name}` | `{name}` | {_num(detection['precision'])} | {_num(detection['recall'])} | "
                    f"{_num(detection.get('source_consistent'))} | {_num(detection.get('strict_end_to_end'))} |"
                )
        lines.append("")
    if not comparison["paired"]:
        lines.extend(["## Porównanie sparowane", "", "Oceniono jeden silnik; porównanie sparowane wymaga co najmniej dwóch.", ""])
    for name, paired in comparison["paired"].items():
        lines.extend([f"## Porównanie sparowane: `{base}` → `{name}`", "", f"Par łącznie: {paired['n_pairs_total']}. "
                      "Różnica = odsetek `" + name + "` − odsetek `" + base + "`; „tylko odniesienie / tylko kandydat” to pary niezgodne (b, c).", ""])
        for outcome in OUTCOMES:
            lines.extend([f"### {OUTCOME_LABELS[outcome]}", ""])
            lines.append("| Wycinek | pary | próbki | odniesienie | kandydat | różnica | 95% CI | b / c | p McNemar |")
            lines.append("|---|---:|---:|---:|---:|---:|---|---|---:|")
            rows = [("overall", paired["overall"][outcome])]
            for dimension in ("format", "gmina", "split"):
                rows.extend((f"{dimension}={v}", s[outcome]) for v, s in paired[f"by_{dimension}"].items())
            for label, stats in rows:
                if stats["n_pairs"] == 0:
                    continue
                lines.append(
                    f"| `{label}` | {stats['n_pairs']} | {stats['n_samples']} | "
                    f"{stats['baseline_successes']}/{stats['n_pairs']} | {stats['candidate_successes']}/{stats['n_pairs']} | "
                    f"{_pct(stats['difference'])} | {_interval(stats['bootstrap_ci95'])} | "
                    f"{stats['only_baseline']} / {stats['only_candidate']} | {_p(stats['mcnemar_exact_p'])} |"
                )
            lines.append("")
    lines.extend(["## Bramki odrzuceń kandydatów", ""])
    any_gate = False
    for name, table in comparison["rejection_gates"].items():
        if not table:
            lines.append(f"- `{name}`: silnik nie zgłosił odrzuceń (brak bramek albo żaden kandydat nie został odrzucony).")
            continue
        any_gate = True
        lines.extend([f"### `{name}`", "", "| Bramka | odrzucone | wartość była poprawna | wartość była błędna | bez wartości liczbowej |", "|---|---:|---:|---:|---:|"])
        for gate, counts in table.items():
            lines.append(
                f"| `{gate}` | {counts.get('rejected', 0)} | {counts.get('value_correct', 0)} | "
                f"{counts.get('value_wrong', 0)} | {counts.get('value_unknown', 0)} |"
            )
        lines.append("")
    if not any_gate:
        lines.append("")
    lines.extend(
        [
            "„Wartość była poprawna” oznacza odrzucenie wartości zgodnej z adnotacją (koszt bramki dla recall); "
            "„błędna” — słuszne odrzucenie.", "",
            "## Koszt i opóźnienie (obserwacje, nie wynik merytoryczny)", "",
            "Wartości zależą od środowiska i biegu; w trybie odtwarzania tokeny, opóźnienie modelu i koszt pochodzą z "
            "nagrania odpowiedzi, a nie z bieżącego wywołania. `—` oznacza „silnik nie raportuje”, nie zero.", "",
            "| Silnik | wywołania | tokeny wejściowe | tokeny wyjściowe | czas ścienny p50 / p95 [ms] | opóźnienie modelu p50 / p95 [ms] | koszt łącznie [USD] | na dokument | na strefę |",
            "|---|---:|---:|---:|---|---|---:|---:|---:|",
        ]
    )
    for name in comparison["engines"]:
        s = summarize_observations(observations[name])
        lines.append(
            f"| `{name}` | {_fmt(s['calls'])} | {_fmt(s['input_tokens'])} | {_fmt(s['output_tokens'])} | "
            f"{_fmt(s['wall_ms_p50'], 1)} / {_fmt(s['wall_ms_p95'], 1)} | "
            f"{_fmt(s['model_latency_ms_p50'], 1)} / {_fmt(s['model_latency_ms_p95'], 1)} | "
            f"{_fmt(s['cost_usd_total'], 4)} | {_fmt(s['cost_usd_per_document'], 5)} | {_fmt(s['cost_usd_per_zone'], 5)} |"
        )
    lines.append("")
    return "\n".join(lines)


def comparison_svg(comparison: Mapping[str, Any]) -> str:
    """Strict (source-aware) end-to-end rate per engine and format, as grouped bars."""
    engines = comparison["engines"]
    formats = sorted({f for n in engines for f in comparison["headline"][n]["by_format"]})
    colours = ("#2c6fbb", "#c0392b", "#27ae60", "#8e44ad")
    width, height, left, bar_h = 640, 70 + len(formats) * (len(engines) * 18 + 14), 150, 14
    body = [
        '<text x="10" y="24" font-size="15" font-weight="bold">Dokładność end-to-end ze źródłem według formatu</text>',
    ]
    for index, name in enumerate(engines):
        body.append(f'<rect x="{left + index * 120}" y="34" width="10" height="10" fill="{colours[index % 4]}"/>')
        body.append(f'<text x="{left + index * 120 + 14}" y="43" font-size="12">{html.escape(name)}</text>')
    y = 62
    for fmt in formats:
        body.append(f'<text x="10" y="{y + 12}" font-size="12">{html.escape(fmt)}</text>')
        for index, name in enumerate(engines):
            metric = comparison["headline"][name]["by_format"].get(fmt, {}).get("strict_end_to_end")
            value = (metric or {}).get("value")
            length = 0.0 if value is None else 400 * value
            label = "n/a" if value is None else f"{value:.0%}"
            body.append(f'<rect x="{left}" y="{y}" width="{length:.1f}" height="{bar_h}" fill="{colours[index % 4]}"/>')
            body.append(f'<text x="{left + length + 6:.1f}" y="{y + 11}" font-size="11">{label}</text>')
            y += 18
        y += 14
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        '<rect width="100%" height="100%" fill="white"/>' + "".join(body) + "</svg>\n"
    )
