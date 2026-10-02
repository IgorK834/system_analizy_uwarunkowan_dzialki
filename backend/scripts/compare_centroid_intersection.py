#!/usr/bin/env python3
"""Centroid zone assignment versus full polygon intersection (BK-602).

The experiment compares two ways of assigning planning zones to a parcel in
EPSG:2180, both on the same frozen geometries:

* ``centroid``: the zone(s) that *cover* ``shapely`` ``parcel.centroid``. The
  centroid is kept where it falls (outside a concave parcel, inside a hole, on a
  zone boundary); it is never replaced by ``representative_point``. Boundary
  points use ``covers`` (boundary inclusive). When several zones cover the point
  the result is a *tie* and every tied zone is reported; a point covered by no
  zone is reported as ``none``. Both are counted as unresolved.
* ``intersection``: the production analyzers ``analyze_pog_vectors`` and
  ``calculate_mpzp_zone_intersections`` (area based, never the ``intersects``
  predicate).

Three independent evidence sources are produced, none of them averaged:

1. hand-calculated control cases (60/40 rectangle, concave polygon, hole,
   centroid on a boundary);
2. guaranteed bounds computed from the frozen zone shares of the reference
   corpus (no zone geometry needed): a single assignment must omit at least
   ``zones - 1`` zones;
3. a deterministic simulation over the real frozen parcel geometries with
   straight cut lines (labelled as a simulation, not as municipal zoning), plus
   exact results on frozen real zone layers when ``--zone-layers`` supplies them.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import platform
import subprocess
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shapely.geometry import MultiPolygon, Point, Polygon, box, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_IMPORT_CWD = Path.cwd()
try:
    os.chdir(BACKEND_DIR)
    from app.schemas.source import SourceMetadata  # noqa: E402
    from app.services.mpzp_zones import (  # noqa: E402
        ZoneGeometryCandidate,
        calculate_mpzp_zone_intersections,
    )
    from app.services.pog_analyzer import (  # noqa: E402
        INTERSECTION_AREA_TOLERANCE_SQM,
        analyze_pog_vectors,
    )
    from app.services.pog_fetch import PogVectorData, PogVectorFeature  # noqa: E402
finally:
    os.chdir(_IMPORT_CWD)

SCHEMA_VERSION = "1.0.0"
DEFAULT_CORPUS = BACKEND_DIR / "tests" / "fixtures" / "reference_corpus" / "manifest.json"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "evaluation" / "results" / "centroid"
# Shares below this are boundary touches in the MPZP analyzer; used as a
# second, stricter notion of "significant" zone next to the 1e-6 m2 tolerance.
SIGNIFICANT_SHARE_PCT = 0.1
OMITTED_AREA_THRESHOLDS_PCT = (1.0, 5.0, 10.0, 25.0, 50.0)
BOUNDARY_TOLERANCE_M = 1e-9
SIMULATION_FRACTIONS = tuple(round(0.1 * step, 1) for step in range(1, 10))
SIMULATION_ORIENTATIONS = ("vertical", "horizontal")
ENGINES = ("pog", "mpzp")


@dataclass(frozen=True)
class Zone:
    """A planning zone in EPSG:2180 with optional user-visible parameters."""

    zone_id: str
    symbol: str
    geometry: BaseGeometry
    parameters: Mapping[str, float | str | None] | None = None


@dataclass
class CaseSpec:
    case_id: str
    kind: str  # control | real_layer | simulation
    parcel: BaseGeometry
    zones: list[Zone]
    engine: str = "pog"
    source: dict[str, Any] = field(default_factory=dict)
    expected: dict[str, Any] | None = None


# --- geometry helpers ---------------------------------------------------------


def _polygons(geometry: BaseGeometry) -> list[Polygon]:
    if isinstance(geometry, Polygon):
        return [geometry]
    if isinstance(geometry, MultiPolygon):
        return list(geometry.geoms)
    return []


def centroid_location(parcel: BaseGeometry, point: Point) -> str:
    """Where the centroid falls relative to the parcel, holes included."""
    if parcel.covers(point):
        if parcel.boundary.distance(point) <= BOUNDARY_TOLERANCE_M:
            return "on_parcel_boundary"
        return "inside_parcel"
    for polygon in _polygons(parcel):
        if Polygon(polygon.exterior).covers(point):
            return "in_hole"
    return "outside_parcel"


def parcel_descriptors(parcel: BaseGeometry) -> dict[str, Any]:
    hull_area = parcel.convex_hull.area
    point = parcel.centroid
    return {
        "area_sqm": parcel.area,
        "solidity": parcel.area / hull_area if hull_area > 0 else None,
        "hole_count": sum(len(polygon.interiors) for polygon in _polygons(parcel)),
        "part_count": len(_polygons(parcel)),
        "centroid_x": point.x,
        "centroid_y": point.y,
        "centroid_location": centroid_location(parcel, point),
    }


def production_intersections(
    parcel: BaseGeometry, zones: Sequence[Zone], engine: str
) -> list[dict[str, Any]]:
    """Areas of the parcel per zone computed by a production analyzer."""
    if engine == "pog":
        metadata = SourceMetadata(source_name="centroid-experiment", confidence=1.0, manual_review_required=False)
        data = PogVectorData(
            planning_zones=[
                PogVectorFeature(
                    geometry=zone.geometry,
                    attributes={
                        "feature_id": zone.zone_id,
                        "symbol": zone.symbol,
                        **(dict(zone.parameters) if zone.parameters else {}),
                    },
                    source_crs="EPSG:2180",
                    layer_type="planning_zone",
                )
                for zone in zones
            ],
            ouz_areas=[],
            downtown_areas=[],
            app_metadata={},
            status="available",
            wms_fallback_required=False,
            source_metadata=metadata,
        )
        analysis = analyze_pog_vectors(parcel, data)
        return [
            {"zone_id": item.zone_id, "area_sqm": item.area_sqm, "pct": item.area_ratio * 100.0}
            for item in analysis.zones
        ]
    if engine == "mpzp":
        metadata = SourceMetadata(source_name="centroid-experiment", confidence=1.0, manual_review_required=False)
        result = calculate_mpzp_zone_intersections(
            parcel,
            [ZoneGeometryCandidate(zone.zone_id, zone.geometry, metadata) for zone in zones],
        )
        return [
            {"zone_id": item.zone_symbol, "area_sqm": item.intersection_area_sqm, "pct": item.area_ratio}
            for item in result.zones
            if item.intersection_area_sqm > INTERSECTION_AREA_TOLERANCE_SQM
        ]
    raise ValueError(f"Unknown engine {engine!r}")


def centroid_assignment(parcel: BaseGeometry, zones: Sequence[Zone]) -> dict[str, Any]:
    """Zones covering ``parcel.centroid``; boundary counts as covered."""
    point = parcel.centroid
    covering = sorted(zone.zone_id for zone in zones if zone.geometry.covers(point))
    status = "none" if not covering else "unique" if len(covering) == 1 else "tie"
    return {
        "point": (point.x, point.y),
        "location": centroid_location(parcel, point),
        "zone_ids": covering,
        "status": status,
    }


def _parameter_differences(
    zones: Sequence[Zone], centroid_ids: Sequence[str], intersections: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    by_id = {zone.zone_id: zone for zone in zones}
    names = sorted({name for zone in zones if zone.parameters for name in zone.parameters})
    differences = []
    for name in names:
        centroid_values = [
            by_id[zone_id].parameters.get(name) if by_id[zone_id].parameters else None
            for zone_id in centroid_ids
        ]
        visible = [
            {
                "zone_id": item["zone_id"],
                "value": (by_id[item["zone_id"]].parameters or {}).get(name),
                "share_pct": item["pct"],
            }
            for item in intersections
        ]
        distinct = {repr(entry["value"]) for entry in visible}
        seen_by_centroid = {repr(value) for value in centroid_values}
        differences.append(
            {
                "parameter": name,
                "centroid_values": centroid_values,
                "intersection_values": visible,
                "differs": distinct != seen_by_centroid,
            }
        )
    return differences


def compare_case(case: CaseSpec) -> dict[str, Any]:
    """Centroid versus intersection for one parcel; no averaging."""
    parcel_area = case.parcel.area
    intersections = production_intersections(case.parcel, case.zones, case.engine)
    intersecting = [item for item in intersections if item["area_sqm"] > INTERSECTION_AREA_TOLERANCE_SQM]
    intersecting.sort(key=lambda item: (-item["area_sqm"], item["zone_id"]))
    assignment = centroid_assignment(case.parcel, case.zones)
    intersection_ids = sorted(item["zone_id"] for item in intersecting)
    significant_ids = sorted(item["zone_id"] for item in intersecting if item["pct"] >= SIGNIFICANT_SHARE_PCT)
    centroid_ids = assignment["zone_ids"]
    missing = sorted(set(intersection_ids) - set(centroid_ids))
    missing_significant = sorted(set(significant_ids) - set(centroid_ids))
    phantom = sorted(set(centroid_ids) - set(intersection_ids))
    assigned_geometry = unary_union([z.geometry for z in case.zones if z.zone_id in set(centroid_ids)])
    assigned_area = case.parcel.intersection(assigned_geometry).area if centroid_ids else 0.0
    omitted_pct = 100.0 * (1.0 - assigned_area / parcel_area) if parcel_area > 0 else None
    dominant_id = None
    dominant_tie = False
    if intersecting:
        top = intersecting[0]["area_sqm"]
        leaders = [item for item in intersecting if abs(item["area_sqm"] - top) <= 1e-9 * max(1.0, top)]
        dominant_tie = len(leaders) > 1
        dominant_id = None if dominant_tie else leaders[0]["zone_id"]
    if assignment["status"] == "unique" and dominant_id is not None:
        centroid_is_dominant: bool | None = centroid_ids[0] == dominant_id
    elif assignment["status"] == "none" and dominant_id is not None:
        centroid_is_dominant = False
    else:
        centroid_is_dominant = None
    return {
        "case_id": case.case_id,
        "kind": case.kind,
        "engine": case.engine,
        "source": case.source,
        "parcel_area_sqm": parcel_area,
        "centroid": {
            "x": assignment["point"][0],
            "y": assignment["point"][1],
            "location": assignment["location"],
        },
        "centroid_status": assignment["status"],
        "centroid_zone_ids": centroid_ids,
        "intersection_zone_ids": intersection_ids,
        "significant_zone_ids": significant_ids,
        "missing_ids": missing,
        "missing_significant_ids": missing_significant,
        "phantom_ids": phantom,
        "dominant_id": dominant_id,
        "dominant_tie": dominant_tie,
        "centroid_is_dominant": centroid_is_dominant,
        "omitted_area_pct": omitted_pct,
        "intersections": [
            {"zone_id": item["zone_id"], "area_sqm": item["area_sqm"], "pct": item["pct"]}
            for item in intersecting
        ],
        "parameter_differences": _parameter_differences(case.zones, centroid_ids, intersecting),
        "zone_count": len(intersection_ids),
    }


# --- control cases (hand-calculated expectations) ---------------------------------


def _zone(zone_id: str, geometry: BaseGeometry, **parameters: float | str | None) -> Zone:
    return Zone(zone_id, zone_id, geometry, parameters or None)


def control_cases() -> list[CaseSpec]:
    """Four controls with expected results fixed before any run.

    Hand calculation (areas in m2):

    * ``rectangle-60-40``: parcel 10×10 = 100, zone A is x<=6 (60), B is x>=6
      (40); centroid (5, 5) lies in A, which is dominant; B is omitted (40%).
    * ``concave-l-shape``: L = 10×2 + 2×8 = 36, centroid (116/36, 116/36) =
      (3.222, 3.222) lies in the notch, outside the parcel, inside a zone that
      touches the parcel only along its boundary: a phantom zone; S (20) and W
      (16) are both missed.
    * ``hole``: 10×10 parcel with a 4×4 hole at (3..7); zones x<=5.5 (A) and
      x>=5.5 (B); parcel 84, A 45, B 39; centroid (5, 5) is in the hole but in
      A, the dominant zone; B omitted 39/84.
    * ``boundary-tie``: 10×10 split at x=5; centroid (5, 5) is on the shared
      boundary: both zones cover it (tie), shares 50/50, no dominant zone.
    """
    specs: list[CaseSpec] = []
    parcel = box(0, 0, 10, 10)
    specs.append(
        CaseSpec(
            "control-rectangle-60-40", "control", parcel,
            [_zone("A", box(0, 0, 6, 10), max_building_height_m=9.0),
             _zone("B", box(6, 0, 10, 10), max_building_height_m=12.0)],
            expected={
                "intersection": {"A": 60.0, "B": 40.0}, "centroid_zone_ids": ["A"],
                "centroid_status": "unique", "missing_ids": ["B"], "dominant_id": "A",
                "centroid_is_dominant": True, "omitted_area_pct": 40.0, "location": "inside_parcel",
            },
        )
    )
    l_shape = unary_union([box(0, 0, 10, 2), box(0, 2, 2, 10)])
    specs.append(
        CaseSpec(
            "control-concave-l-shape", "control", l_shape,
            [_zone("S", box(-1, -1, 20, 2)), _zone("W", box(-1, 2, 2, 20)), _zone("N", box(2, 2, 20, 20))],
            expected={
                "intersection": {"S": 20 / 36 * 100, "W": 16 / 36 * 100}, "centroid_zone_ids": ["N"],
                "centroid_status": "unique", "missing_ids": ["S", "W"], "phantom_ids": ["N"],
                "dominant_id": "S", "centroid_is_dominant": False, "omitted_area_pct": 100.0,
                "location": "outside_parcel",
            },
        )
    )
    holed = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)], holes=[[(3, 3), (7, 3), (7, 7), (3, 7)]])
    specs.append(
        CaseSpec(
            "control-hole", "control", holed,
            [_zone("A", box(0, 0, 5.5, 10)), _zone("B", box(5.5, 0, 10, 10))],
            expected={
                "intersection": {"A": 45 / 84 * 100, "B": 39 / 84 * 100}, "centroid_zone_ids": ["A"],
                "centroid_status": "unique", "missing_ids": ["B"], "dominant_id": "A",
                "centroid_is_dominant": True, "omitted_area_pct": 39 / 84 * 100, "location": "in_hole",
            },
        )
    )
    specs.append(
        CaseSpec(
            "control-boundary-tie", "control", parcel,
            [_zone("A", box(0, 0, 5, 10)), _zone("B", box(5, 0, 10, 10))],
            expected={
                "intersection": {"A": 50.0, "B": 50.0}, "centroid_zone_ids": ["A", "B"],
                "centroid_status": "tie", "missing_ids": [], "dominant_id": None,
                "centroid_is_dominant": None, "omitted_area_pct": 0.0, "location": "inside_parcel",
            },
        )
    )
    return specs


# --- guaranteed bounds from frozen corpus shares ----------------------------------


def _unwrap(value: Any) -> Any:
    if isinstance(value, Mapping) and set(value) == {"value", "unit"}:
        return value["value"]
    return value


def bounds_from_corpus(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Lower bounds for the centroid loss from frozen intersection shares.

    A case is included when its expected section lists at least one zone with a
    numeric share. A single assignment omits at least ``zones - 1`` zones and at
    least ``100 - dominant share`` percent of the parcel, whichever zone the
    centroid falls in; the maximum occurs when it falls in the smallest zone.
    """
    rows = []
    for case in sorted(manifest["cases"], key=lambda item: item["case_id"]):
        for layer in ("pog", "mpzp"):
            values = case["expected"][layer].get("values", {})
            zones = []
            for zone in values.get("zones", []) or []:
                share = _unwrap(zone.get("share"))
                if isinstance(share, (int, float)):
                    zones.append({"symbol": _unwrap(zone.get("symbol")), "share_pct": float(share),
                                  "area_sqm": _unwrap(zone.get("area"))})
            if not zones:
                continue
            shares = sorted((zone["share_pct"] for zone in zones), reverse=True)
            significant = [s for s in shares if s >= SIGNIFICANT_SHARE_PCT]
            rows.append(
                {
                    "case_id": case["case_id"],
                    "municipality": case.get("municipality"),
                    "layer": layer,
                    "zone_symbols": [zone["symbol"] for zone in zones],
                    "distinct_symbol_count": len({zone["symbol"] for zone in zones}),
                    "zone_count": len(zones),
                    "significant_zone_count": len(significant),
                    "shares_pct": shares,
                    "dominant_share_pct": shares[0],
                    "centroid_loses_zone_guaranteed": len(zones) >= 2,
                    "centroid_loses_significant_zone_guaranteed": len(significant) >= 2,
                    "min_missing_zones": len(zones) - 1,
                    "min_omitted_area_pct": 100.0 - shares[0],
                    "max_omitted_area_pct": 100.0 - shares[-1],
                    "status": case["expected"][layer].get("status"),
                }
            )
    return rows


