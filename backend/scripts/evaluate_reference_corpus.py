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
import platform
import subprocess
import sys
import time
from collections import Counter
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


# Styk granicy: pole przecięcia poniżej epsilonu (spójnie z adapterami ISOK/GDOŚ).
RISK_AREA_EPSILON_SQM = 1e-6
_RISK_SECTION_KEYS = ("feature_count", "features", "relation", "union_area")


def _risk_section(raw: object, kind: str) -> dict[str, object]:
    """Zamrożoną obserwację mapuje na kontrakt strukturalny, potem ocenia.

    Frozen runner i odpowiedź API przechodzą tę samą ścieżkę
    ``risk_section_from_structured`` — ewaluator nie czyta ``description``.
    """
    section, risks = _observation_as_structured(raw, kind)
    return risk_section_from_structured(section, risks)


def _observation_as_structured(
    raw: object, kind: str
) -> tuple[dict[str, object] | None, list[dict[str, object]]]:
    if not isinstance(raw, Mapping):
        return None, []
    if raw.get("status") != "available":
        return {"section": kind, "status": "unavailable"}, []
    raw_features = raw.get("features")
    raw_features = raw_features if isinstance(raw_features, list) else []
    risks: list[dict[str, object]] = []
    for feature in raw_features:
        if not isinstance(feature, Mapping):
            continue
        area = feature.get("intersection_area_sqm")
        ratio = feature.get("area_ratio")
        risks.append(
            {
                "section": kind,
                "risk_type": feature.get("type"),
                "feature_id": feature.get("feature_id"),
                "probability_class": feature.get("probability_class"),
                "protection_type": feature.get("type") if kind == "nature" else None,
                "name": feature.get("name"),
                "severity": feature.get("severity"),
                "intersection_area_sqm": area,
                "intersection_pct": (
                    float(ratio) * 100.0 if isinstance(ratio, (int, float)) else None
                ),
                "touches_boundary": (
                    float(area) < RISK_AREA_EPSILON_SQM
                    if isinstance(area, (int, float))
                    else None
                ),
            }
        )
    return {"section": kind, "status": "available"}, risks


def risk_section_from_structured(
    section: Mapping[str, Any] | None,
    risks: Sequence[Mapping[str, Any]],
) -> dict[str, object]:
    """Sekcja ewaluatora wyłącznie z pól strukturalnych ``RiskSectionResult``/``RiskResult``.

    Status inny niż ``available`` (awaria, błąd, stary zapis) daje sekcję
    ``unknown`` — nie jest liczony jako „brak ryzyka” w confusion matrix.
    """
    if not isinstance(section, Mapping) or section.get("status") != "available":
        return _unknown_section(_RISK_SECTION_KEYS)
    features = []
    for risk in risks:
        if not isinstance(risk, Mapping):
            continue
        area = risk.get("intersection_area_sqm")
        touches = risk.get("touches_boundary")
        if touches is None and isinstance(area, (int, float)):
            touches = float(area) < RISK_AREA_EPSILON_SQM
        features.append(
            {
                "area": area,
                "class": risk.get("probability_class") or risk.get("name"),
                "severity": risk.get("severity"),
                "share": risk.get("intersection_pct"),
                "type": risk.get("protection_type") or risk.get("risk_type"),
                "feature_id": risk.get("feature_id"),
                "touches_boundary": touches,
            }
        )
    intersecting = [item for item in features if item["touches_boundary"] is not True]
    relation = "none" if not features else "intersection" if intersecting else "boundary"
    return {
        "status": "available",
        "values": {
            "feature_count": len(features),
            "features": features,
            "relation": relation,
            "union_area": section.get("union_intersection_area_sqm"),
        },
    }


