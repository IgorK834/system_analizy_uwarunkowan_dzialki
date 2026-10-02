"""Inter-annotator agreement for the MPZP annotation protocol (PV3-02).

Two people annotate the same samples independently, using the same schema as the corpus
manifest (``samples[*].zones[*].annotations[*]``). This module compares them on the unit
of the evaluation, the pair (sample, zone, parameter), and lists every disagreement so it
can be adjudicated in a log. It never decides which annotator is right.

Reported (all with numerator and denominator, none rounded away):

* **presence** — does the annotator give a required zone-section value for the pair?
  Observed agreement and Cohen's kappa over all catalog pairs of the compared zones;
* **exact value** — among pairs where both give a required value, are the value sets equal?
* **all values** — same, over every annotated value (any status, any applicability).

Disagreement types: ``zone_only_one`` (a zone annotated by one person only), ``presence``,
``value`` (different required values), ``status_or_scope`` (same values, different
required / general-clause classification).
"""

from __future__ import annotations

import csv
import io
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

PRECISION = 6
ADJUDICATION_COLUMNS = (
    "sample_id", "zone_symbol", "parameter", "type", "annotator_a", "annotator_b",
    "resolution", "resolved_by", "rationale", "resolved_at",
)
RESOLUTIONS = ("a", "b", "both_acceptable", "other")


def _values(annotations: Sequence[Mapping[str, Any]], *, required_only: bool) -> list[float]:
    values = []
    for item in annotations:
        if required_only and (
            item.get("status", "required") != "required" or item.get("applicability", "zone_section") != "zone_section"
        ):
            continue
        values.append(round(float(item["normalized_value"]), PRECISION))
    return sorted(set(values))