# --- simulation over real parcel geometries ----------------------------------------


def load_parcels(manifest: Mapping[str, Any], corpus_dir: Path) -> list[dict[str, Any]]:
    parcels = []
    for case in sorted(manifest["cases"], key=lambda item: item["case_id"]):
        artifact_id = next(a for a in case["artifact_ids"] if a.startswith("geometry:"))
        artifact = manifest["artifacts"][artifact_id]
        feature = json.loads((corpus_dir / artifact["path"]).read_text(encoding="utf-8"))
        parcels.append(
            {
                "case_id": case["case_id"],
                "municipality": case.get("municipality"),
                "geometry": shape(feature["geometry"]),
                "artifact_id": artifact_id,
                "artifact_sha256": artifact["sha256"],
                "crs": artifact.get("crs"),
            }
        )
    return parcels


def partition_zones(parcel: BaseGeometry, orientation: str, fraction: float) -> list[Zone]:
    """Two half-plane zones cutting the parcel bounding box at ``fraction``."""
    minx, miny, maxx, maxy = parcel.bounds
    pad = 1.0
    if orientation == "vertical":
        cut = minx + fraction * (maxx - minx)
        return [
            Zone("Z1", "Z1", box(minx - pad, miny - pad, cut, maxy + pad)),
            Zone("Z2", "Z2", box(cut, miny - pad, maxx + pad, maxy + pad)),
        ]
    cut = miny + fraction * (maxy - miny)
    return [
        Zone("Z1", "Z1", box(minx - pad, miny - pad, maxx + pad, cut)),
        Zone("Z2", "Z2", box(minx - pad, cut, maxx + pad, maxy + pad)),
    ]


