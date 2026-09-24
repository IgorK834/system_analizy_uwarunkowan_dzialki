#!/usr/bin/env python3
"""Offline evaluation of the real reference corpus (BK-004).

The runner consumes frozen source observations and parcel geometry. Expected
values are passed only to the comparison layer, never to the analysis adapter.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
import os
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shapely.geometry import shape

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_IMPORT_CWD = Path.cwd()
try:
    # app.core.settings reads a relative .env during the production geometry
    # import. Keep that import anchored in backend, then restore CLI path rules.
    os.chdir(BACKEND_DIR)
    from app.modules.analysis.application.ports import AnalysisCaseRunner  # noqa: E402
    from app.services.geometry import calculate_geometry_metrics  # noqa: E402
    from tests.parcel_fixtures_config import (  # noqa: E402
        ReferenceCorpusError,
        validate_reference_corpus,
    )
finally:
    os.chdir(_IMPORT_CWD)

SCHEMA_VERSION = "1.0.0"
DEFAULT_SEED = 20260923
DEFAULT_CORPUS = BACKEND_DIR / "tests" / "fixtures" / "reference_corpus" / "manifest.json"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "evaluation" / "results" / "reference-corpus"

COMPLETENESS_PATHS = (
    "geometry.area",
    "geometry.perimeter",
    "pog.legal_status",
    "pog.zone_count",
    "ouz.intersection_area",
    "ouz.relation",
    "ouz.share",
    "mpzp.mode",
    "mpzp.plan_id",
    "mpzp.zone_count",
    "flood.feature_count",
    "flood.relation",
    "nature.feature_count",
    "nature.relation",
    "terrain.minimum",
    "terrain.maximum",
    "terrain.relief",
    "terrain.class",
)


class EvaluationError(ValueError):
    """The corpus or evaluation contract is invalid."""


class FrozenArtifactAnalysisRunner:
    """Production-port adapter backed only by frozen, official observations."""

    def __init__(self, clock: Callable[[], float] = time.perf_counter) -> None:
        self._clock = clock
        self._seen_sources: set[str] = set()

    def analyze(self, case_input: Mapping[str, object]) -> Mapping[str, object]:
        started = self._clock()
        feature = _require_mapping(case_input.get("geometry"), "geometry")
        observations = _require_mapping(case_input.get("observations"), "observations")
        geometry_data = _require_mapping(feature.get("geometry"), "geometry.geometry")
        parcel = shape(geometry_data)
        if parcel.is_empty or not parcel.is_valid:
            raise EvaluationError("Parcel geometry must be a non-empty valid polygon.")
        geometry_metrics = calculate_geometry_metrics(parcel)

        properties = _require_mapping(feature.get("properties"), "geometry.properties")
        parcel_identifier = str(properties.get("parcel_identifier", ""))
        if not parcel_identifier:
            raise EvaluationError("Frozen parcel geometry has no parcel identifier.")

        sections = {
            "geometry": {
                "status": "available",
                "values": {
                    "area": geometry_metrics.area_sqm,
                    "perimeter": geometry_metrics.perimeter_m,
                },
            },
            "pog": _pog_section(observations.get("pog_ouz")),
            "ouz": _ouz_section(observations.get("pog_ouz")),
            "mpzp": _mpzp_section(
                observations.get("mpzp_discovery"),
                observations.get("krakow_mpzp_derived"),
            ),
            "flood": _risk_section(observations.get("isok"), "flood"),
            "nature": _risk_section(observations.get("gdos"), "nature"),
            "terrain": _terrain_section(observations.get("nmt")),
        }
        statuses = {section["status"] for section in sections.values()}
        manual_review_required = "manual_review" in statuses
        analysis_status = (
            "complete" if statuses == {"available"} else "partial"
        )

        source_ids = {
            str(source_id)
            for source_id in case_input.get("source_ids", [])  # type: ignore[arg-type]
            if str(source_id)
        }
        cache_status = "hit" if source_ids <= self._seen_sources else "miss"
        self._seen_sources.update(source_ids)
        elapsed_ms = max(0.0, (self._clock() - started) * 1000.0)
        return {
            "parcel_identifier": parcel_identifier,
            "status": analysis_status,
            "manual_review_required": manual_review_required,
            "cache_status": cache_status,
            "duration_ms": elapsed_ms,
            "sections": sections,
        }


def _require_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise EvaluationError(f"{label} must be an object.")
    return value


def _unknown_section(keys: Sequence[str]) -> dict[str, object]:
    return {"status": "unknown", "values": {key: None for key in keys}}


def _pog_section(raw: object) -> dict[str, object]:
    if not isinstance(raw, Mapping):
        return _unknown_section(("legal_status", "zone_count", "zones"))
    zones = [
        {
            "area": zone.get("area_sqm"),
            "local_id": zone.get("local_id"),
            "share": zone.get("share_pct"),
            "symbol": zone.get("symbol"),
        }
        for zone in raw.get("zones", [])
        if isinstance(zone, Mapping)
    ]
    return {
        "status": "available",
        "values": {
            "legal_status": "legal_force",
            "zone_count": len(zones),
            "zones": zones,
        },
    }


def _ouz_section(raw: object) -> dict[str, object]:
    if not isinstance(raw, Mapping):
        return _unknown_section(("intersection_area", "relation", "share"))
    area = raw.get("ouz_area_sqm")
    share = raw.get("ouz_share_pct")
    if not isinstance(area, (int, float)) or not isinstance(share, (int, float)):
        return _unknown_section(("intersection_area", "relation", "share"))
    if area == 0:
        relation, status = "outside", "available"
    elif area < 1.0:
        relation, status = "boundary", "manual_review"
    elif share >= 99.99:
        relation, status = "inside", "available"
    else:
        relation, status = "partial", "available"
    return {
        "status": status,
        "values": {
            "intersection_area": float(area),
            "relation": relation,
            "share": float(share),
        },
    }


def _mpzp_section(discovery_raw: object, derived_raw: object) -> dict[str, object]:
    discovery = discovery_raw if isinstance(discovery_raw, Mapping) else {}
    derived = derived_raw if isinstance(derived_raw, Mapping) else None
    if derived is not None:
        layers = [layer for layer in derived.get("layers", []) if isinstance(layer, Mapping)]
        intersections = layers[0].get("intersections", []) if layers else []
        zones: list[dict[str, object]] = []
        for intersection in intersections:
            if not isinstance(intersection, Mapping):
                continue
            area = intersection.get("area_sqm")
            if not isinstance(area, (int, float)) or area < 1.0:
                continue
            attributes = intersection.get("attributes")
            attributes = attributes if isinstance(attributes, Mapping) else {}
            zones.append(
                {
                    "area": float(area),
                    "share": intersection.get("share_pct"),
                    "symbol": attributes.get("Oznaczenie"),
                }
            )
        return {
            "status": "available",
            "values": {
                "document_url": discovery.get("uchwala_url"),
                "mode": "vector",
                "plan_id": discovery.get("plan_id"),
                "zone_count": len(zones),
                "zones": zones,
            },
        }

    if discovery.get("status") == "found":
        symbols = discovery.get("candidate_zone_symbols")
        symbols = symbols if isinstance(symbols, list) else []
        mode = "raster_manual" if discovery.get("brak_wektorow") else "vector_discovery"
        return {
            "status": "manual_review",
            "values": {
                "document_url": discovery.get("uchwala_url"),
                "mode": mode,
                "plan_id": discovery.get("plan_id"),
                "zone_count": len(symbols) if symbols else None,
                "zones": [
                    {"area": None, "share": None, "symbol": symbol}
                    for symbol in symbols
                ],
            },
        }
    return _unknown_section(("document_url", "mode", "plan_id", "zone_count", "zones"))


def _risk_section(raw: object, kind: str) -> dict[str, object]:
    if not isinstance(raw, Mapping) or raw.get("status") != "available":
        return _unknown_section(("feature_count", "features", "relation"))
    raw_features = raw.get("features")
    raw_features = raw_features if isinstance(raw_features, list) else []
    features = []
    for feature in raw_features:
        if not isinstance(feature, Mapping):
            continue
        area_ratio = feature.get("area_ratio")
        features.append(
            {
                "area": feature.get("intersection_area_sqm"),
                "class": feature.get("probability_class") or feature.get("name"),
                "severity": feature.get("severity"),
                "share": (
                    float(area_ratio) * 100.0
                    if isinstance(area_ratio, (int, float))
                    else None
                ),
                "type": feature.get("type"),
            }
        )
    return {
        "status": "available",
        "values": {
            "feature_count": len(features),
            "features": features,
            "relation": "none" if not features else "intersection",
        },
    }


def _terrain_section(raw: object) -> dict[str, object]:
    if not isinstance(raw, Mapping) or raw.get("status") != "available":
        return _unknown_section(("minimum", "maximum", "relief", "class"))
    relief = raw.get("relief_m")
    terrain_class = None
    if isinstance(relief, (int, float)):
        terrain_class = "flat" if relief <= 2.0 else "moderate" if relief <= 10.0 else "relief"
    return {
        "status": "available",
        "values": {
            "minimum": raw.get("min_elevation_m"),
            "maximum": raw.get("max_elevation_m"),
            "relief": relief,
            "class": terrain_class,
        },
    }


def load_corpus(path: Path) -> tuple[dict[str, Any], str]:
    """Load a corpus by explicit path and verify files and SHA-256 hashes."""

    try:
        payload = path.read_bytes()
        manifest = json.loads(payload)
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationError(f"Cannot load corpus {path}: {exc}") from exc
    errors = validate_reference_corpus(manifest, path.parent)
    if errors:
        raise EvaluationError("Invalid corpus: " + "; ".join(errors))
    return manifest, hashlib.sha256(payload).hexdigest()


def _artifact_for_case(
    manifest: Mapping[str, Any], case: Mapping[str, Any], media_type: str
) -> Path:
    matches = []
    for artifact_id in case["artifact_ids"]:
        artifact = manifest["artifacts"][artifact_id]
        if artifact["media_type"] == media_type:
            matches.append(Path(artifact["path"]))
    if len(matches) != 1:
        raise EvaluationError(
            f"{case['case_id']}: expected one {media_type} artifact, found {len(matches)}."
        )
    return matches[0]


def _source_ids(observations: Mapping[str, Any]) -> list[str]:
    mapping = {
        "gdos": "gdos",
        "isok": "isok",
        "mpzp_discovery": "kimpzp",
        "krakow_mpzp_derived": "krakow_mpzp",
        "nmt": "nmt",
        "pog_ouz": "ru_pog",
    }
    return ["uldk", *(source for key, source in mapping.items() if observations.get(key) is not None)]


def build_case_input(
    manifest: Mapping[str, Any], corpus_dir: Path, case: Mapping[str, Any]
) -> dict[str, object]:
    """Build adapter input without copying the expected result."""

    geometry_path = corpus_dir / _artifact_for_case(
        manifest, case, "application/geo+json"
    )
    evidence_path = corpus_dir / _artifact_for_case(
        manifest, case, "application/json"
    )
    geometry = json.loads(geometry_path.read_text(encoding="utf-8"))
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    observations = _require_mapping(evidence.get("observations"), "evidence.observations")
    return {
        "case_id": case["case_id"],
        "parcel_identifier": case["parcel_identifier"],
        "geometry": geometry,
        "observations": observations,
        "source_ids": _source_ids(observations),
    }


def _unwrap(value: object) -> object:
    if isinstance(value, Mapping):
        if set(value) == {"value", "unit"}:
            return value["value"]
        return {key: _unwrap(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_unwrap(child) for child in value]
    return value


def _expected_sections(case: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        name: {"status": section["status"], "values": _unwrap(section["values"])}
        for name, section in case["expected"].items()
    }


def _metric(
    value: float | int | None,
    unit: str,
    *,
    numerator: int | None = None,
    denominator: int | None = None,
    reason: str | None = None,
) -> dict[str, object]:
    return {
        "value": value,
        "unit": unit,
        "numerator": numerator,
        "denominator": denominator,
        "reason": reason,
    }


def _ratio(numerator: int, denominator: int, unit: str = "ratio") -> dict[str, object]:
    if denominator == 0:
        return _metric(
            None,
            unit,
            numerator=numerator,
            denominator=denominator,
            reason="no_eligible_cases",
        )
    return _metric(
        numerator / denominator,
        unit,
        numerator=numerator,
        denominator=denominator,
    )


def _symbols(section: Mapping[str, Any]) -> set[str]:
    values = section.get("values")
    values = values if isinstance(values, Mapping) else {}
    zones = values.get("zones")
    zones = zones if isinstance(zones, list) else []
    return {
        str(zone["symbol"])
        for zone in zones
        if isinstance(zone, Mapping) and zone.get("symbol") not in {None, ""}
    }


def _share_errors(
    expected: Mapping[str, Any], actual: Mapping[str, Any]
) -> list[float]:
    expected_values = expected.get("values")
    actual_values = actual.get("values")
    if not isinstance(expected_values, Mapping) or not isinstance(actual_values, Mapping):
        return []
    expected_zones = expected_values.get("zones")
    actual_zones = actual_values.get("zones")
    if not isinstance(expected_zones, list) or not isinstance(actual_zones, list):
        return []
    actual_by_symbol = {
        zone.get("symbol"): zone
        for zone in actual_zones
        if isinstance(zone, Mapping) and zone.get("symbol")
    }
    errors = []
    for expected_zone in expected_zones:
        if not isinstance(expected_zone, Mapping):
            continue
        actual_zone = actual_by_symbol.get(expected_zone.get("symbol"))
        expected_share = expected_zone.get("share")
        actual_share = actual_zone.get("share") if isinstance(actual_zone, Mapping) else None
        if isinstance(expected_share, (int, float)) and isinstance(actual_share, (int, float)):
            errors.append(abs(float(expected_share) - float(actual_share)))
    return errors


def _relation_as_bool(value: object) -> bool | None:
    if value in {"none", "outside"}:
        return False
    if value in {"intersection", "intersection_and_boundary", "inside", "partial"}:
        return True
    return None


def _value_at(sections: Mapping[str, Any], path: str) -> object:
    section_name, value_name = path.split(".", 1)
    section = sections.get(section_name)
    if not isinstance(section, Mapping):
        return None
    values = section.get("values")
    return values.get(value_name) if isinstance(values, Mapping) else None


def _known(value: object) -> bool:
    return value is not None and value != "unknown"


def percentile(values: Sequence[float], quantile: float) -> float | None:
    """Linear percentile with rank ``(n - 1) * quantile``."""

    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    rank = (len(ordered) - 1) * quantile
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)


def calculate_metrics(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    total = len(records)
    parcel_correct = sum(record.get("parcel_identification_correct") is True for record in records)
    zone_correct = 0
    zone_total = 0
    share_errors: list[float] = []
    completeness_known = 0
    completeness_total = total * len(COMPLETENESS_PATHS)
    confusion = {
        name: {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
        for name in ("flood_intersection", "nature_intersection", "ouz_presence")
    }

    for record in records:
        expected = record.get("expected_sections")
        actual = record.get("actual_sections")
        expected = expected if isinstance(expected, Mapping) else {}
        actual = actual if isinstance(actual, Mapping) else {}
        ambiguous = set(record.get("ambiguous_metrics", []))

        for section_name in ("pog", "mpzp"):
            expected_section = expected.get(section_name)
            actual_section = actual.get(section_name)
            if (
                isinstance(expected_section, Mapping)
                and isinstance(actual_section, Mapping)
                and expected_section.get("status") == "available"
                and f"{section_name}.zones" not in ambiguous
            ):
                zone_total += 1
                zone_correct += _symbols(expected_section) == _symbols(actual_section)
                share_errors.extend(_share_errors(expected_section, actual_section))

        for metric_name, section_name in (
            ("flood_intersection", "flood"),
            ("nature_intersection", "nature"),
            ("ouz_presence", "ouz"),
        ):
            path = f"{section_name}.relation"
            expected_section = expected.get(section_name)
            actual_section = actual.get(section_name)
            if (
                path in ambiguous
                or not isinstance(expected_section, Mapping)
                or expected_section.get("status") != "available"
                or not isinstance(actual_section, Mapping)
            ):
                continue
            expected_bool = _relation_as_bool(_value_at(expected, path))
            actual_bool = _relation_as_bool(_value_at(actual, path))
            if expected_bool is None or actual_bool is None:
                continue
            key = (
                "tp" if expected_bool and actual_bool else
                "fn" if expected_bool else
                "fp" if actual_bool else
                "tn"
            )
            confusion[metric_name][key] += 1

        completeness_known += sum(
            _known(_value_at(actual, path)) for path in COMPLETENESS_PATHS
        )

    binary_metrics = {}
    aggregate = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    for name, counts in confusion.items():
        for key in aggregate:
            aggregate[key] += counts[key]
        precision_denominator = counts["tp"] + counts["fp"]
        recall_denominator = counts["tp"] + counts["fn"]
        precision = _ratio(counts["tp"], precision_denominator)
        recall = _ratio(counts["tp"], recall_denominator)
        p_value, r_value = precision["value"], recall["value"]
        f1 = (
            _metric(None, "ratio", reason="precision_or_recall_undefined")
            if p_value is None or r_value is None or p_value + r_value == 0
            else _metric(2 * p_value * r_value / (p_value + r_value), "ratio")
        )
        binary_metrics[name] = {**counts, "precision": precision, "recall": recall, "f1": f1}

    precision_denominator = aggregate["tp"] + aggregate["fp"]
    recall_denominator = aggregate["tp"] + aggregate["fn"]
    aggregate_precision = _ratio(aggregate["tp"], precision_denominator)
    aggregate_recall = _ratio(aggregate["tp"], recall_denominator)
    p_value = aggregate_precision["value"]
    r_value = aggregate_recall["value"]
    aggregate_f1 = (
        _metric(None, "ratio", reason="precision_or_recall_undefined")
        if p_value is None or r_value is None or p_value + r_value == 0
        else _metric(2 * p_value * r_value / (p_value + r_value), "ratio")
    )
    binary_metrics["aggregate"] = {
        **aggregate,
        "precision": aggregate_precision,
        "recall": aggregate_recall,
        "f1": aggregate_f1,
    }

    durations = [float(record.get("duration_ms", 0.0)) for record in records]
    cache_hits = sum(record.get("cache_status") == "hit" for record in records)
    cache_misses = sum(record.get("cache_status") == "miss" for record in records)
    failed = sum(record.get("status") == "failed" for record in records)
    unknown = sum(bool(record.get("has_unknown")) for record in records)
    partial = sum(record.get("status") == "partial" for record in records)
    manual = sum(bool(record.get("manual_review_required")) for record in records)

    return {
        "case_count": _metric(total, "case"),
        "failed_cases": _metric(failed, "case"),
        "parcel_identification_accuracy": _ratio(parcel_correct, total),
        "zone_class_accuracy": _ratio(zone_correct, zone_total),
        "zone_share_mae": _metric(
            sum(share_errors) / len(share_errors) if share_errors else None,
            "percentage_point",
            denominator=len(share_errors),
            reason=None if share_errors else "no_eligible_zone_shares",
        ),
        "zone_share_max_absolute_error": _metric(
            max(share_errors) if share_errors else None,
            "percentage_point",
            denominator=len(share_errors),
            reason=None if share_errors else "no_eligible_zone_shares",
        ),
        "binary_conditions": binary_metrics,
        "unknown_case_share": _ratio(unknown, total),
        "partial_case_share": _ratio(partial, total),
        "manual_review_case_share": _ratio(manual, total),
        "field_completeness": _ratio(completeness_known, completeness_total),
        "duration_p50": _metric(percentile(durations, 0.5), "millisecond"),
        "duration_p95": _metric(percentile(durations, 0.95), "millisecond"),
        "cache_hits": _metric(cache_hits, "case"),
        "cache_misses": _metric(cache_misses, "case"),
    }


def evaluate(
    manifest: Mapping[str, Any],
    corpus_dir: Path,
    runner: AnalysisCaseRunner | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    analysis_runner = runner or FrozenArtifactAnalysisRunner()
    records: list[dict[str, Any]] = []
    for case in sorted(manifest["cases"], key=lambda item: item["case_id"]):
        expected = _expected_sections(case)
        try:
            case_input = build_case_input(manifest, corpus_dir, case)
            actual = dict(analysis_runner.analyze(case_input))
            actual_sections = actual.get("sections")
            actual_sections = actual_sections if isinstance(actual_sections, Mapping) else {}
            status = str(actual.get("status", "partial"))
            error = None
        except Exception as exc:  # one bad case must remain visible in the report
            actual = {}
            actual_sections = {}
            status = "failed"
            error = f"{type(exc).__name__}: {exc}"
        section_statuses = {
            str(section.get("status"))
            for section in actual_sections.values()
            if isinstance(section, Mapping)
        }
        records.append(
            {
                "case_id": case["case_id"],
                "parcel_identifier": case["parcel_identifier"],
                "status": status,
                "error": error,
                "duration_ms": float(actual.get("duration_ms", 0.0)),
                "cache_status": actual.get("cache_status", "miss"),
                "manual_review_required": bool(actual.get("manual_review_required", False)),
                "has_unknown": "unknown" in section_statuses or status == "failed",
                "parcel_identification_correct": (
                    actual.get("parcel_identifier") == case["parcel_identifier"]
                ),
                "ambiguous_metrics": sorted(case.get("ambiguous_metrics", [])),
                "expected_sections": expected,
                "actual_sections": actual_sections,
            }
        )
    return records, calculate_metrics(records)


def _git_commit_sha(repo_root: Path) -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_metadata(
    manifest: Mapping[str, Any], corpus_sha256: str, started_at: str, finished_at: str
) -> dict[str, Any]:
    source_release_ids = {
        source_id: f"{source_id}@{source['captured_at']}"
        for source_id, source in sorted(manifest["sources"].items())
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "commit_sha": _git_commit_sha(REPO_ROOT),
        "corpus_sha256": corpus_sha256,
        "source_release_ids": source_release_ids,
        "seed": DEFAULT_SEED,
        "timestamps": {"started_at": started_at, "finished_at": finished_at},
    }


def _threshold_failures(metrics: Mapping[str, Any], thresholds: Mapping[str, Any]) -> list[str]:
    checks = (
        ("parcel_identification_accuracy", "min_parcel_identification_accuracy", "min"),
        ("zone_class_accuracy", "min_zone_class_accuracy", "min"),
        ("field_completeness", "min_field_completeness", "min"),
        ("zone_share_mae", "max_zone_share_mae_pp", "max"),
        ("failed_cases", "max_failed_cases", "max"),
    )
    failures = []
    for metric_name, threshold_name, direction in checks:
        if threshold_name not in thresholds:
            continue
        metric = metrics.get(metric_name)
        value = metric.get("value") if isinstance(metric, Mapping) else None
        threshold = thresholds[threshold_name]
        if value is None:
            failures.append(f"{metric_name}=null ({metric.get('reason') if isinstance(metric, Mapping) else 'missing'})")
        elif direction == "min" and value < threshold:
            failures.append(f"{metric_name}={value:.6g} < {threshold}")
        elif direction == "max" and value > threshold:
            failures.append(f"{metric_name}={value:.6g} > {threshold}")
    return failures


def _format_value(metric: Mapping[str, Any]) -> str:
    value = metric.get("value")
    if value is None:
        return f"null ({metric.get('reason') or 'undefined'})"
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


def _markdown(metadata: Mapping[str, Any], metrics: Mapping[str, Any], records: Sequence[Mapping[str, Any]], regressions: Sequence[str]) -> str:
    lines = [
        "# Wynik ewaluacji korpusu referencyjnego",
        "",
        "## Metadane eksperymentu",
        "",
        "| Pole | Wartość |",
        "|---|---|",
    ]
    for key in ("schema_version", "commit_sha", "corpus_sha256", "seed"):
        lines.append(f"| `{key}` | `{metadata[key]}` |")
    lines.append(f"| `source_release_ids` | `{json.dumps(metadata['source_release_ids'], sort_keys=True)}` |")
    lines.append(f"| `timestamps` | `{json.dumps(metadata['timestamps'], sort_keys=True)}` |")
    lines.extend(["", "## Metryki", "", "| Metryka | Wartość | Jednostka | Mianownik |", "|---|---:|---|---:|"])
    for name, metric in metrics.items():
        if name == "binary_conditions" or not isinstance(metric, Mapping):
            continue
        lines.append(
            f"| `{name}` | {_format_value(metric)} | {metric.get('unit', '')} | {metric.get('denominator') or ''} |"
        )
    lines.extend(["", "### Warunki binarne", "", "| Warunek | TP | FP | FN | TN | Precision | Recall | F1 |", "|---|---:|---:|---:|---:|---:|---:|---:|"])
    for name, item in metrics["binary_conditions"].items():
        lines.append(
            f"| `{name}` | {item['tp']} | {item['fp']} | {item['fn']} | {item['tn']} | "
            f"{_format_value(item['precision'])} | {_format_value(item['recall'])} | {_format_value(item['f1'])} |"
        )
    lines.extend(["", "## Przypadki", "", "| case_id | status | cache | czas [ms] | unknown | manual review | błąd |", "|---|---|---|---:|---|---|---|"])
    for record in records:
        error = str(record.get("error") or "").replace("|", "\\|")
        lines.append(
            f"| `{record['case_id']}` | {record['status']} | {record['cache_status']} | "
            f"{float(record['duration_ms']):.3f} | {str(bool(record['has_unknown'])).lower()} | "
            f"{str(bool(record['manual_review_required'])).lower()} | {error} |"
        )
    lines.extend(["", "## Próg regresji", ""])
    lines.extend(
        [f"- FAIL: {failure}" for failure in regressions]
        if regressions
        else ["Wszystkie zadeklarowane progi zostały spełnione."]
    )
    lines.extend(
        [
            "",
            "`ambiguous` usuwa wyłącznie wskazaną metrykę z jej accuracy/MAE. ",
            "Przypadek nadal występuje w tabeli i mianowniku kompletności.",
            "",
        ]
    )
    return "\n".join(lines)


def _svg(metrics: Mapping[str, Any]) -> str:
    bars = [
        ("Identyfikacja działki", metrics["parcel_identification_accuracy"]["value"]),
        ("Klasa strefy", metrics["zone_class_accuracy"]["value"]),
        ("Kompletność pól", metrics["field_completeness"]["value"]),
    ]
    body = []
    for index, (label, raw_value) in enumerate(bars):
        value = float(raw_value or 0.0)
        y = 45 + index * 45
        body.append(f'<text x="10" y="{y}" font-size="13">{html.escape(label)}</text>')
        body.append(f'<rect x="180" y="{y - 15}" width="{300 * value:.3f}" height="18" fill="#2f6fed"/>')
        body.append(f'<text x="490" y="{y}" text-anchor="end" font-size="13">{value:.1%}</text>')
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="510" height="180" viewBox="0 0 510 180">'
        '<rect width="100%" height="100%" fill="white"/>'
        '<text x="10" y="20" font-size="15" font-weight="bold">Reference corpus metrics</text>'
        + "".join(body)
        + "</svg>\n"
    )


def write_outputs(
    output_dir: Path,
    metadata: Mapping[str, Any],
    metrics: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
    regressions: Sequence[str],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "metadata": metadata,
        "metrics": metrics,
        "regressions": list(regressions),
        "cases": list(records),
    }
    (output_dir / "evaluation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    fieldnames = [
        "schema_version", "commit_sha", "corpus_sha256", "source_release_ids",
        "seed", "timestamps", "case_id", "parcel_identifier", "status", "error",
        "duration_ms", "cache_status", "has_unknown", "manual_review_required",
        "parcel_identification_correct", "ambiguous_metrics",
    ]
    with (output_dir / "cases.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        common = {
            "schema_version": metadata["schema_version"],
            "commit_sha": metadata["commit_sha"],
            "corpus_sha256": metadata["corpus_sha256"],
            "source_release_ids": json.dumps(metadata["source_release_ids"], sort_keys=True),
            "seed": metadata["seed"],
            "timestamps": json.dumps(metadata["timestamps"], sort_keys=True),
        }
        for record in records:
            writer.writerow(
                {
                    **common,
                    **{key: record.get(key) for key in fieldnames if key not in common},
                    "ambiguous_metrics": json.dumps(record.get("ambiguous_metrics", [])),
                }
            )

    metric_fieldnames = [
        "schema_version", "commit_sha", "corpus_sha256", "source_release_ids",
        "seed", "timestamps", "metric", "value", "unit", "numerator",
        "denominator", "reason", "tp", "fp", "fn", "tn", "precision",
        "recall", "f1",
    ]
    with (output_dir / "metrics.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=metric_fieldnames)
        writer.writeheader()
        common = {
            "schema_version": metadata["schema_version"],
            "commit_sha": metadata["commit_sha"],
            "corpus_sha256": metadata["corpus_sha256"],
            "source_release_ids": json.dumps(metadata["source_release_ids"], sort_keys=True),
            "seed": metadata["seed"],
            "timestamps": json.dumps(metadata["timestamps"], sort_keys=True),
        }
        for name, metric in metrics.items():
            if name == "binary_conditions" or not isinstance(metric, Mapping):
                continue
            writer.writerow({**common, "metric": name, **metric})
        for name, counts in metrics["binary_conditions"].items():
            writer.writerow(
                {
                    **common,
                    "metric": f"binary_conditions.{name}",
                    **{key: counts[key] for key in ("tp", "fp", "fn", "tn")},
                    "precision": counts["precision"]["value"],
                    "recall": counts["recall"]["value"],
                    "f1": counts["f1"]["value"],
                }
            )
    (output_dir / "report.md").write_text(
        _markdown(metadata, metrics, records, regressions), encoding="utf-8"
    )
    (output_dir / "metrics.svg").write_text(_svg(metrics), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate the frozen reference corpus offline.")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--mode", choices=("offline",), default="offline")
    parser.add_argument("--fail-on-regression", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    started_at = _timestamp()
    try:
        manifest, corpus_sha256 = load_corpus(args.corpus.resolve())
        records, metrics = evaluate(manifest, args.corpus.resolve().parent)
        finished_at = _timestamp()
        metadata = build_metadata(manifest, corpus_sha256, started_at, finished_at)
        thresholds = manifest.get("evaluation_thresholds", {})
        regressions = _threshold_failures(metrics, thresholds)
        write_outputs(args.output_dir.resolve(), metadata, metrics, records, regressions)
    except (EvaluationError, ReferenceCorpusError, OSError, ValueError) as exc:
        print(f"evaluation failed: {exc}", file=sys.stderr)
        return 1

    failed_cases = metrics["failed_cases"]["value"]
    if failed_cases:
        print(f"evaluation failed: {failed_cases} case(s) failed", file=sys.stderr)
        return 1
    if args.fail_on_regression and regressions:
        print("evaluation regression: " + "; ".join(regressions), file=sys.stderr)
        return 1
    print(f"evaluation complete: {len(records)} cases -> {args.output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