def sections_from_analyze_response(payload: Mapping[str, Any]) -> dict[str, dict[str, object]]:
    """Sekcje flood/nature z odpowiedzi ``/analyze`` (API, snapshot, cache)."""
    risks = payload.get("risks")
    risks = risks if isinstance(risks, list) else []
    raw_sections = payload.get("risk_sections")
    by_name = {
        section.get("section"): section
        for section in (raw_sections if isinstance(raw_sections, list) else [])
        if isinstance(section, Mapping)
    }
    return {
        name: risk_section_from_structured(
            by_name.get(name),
            [risk for risk in risks if isinstance(risk, Mapping) and risk.get("section") == name],
        )
        for name in ("flood", "nature")
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


def _risk_errors(
    expected: Mapping[str, Any], actual: Mapping[str, Any]
) -> tuple[list[float], list[float]]:
    """Błędy pola [m²] i udziału [pp] dopasowanych obiektów ryzyka.

    Obiekty dopasowuje się po klasie (klasa prawdopodobieństwa albo nazwa
    formy ochrony), a przy remisie — po najmniejszej różnicy pola. Obiekty bez
    pary nie wchodzą do błędu pola; są widoczne w confusion matrix.
    """
    expected_values = expected.get("values")
    actual_values = actual.get("values")
    if not isinstance(expected_values, Mapping) or not isinstance(actual_values, Mapping):
        return [], []
    expected_features = expected_values.get("features")
    actual_features = actual_values.get("features")
    if not isinstance(expected_features, list) or not isinstance(actual_features, list):
        return [], []
    remaining = [item for item in actual_features if isinstance(item, Mapping)]
    area_errors: list[float] = []
    share_errors: list[float] = []
    for expected_feature in expected_features:
        if not isinstance(expected_feature, Mapping):
            continue
        expected_area = expected_feature.get("area")
        candidates = [
            item for item in remaining if item.get("class") == expected_feature.get("class")
        ]
        if not candidates or not isinstance(expected_area, (int, float)):
            continue
        best = min(
            candidates,
            key=lambda item: abs(float(item.get("area") or 0.0) - float(expected_area)),
        )
        remaining.remove(best)
        actual_area = best.get("area")
        if isinstance(actual_area, (int, float)):
            area_errors.append(abs(float(actual_area) - float(expected_area)))
        expected_share = expected_feature.get("share")
        actual_share = best.get("share")
        if isinstance(expected_share, (int, float)) and isinstance(actual_share, (int, float)):
            share_errors.append(abs(float(actual_share) - float(expected_share)))
    return area_errors, share_errors


def _relation_as_bool(value: object) -> bool | None:
    if value in {"none", "outside", "boundary"}:
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
    risk_area_errors: list[float] = []
    risk_share_errors: list[float] = []
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
            if section_name in {"flood", "nature"} and expected_bool and actual_bool:
                area_errors, pct_errors = _risk_errors(expected_section, actual_section)
                risk_area_errors.extend(area_errors)
                risk_share_errors.extend(pct_errors)

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
        "risk_area_mae": _metric(
            sum(risk_area_errors) / len(risk_area_errors) if risk_area_errors else None,
            "square_metre",
            denominator=len(risk_area_errors),
            reason=None if risk_area_errors else "no_matched_risk_features",
        ),
        "risk_area_max_absolute_error": _metric(
            max(risk_area_errors) if risk_area_errors else None,
            "square_metre",
            denominator=len(risk_area_errors),
            reason=None if risk_area_errors else "no_matched_risk_features",
        ),
        "risk_share_mae": _metric(
            sum(risk_share_errors) / len(risk_share_errors) if risk_share_errors else None,
            "percentage_point",
            denominator=len(risk_share_errors),
            reason=None if risk_share_errors else "no_matched_risk_features",
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


# --------------------------------------------------------------------------
# BK-601 — badanie poprawności na zamrożonym korpusie
#
# Warstwa badania nie zmienia metryk BK-004. Dodaje: zamrożony manifest biegu,
# porównanie na poziomie pól (nie tylko agregaty), rejestr każdej rozbieżności
# i ograniczenia z atrybucją przyczyny oraz kontrolę determinizmu. Czas jest
# raportowany osobno i nie wchodzi do skrótu merytorycznego.
# --------------------------------------------------------------------------

STUDY_SCHEMA_VERSION = "1.0.0"
# Rozbieżność udziału większa od tej wartości wymaga osobnego wpisu z dowodem.
DISCREPANCY_THRESHOLD_PP = 0.5
# Tolerancja obwodu: klasa „transformacja CRS i powrót” z protokołu BK-003
# (0,02 m na wierzchołek). Ustalona przed badaniem, nie dopasowywana do wyniku.
PERIMETER_TOLERANCE_M = 0.02
MIN_STUDY_CASES = 24
DEFAULT_REPEAT = 2
DEFAULT_STUDY_DIR = REPO_ROOT / "docs" / "evaluation" / "results" / "accuracy"
DEFAULT_ERROR_ANALYSIS = REPO_ROOT / "docs" / "evaluation" / "error_analysis.md"

SECTION_ORDER = ("geometry", "pog", "ouz", "mpzp", "flood", "nature", "terrain")
ERROR_CATEGORIES = ("source", "data", "geometry", "parser", "presentation")

V_MATCH = "match"
V_WITHIN = "within_tolerance"
V_MISMATCH = "mismatch"
V_MISSING = "missing_actual"
V_UNEXPECTED = "unexpected_actual"
V_BOTH_UNKNOWN = "both_unknown"
V_AMBIGUOUS = "ambiguous_excluded"
_AGREE_VERDICTS = frozenset({V_MATCH, V_WITHIN})
_COMPARABLE_VERDICTS = frozenset({V_MATCH, V_WITHIN, V_MISMATCH, V_MISSING})

# (pole, rodzaj, tolerancja, jednostka). Tolerancja tekstowa to klucz
# ``tolerances`` przypadku; liczba to stała zadeklarowana w protokole.
_SCALAR_FIELD_SPECS: dict[str, tuple[tuple[str, str, str | float | None, str], ...]] = {
    "geometry": (
        ("area", "numeric", "geometry_area_abs", "square_metre"),
        ("perimeter", "numeric", PERIMETER_TOLERANCE_M, "metre"),
    ),
    "pog": (
        ("legal_status", "categorical", None, "state"),
        ("zone_count", "count", 0.0, "count"),
    ),
    "ouz": (
        ("intersection_area", "numeric", "intersection_area_abs", "square_metre"),
        ("relation", "categorical", None, "class"),
        ("share", "numeric", "share_abs", "percentage_point"),
    ),
    "mpzp": (
        ("mode", "categorical", None, "class"),
        ("plan_id", "categorical", None, "identifier"),
        ("document_url", "categorical", None, "url"),
        ("zone_count", "count", 0.0, "count"),
    ),
    "flood": (
        ("feature_count", "count", 0.0, "count"),
        ("relation", "categorical", None, "class"),
        ("boundary_feature_area", "numeric", "intersection_area_abs", "square_metre"),
    ),
    "nature": (
        ("feature_count", "count", 0.0, "count"),
        ("relation", "categorical", None, "class"),
    ),
    "terrain": (
        ("minimum", "numeric", "elevation_abs", "metre"),
        ("maximum", "numeric", "elevation_abs", "metre"),
        ("relief", "numeric", "elevation_abs", "metre"),
        ("class", "categorical", None, "class"),
    ),
}

# Co faktycznie waliduje dana ścieżka. To rozróżnienie jest wynikiem badania:
# „passthrough” potwierdza wierność odczytu zamrożonej obserwacji, a nie
# poprawność obliczeń aplikacji.
MEASUREMENT_KINDS: dict[str, dict[str, str]] = {
    "geometry.area": {
        "kind": "production_computation",
        "what": "calculate_geometry_metrics na zamrożonej geometrii ULDK",
    },
    "geometry.perimeter": {
        "kind": "production_computation",
        "what": "calculate_geometry_metrics na zamrożonej geometrii ULDK",
    },
    "pog.*": {
        "kind": "observation_passthrough",
        "what": "udziały i pola stref skopiowane z zamrożonej obserwacji RU; "
        "brak przecięcia na geometrii stref (geometrii stref nie zamrożono)",
    },
    "ouz.*": {
        "kind": "observation_passthrough",
        "what": "pole i udział OUZ skopiowane z obserwacji; relacja z reguł progowych harnessu",
    },
    "mpzp.*": {
        "kind": "observation_passthrough",
        "what": "symbole i udziały z zamrożonego discovery KIMPZP / pomiaru Krakowa; "
        "tryb z reguły harnessu",
    },
    "flood.*": {
        "kind": "production_contract_mapping",
        "what": "obserwacja ISOK mapowana przez risk_section_from_structured "
        "(ten sam kontrakt co API); wartości przecięć z obserwacji",
    },
    "nature.*": {
        "kind": "production_contract_mapping",
        "what": "obserwacja GDOŚ mapowana przez risk_section_from_structured; "
        "wartości przecięć z obserwacji",
    },
    "terrain.*": {
        "kind": "observation_passthrough",
        "what": "wysokości z obserwacji NMT; klasa z progów harnessu (2 m, 10 m)",
    },
}

# Paths are relative to the backend directory, which is also ``/app`` in the
# container where the repository root is not the parent of the code.
_CODE_FINGERPRINT_FILES = (
    "scripts/evaluate_reference_corpus.py",
    "tests/parcel_fixtures_config.py",
    "app/services/geometry.py",
    "app/services/mpzp.py",
    "app/services/pog_analyzer.py",
    "app/services/mpzp_zones.py",
    "app/services/mpzp_parser.py",
)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _case_tolerance(case: Mapping[str, Any], spec: str | float | None) -> float:
    if isinstance(spec, str):
        entry = case.get("tolerances", {}).get(spec)
        value = entry.get("value") if isinstance(entry, Mapping) else None
        if not _is_number(value):
            raise EvaluationError(f"{case['case_id']}: missing tolerance {spec!r}.")
        return float(value)
    return float(spec or 0.0)


def _compare_values(
    expected: object, actual: object, kind: str, tolerance: float
) -> tuple[str, float | None]:
    expected_known, actual_known = _known(expected), _known(actual)
    if not expected_known and not actual_known:
        return V_BOTH_UNKNOWN, None
    if expected_known and not actual_known:
        return V_MISSING, None
    if actual_known and not expected_known:
        return V_UNEXPECTED, None
    if kind in {"numeric", "count"} and _is_number(expected) and _is_number(actual):
        difference = abs(float(expected) - float(actual))  # type: ignore[arg-type]
        if difference <= 1e-9:
            return V_MATCH, difference
        return (V_WITHIN if difference <= tolerance else V_MISMATCH), difference
    return (V_MATCH if expected == actual else V_MISMATCH), None


def _field_row(
    case_id: str,
    section: str,
    field: str,
    kind: str,
    unit: str,
    expected: object,
    actual: object,
    tolerance: float,
    ambiguous_scopes: set[str],
    scope: str,
) -> dict[str, Any]:
    raw_verdict, difference = _compare_values(expected, actual, kind, tolerance)
    excluded = scope in ambiguous_scopes
    return {
        "case_id": case_id,
        "section": section,
        "field": field,
        "kind": kind,
        "unit": unit,
        "expected": expected,
        "actual": actual,
        "difference": difference,
        "tolerance": tolerance if kind in {"numeric", "count"} else None,
        "verdict": V_AMBIGUOUS if excluded else raw_verdict,
        "raw_verdict": raw_verdict,
        "ambiguity_scope": scope if excluded else None,
    }


def _values_of(section: object) -> Mapping[str, Any]:
    if isinstance(section, Mapping):
        values = section.get("values")
        if isinstance(values, Mapping):
            return values
    return {}


def _dict_list(value: object) -> list[Mapping[str, Any]]:
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def _zone_rows(
    case: Mapping[str, Any],
    section: str,
    expected: Mapping[str, Any],
    actual: Mapping[str, Any],
    ambiguous: set[str],
) -> list[dict[str, Any]]:
    case_id = case["case_id"]
    scope = f"{section}.zones"
    expected_zones = [z for z in _dict_list(expected.get("zones")) if z.get("symbol")]
    actual_zones = [z for z in _dict_list(actual.get("zones")) if z.get("symbol")]
    expected_symbols = sorted({str(z["symbol"]) for z in expected_zones})
    actual_symbols = sorted({str(z["symbol"]) for z in actual_zones})
    share_tol = _case_tolerance(case, "share_abs")
    area_tol = _case_tolerance(case, "intersection_area_abs")

    if expected_symbols == actual_symbols:
        symbol_expected: object = expected_symbols or None
        symbol_actual: object = actual_symbols or None
    else:
        symbol_expected, symbol_actual = expected_symbols or None, actual_symbols or None
    rows = [
        _field_row(
            case_id, section, "zones.symbols", "set", "code",
            symbol_expected, symbol_actual, 0.0, ambiguous, scope,
        )
    ]
    actual_by_symbol: dict[str, Mapping[str, Any]] = {}
    for zone in actual_zones:
        actual_by_symbol.setdefault(str(zone["symbol"]), zone)
    for zone in expected_zones:
        symbol = str(zone["symbol"])
        match = actual_by_symbol.get(symbol)
        for name, unit, tolerance in (
            ("share", "percentage_point", share_tol),
            ("area", "square_metre", area_tol),
        ):
            rows.append(
                _field_row(
                    case_id, section, f"zones[{symbol}].{name}", "numeric", unit,
                    zone.get(name), match.get(name) if match else None,
                    tolerance, ambiguous, scope,
                )
            )
    if expected_symbols:
        for symbol in sorted(set(actual_symbols) - set(expected_symbols)):
            rows.append(
                _field_row(
                    case_id, section, f"zones[{symbol}].presence", "categorical",
                    "code", None, symbol, 0.0, ambiguous, scope,
                )
            )
    return rows


def _risk_rows(
    case: Mapping[str, Any],
    section: str,
    expected: Mapping[str, Any],
    actual: Mapping[str, Any],
    ambiguous: set[str],
) -> list[dict[str, Any]]:
    case_id = case["case_id"]
    scope = f"{section}.features"
    area_tol = _case_tolerance(case, "intersection_area_abs")
    share_tol = _case_tolerance(case, "share_abs")
    remaining = list(_dict_list(actual.get("features")))
    rows: list[dict[str, Any]] = []
    for feature in _dict_list(expected.get("features")):
        key = feature.get("class") or feature.get("name")
        candidates = [item for item in remaining if item.get("class") == key]
        label = f"features[{key}]"
        if not candidates:
            rows.append(
                _field_row(
                    case_id, section, f"{label}.presence", "categorical", "class",
                    key, None, 0.0, ambiguous, scope,
                )
            )
            continue
        expected_area = feature.get("area")
        best = min(
            candidates,
            key=lambda item: abs(
                float(item.get("area") or 0.0)
                - float(expected_area if _is_number(expected_area) else 0.0)
            ),
        )
        remaining.remove(best)
        for name, kind, unit, tolerance in (
            ("area", "numeric", "square_metre", area_tol),
            ("share", "numeric", "percentage_point", share_tol),
            ("severity", "categorical", "class", 0.0),
            ("type", "categorical", "class", 0.0),
        ):
            if name not in feature:
                continue
            rows.append(
                _field_row(
                    case_id, section, f"{label}.{name}", kind, unit,
                    feature.get(name), best.get(name), tolerance, ambiguous, scope,
                )
            )
    for item in remaining:
        rows.append(
            _field_row(
                case_id, section, f"features[{item.get('class')}].presence",
                "categorical", "class", None, item.get("class"), 0.0, ambiguous, scope,
            )
        )
    return rows


def compare_case_fields(
    case: Mapping[str, Any],
    expected_sections: Mapping[str, Any],
    actual_sections: Mapping[str, Any],
    ambiguous_metrics: Sequence[str],
) -> list[dict[str, Any]]:
    """Porównuje każde pole sekcji z oczekiwaną wartością i tolerancją przypadku.

    ``ambiguous`` wyłącza wyłącznie wskazaną ścieżkę (``sekcja.pole``); pozostałe
    pola tej samej sekcji i przypadku pozostają ocenione (protokół BK-003).
    """
    case_id = case["case_id"]
    ambiguous = set(ambiguous_metrics)
    rows: list[dict[str, Any]] = []
    for section in SECTION_ORDER:
        expected = _values_of(expected_sections.get(section))
        actual = _values_of(actual_sections.get(section))
        for field, kind, tolerance_spec, unit in _SCALAR_FIELD_SPECS[section]:
            rows.append(
                _field_row(
                    case_id, section, field, kind, unit,
                    expected.get(field), actual.get(field),
                    _case_tolerance(case, tolerance_spec), ambiguous,
                    f"{section}.{field}",
                )
            )
        if section in {"pog", "mpzp"}:
            rows.extend(_zone_rows(case, section, expected, actual, ambiguous))
        elif section in {"flood", "nature"}:
            rows.extend(_risk_rows(case, section, expected, actual, ambiguous))
    return rows


def compare_section_status(
    records: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    for record in records:
        expected = record.get("expected_sections") or {}
        actual = record.get("actual_sections") or {}
        for section in SECTION_ORDER:
            expected_status = (expected.get(section) or {}).get("status")
            actual_status = (actual.get(section) or {}).get("status")
            rows.append(
                {
                    "case_id": record["case_id"],
                    "section": section,
                    "expected_status": expected_status,
                    "actual_status": actual_status,
                    "agrees": expected_status == actual_status,
                }
            )
    return rows


def _row_pointer_value(row: Mapping[str, Any]) -> tuple[object, object]:
    return row["expected"], row["actual"]


def calculate_study_metrics(
    records: Sequence[Mapping[str, Any]],
    field_rows: Sequence[Mapping[str, Any]],
    status_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Metryki poziomu pola i sekcji z jawnym licznikiem i mianownikiem."""
    total = len(records)
    statuses = Counter(str(record.get("status")) for record in records)
    verdicts = Counter(str(row["verdict"]) for row in field_rows)

    def agreement(rows: Sequence[Mapping[str, Any]]) -> dict[str, object]:
        comparable = [row for row in rows if row["verdict"] in _COMPARABLE_VERDICTS]
        agreeing = [row for row in comparable if row["verdict"] in _AGREE_VERDICTS]
        return _ratio(len(agreeing), len(comparable))

    per_section: dict[str, Any] = {}
    status_per_section: dict[str, Any] = {}
    for section in SECTION_ORDER:
        rows = [row for row in field_rows if row["section"] == section]
        per_section[section] = agreement(rows)
        section_status = [row for row in status_rows if row["section"] == section]
        status_per_section[section] = _ratio(
            sum(bool(row["agrees"]) for row in section_status), len(section_status)
        )

    unknown_rows = [
        row for row in field_rows if row["verdict"] in {V_BOTH_UNKNOWN, V_UNEXPECTED}
    ]
    numeric_errors: dict[str, Any] = {}
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in field_rows:
        if row["kind"] != "numeric" or row["difference"] is None:
            continue
        name = f"{row['section']}.{_generic_field(str(row['field']))}"
        grouped.setdefault(name, []).append(row)
    for name, rows in sorted(grouped.items()):
        differences = [float(row["difference"]) for row in rows]
        numeric_errors[name] = {
            "unit": rows[0]["unit"],
            "n": len(rows),
            "mean_absolute_error": sum(differences) / len(differences),
            "max_absolute_error": max(differences),
            "tolerance_exceeded": sum(row["verdict"] == V_MISMATCH for row in rows),
        }

    share_rows = [
        row
        for row in field_rows
        if row["unit"] == "percentage_point" and row["difference"] is not None
    ]
    above_threshold = [
        row for row in share_rows if float(row["difference"]) > DISCREPANCY_THRESHOLD_PP
    ]
    share_errors = [float(row["difference"]) for row in share_rows]
    section_status_counts = {
        section: dict(
            sorted(
                Counter(
                    str(((record.get("actual_sections") or {}).get(section) or {}).get("status"))
                    for record in records
                ).items()
            )
        )
        for section in SECTION_ORDER
    }
    return {
        "input_cases": _metric(total, "case"),
        "complete_cases": _metric(statuses.get("complete", 0), "case"),
        "partial_cases": _metric(statuses.get("partial", 0), "case"),
        "failed_cases": _metric(statuses.get("failed", 0), "case"),
        "case_identity_holds": _metric(
            int(total == statuses.get("complete", 0) + statuses.get("partial", 0) + statuses.get("failed", 0)),
            "boolean",
            denominator=total,
        ),
        "field_agreement": agreement(field_rows),
        "field_agreement_by_section": per_section,
        "status_agreement": _ratio(
            sum(bool(row["agrees"]) for row in status_rows), len(status_rows)
        ),
        "status_agreement_by_section": status_per_section,
        "field_verdict_counts": dict(sorted(verdicts.items())),
        "false_certainty_fields": _ratio(
            sum(row["verdict"] == V_UNEXPECTED for row in unknown_rows), len(unknown_rows)
        ),
        "numeric_errors": numeric_errors,
        "share_comparisons": _metric(len(share_rows), "comparison"),
        "share_mean_absolute_error": _metric(
            sum(share_errors) / len(share_errors) if share_errors else None,
            "percentage_point",
            denominator=len(share_errors),
            reason=None if share_errors else "no_share_comparisons",
        ),
        "share_max_absolute_error": _metric(
            max(share_errors) if share_errors else None,
            "percentage_point",
            denominator=len(share_errors),
            reason=None if share_errors else "no_share_comparisons",
        ),
        "share_differences_above_threshold": _ratio(
            len(above_threshold), len(share_rows), "ratio"
        ),
        "section_status_counts": section_status_counts,
    }


def _generic_field(field: str) -> str:
    """``zones[KP.1].share`` → ``zones.share`` do zbiorczych statystyk."""
    if "[" in field and "]" in field:
        head, rest = field.split("[", 1)
        return head + rest.split("]", 1)[1]
    return field


# --- Rejestr błędów i atrybucja przyczyn -----------------------------------


class _CaseContext:
    """Dowody jednego przypadku: manifest, obserwacje i ich skróty."""

    def __init__(
        self,
        manifest: Mapping[str, Any],
        corpus_dir: Path,
        case: Mapping[str, Any],
        case_index: int,
    ) -> None:
        self.case = case
        self.case_index = case_index
        artifact = manifest["artifacts"][f"evidence:{case['case_id']}"]
        self.artifact_id = f"evidence:{case['case_id']}"
        self.artifact_path = str(artifact["path"])
        self.artifact_sha256 = str(artifact["sha256"])
        evidence = json.loads((corpus_dir / artifact["path"]).read_text(encoding="utf-8"))
        self.observations: Mapping[str, Any] = evidence.get("observations", {})

    def observation(self, pointer: str, value: object) -> dict[str, object]:
        return {
            "type": "frozen_observation",
            "artifact_id": self.artifact_id,
            "path": self.artifact_path,
            "sha256": self.artifact_sha256,
            "json_pointer": pointer,
            "value": value,
        }

    def ground_truth(self, section: str, field: str | None = None) -> dict[str, object]:
        expected = self.case["expected"][section]
        pointer = f"/cases/{self.case_index}/expected/{section}"
        entry: dict[str, object] = {
            "type": "ground_truth",
            "manifest_pointer": pointer + ("/values/" + field if field else "/status"),
            "method": expected.get("method"),
        }
        if field is not None:
            entry["value"] = _unwrap(expected.get("values", {}).get(field))
        else:
            entry["value"] = expected.get("status")
        return entry


def _code_ref(ref: str, note: str) -> dict[str, object]:
    return {"type": "code", "ref": ref, "note": note}


def probe_null_symbol() -> dict[str, object]:
    """Uruchamia produkcyjny parser odpowiedzi KIMPZP na symbolu ``NULL``.

    Wynik jest dowodem liczonym w czasie badania: po naprawie adaptera
    ``zone_symbol`` przestanie być równe ``NULL`` i rejestr to pokaże.
    """
    html_payload = (
        "<table><tr><th>Symbol</th><td>NULL</td></tr>"
        "<tr><th>Poziom informatyzacji</th><td>wektor</td></tr></table>"
    )
    json_payload = json.dumps({"features": [{"properties": {"symbol": "NULL"}}]})
    try:
        from app.services import mpzp

        # AU-004: wynik punktu niesie listę symboli (``zone_symbols``); brak symbolu → ``None``.
        results = {
            name: next(iter(mpzp._parse_get_feature_info_response(payload).zone_symbols), None)
            for name, payload in (("html", html_payload), ("json", json_payload))
        }
    except Exception as exc:  # brak importu nie może zatrzymać badania
        return {"error": f"{type(exc).__name__}: {exc}"}
    return {"zone_symbol_from_payload_NULL": results}


# (root_cause_id, kategoria, dyspozycja, tytuł, opis)
ROOT_CAUSES: dict[str, dict[str, str]] = {
    "RC-01": {
        "category": "parser",
        "disposition": "open_defect",
        "title": "Literał NULL z KIMPZP GetFeatureInfo traktowany jak symbol strefy",
        "description": (
            "`discover_mpzp` przyjmuje każdy niepusty napis z atrybutu symbolu "
            "(`_first_matching_attribute`) jako symbol strefy. Dla planów rastrowych "
            "KIMPZP zwraca napis `NULL`; trafia on do `candidate_zone_symbols`, sekcja "
            "dostaje tryb `vector_discovery` i liczbę stref większą o jeden, a ground "
            "truth klasyfikuje taki przypadek jako `raster_manual`."
        ),
    },
    "RC-02": {
        "category": "presentation",
        "disposition": "definition_gap",
        "title": "Klasy terenu flat/moderate/relief bez definicji w protokole i kodzie produkcyjnym",
        "description": (
            "Kod produkcyjny ma wyłącznie klasy spadku w procentach "
            "(`slope-classes-pl-v1`), a protokół BK-003 nie definiuje klas deniwelacji. "
            "Harness przyjmuje progi 2 m i 10 m, natomiast etykiety korpusu dzielą "
            "przypadki inaczej (9,0 m = `relief`, 7,2 m = `moderate`). Wartości "
            "liczbowe są zgodne; różni się etykieta pochodna."
        ),
    },
    "RC-03": {
        "category": "data",
        "disposition": "ground_truth_inconsistency",
        "title": "Niespójna etykieta statusu MPZP w korpusie dla tej samej metody pomiaru",
        "description": (
            "Przypadki Krakowa real-005…008 mają ten sam rodzaj dowodu wektorowego "
            "(symbol, pole, udział) i równoważne zastrzeżenie, że interpretacja "
            "parametrów tekstowych wymaga aktu BIP. Mimo to real-008 ma w korpusie "
            "status `manual_review` i tryb `document_parse`, a real-005…007 — "
            "`available` i `vector`. Harness nie odtwarza rozróżnienia, bo dane nie "
            "zawierają kryterium, które je uzasadnia."
        ),
    },
    "RC-04": {
        "category": "data",
        "disposition": "unresolved_hypothesis",
        "title": "Cecha ISOK styczna do granicy opisana w ground truth, nieobecna w zamrożonej obserwacji",
        "description": (
            "Ground truth real-030 opisuje styk z granicą jednej cechy Q0,2% "
            "(`boundary_feature_area=0`, relacja `intersection_and_boundary`). "
            "Zamrożona obserwacja ISOK ma cztery cechy o dodatnim polu i żadnej "
            "cechy stycznej, więc relacja to `intersection`. Offline nie da się "
            "rozstrzygnąć, czy styk pominął adapter przy zbieraniu obserwacji, czy "
            "został wykryty wyłącznie osobną sondą ground truth."
        ),
    },
    "RC-10": {
        "category": "source",
        "disposition": "expected_limitation",
        "title": "Brak zweryfikowanej lokalnej geometrii POG dla gminy",
        "description": (
            "Korpus nie zawiera zweryfikowanej lokalnej geometrii POG dla tej gminy. "
            "Z danych offline nie wynika, czy RU nie publikuje aktu, czy geometrii; "
            "`unknown` nie jest dowodem braku aktu ani braku ograniczeń."
        ),
    },
    "RC-11": {
        "category": "source",
        "disposition": "expected_limitation",
        "title": "Brak zweryfikowanej geometrii OUZ",
        "description": (
            "Bez zweryfikowanej geometrii POG/OUZ relacja działki z OUZ pozostaje "
            "nieznana."
        ),
    },
    "RC-12": {
        "category": "source",
        "disposition": "expected_limitation",
        "title": "KIMPZP nie zwróciło planu miejscowego dla próbki punktów działki",
        "description": (
            "Discovery na próbce punktów nie znalazło planu. To nie dowodzi braku "
            "ograniczeń, tylko braku danych z tego źródła."
        ),
    },
    "RC-13": {
        "category": "source",
        "disposition": "expected_limitation",
        "title": "MPZP tylko jako discovery lub raster: udziałów stref nie da się ustalić z wektora",
        "description": (
            "Źródło nie udostępnia wektora stref dla działki; symbole są kandydatami "
            "z GetFeatureInfo, a udział stref pozostaje `null` do ręcznej kontroli "
            "dokumentu i rysunku."
        ),
    },
    "RC-14": {
        "category": "source",
        "disposition": "expected_limitation",
        "title": "Przejściowy błąd usługi GDOŚ podczas zbierania obserwacji",
        "description": (
            "Usługa zgłosiła błąd; wynik zachowano jako `unknown`, nie jako brak "
            "obszaru chronionego."
        ),
    },
    "RC-15": {
        "category": "source",
        "disposition": "expected_limitation",
        "title": "Limit powierzchni poligonu NMT (100 000 m²) lub brak pokrycia",
        "description": (
            "Usługa NMT GetMinMaxByPolygon odrzuca poligony większe niż 100 000 m²; "
            "wynik zachowano jako `unknown`."
        ),
    },
    "RC-16": {
        "category": "geometry",
        "disposition": "expected_limitation",
        "title": "Przecięcie OUZ mniejsze niż tolerancja ręcznego pomiaru",
        "description": (
            "Ślad 0,158 m² jest mniejszy od tolerancji 1 m²; relacja OUZ jest "
            "świadomie niejednoznaczna (`ambiguous` tylko dla `ouz.relation`)."
        ),
    },
    "RC-99": {
        "category": "data",
        "disposition": "needs_manual_triage",
        "title": "Rozbieżność bez reguły atrybucji",
        "description": "Wpis nie pasuje do żadnej zadeklarowanej reguły; wymaga ręcznej analizy.",
    },
}


def _entry(
    *,
    kind: str,
    case_id: str,
    section: str,
    field: str | None,
    expected: object,
    actual: object,
    difference: object = None,
    unit: str | None = None,
    threshold: object = None,
    root_cause: str,
    basis: str,
    explanation: str,
    evidence: list[dict[str, object]],
) -> dict[str, Any]:
    cause = ROOT_CAUSES[root_cause]
    exceeds = (
        bool(_is_number(difference) and difference > DISCREPANCY_THRESHOLD_PP)  # type: ignore[operator]
        if unit == "percentage_point"
        else None
    )
    return {
        "kind": kind,
        "case_id": case_id,
        "section": section,
        "field": field,
        "expected": expected,
        "actual": actual,
        "difference": difference,
        "unit": unit,
        "threshold": threshold,
        "exceeds_0_5_pp": exceeds,
        "category": cause["category"],
        "disposition": cause["disposition"],
        "root_cause": root_cause,
        "basis": basis,
        "explanation": explanation,
        "evidence": evidence,
    }


def _attribute_field(
    row: Mapping[str, Any], ctx: _CaseContext
) -> tuple[str, str, str, list[dict[str, object]]]:
    """Reguły atrybucji rozbieżności pola: (przyczyna, reguła, wyjaśnienie, dowody)."""
    section, field = str(row["section"]), str(row["field"])
    case_id = str(row["case_id"])
    discovery = ctx.observations.get("mpzp_discovery")
    discovery = discovery if isinstance(discovery, Mapping) else {}
    symbols = discovery.get("candidate_zone_symbols")
    symbols = symbols if isinstance(symbols, list) else []

    if section == "mpzp" and "NULL" in symbols:
        explanation = (
            f"Obserwacja discovery zawiera `NULL` wśród symboli {symbols}; "
            f"`{field}`: oczekiwano {row['expected']!r}, harness/adapter daje "
            f"{row['actual']!r}."
        )
        return (
            "RC-01",
            "rule:mpzp_null_symbol",
            explanation,
            [
                ctx.observation(
                    "/observations/mpzp_discovery/candidate_zone_symbols", symbols
                ),
                ctx.ground_truth("mpzp", "mode" if field == "mode" else None)
                if field == "mode"
                else ctx.ground_truth("mpzp"),
                _code_ref(
                    "backend/app/services/mpzp.py::_first_matching_attribute",
                    "przyjmuje dowolny niepusty napis, także NULL, jako symbol strefy",
                ),
                {
                    "type": "code_probe",
                    "call": "app.services.mpzp._parse_get_feature_info_response(symbol=NULL)",
                    "result": probe_null_symbol(),
                },
            ],
        )
    if section == "terrain" and field == "class":
        relief = ctx.observations.get("nmt", {}).get("relief_m") if isinstance(
            ctx.observations.get("nmt"), Mapping
        ) else None
        return (
            "RC-02",
            "rule:terrain_class_definition",
            f"Deniwelacja {relief} m: korpus `{row['expected']}`, harness "
            f"`{row['actual']}` (progi harnessu 2 m / 10 m nie są zdefiniowane w protokole).",
            [
                ctx.observation("/observations/nmt/relief_m", relief),
                ctx.ground_truth("terrain", "class"),
                _code_ref(
                    "backend/scripts/evaluate_reference_corpus.py::_terrain_section",
                    "progi klas 2 m i 10 m przyjęte przez harness",
                ),
            ],
        )
    if case_id.startswith("real-008") and section == "mpzp":
        return (
            "RC-03",
            "rule:mpzp_status_label_inconsistency",
            f"`{field}`: korpus {row['expected']!r}, harness {row['actual']!r}; "
            "te same dowody wektorowe co w real-005…007, które mają inną etykietę.",
            [
                ctx.observation(
                    "/observations/krakow_mpzp_derived/layers/0/intersections",
                    (ctx.observations.get("krakow_mpzp_derived") or {}).get("layers", [{}])[0]
                    .get("intersections"),
                ),
                ctx.ground_truth("mpzp", field if field in {"mode", "zone_count"} else None),
            ],
        )
    if section == "flood" and case_id.startswith("real-030"):
        isok = ctx.observations.get("isok")
        features = isok.get("features") if isinstance(isok, Mapping) else None
        return (
            "RC-04",
            "rule:isok_boundary_feature_absent",
            f"`{field}`: korpus {row['expected']!r}, harness {row['actual']!r}; "
            f"obserwacja ISOK ma {len(features or [])} cech, wszystkie z dodatnim polem.",
            [
                ctx.observation("/observations/isok/features", features),
                ctx.ground_truth("flood", "boundary_feature_area"),
            ],
        )
    return (
        "RC-99",
        "rule:none",
        f"`{section}.{field}`: oczekiwano {row['expected']!r}, otrzymano {row['actual']!r}.",
        [ctx.ground_truth(section)],
    )


def _attribute_limitation(
    section: str, status: str, ctx: _CaseContext
) -> tuple[str, str, str, list[dict[str, object]]]:
    observations = ctx.observations
    if section == "pog":
        return (
            "RC-10",
            "rule:pog_unknown",
            "Sekcja POG pozostaje `unknown`; ground truth: brak zweryfikowanej lokalnej geometrii POG.",
            [ctx.ground_truth("pog")],
        )
    if section == "ouz" and status == "unknown":
        return (
            "RC-11",
            "rule:ouz_unknown",
            "Sekcja OUZ pozostaje `unknown`; brak zweryfikowanej geometrii POG/OUZ.",
            [ctx.ground_truth("ouz")],
        )
    if section == "ouz":
        return (
            "RC-16",
            "rule:ouz_boundary_sliver",
            "Przecięcie OUZ poniżej tolerancji 1 m²; status `manual_review`, relacja niejednoznaczna.",
            [
                ctx.observation(
                    "/observations/pog_ouz/ouz_area_sqm",
                    (observations.get("pog_ouz") or {}).get("ouz_area_sqm"),
                ),
                ctx.ground_truth("ouz", "intersection_area"),
            ],
        )
    if section == "mpzp" and status == "unknown":
        return (
            "RC-12",
            "rule:mpzp_no_plan",
            "KIMPZP nie zwróciło planu dla próbki punktów; wynik `unknown`.",
            [
                ctx.observation(
                    "/observations/mpzp_discovery/status",
                    (observations.get("mpzp_discovery") or {}).get("status"),
                ),
                ctx.ground_truth("mpzp"),
            ],
        )
    if section == "mpzp":
        return (
            "RC-13",
            "rule:mpzp_discovery_only",
            "Discovery lub raster bez wektora stref; udział stref `null`, wymagana kontrola dokumentu.",
            [
                ctx.observation(
                    "/observations/mpzp_discovery/candidate_zone_symbols",
                    (observations.get("mpzp_discovery") or {}).get("candidate_zone_symbols"),
                ),
                ctx.ground_truth("mpzp"),
            ],
        )
    if section == "nature":
        return (
            "RC-14",
            "rule:gdos_error",
            f"Sekcja GDOŚ `{status}`: {(observations.get('gdos') or {}).get('error')}",
            [
                ctx.observation("/observations/gdos", observations.get("gdos")),
                ctx.ground_truth("nature"),
            ],
        )
    if section == "terrain":
        return (
            "RC-15",
            "rule:nmt_limit",
            f"NMT `{status}`: {(observations.get('nmt') or {}).get('error')}",
            [
                ctx.observation("/observations/nmt", observations.get("nmt")),
                ctx.ground_truth("terrain"),
            ],
        )
    return (
        "RC-99",
        "rule:none",
        f"Sekcja `{section}` ma status `{status}`.",
        [ctx.ground_truth(section)],
    )


def build_error_ledger(
    manifest: Mapping[str, Any],
    corpus_dir: Path,
    records: Sequence[Mapping[str, Any]],
    field_rows: Sequence[Mapping[str, Any]],
    status_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Każda rozbieżność, błędna klasyfikacja, awaria i ograniczenie jako wpis.

    Wpisy są atomowe (jedno pole/sekcja); ``root_cause`` grupuje wpisy o wspólnej
    przyczynie. Numery ``entry_id`` są nadawane po posortowaniu, więc ten sam
    manifest daje identyczny rejestr.
    """
    cases = {case["case_id"]: (index, case) for index, case in enumerate(manifest["cases"])}
    contexts: dict[str, _CaseContext] = {}

    def context(case_id: str) -> _CaseContext:
        if case_id not in contexts:
            index, case = cases[case_id]
            contexts[case_id] = _CaseContext(manifest, corpus_dir, case, index)
        return contexts[case_id]

    entries: list[dict[str, Any]] = []
    for record in records:
        if record.get("status") == "failed":
            ctx = context(record["case_id"])
            entries.append(
                _entry(
                    kind="case_failure",
                    case_id=record["case_id"],
                    section="case",
                    field=None,
                    expected="completed",
                    actual=record.get("error"),
                    root_cause="RC-99",
                    basis="rule:case_failure_default",
                    explanation=f"Przypadek nie został ukończony: {record.get('error')}",
                    evidence=[ctx.observation("/observations", None)],
                )
            )

    for row in field_rows:
        if row["verdict"] not in {V_MISMATCH, V_MISSING, V_UNEXPECTED}:
            continue
        ctx = context(str(row["case_id"]))
        cause, basis, explanation, evidence = _attribute_field(row, ctx)
        entries.append(
            _entry(
                kind="field_discrepancy",
                case_id=str(row["case_id"]),
                section=str(row["section"]),
                field=str(row["field"]),
                expected=row["expected"],
                actual=row["actual"],
                difference=row["difference"],
                unit=str(row["unit"]),
                threshold=row["tolerance"],
                root_cause=cause,
                basis=f"{basis};verdict={row['verdict']}",
                explanation=explanation,
                evidence=evidence,
            )
        )

    for row in status_rows:
        if row["agrees"]:
            continue
        ctx = context(str(row["case_id"]))
        synthetic = {
            "section": row["section"],
            "field": "status",
            "case_id": row["case_id"],
            "expected": row["expected_status"],
            "actual": row["actual_status"],
        }
        cause, basis, explanation, evidence = _attribute_field(synthetic, ctx)
        entries.append(
            _entry(
                kind="status_disagreement",
                case_id=str(row["case_id"]),
                section=str(row["section"]),
                field="status",
                expected=row["expected_status"],
                actual=row["actual_status"],
                root_cause=cause,
                basis=f"{basis};status",
                explanation=explanation,
                evidence=evidence,
            )
        )

    for record in records:
        actual = record.get("actual_sections") or {}
        expected = record.get("expected_sections") or {}
        for section in SECTION_ORDER:
            status = (actual.get(section) or {}).get("status")
            if status in {None, "available"}:
                continue
            if (expected.get(section) or {}).get("status") != status:
                continue  # rozbieżność statusu ma własny wpis powyżej
            ctx = context(record["case_id"])
            cause, basis, explanation, evidence = _attribute_limitation(
                section, str(status), ctx
            )
            entries.append(
                _entry(
                    kind="limitation",
                    case_id=record["case_id"],
                    section=section,
                    field="status",
                    expected=(expected.get(section) or {}).get("status"),
                    actual=status,
                    root_cause=cause,
                    basis=basis,
                    explanation=explanation,
                    evidence=evidence,
                )
            )

    kind_order = {"case_failure": 0, "field_discrepancy": 1, "status_disagreement": 2, "limitation": 3}
    entries.sort(
        key=lambda item: (
            str(item["case_id"]),
            SECTION_ORDER.index(item["section"]) if item["section"] in SECTION_ORDER else -1,
            kind_order[item["kind"]],
            str(item["field"]),
        )
    )
    for number, item in enumerate(entries, start=1):
        item["entry_id"] = f"E-{number:03d}"
    return entries


# --- Zamrożony manifest, determinizm, artefakty ----------------------------


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _code_fingerprint(backend_dir: Path = BACKEND_DIR) -> dict[str, str]:
    result = {}
    for relative in _CODE_FINGERPRINT_FILES:
        path = backend_dir / relative
        result[relative] = _sha256_bytes(path.read_bytes()) if path.is_file() else "missing"
    return result


def _worktree_state(repo_root: Path) -> dict[str, Any]:
    try:
        output = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {"dirty": None, "changed_paths": []}
    paths = sorted(line[3:] for line in output.splitlines() if line.strip())
    return {"dirty": bool(paths), "changed_paths": paths}


def _collect_versions() -> dict[str, str]:
    """Wersje parsera, stylu i kontraktów; brak importu jest zapisany jawnie."""
    loaders: dict[str, Callable[[], str]] = {}

    def register(name: str, loader: Callable[[], str]) -> None:
        loaders[name] = loader

    def attribute(module: str, name: str) -> Callable[[], str]:
        def load() -> str:
            import importlib

            return str(getattr(importlib.import_module(module), name))

        return load

    def pog_style() -> str:
        from app.core.pog_presentation import style_snapshot

        return str(style_snapshot()["style_version"])

    register("mpzp_parser", attribute("app.services.mpzp_parser", "MPZP_PARSER_VERSION"))
    register("pog_presentation_style", pog_style)
    register("analysis_result_contract", attribute("app.services.cache", "RESULT_CONTRACT_VERSION"))
    register("quality_policy_schema", attribute("app.services.section_quality", "QUALITY_POLICY_SCHEMA"))
    register("report_layout", attribute("app.modules.reporting.domain.sections", "REPORT_LAYOUT_VERSION"))
    register("report_map_config", attribute("app.core.report_config", "REPORT_MAP_CONFIG_VERSION"))
    register("terrain_algorithm", attribute("app.modules.analysis.domain.terrain", "ALGORITHM_VERSION"))
    register("terrain_slope_classes", attribute("app.modules.analysis.domain.terrain", "SLOPE_CLASSES_VERSION"))
    register("planning_rule_set", attribute("app.core.planning_compatibility", "RULE_SET_VERSION"))
    versions: dict[str, str] = {}
    for name, loader in sorted(loaders.items()):
        try:
            versions[name] = loader()
        except Exception as exc:  # brak modułu nie może zatrzymać badania
            versions[name] = f"unavailable:{type(exc).__name__}"
    return versions


def _environment() -> dict[str, str]:
    import shapely

    environment = {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": f"{platform.system()} {platform.machine()}",
        "shapely": shapely.__version__,
        "geos": shapely.geos_version_string,
    }
    try:
        import pyproj

        environment["pyproj"] = pyproj.__version__
        environment["proj"] = pyproj.proj_version_str
    except ImportError:  # pragma: no cover - pyproj is a declared dependency
        environment["pyproj"] = "unavailable"
    return environment


def build_run_manifest(
    manifest: Mapping[str, Any],
    corpus_sha256: str,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """Zamrożone wejście badania; ``manifest_sha256`` nie zależy od czasu."""
    artifact_hashes = {
        artifact_id: artifact["sha256"]
        for artifact_id, artifact in sorted(manifest["artifacts"].items())
    }
    frozen: dict[str, Any] = {
        "study_schema_version": STUDY_SCHEMA_VERSION,
        "commit_sha": _git_commit_sha(repo_root),
        "code_fingerprint": _code_fingerprint(),
        "corpus_id": manifest.get("corpus_id"),
        "corpus_sha256": corpus_sha256,
        "corpus_artifact_count": len(artifact_hashes),
        "corpus_artifact_hashes": artifact_hashes,
        "source_release_ids": {
            source_id: f"{source_id}@{source['captured_at']}"
            for source_id, source in sorted(manifest["sources"].items())
        },
        "versions": _collect_versions(),
        "environment": _environment(),
        "parameters": {
            "mode": "offline",
            "seed": DEFAULT_SEED,
            "runner": "FrozenArtifactAnalysisRunner",
            "discrepancy_threshold_pp": DISCREPANCY_THRESHOLD_PP,
            "perimeter_tolerance_m": PERIMETER_TOLERANCE_M,
            "min_cases": MIN_STUDY_CASES,
            "completeness_paths": list(COMPLETENESS_PATHS),
            "evaluation_thresholds": dict(manifest.get("evaluation_thresholds", {})),
            "tolerance_policy": "per case from manifest.cases[].tolerances",
            "binary_conditions": ["flood_intersection", "nature_intersection", "ouz_presence"],
            "measurement_kinds": MEASUREMENT_KINDS,
        },
    }
    frozen["manifest_sha256"] = _sha256_bytes(canonical_json(frozen).encode("utf-8"))
    frozen["worktree"] = _worktree_state(repo_root)
    return frozen


_TIMING_METRICS = frozenset({"duration_p50", "duration_p95"})


def split_timing(
    records: Sequence[Mapping[str, Any]], metrics: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Oddziela czas (obserwacja) od wyniku merytorycznego."""
    substantive_records = [
        {key: value for key, value in record.items() if key != "duration_ms"}
        for record in records
    ]
    substantive_metrics = {
        key: value for key, value in metrics.items() if key not in _TIMING_METRICS
    }
    timing = {
        "unit": "millisecond",
        "p50": metrics["duration_p50"]["value"],
        "p95": metrics["duration_p95"]["value"],
        "per_case": {
            record["case_id"]: float(record.get("duration_ms", 0.0)) for record in records
        },
    }
    return substantive_records, substantive_metrics, timing


def run_study_once(
    manifest: Mapping[str, Any], corpus_dir: Path
) -> dict[str, Any]:
    """Jeden bieg badania; zwraca część merytoryczną i czas osobno."""
    records, metrics = evaluate(manifest, corpus_dir)
    cases = {case["case_id"]: case for case in manifest["cases"]}
    field_rows: list[dict[str, Any]] = []
    for record in records:
        field_rows.extend(
            compare_case_fields(
                cases[record["case_id"]],
                record.get("expected_sections") or {},
                record.get("actual_sections") or {},
                record.get("ambiguous_metrics") or [],
            )
        )
    status_rows = compare_section_status(records)
    study_metrics = calculate_study_metrics(records, field_rows, status_rows)
    ledger = build_error_ledger(manifest, corpus_dir, records, field_rows, status_rows)
    substantive_records, substantive_metrics, timing = split_timing(records, metrics)
    substantive = {
        "metrics": substantive_metrics,
        "study_metrics": study_metrics,
        "cases": substantive_records,
        "field_results": field_rows,
        "section_status": status_rows,
        "error_ledger": ledger,
    }
    return {
        "substantive": substantive,
        "substantive_sha256": _sha256_bytes(canonical_json(substantive).encode("utf-8")),
        "timing": timing,
    }


def study_invariants(study_metrics: Mapping[str, Any]) -> list[str]:
    """Warunki odbioru niezależne od wartości metryk."""
    failures = []
    count = study_metrics["input_cases"]["value"]
    if count < MIN_STUDY_CASES:
        failures.append(f"input_cases={count} < {MIN_STUDY_CASES}")
    if not study_metrics["case_identity_holds"]["value"]:
        failures.append("input_cases != complete + partial + failed")
    return failures


def run_study(
    manifest: Mapping[str, Any],
    corpus_dir: Path,
    corpus_sha256: str,
    repeat: int = DEFAULT_REPEAT,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    if repeat < 1:
        raise EvaluationError("repeat must be at least 1.")
    run_manifest = build_run_manifest(manifest, corpus_sha256, repo_root)
    runs = []
    for index in range(repeat):
        started = _timestamp()
        result = run_study_once(manifest, corpus_dir)
        runs.append(
            {
                "run": index + 1,
                "started_at": started,
                "finished_at": _timestamp(),
                "substantive_sha256": result["substantive_sha256"],
                "timing": result["timing"],
                "payload": result["substantive"],
            }
        )
    hashes = [run["substantive_sha256"] for run in runs]
    determinism = {
        "repeat": repeat,
        "substantive_sha256": hashes,
        "identical": len(set(hashes)) == 1,
        "manifest_sha256": run_manifest["manifest_sha256"],
        "timing_ms": [
            {"run": run["run"], "p50": run["timing"]["p50"], "p95": run["timing"]["p95"]}
            for run in runs
        ],
        "timing_note": "Czas jest obserwacją i nie wchodzi do substantive_sha256.",
    }
    return {
        "run_manifest": run_manifest,
        "payload": runs[0]["payload"],
        "substantive_sha256": hashes[0],
        "timing": runs[0]["timing"],
        "determinism": determinism,
        "runs": runs,
    }


# --- Zapis artefaktów badania ----------------------------------------------


def _fmt(value: object, digits: int = 6) -> str:
    if value is None:
        return "null"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _md_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _metric_cell(metric: Mapping[str, Any]) -> str:
    value = metric.get("value")
    if value is None:
        return f"null ({metric.get('reason') or 'undefined'})"
    numerator, denominator = metric.get("numerator"), metric.get("denominator")
    text = f"{value:.6f}" if isinstance(value, float) else str(value)
    if numerator is not None and denominator is not None:
        return f"{text} ({numerator}/{denominator})"
    return text


def _ledger_counts(ledger: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for item in ledger:
        bucket = counts.setdefault(str(item["kind"]), {})
        bucket[str(item["category"])] = bucket.get(str(item["category"]), 0) + 1
    return counts


def render_study_report(
    run_manifest: Mapping[str, Any],
    payload: Mapping[str, Any],
    determinism: Mapping[str, Any],
    timing: Mapping[str, Any],
    substantive_sha256: str,
) -> str:
    metrics = payload["metrics"]
    study = payload["study_metrics"]
    ledger = payload["error_ledger"]
    lines = [
        "# Badanie poprawności na zamrożonym korpusie (BK-601)",
        "",
        "Wszystkie liczby pochodzą z `evaluation.json`; ten plik jest generowany i nie "
        "jest edytowany ręcznie.",
        "",
        "## Zamrożony manifest biegu",
        "",
        "| Pole | Wartość |",
        "|---|---|",
        f"| `commit_sha` | `{run_manifest['commit_sha']}` |",
        f"| `manifest_sha256` | `{run_manifest['manifest_sha256']}` |",
        f"| `corpus_sha256` | `{run_manifest['corpus_sha256']}` |",
        f"| `corpus_id` | `{run_manifest['corpus_id']}` |",
        f"| `substantive_sha256` | `{substantive_sha256}` |",
        f"| `source_release_ids` | `{canonical_json(run_manifest['source_release_ids'])}` |",
        f"| `versions` | `{canonical_json(run_manifest['versions'])}` |",
        f"| `environment` | `{canonical_json(run_manifest['environment'])}` |",
        f"| `worktree.dirty` | `{run_manifest['worktree']['dirty']}` "
        f"({len(run_manifest['worktree']['changed_paths'])} ścieżek) |",
        "",
        "Pełne parametry ewaluacji, skróty wszystkich artefaktów korpusu i odcisk kodu "
        "są w `run_manifest.json`. `manifest_sha256` obejmuje wejście badania, a nie "
        "czas ani listę zmienionych plików.",
        "",
        "## Liczność i mianowniki",
        "",
        "| Wielkość | Wartość |",
        "|---|---:|",
        f"| wejścia | {study['input_cases']['value']} |",
        f"| ukończone (`complete`) | {study['complete_cases']['value']} |",
        f"| częściowe (`partial`) | {study['partial_cases']['value']} |",
        f"| błędne (`failed`) | {study['failed_cases']['value']} |",
        f"| wejścia = ukończone + częściowe + błędne | "
        f"{'tak' if study['case_identity_holds']['value'] else 'NIE'} |",
        "",
        "## Metryki BK-004 (nagłówkowe) z mianownikami",
        "",
        "| Metryka | Wartość | Jednostka |",
        "|---|---|---|",
    ]
    for name, metric in metrics.items():
        if name == "binary_conditions" or not isinstance(metric, Mapping):
            continue
        lines.append(f"| `{name}` | {_metric_cell(metric)} | {metric.get('unit', '')} |")
    lines.extend(
        [
            "",
            "### Macierze confusion warunków binarnych",
            "",
            "| Warunek | TP | FP | FN | TN | n | Precision | Recall | F1 |",
            "|---|---:|---:|---:|---:|---:|---|---|---|",
        ]
    )
    for name, item in metrics["binary_conditions"].items():
        n = item["tp"] + item["fp"] + item["fn"] + item["tn"]
        lines.append(
            f"| `{name}` | {item['tp']} | {item['fp']} | {item['fn']} | {item['tn']} | {n} | "
            f"{_metric_cell(item['precision'])} | {_metric_cell(item['recall'])} | "
            f"{_metric_cell(item['f1'])} |"
        )
    lines.extend(
        [
            "",
            "## Co naprawdę waliduje każda metryka",
            "",
            "Metryka nagłówkowa może być wysoka, bo runner BK-004 odczytuje zamrożone "
            "obserwacje. Poniższa tabela mówi, co potwierdza dany wynik.",
            "",
            "| Ścieżka | Rodzaj pomiaru | Co jest sprawdzane |",
            "|---|---|---|",
        ]
    )
    for path, item in run_manifest["parameters"]["measurement_kinds"].items():
        lines.append(f"| `{path}` | `{item['kind']}` | {_md_cell(item['what'])} |")
    lines.extend(
        [
            "",
            "## Zgodność na poziomie pól i statusów",
            "",
            f"- zgodność pól: {_metric_cell(study['field_agreement'])} "
            "(licznik: `match` + `within_tolerance`; mianownik: pola o znanej wartości "
            "oczekiwanej, bez `ambiguous` i bez `both_unknown`);",
            f"- zgodność statusów sekcji: {_metric_cell(study['status_agreement'])};",
            f"- fałszywa pewność (wartość podana, gdy ground truth mówi `unknown`): "
            f"{_metric_cell(study['false_certainty_fields'])};",
            f"- werdykty pól: `{canonical_json(study['field_verdict_counts'])}`.",
            "",
            "| Sekcja | Zgodność pól | Zgodność statusu | Statusy wyniku |",
            "|---|---|---|---|",
        ]
    )
    for section in SECTION_ORDER:
        lines.append(
            f"| `{section}` | {_metric_cell(study['field_agreement_by_section'][section])} | "
            f"{_metric_cell(study['status_agreement_by_section'][section])} | "
            f"`{canonical_json(study['section_status_counts'][section])}` |"
        )
    lines.extend(
        [
            "",
            "## Błędy pól i udziałów",
            "",
            "| Pole | Jednostka | n | MAE | max | ponad tolerancję |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for name, item in study["numeric_errors"].items():
        lines.append(
            f"| `{name}` | {item['unit']} | {item['n']} | "
            f"{item['mean_absolute_error']:.6f} | {item['max_absolute_error']:.6f} | "
            f"{item['tolerance_exceeded']} |"
        )
    above = [item for item in ledger if item.get("exceeds_0_5_pp")]
    lines.extend(
        [
            "",
            f"Porównań udziałów: {study['share_comparisons']['value']}; "
            f"MAE {_metric_cell(study['share_mean_absolute_error'])} pp; "
            f"maksimum {_metric_cell(study['share_max_absolute_error'])} pp; "
            f"powyżej {DISCREPANCY_THRESHOLD_PP} pp: "
            f"{_metric_cell(study['share_differences_above_threshold'])}.",
            "",
        ]
    )
    if above:
        lines.append("Rozbieżności powyżej progu (każda ma wpis w rejestrze błędów):")
        lines.append("")
        for item in above:
            lines.append(f"- `{item['entry_id']}` {item['case_id']} `{item['field']}`")
    else:
        lines.append(
            f"Żadne porównanie udziału nie przekracza {DISCREPANCY_THRESHOLD_PP} pp; "
            "uwaga: udziały stref POG/MPZP/OUZ są w tym runnerze odczytem obserwacji, "
            "więc wynik 0 nie dowodzi poprawności przecięcia (patrz tabela pomiarów)."
        )
    lines.extend(
        [
            "",
            "## Kompletność i manual review",
            "",
            f"- kompletność pól (stały zestaw {len(COMPLETENESS_PATHS)} pól): "
            f"{_metric_cell(metrics['field_completeness'])};",
            f"- przypadki z nieznaną sekcją: {_metric_cell(metrics['unknown_case_share'])};",
            f"- przypadki częściowe: {_metric_cell(metrics['partial_case_share'])};",
            f"- przypadki wymagające manual review: "
            f"{_metric_cell(metrics['manual_review_case_share'])}.",
            "",
            "## Rejestr błędów i ograniczeń",
            "",
            f"Wpisów: {len(ledger)}. Podział według rodzaju i kategorii przyczyny:",
            "",
            "| Rodzaj | " + " | ".join(ERROR_CATEGORIES) + " | razem |",
            "|---|" + "---:|" * (len(ERROR_CATEGORIES) + 1),
        ]
    )
    counts = _ledger_counts(ledger)
    for kind in ("case_failure", "field_discrepancy", "status_disagreement", "limitation"):
        bucket = counts.get(kind, {})
        lines.append(
            f"| `{kind}` | "
            + " | ".join(str(bucket.get(category, 0)) for category in ERROR_CATEGORIES)
            + f" | {sum(bucket.values())} |"
        )
    lines.extend(
        [
            "",
            "Szczegóły każdego wpisu, dowody i wyjaśnienia: "
            "[`error_analysis.md`](../../error_analysis.md) oraz `error_ledger.json`.",
            "",
            "## Determinizm i czas",
            "",
            f"Powtórzeń: {determinism['repeat']}; `substantive_sha256` identyczne: "
            f"**{'tak' if determinism['identical'] else 'NIE'}**.",
            "",
            "| Bieg | `substantive_sha256` |",
            "|---:|---|",
        ]
    )
    for index, digest in enumerate(determinism["substantive_sha256"], start=1):
        lines.append(f"| {index} | `{digest}` |")
    lines.extend(
        [
            "",
            f"Czas (osobna obserwacja, `timing.json`): p50 {_fmt(timing['p50'], 4)} ms, "
            f"p95 {_fmt(timing['p95'], 4)} ms. Czas różni się między biegami i nie wchodzi "
            "do skrótu merytorycznego.",
            "",
        ]
    )
    return "\n".join(lines)


def _evidence_line(item: Mapping[str, Any]) -> str:
    if item["type"] == "frozen_observation":
        return (
            f"obserwacja `{item['artifact_id']}` `{item['json_pointer']}` "
            f"(`{item['path']}`, sha256 `{str(item['sha256'])[:12]}…`) = "
            f"`{canonical_json(item['value'])[:160]}`"
        )
    if item["type"] == "ground_truth":
        return (
            f"ground truth `{item['manifest_pointer']}` = "
            f"`{canonical_json(item.get('value'))[:160]}`"
        )
    if item["type"] == "code_probe":
        return f"próba kodu `{item['call']}` → `{canonical_json(item['result'])}`"
    return f"kod `{item['ref']}` — {item['note']}"


def render_error_analysis(
    run_manifest: Mapping[str, Any],
    payload: Mapping[str, Any],
    substantive_sha256: str,
) -> str:
    """Generuje ``error_analysis.md`` wyłącznie z rejestru błędów."""
    ledger = payload["error_ledger"]
    study = payload["study_metrics"]
    by_cause: dict[str, list[Mapping[str, Any]]] = {}
    for item in ledger:
        by_cause.setdefault(str(item["root_cause"]), []).append(item)
    defects = [i for i in ledger if i["kind"] != "limitation"]
    lines = [
        "# Analiza błędów korpusu referencyjnego (BK-601)",
        "",
        f"Dokument jest generowany przez `evaluate_reference_corpus.py --study` z "
        f"`error_ledger.json` (`substantive_sha256` `{substantive_sha256}`, "
        f"`manifest_sha256` `{run_manifest['manifest_sha256']}`, commit "
        f"`{run_manifest['commit_sha']}`). Nie jest edytowany ręcznie.",
        "",
        "## Jak czytać wynik",
        "",
        "Metryki nagłówkowe BK-004 (accuracy klas stref, MAE udziałów, confusion "
        "matrix) zostały policzone na kontrakcie, w którym runner odczytuje "
        "zamrożone obserwacje. Dla udziałów stref POG/MPZP/OUZ oraz wartości NMT jest "
        "to **odczyt obserwacji**, nie niezależne przecięcie, więc wartość 1,0 lub 0 pp "
        "potwierdza wierność odczytu, a nie poprawność obliczeń. Poprawność obliczeń "
        "potwierdzają tylko pola `production_computation` (pole i obwód działki) oraz "
        "mapowanie kontraktu ryzyk. Zakres każdej metryki jest w `report.md`.",
        "",
        "Badanie nie usunęło żadnego trudnego przypadku: wszystkie "
        f"{study['input_cases']['value']} wejść są w rejestrze i w mianownikach.",
        "",
        "## Podsumowanie",
        "",
        f"- wpisów w rejestrze: {len(ledger)} (rozbieżności i awarie: {len(defects)}, "
        f"ograniczenia: {len(ledger) - len(defects)});",
        f"- zgodność pól: {_metric_cell(study['field_agreement'])}; "
        f"zgodność statusów: {_metric_cell(study['status_agreement'])};",
        f"- fałszywa pewność: {_metric_cell(study['false_certainty_fields'])};",
        f"- rozbieżności udziału > {DISCREPANCY_THRESHOLD_PP} pp: "
        f"{sum(bool(i.get('exceeds_0_5_pp')) for i in ledger)} "
        f"(z {study['share_comparisons']['value']} porównań udziałów).",
        "",
        "## Przyczyny źródłowe",
        "",
        "| Przyczyna | Kategoria | Dyspozycja | Wpisów | Przypadków |",
        "|---|---|---|---:|---:|",
    ]
    for cause_id in sorted(by_cause):
        cause = ROOT_CAUSES[cause_id]
        items = by_cause[cause_id]
        lines.append(
            f"| `{cause_id}` {_md_cell(cause['title'])} | `{cause['category']}` | "
            f"`{cause['disposition']}` | {len(items)} | {len({i['case_id'] for i in items})} |"
        )
    lines.extend(
        [
            "",
            "Kategorie: `source` (usługa lub jej brak pokrycia), `data` (zamrożona "
            "obserwacja lub etykieta ground truth), `geometry` (tolerancja lub topologia), "
            "`parser` (odczyt odpowiedzi/dokumentu), `presentation` (definicja lub "
            "reprezentacja wyniku widoczna dla użytkownika). Kategoria wynika z reguły "
            "(`basis`) i dowodu; tam, gdzie offline nie da się rozstrzygnąć przyczyny, "
            "dyspozycja to `unresolved_hypothesis`.",
            "",
        ]
    )
    for cause_id in sorted(by_cause):
        cause = ROOT_CAUSES[cause_id]
        items = by_cause[cause_id]
        lines.extend(
            [
                f"### {cause_id} — {cause['title']}",
                "",
                f"Kategoria `{cause['category']}`, dyspozycja `{cause['disposition']}`.",
                "",
                cause["description"],
                "",
                "| Wpis | Przypadek | Sekcja | Pole | Oczekiwano | Otrzymano |",
                "|---|---|---|---|---|---|",
            ]
        )
        for item in items:
            lines.append(
                f"| `{item['entry_id']}` | `{item['case_id']}` | {item['section']} | "
                f"{_md_cell(item['field'])} | `{_md_cell(canonical_json(item['expected']))}` | "
                f"`{_md_cell(canonical_json(item['actual']))}` |"
            )
        lines.append("")
    lines.extend(["## Wpisy szczegółowe i dowody", ""])
    for item in ledger:
        lines.extend(
            [
                f"### {item['entry_id']} — {item['case_id']} / {item['section']}"
                + (f" / {item['field']}" if item["field"] else ""),
                "",
                f"- rodzaj: `{item['kind']}`; kategoria: `{item['category']}`; "
                f"przyczyna: `{item['root_cause']}`; reguła: `{item['basis']}`;",
                f"- oczekiwano: `{canonical_json(item['expected'])}`; "
                f"otrzymano: `{canonical_json(item['actual'])}`"
                + (
                    f"; różnica: {item['difference']} {item['unit']}"
                    if item["difference"] is not None
                    else ""
                )
                + ";",
                f"- wyjaśnienie: {item['explanation']}",
                "- dowody:",
            ]
        )
        for evidence in item["evidence"]:
            lines.append(f"  - {_evidence_line(evidence)}")
        lines.append("")
    return "\n".join(lines)


def _study_svg(study: Mapping[str, Any]) -> str:
    rows = [
        (f"pola: {section}", study["field_agreement_by_section"][section])
        for section in SECTION_ORDER
    ]
    rows.append(("statusy sekcji", study["status_agreement"]))
    body = []
    for index, (label, metric) in enumerate(rows):
        value = float(metric["value"]) if metric["value"] is not None else 0.0
        denominator = metric["denominator"]
        y = 48 + index * 30
        body.append(f'<text x="10" y="{y}" font-size="13">{html.escape(label)}</text>')
        body.append(
            f'<rect x="170" y="{y - 14}" width="{300 * value:.3f}" height="16" fill="#2f6fed"/>'
        )
        body.append(
            f'<text x="480" y="{y}" font-size="13">{value:.1%} (n={denominator})</text>'
        )
    height = 60 + len(rows) * 30
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="{height}" '
        f'viewBox="0 0 640 {height}"><rect width="100%" height="100%" fill="white"/>'
        '<text x="10" y="22" font-size="15" font-weight="bold">'
        "Zgodność z ground truth na poziomie pól i statusów</text>"
        + "".join(body)
        + "</svg>\n"
    )


def _write_csv(path: Path, fieldnames: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: canonical_json(value) if isinstance(value, (list, dict)) else value
                    for key, value in row.items()
                }
            )


def _dump(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_study_outputs(
    output_dir: Path, error_analysis_path: Path | None, study: Mapping[str, Any]
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    run_manifest, payload = study["run_manifest"], study["payload"]
    determinism, timing = study["determinism"], study["timing"]
    digest = study["substantive_sha256"]
    _dump(output_dir / "run_manifest.json", run_manifest)
    _dump(
        output_dir / "evaluation.json",
        {
            "study_schema_version": STUDY_SCHEMA_VERSION,
            "manifest_sha256": run_manifest["manifest_sha256"],
            "substantive_sha256": digest,
            **payload,
        },
    )
    _dump(output_dir / "timing.json", timing)
    _dump(output_dir / "determinism.json", determinism)
    _dump(
        output_dir / "error_ledger.json",
        {
            "manifest_sha256": run_manifest["manifest_sha256"],
            "root_causes": ROOT_CAUSES,
            "entries": payload["error_ledger"],
        },
    )
    _write_csv(
        output_dir / "error_ledger.csv",
        (
            "entry_id", "kind", "case_id", "section", "field", "expected", "actual",
            "difference", "unit", "threshold", "exceeds_0_5_pp", "category",
            "root_cause", "disposition", "basis", "explanation",
        ),
        payload["error_ledger"],
    )
    _write_csv(
        output_dir / "field_results.csv",
        (
            "case_id", "section", "field", "kind", "unit", "expected", "actual",
            "difference", "tolerance", "verdict", "raw_verdict", "ambiguity_scope",
        ),
        payload["field_results"],
    )
    _write_csv(
        output_dir / "section_results.csv",
        ("case_id", "section", "expected_status", "actual_status", "agrees"),
        payload["section_status"],
    )
    confusion_rows = [
        {"condition": name, **{key: item[key] for key in ("tp", "fp", "fn", "tn")},
         "precision": item["precision"]["value"], "recall": item["recall"]["value"],
         "f1": item["f1"]["value"]}
        for name, item in payload["metrics"]["binary_conditions"].items()
    ]
    _write_csv(
        output_dir / "confusion.csv",
        ("condition", "tp", "fp", "fn", "tn", "precision", "recall", "f1"),
        confusion_rows,
    )
    (output_dir / "report.md").write_text(
        render_study_report(run_manifest, payload, determinism, timing, digest),
        encoding="utf-8",
    )
    (output_dir / "accuracy.svg").write_text(
        _study_svg(payload["study_metrics"]), encoding="utf-8"
    )
    if error_analysis_path is not None:
        error_analysis_path.parent.mkdir(parents=True, exist_ok=True)
        error_analysis_path.write_text(
            render_error_analysis(run_manifest, payload, digest), encoding="utf-8"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate the frozen reference corpus offline.")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--mode", choices=("offline",), default="offline")
    parser.add_argument("--fail-on-regression", action="store_true")
    parser.add_argument(
        "--study",
        action="store_true",
        help="BK-601: zamrożony manifest, porównanie pól, rejestr błędów, determinizm.",
    )
    parser.add_argument("--study-dir", type=Path, default=DEFAULT_STUDY_DIR)
    parser.add_argument("--error-analysis", type=Path, default=DEFAULT_ERROR_ANALYSIS)
    parser.add_argument("--repeat", type=int, default=DEFAULT_REPEAT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.study:
        return _main_study(args)
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


def _main_study(args: argparse.Namespace) -> int:
    try:
        corpus_path = args.corpus.resolve()
        manifest, corpus_sha256 = load_corpus(corpus_path)
        study = run_study(
            manifest, corpus_path.parent, corpus_sha256, repeat=args.repeat
        )
        write_study_outputs(args.study_dir.resolve(), args.error_analysis.resolve(), study)
    except (EvaluationError, ReferenceCorpusError, OSError, ValueError) as exc:
        print(f"study failed: {exc}", file=sys.stderr)
        return 1
    failures = study_invariants(study["payload"]["study_metrics"])
    if not study["determinism"]["identical"]:
        failures.append("repeated runs differ in substantive_sha256")
    regressions = _threshold_failures(
        study["payload"]["metrics"], manifest.get("evaluation_thresholds", {})
    )
    if failures or (args.fail_on_regression and regressions):
        print("study failed: " + "; ".join(failures + regressions), file=sys.stderr)
        return 1
    ledger = study["payload"]["error_ledger"]
    print(
        f"study complete: {study['payload']['study_metrics']['input_cases']['value']} cases, "
        f"{len(ledger)} ledger entries, identical repeats={study['determinism']['identical']} "
        f"-> {args.study_dir.resolve()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