def _zone_map(sample: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {zone["symbol"]: zone for zone in sample.get("zones", [])}


def _by_parameter(zone: Mapping[str, Any], parameter: str) -> list[Mapping[str, Any]]:
    return [item for item in zone.get("annotations", []) if item["parameter"] == parameter]


def kappa(both: int, only_a: int, only_b: int, neither: int) -> float | None:
    """Cohen's kappa for a 2x2 presence table; ``None`` when chance agreement is total."""
    total = both + only_a + only_b + neither
    if total == 0:
        return None
    observed = (both + neither) / total
    a_yes, b_yes = (both + only_a) / total, (both + only_b) / total
    expected = a_yes * b_yes + (1 - a_yes) * (1 - b_yes)
    if expected >= 1.0:
        return None
    return (observed - expected) / (1 - expected)


def _ratio(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "value": numerator / denominator if denominator else None,
        "numerator": numerator,
        "denominator": denominator,
    }


def compute_agreement(
    primary: Sequence[Mapping[str, Any]],
    second: Sequence[Mapping[str, Any]],
    catalog: Sequence[str],
) -> dict[str, Any]:
    """Agreement and the full list of disagreements for the samples both annotated."""
    second_by_id = {sample["sample_id"]: sample for sample in second}
    compared = [sample for sample in primary if sample["sample_id"] in second_by_id]
    table: Counter[tuple[bool, bool]] = Counter()
    exact_num = exact_den = all_num = all_den = 0
    disagreements: list[dict[str, Any]] = []
    for sample in compared:
        sid = sample["sample_id"]
        zones_a, zones_b = _zone_map(sample), _zone_map(second_by_id[sid])
        for symbol in sorted(set(zones_a) | set(zones_b)):
            if symbol not in zones_a or symbol not in zones_b:
                disagreements.append({
                    "sample_id": sid, "zone_symbol": symbol, "parameter": "*", "type": "zone_only_one",
                    "annotator_a": "present" if symbol in zones_a else "absent",
                    "annotator_b": "present" if symbol in zones_b else "absent",
                })
                continue
            for parameter in catalog:
                items_a, items_b = _by_parameter(zones_a[symbol], parameter), _by_parameter(zones_b[symbol], parameter)
                req_a, req_b = _values(items_a, required_only=True), _values(items_b, required_only=True)
                all_a, all_b = _values(items_a, required_only=False), _values(items_b, required_only=False)
                present_a, present_b = bool(req_a), bool(req_b)
                table[(present_a, present_b)] += 1
                kind = None
                if present_a != present_b:
                    kind = "presence"
                elif present_a:
                    exact_den += 1
                    if req_a == req_b:
                        exact_num += 1
                    else:
                        kind = "value"
                if all_a or all_b:
                    all_den += 1
                    if all_a == all_b:
                        all_num += 1
                    elif kind is None:
                        kind = "value"
                if all_a == all_b and (kind is not None or _labelled(items_a) != _labelled(items_b)):
                    kind = "status_or_scope" if _labelled(items_a) != _labelled(items_b) else kind
                if kind is not None:
                    disagreements.append({
                        "sample_id": sid, "zone_symbol": symbol, "parameter": parameter, "type": kind,
                        "annotator_a": _describe(items_a), "annotator_b": _describe(items_b),
                    })
    pairs = sum(table.values())
    both, only_a = table[(True, True)], table[(True, False)]
    only_b, neither = table[(False, True)], table[(False, False)]
    return {
        "samples_compared": [sample["sample_id"] for sample in compared],
        "pairs": pairs,
        "presence": {"agreement": _ratio(both + neither, pairs), "cohen_kappa": kappa(both, only_a, only_b, neither),
                     "table": {"both": both, "only_a": only_a, "only_b": only_b, "neither": neither}},
        "exact_required_value": _ratio(exact_num, exact_den),
        "all_values": _ratio(all_num, all_den),
        "disagreements": sorted(disagreements, key=lambda d: (d["sample_id"], d["zone_symbol"], d["parameter"])),
    }


def _labelled(items: Sequence[Mapping[str, Any]]) -> list[tuple[float, str, str]]:
    return sorted(
        (round(float(i["normalized_value"]), PRECISION), i.get("status", "required"), i.get("applicability", "zone_section"))
        for i in items
    )


def _describe(items: Sequence[Mapping[str, Any]]) -> str:
    if not items:
        return "—"
    parts = sorted(
        f"{round(float(i['normalized_value']), PRECISION):g}/{i.get('status', 'required')}/{i.get('applicability', 'zone_section')}"
        for i in items
    )
    return "; ".join(parts)


def adjudication_skeleton(disagreements: Sequence[Mapping[str, Any]]) -> str:
    """CSV with one row per disagreement and empty resolution columns."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(ADJUDICATION_COLUMNS), lineterminator="\n")
    writer.writeheader()
    for item in disagreements:
        writer.writerow({column: item.get(column, "") for column in ADJUDICATION_COLUMNS})
    return buffer.getvalue()


def check_adjudication(log_text: str, disagreements: Sequence[Mapping[str, Any]]) -> list[str]:
    """Every disagreement needs a resolved row (resolution, who, why); no foreign rows."""
    rows = list(csv.DictReader(io.StringIO(log_text)))
    problems: list[str] = []
    expected = {(d["sample_id"], d["zone_symbol"], d["parameter"]) for d in disagreements}
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        key = (row.get("sample_id", ""), row.get("zone_symbol", ""), row.get("parameter", ""))
        if key not in expected:
            problems.append(f"adjudication row {key} is not a disagreement of the report")
            continue
        if key in seen:
            problems.append(f"adjudication row {key} is duplicated")
        seen.add(key)
        if row.get("resolution") not in RESOLUTIONS:
            problems.append(f"adjudication row {key}: resolution must be one of {', '.join(RESOLUTIONS)}")
        if not (row.get("resolved_by") or "").strip():
            problems.append(f"adjudication row {key}: resolved_by is empty")
        if not (row.get("rationale") or "").strip():
            problems.append(f"adjudication row {key}: rationale is empty")
    for key in sorted(expected - seen):
        problems.append(f"disagreement {key} has no adjudication row")
    return problems


def check_resolutions_applied(
    log_text: str,
    final: Sequence[Mapping[str, Any]],
    first: Sequence[Mapping[str, Any]],
    second: Sequence[Mapping[str, Any]],
) -> list[str]:
    """Resolutions ``a`` / ``b`` must be what the frozen annotations actually say.

    A log that is not reflected in the corpus would be decoration; ``both_acceptable`` and
    ``other`` are judgement calls and are only required to be explained (checked elsewhere).
    """
    problems: list[str] = []
    final_by_id = {sample["sample_id"]: _zone_map(sample) for sample in final}
    sources = {"a": {s["sample_id"]: _zone_map(s) for s in first}, "b": {s["sample_id"]: _zone_map(s) for s in second}}
    for row in csv.DictReader(io.StringIO(log_text)):
        choice = row.get("resolution")
        if choice not in ("a", "b") or row.get("parameter") in (None, "", "*"):
            continue
        sid, symbol, parameter = row["sample_id"], row["zone_symbol"], row["parameter"]
        picked_zone = sources[choice].get(sid, {}).get(symbol)
        final_zone = final_by_id.get(sid, {}).get(symbol)
        if picked_zone is None or final_zone is None:
            problems.append(f"resolution {choice} for {(sid, symbol, parameter)}: the zone is missing in the annotation or the corpus")
            continue
        wanted = _labelled(_by_parameter(picked_zone, parameter))
        actual = _labelled(_by_parameter(final_zone, parameter))
        if wanted != actual:
            problems.append(f"resolution {choice} for {(sid, symbol, parameter)} is not reflected in the frozen annotations")
    return problems