def simulation_cases(parcels: Sequence[Mapping[str, Any]]) -> list[CaseSpec]:
    cases = []
    for parcel in parcels:
        for orientation in SIMULATION_ORIENTATIONS:
            for fraction in SIMULATION_FRACTIONS:
                cases.append(
                    CaseSpec(
                        f"{parcel['case_id']}|{orientation}|{fraction:.1f}",
                        "simulation",
                        parcel["geometry"],
                        partition_zones(parcel["geometry"], orientation, fraction),
                        source={
                            "parcel_case_id": parcel["case_id"],
                            "artifact_id": parcel["artifact_id"],
                            "artifact_sha256": parcel["artifact_sha256"],
                            "orientation": orientation,
                            "cut_fraction": fraction,
                        },
                    )
                )
    return cases


# --- frozen real zone layers ------------------------------------------------------------


def load_zone_layers(directory: Path | None, manifest: Mapping[str, Any], corpus_dir: Path) -> tuple[list[CaseSpec], list[dict[str, Any]]]:
    """Real zone geometries frozen by ``freeze_pog_zone_layers.py``.

    Returns the loadable cases and the list of corpus cases without a layer with
    the reason, so the report never hides a missing case.
    """
    parcels = {item["case_id"]: item for item in load_parcels(manifest, corpus_dir)}
    bounds = {row["case_id"]: row for row in bounds_from_corpus(manifest)}
    layers: dict[str, Any] = {}
    if directory is not None and (directory / "manifest.json").is_file():
        layers = json.loads((directory / "manifest.json").read_text(encoding="utf-8")).get("layers", {})
    cases: list[CaseSpec] = []
    missing = []
    for case_id, row in bounds.items():
        entry = layers.get(f"{case_id}:{row['layer']}")
        if entry is not None and entry.get("status") != "verified":
            missing.append({"case_id": case_id, "layer": row["layer"],
                            "reason": f"frozen layer is {entry.get('status')}, not verified against corpus shares"})
            continue
        if entry is None:
            reason = (
                "zone geometry not frozen (RU WFS layer not captured)" if row["layer"] == "pog"
                else "source not redistributable (contract_required); geometry not frozen"
            )
            missing.append({"case_id": case_id, "layer": row["layer"], "reason": reason})
            continue
        payload = (directory / entry["path"]).read_bytes()  # type: ignore[operator]
        if hashlib.sha256(payload).hexdigest() != entry["sha256"]:
            raise ValueError(f"Zone layer {entry['path']} does not match its SHA-256.")
        collection = json.loads(payload)
        zones = [
            Zone(
                str(feature["properties"].get("zone_id") or f"zone-{index}"),
                str(feature["properties"].get("symbol") or ""),
                shape(feature["geometry"]),
            )
            for index, feature in enumerate(collection["features"], start=1)
        ]
        cases.append(
            CaseSpec(
                case_id, "real_layer", parcels[case_id]["geometry"], zones, engine="pog",
                source={
                    "layer_artifact": entry["path"], "layer_sha256": entry["sha256"],
                    "parcel_artifact_sha256": parcels[case_id]["artifact_sha256"],
                    "release": entry.get("source_release_id"),
                },
            )
        )
    return cases, missing


# --- summaries --------------------------------------------------------------------------


def _rate(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "numerator": numerator, "denominator": denominator,
        "value": numerator / denominator if denominator else None,
        "reason": None if denominator else "no_eligible_cases",
    }


def summarize(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Proportions with explicit denominators (no averages)."""
    total = len(results)
    with_zones = [r for r in results if r["zone_count"] >= 1]
    multi = [r for r in results if r["zone_count"] >= 2]
    decided = [r for r in with_zones if r["centroid_is_dominant"] is not None]
    unresolved = [r for r in results if r["centroid_status"] in {"none", "tie"}]
    omit = [r for r in results if r["missing_ids"]]
    omit_significant = [r for r in results if r["missing_significant_ids"]]
    locations = Counter(r["centroid"]["location"] for r in results)
    return {
        "cases": total,
        "cases_with_intersecting_zone": len(with_zones),
        "multi_zone_cases": len(multi),
        "centroid_omits_at_least_one_zone": _rate(len(omit), total),
        "centroid_omits_at_least_one_significant_zone": _rate(len(omit_significant), total),
        "multi_zone_centroid_omits_zone": _rate(sum(bool(r["missing_ids"]) for r in multi), len(multi)),
        "omitted_zones_total": sum(len(r["missing_ids"]) for r in results),
        "omitted_significant_zones_total": sum(len(r["missing_significant_ids"]) for r in results),
        "centroid_not_dominant_among_decided": _rate(
            sum(r["centroid_is_dominant"] is False for r in decided), len(decided)
        ),
        "centroid_not_dominant_unresolved_counted_as_not": _rate(
            sum(r["centroid_is_dominant"] is not True for r in with_zones), len(with_zones)
        ),
        "centroid_unresolved": _rate(len(unresolved), total),
        "omitted_area_at_least": {
            f"{threshold:g}": _rate(
                sum(
                    r["omitted_area_pct"] is not None and r["omitted_area_pct"] >= threshold
                    for r in with_zones
                ),
                len(with_zones),
            )
            for threshold in OMITTED_AREA_THRESHOLDS_PCT
        },
        "centroid_status_counts": dict(sorted(Counter(r["centroid_status"] for r in results).items())),
        "centroid_phantom_zone": _rate(sum(bool(r["phantom_ids"]) for r in results), total),
        "centroid_location_counts": dict(sorted(locations.items())),
        "parameter_differences": {
            "cases_with_parameters": sum(bool(r["parameter_differences"]) for r in results),
            "cases_where_user_would_see_a_difference": sum(
                any(d["differs"] for d in r["parameter_differences"]) for r in results
            ),
        },
    }


def simulation_breakdown(results: Sequence[Mapping[str, Any]], solidity: Mapping[str, float | None]) -> dict[str, Any]:
    """Simulation split by parcel solidity (area / convex hull area)."""
    bins = (("solidity<0.80", 0.0, 0.80), ("0.80<=solidity<0.95", 0.80, 0.95), ("solidity>=0.95", 0.95, 1.0001))
    result = {}
    for name, low, high in bins:
        subset = [
            r for r in results
            if solidity.get(r["source"]["parcel_case_id"]) is not None
            and low <= solidity[r["source"]["parcel_case_id"]] < high
        ]
        result[name] = {
            "parcels": len({r["source"]["parcel_case_id"] for r in subset}),
            **summarize(subset),
        }
    return result


def check_controls(results: Sequence[Mapping[str, Any]], specs: Sequence[CaseSpec]) -> list[str]:
    """Compares control results with their hand-calculated expectations."""
    failures: list[str] = []
    by_id = {r["case_id"]: r for r in results}
    for spec in specs:
        expected, actual = spec.expected or {}, by_id[spec.case_id]
        for zone_id, pct in expected["intersection"].items():
            found = {i["zone_id"]: i["pct"] for i in actual["intersections"]}.get(zone_id)
            if found is None or abs(found - pct) > 1e-6:
                failures.append(f"{spec.case_id}: share of {zone_id} is {found}, expected {pct}")
        if sorted(actual["intersection_zone_ids"]) != sorted(expected["intersection"]):
            failures.append(f"{spec.case_id}: intersection zones differ")
        for key, actual_key in (
            ("centroid_zone_ids", "centroid_zone_ids"), ("centroid_status", "centroid_status"),
            ("missing_ids", "missing_ids"), ("dominant_id", "dominant_id"),
            ("centroid_is_dominant", "centroid_is_dominant"),
        ):
            if actual[actual_key] != expected[key]:
                failures.append(f"{spec.case_id}: {key} is {actual[actual_key]!r}, expected {expected[key]!r}")
        if abs(actual["omitted_area_pct"] - expected["omitted_area_pct"]) > 1e-6:
            failures.append(f"{spec.case_id}: omitted {actual['omitted_area_pct']}, expected {expected['omitted_area_pct']}")
        if actual["centroid"]["location"] != expected["location"]:
            failures.append(f"{spec.case_id}: centroid location {actual['centroid']['location']}")
        if "phantom_ids" in expected and actual["phantom_ids"] != expected["phantom_ids"]:
            failures.append(f"{spec.case_id}: phantom zones {actual['phantom_ids']}")
    return failures


def engine_agreement(specs: Sequence[CaseSpec]) -> list[dict[str, Any]]:
    """The two production analyzers must give the same areas on the same input."""
    rows = []
    for spec in specs:
        pog = {i["zone_id"]: i for i in production_intersections(spec.parcel, spec.zones, "pog")}
        mpzp = {i["zone_id"]: i for i in production_intersections(spec.parcel, spec.zones, "mpzp")}
        same = set(pog) == set(mpzp) and all(abs(pog[z]["area_sqm"] - mpzp[z]["area_sqm"]) < 1e-9 for z in pog)
        rows.append({"case_id": spec.case_id, "same_zones_and_areas": same,
                     "pog_zones": sorted(pog), "mpzp_zones": sorted(mpzp)})
    return rows


# --- SVG maps ---------------------------------------------------------------------------


_PALETTE = ("#4c78a8", "#f58518", "#54a24b", "#b279a2", "#e45756")


def _path(geometry: BaseGeometry, transform: Any) -> str:
    parts = []
    for polygon in _polygons(geometry):
        for ring in (polygon.exterior, *polygon.interiors):
            points = " L ".join(f"{transform(x, y)[0]:.2f} {transform(x, y)[1]:.2f}" for x, y in ring.coords)
            parts.append(f"M {points} Z")
    return " ".join(parts)


def render_map(result: Mapping[str, Any], case: CaseSpec) -> str:
    """One self-describing SVG: parcel, zones clipped to the view, centroid, hashes."""
    minx, miny, maxx, maxy = case.parcel.bounds
    pad = 0.08 * max(maxx - minx, maxy - miny, 1.0)
    view = box(minx - pad, miny - pad, maxx + pad, maxy + pad)
    width = 320.0
    scale = width / (view.bounds[2] - view.bounds[0])
    height = (view.bounds[3] - view.bounds[1]) * scale

    def transform(x: float, y: float) -> tuple[float, float]:
        return (x - view.bounds[0]) * scale, height - (y - view.bounds[1]) * scale

    layers = []
    for index, zone in enumerate(sorted(case.zones, key=lambda z: z.zone_id)):
        clipped = zone.geometry.intersection(view)
        if clipped.is_empty:
            continue
        colour = _PALETTE[index % len(_PALETTE)]
        layers.append(
            f'<path d="{_path(clipped, transform)}" fill="{colour}" fill-opacity="0.25" '
            f'stroke="{colour}" stroke-width="1"><title>{html.escape(zone.zone_id)}</title></path>'
        )
    layers.append(
        f'<path d="{_path(case.parcel, transform)}" fill="none" stroke="#111" stroke-width="2" fill-rule="evenodd"/>'
    )
    cx, cy = transform(result["centroid"]["x"], result["centroid"]["y"])
    layers.append(f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="4" fill="#d00"/>')
    caption = (
        f"{result['case_id']} | centroid {result['centroid']['location']} -> "
        f"{','.join(result['centroid_zone_ids']) or 'none'} ({result['centroid_status']}) | "
        f"intersection {','.join(result['intersection_zone_ids'])} | dominant {result['dominant_id']}"
    )
    source = json.dumps(result["source"], ensure_ascii=False, sort_keys=True)
    geometry_hash = hashlib.sha256(
        (case.parcel.wkb_hex + "".join(z.geometry.wkb_hex for z in case.zones)).encode()
    ).hexdigest()
    total_height = height + 54
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{total_height:.0f}" '
        f'viewBox="0 0 {width:.0f} {total_height:.0f}"><metadata>{html.escape(source)}; '
        f"input_geometry_sha256={geometry_hash}</metadata>"
        f'<rect width="100%" height="100%" fill="white"/>{"".join(layers)}'
        f'<text x="4" y="{height + 14:.0f}" font-size="9">{html.escape(caption)}</text>'
        f'<text x="4" y="{height + 28:.0f}" font-size="8">geometry sha256 {geometry_hash[:32]}…</text>'
        f'<text x="4" y="{height + 42:.0f}" font-size="8">EPSG:2180; centroid = shapely centroid, not representative_point</text>'
        "</svg>\n"
    )


# --- run, manifest, reports ---------------------------------------------------------------


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _git_commit_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, check=True, capture_output=True, text=True, timeout=5
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run_experiment(
    manifest: Mapping[str, Any], corpus_dir: Path, zone_layers: Path | None = None
) -> dict[str, Any]:
    specs = control_cases()
    controls = [compare_case(spec) for spec in specs]
    control_failures = check_controls(controls, specs)
    parcels = load_parcels(manifest, corpus_dir)
    descriptors = {p["case_id"]: parcel_descriptors(p["geometry"]) for p in parcels}
    sim_specs = simulation_cases(parcels)
    simulation = [compare_case(spec) for spec in sim_specs]
    layer_specs, layer_missing = load_zone_layers(zone_layers, manifest, corpus_dir)
    layer_results = [compare_case(spec) for spec in layer_specs]
    bounds = bounds_from_corpus(manifest)
    solidity = {case_id: d["solidity"] for case_id, d in descriptors.items()}
    two_zone = [r for r in simulation if r["zone_count"] >= 2]
    return {
        "controls": controls,
        "control_failures": control_failures,
        "engine_agreement": engine_agreement(specs),
        "real_layers": {"results": layer_results, "missing": layer_missing,
                        "summary": summarize(layer_results)},
        "bounds": bounds,
        "bounds_summary": {
            "cases": len(bounds),
            "multi_zone_guaranteed_loss": _rate(sum(r["centroid_loses_zone_guaranteed"] for r in bounds), len(bounds)),
            "multi_significant_zone_guaranteed_loss": _rate(
                sum(r["centroid_loses_significant_zone_guaranteed"] for r in bounds), len(bounds)
            ),
            "min_missing_zones_total": sum(r["min_missing_zones"] for r in bounds),
        },
        "parcel_descriptors": descriptors,
        "parcel_descriptor_summary": {
            "parcels": len(descriptors),
            "centroid_location_counts": dict(sorted(Counter(d["centroid_location"] for d in descriptors.values()).items())),
            "parcels_with_holes": sum(d["hole_count"] > 0 for d in descriptors.values()),
            "parcels_solidity_below_0_95": sum(
                d["solidity"] is not None and d["solidity"] < 0.95 for d in descriptors.values()
            ),
        },
        "simulation": simulation,
        "simulation_summary": {
            "parameters": {
                "orientations": list(SIMULATION_ORIENTATIONS), "cut_fractions": list(SIMULATION_FRACTIONS),
                "zones_per_scenario": 2, "engine": "pog",
            },
            "all_scenarios": summarize(simulation),
            "two_zone_scenarios": summarize(two_zone),
            "by_solidity": simulation_breakdown(two_zone, solidity),
        },
    }


def build_run_manifest(manifest: Mapping[str, Any], corpus_sha256: str, zone_layers: Path | None) -> dict[str, Any]:
    import shapely

    files = ("scripts/compare_centroid_intersection.py", "app/services/pog_analyzer.py",
             "app/services/mpzp_zones.py")
    layers_manifest = (zone_layers / "manifest.json") if zone_layers else None
    frozen = {
        "schema_version": SCHEMA_VERSION,
        "commit_sha": _git_commit_sha(),
        "corpus_id": manifest.get("corpus_id"),
        "corpus_sha256": corpus_sha256,
        "zone_layers_manifest_sha256": (
            hashlib.sha256(layers_manifest.read_bytes()).hexdigest()
            if layers_manifest is not None and layers_manifest.is_file() else None
        ),
        "code_fingerprint": {
            name: hashlib.sha256((BACKEND_DIR / name).read_bytes()).hexdigest() for name in files
        },
        "parameters": {
            "crs": "EPSG:2180", "centroid": "shapely parcel.centroid (never representative_point)",
            "boundary_rule": "covers (boundary inclusive); several covering zones = tie, none = none",
            "intersection_engine": "production analyze_pog_vectors / calculate_mpzp_zone_intersections",
            "area_tolerance_sqm": INTERSECTION_AREA_TOLERANCE_SQM,
            "significant_share_pct": SIGNIFICANT_SHARE_PCT,
            "simulation_fractions": list(SIMULATION_FRACTIONS),
            "simulation_orientations": list(SIMULATION_ORIENTATIONS),
            "randomness": "none (deterministic)",
        },
        "environment": {"python": platform.python_version(), "shapely": shapely.__version__,
                        "geos": shapely.geos_version_string},
    }
    frozen["manifest_sha256"] = hashlib.sha256(canonical_json(frozen).encode()).hexdigest()
    return frozen


def _cell(rate: Mapping[str, Any]) -> str:
    if rate["value"] is None:
        return f"null ({rate['reason']})"
    return f"{rate['value']:.3f} ({rate['numerator']}/{rate['denominator']})"


def _summary_rows(title: str, summary: Mapping[str, Any]) -> list[str]:
    return [
        f"### {title}", "",
        f"- przypadków: {summary['cases']} (z przecięciem ze strefą: {summary['cases_with_intersecting_zone']}, "
        f"wielostrefowych: {summary['multi_zone_cases']});",
        f"- centroid pomija co najmniej jedną strefę: {_cell(summary['centroid_omits_at_least_one_zone'])};",
        f"- … istotną (udział ≥ {SIGNIFICANT_SHARE_PCT}%): {_cell(summary['centroid_omits_at_least_one_significant_zone'])};",
        f"- wśród wielostrefowych: {_cell(summary['multi_zone_centroid_omits_zone'])};",
        f"- liczba pominiętych stref (suma): {summary['omitted_zones_total']} "
        f"(istotnych: {summary['omitted_significant_zones_total']}); dla działek przecinających dwie strefy "
        "pominięcie strefy jest z definicji pewne (jedno przypisanie), więc informacyjna jest wielkość "
        "pominiętego obszaru;",
        "- pominięty obszar działki ≥ próg: "
        + "; ".join(
            f"≥{threshold}%: {_cell(rate)}" for threshold, rate in summary["omitted_area_at_least"].items()
        )
        + ";",
        f"- strefa centroidu ≠ strefa o największym polu (tylko rozstrzygnięte): "
        f"{_cell(summary['centroid_not_dominant_among_decided'])};",
        f"- … gdy remis/brak strefy liczymy jako „nie dominująca”: "
        f"{_cell(summary['centroid_not_dominant_unresolved_counted_as_not'])};",
        f"- brak rozstrzygnięcia centroidu (remis lub brak strefy): {_cell(summary['centroid_unresolved'])} "
        f"`{canonical_json(summary['centroid_status_counts'])}`;",
        f"- centroid w strefie, która nie przecina działki (strefa-widmo): {_cell(summary['centroid_phantom_zone'])};",
        f"- położenie centroidu: `{canonical_json(summary['centroid_location_counts'])}`.", "",
    ]


def render_report(run_manifest: Mapping[str, Any], result: Mapping[str, Any]) -> str:
    lines = [
        "# Eksperyment centroid vs pełne przecięcie (BK-602)", "",
        "Plik jest generowany z `summary.json`; nie jest edytowany ręcznie.", "",
        "## Zamrożony manifest", "",
        "| Pole | Wartość |", "|---|---|",
        f"| `commit_sha` | `{run_manifest['commit_sha']}` |",
        f"| `manifest_sha256` | `{run_manifest['manifest_sha256']}` |",
        f"| `corpus_sha256` | `{run_manifest['corpus_sha256']}` |",
        f"| `zone_layers_manifest_sha256` | `{run_manifest['zone_layers_manifest_sha256']}` |", "",
        "Reguła: centroid to `shapely` `parcel.centroid` w EPSG:2180, zachowany tam, gdzie wypada (poza "
        "działką wklęsłą, w dziurze). Punkt na granicy liczy się przez `covers`; kilka stref pokrywających "
        "punkt to remis (raportowane wszystkie), brak strefy to `none`; oba są „bez rozstrzygnięcia”. "
        "Przecięcia liczą produkcyjne analizatory POG i MPZP (pola w m², nie predykat `intersects`).", "",
        "## 1. Kontrole z ręcznie ustalonymi wynikami", "",
        "| Przypadek | Położenie centroidu | Strefy centroidu | Strefy przecięcia (udział %) | Pominięte | "
        "Widmo | Dominująca | centroid = dominująca | Pominięty obszar % |",
        "|---|---|---|---|---|---|---|---|---:|",
    ]
    for control in result["controls"]:
        shares = ", ".join(f"{i['zone_id']} {i['pct']:.3f}" for i in control["intersections"])
        lines.append(
            f"| `{control['case_id']}` | {control['centroid']['location']} | "
            f"{','.join(control['centroid_zone_ids']) or '—'} ({control['centroid_status']}) | {shares} | "
            f"{','.join(control['missing_ids']) or '—'} | {','.join(control['phantom_ids']) or '—'} | "
            f"{control['dominant_id'] or 'remis'} | {control['centroid_is_dominant']} | "
            f"{control['omitted_area_pct']:.3f} |"
        )
    failures = result["control_failures"]
    lines.extend(
        ["", "Zgodność z oczekiwaniami policzonymi ręcznie: "
         + ("**tak**" if not failures else "**NIE**: " + "; ".join(failures)),
         f"Zgodność dwóch produkcyjnych analizatorów (POG i MPZP) na kontrolach: "
         f"**{'tak' if all(r['same_zones_and_areas'] for r in result['engine_agreement']) else 'NIE'}**.", ""]
    )
    differences = [
        (c["case_id"], d) for c in result["controls"] for d in c["parameter_differences"]
    ]
    if differences:
        lines.append("Różnica parametrów widocznych dla użytkownika (kontrola z parametrami, bez średnich):")
        lines.append("")
        for case_id, item in differences:
            visible = "; ".join(f"{v['zone_id']}={v['value']} ({v['share_pct']:.1f}%)" for v in item["intersection_values"])
            lines.append(f"- `{case_id}` `{item['parameter']}`: centroid {item['centroid_values']}, "
                         f"przecięcie {visible}; różnica: **{'tak' if item['differs'] else 'nie'}**.")
        lines.append("")
    bounds = result["bounds"]
    lines.extend(
        [
            "## 2. Dolne granice utraty informacji z zamrożonych udziałów korpusu (dane rzeczywiste)", "",
            "Jedno przypisanie pomija co najmniej `k−1` stref i co najmniej `100 − udział największej` procent "
            "działki, niezależnie od tego, gdzie wypadnie centroid. Wynik nie wymaga geometrii stref.", "",
            f"Przypadki z zamrożonymi udziałami stref: {result['bounds_summary']['cases']}; gwarantowana utrata "
            f"strefy: {_cell(result['bounds_summary']['multi_zone_guaranteed_loss'])}; utrata strefy o udziale "
            f"≥ {SIGNIFICANT_SHARE_PCT}%: {_cell(result['bounds_summary']['multi_significant_zone_guaranteed_loss'])}.", "",
            "| Przypadek | Warstwa | Strefy | Udziały % | Min. pominięte strefy | Min. pominięty obszar % | Maks. pominięty obszar % |",
            "|---|---|---|---|---:|---:|---:|",
        ]
    )
    for row in bounds:
        shares = ", ".join(f"{s:.4f}" for s in row["shares_pct"])
        lines.append(
            f"| `{row['case_id']}` | {row['layer']} | {','.join(map(str, row['zone_symbols']))} | {shares} | "
            f"{row['min_missing_zones']} | {row['min_omitted_area_pct']:.4f} | {row['max_omitted_area_pct']:.4f} |"
        )
    layers = result["real_layers"]
    lines.extend(["", "## 3. Wyniki dokładne na zamrożonych geometriach stref rzeczywistych", ""])
    if layers["results"]:
        lines.extend(_summary_rows("Zamrożone warstwy stref", layers["summary"]))
        lines.extend(
            ["| Przypadek | Strefy centroidu | Strefy przecięcia | Pominięte | Dominująca | centroid = dominująca | Pominięty obszar % |",
             "|---|---|---|---|---|---|---:|"]
        )
        for item in layers["results"]:
            lines.append(
                f"| `{item['case_id']}` | {','.join(item['centroid_zone_ids']) or '—'} ({item['centroid_status']}) | "
                f"{','.join(item['intersection_zone_ids'])} | {','.join(item['missing_ids']) or '—'} | "
                f"{item['dominant_id'] or 'remis'} | {item['centroid_is_dominant']} | {item['omitted_area_pct']:.3f} |"
            )
        lines.append("")
    else:
        lines.append("Brak zamrożonych geometrii stref rzeczywistych: pomiar dokładny dla poniższych przypadków "
                     "nie został wykonany (nie jest zastąpiony żadną wartością).")
        lines.append("")
    if layers["missing"]:
        lines.extend(["| Przypadek | Warstwa | Powód braku |", "|---|---|---|"])
        for item in layers["missing"]:
            lines.append(f"| `{item['case_id']}` | {item['layer']} | {item['reason']} |")
        lines.append("")
    sim = result["simulation_summary"]
    desc = result["parcel_descriptor_summary"]
    lines.extend(
        [
            "## 4. Symulacja na rzeczywistych geometriach 30 działek (nie jest to zagospodarowanie gminy)", "",
            f"Dla każdej działki korpusu: 2 orientacje × 9 ułamków cięcia ({', '.join(map(str, SIMULATION_FRACTIONS))}) "
            "= 18 scenariuszy; dwie strefy to półpłaszczyzny przecięte w prostokącie otaczającym. Wszystkie "
            "scenariusze są raportowane, bez wyboru niekorzystnych.", "",
            f"Działki: {desc['parcels']}; położenie centroidu względem działki: `{canonical_json(desc['centroid_location_counts'])}`; "
            f"działki z dziurami: {desc['parcels_with_holes']}; działki o wypukłości < 0,95: {desc['parcels_solidity_below_0_95']}.", "",
        ]
    )
    lines.extend(_summary_rows("Wszystkie scenariusze", sim["all_scenarios"]))
    lines.extend(_summary_rows("Scenariusze, w których działka przecina obie strefy", sim["two_zone_scenarios"]))
    lines.extend(["### Według wypukłości działki (solidity = pole / pole otoczki wypukłej; scenariusze dwustrefowe)", "",
                  "| Przedział | działki | scenariusze | pominięty obszar ≥ 10% | pominięty obszar ≥ 25% | centroid ≠ dominująca (rozstrzygnięte) | bez rozstrzygnięcia |",
                  "|---|---:|---:|---|---|---|---|"])
    for name, item in sim["by_solidity"].items():
        lines.append(
            f"| `{name}` | {item['parcels']} | {item['cases']} | {_cell(item['omitted_area_at_least']['10'])} | "
            f"{_cell(item['omitted_area_at_least']['25'])} | "
            f"{_cell(item['centroid_not_dominant_among_decided'])} | {_cell(item['centroid_unresolved'])} |"
        )
    lines.extend(
        ["", "## Ograniczenia", "",
         "- geometrie stref rzeczywistych nie są zamrożone (host Rejestru Urbanistycznego był nieosiągalny "
         "podczas badania); do czasu ich zamrożenia sekcja 3 jest pusta, a wnioski o dokładnym odsetku działek "
         "z pominięciem strefy opierają się na granicach (sekcja 2) i symulacji (sekcja 4);",
         "- warstwy MPZP Krakowa nie mogą być redystrybuowane (katalog: `contract_required`), więc ich geometrii "
         "nie zamrożono nawet po odzyskaniu dostępu; dla nich zostają granice z zamrożonych udziałów;",
         "- symulacja pokazuje własność metody centroidu na rzeczywistych kształtach działek, nie częstość "
         "granic stref w Polsce; liczby zależą od przyjętej rodziny cięć;",
         "- aplikacja produkcyjna nie przypisuje stref po samym centroidzie (discovery MPZP używa wielu punktów, "
         "a analiza POG i MPZP — pełnych przecięć); eksperyment uzasadnia tę decyzję, nie mierzy błędu produktu.", ""]
    )
    return "\n".join(lines)


def _write_csv(path: Path, fieldnames: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: canonical_json(v) if isinstance(v, (list, dict)) else v for k, v in row.items()})


def _flatten(result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **{key: result[key] for key in (
            "case_id", "kind", "engine", "parcel_area_sqm", "centroid_status", "centroid_zone_ids",
            "intersection_zone_ids", "significant_zone_ids", "missing_ids", "missing_significant_ids",
            "phantom_ids", "dominant_id", "dominant_tie", "centroid_is_dominant", "omitted_area_pct",
            "zone_count",
        )},
        "centroid_location": result["centroid"]["location"],
        "intersection_shares": {i["zone_id"]: i["pct"] for i in result["intersections"]},
    }


def write_outputs(
    output_dir: Path, run_manifest: Mapping[str, Any], result: Mapping[str, Any],
    manifest: Mapping[str, Any], corpus_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "examples").mkdir(exist_ok=True)
    payload = {"manifest_sha256": run_manifest["manifest_sha256"], **{
        key: result[key] for key in (
            "controls", "control_failures", "engine_agreement", "real_layers", "bounds", "bounds_summary",
            "parcel_descriptor_summary", "simulation_summary",
        )}}
    (output_dir / "run_manifest.json").write_text(
        json.dumps(run_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_dir / "cases.json").write_text(
        json.dumps(
            {"manifest_sha256": run_manifest["manifest_sha256"], "controls": result["controls"],
             "real_layers": result["real_layers"]["results"], "simulation": result["simulation"],
             "parcel_descriptors": result["parcel_descriptors"]},
            ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    fields = ("case_id", "kind", "engine", "parcel_area_sqm", "centroid_location", "centroid_status",
              "centroid_zone_ids", "intersection_zone_ids", "intersection_shares", "significant_zone_ids",
              "missing_ids", "missing_significant_ids", "phantom_ids", "dominant_id", "dominant_tie",
              "centroid_is_dominant", "omitted_area_pct", "zone_count")
    _write_csv(output_dir / "cases.csv", fields,
               [_flatten(r) for r in (*result["controls"], *result["real_layers"]["results"], *result["simulation"])])
    _write_csv(output_dir / "bounds.csv",
               ("case_id", "municipality", "layer", "zone_symbols", "distinct_symbol_count", "zone_count", "significant_zone_count",
                "shares_pct", "dominant_share_pct", "centroid_loses_zone_guaranteed",
                "centroid_loses_significant_zone_guaranteed", "min_missing_zones", "min_omitted_area_pct",
                "max_omitted_area_pct", "status"), result["bounds"])
    (output_dir / "report.md").write_text(render_report(run_manifest, result), encoding="utf-8")
    # example maps: all controls, plus one simulated real parcel chosen by a fixed rule
    specs = {spec.case_id: spec for spec in control_cases()}
    for control in result["controls"]:
        (output_dir / "examples" / f"{control['case_id']}.svg").write_text(
            render_map(control, specs[control["case_id"]]), encoding="utf-8")
    parcels = {p["case_id"]: p for p in load_parcels(manifest, corpus_dir)}
    descriptors = result["parcel_descriptors"]
    lowest = min(sorted(descriptors), key=lambda case_id: descriptors[case_id]["solidity"] or 1.0)
    sim_case = next(c for c in simulation_cases([parcels[lowest]]) if c.case_id.endswith("|vertical|0.5"))
    sim_result = next(r for r in result["simulation"] if r["case_id"] == sim_case.case_id)
    (output_dir / "examples" / "simulation-lowest-solidity.svg").write_text(
        render_map(sim_result, sim_case), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Centroid versus full intersection experiment (offline).")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--zone-layers", type=Path, default=None,
                        help="Directory with manifest.json from freeze_pog_zone_layers.py (frozen zone geometries).")
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--mode", choices=("offline",), default="offline")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.repeat < 1:
        print("experiment failed: --repeat must be at least 1", file=sys.stderr)
        return 1
    try:
        corpus_path = args.corpus.resolve()
        payload = corpus_path.read_bytes()
        manifest = json.loads(payload)
        corpus_sha256 = hashlib.sha256(payload).hexdigest()
        zone_layers = args.zone_layers.resolve() if args.zone_layers else None
        runs = [run_experiment(manifest, corpus_path.parent, zone_layers) for _ in range(args.repeat)]
    except (OSError, ValueError, KeyError, StopIteration) as exc:
        print(f"experiment failed: {exc}", file=sys.stderr)
        return 1
    digests = [hashlib.sha256(canonical_json(run).encode()).hexdigest() for run in runs]
    if len(set(digests)) != 1:
        print("experiment failed: repeated runs differ", file=sys.stderr)
        return 1
    result = runs[0]
    if result["control_failures"]:
        print("experiment failed: control cases differ from hand calculation: "
              + "; ".join(result["control_failures"]), file=sys.stderr)
        return 1
    run_manifest = build_run_manifest(manifest, corpus_sha256, zone_layers)
    write_outputs(args.output_dir.resolve(), run_manifest, result, manifest, corpus_path.parent)
    (args.output_dir.resolve() / "determinism.json").write_text(
        json.dumps({"repeat": args.repeat, "result_sha256": digests, "identical": True,
                    "timestamp": _timestamp()}, indent=2) + "\n", encoding="utf-8")
    overall = result["simulation_summary"]["two_zone_scenarios"]["omitted_area_at_least"]["10"]
    print(f"experiment complete: {len(result['controls'])} controls, {len(result['bounds'])} corpus bound cases, "
          f"{len(result['simulation'])} simulated scenarios, real layers: {len(result['real_layers']['results'])}; "
          f"two-zone simulated omitted>=10% {_cell(overall)} -> {args.output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
