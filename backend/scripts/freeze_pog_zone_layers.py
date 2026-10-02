#!/usr/bin/env python3
"""Manually freezes POG zone geometries for the centroid experiment (BK-602).

This script performs real HTTP requests to the public Rejestr Urbanistyczny WFS
(``app-pog``), so it is never imported by pytest and never runs in CI. For every
reference-corpus case that has frozen POG intersection shares it requests the
``StrefaPlanistyczna`` features that intersect the parcel bounding box, parses
them with the production GML reader, stores them as GeoJSON in EPSG:2180 and
verifies that the production analyzer reproduces the corpus shares.

The frozen layers are the only input of the *exact* part of
``compare_centroid_intersection.py``; without them that part reports the cases
as not measured.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from shapely.geometry import mapping

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from scripts.compare_centroid_intersection import (  # noqa: E402
    DEFAULT_CORPUS,
    Zone,
    bounds_from_corpus,
    load_parcels,
    production_intersections,
)
from app.modules.planning.domain.pog_features import (  # noqa: E402
    FEATURE_ID_KEYS,
    SYMBOL_KEYS,
    first_string_attribute,
)
from app.services.pog_fetch import _parse_gml  # noqa: E402

WFS_URL = "https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe/app-pog/wfs"
TYPE_NAME = "app-pog:StrefaPlanistyczna"
SHARE_TOLERANCE_PP = 0.1
BBOX_PAD_M = 1.0
TIMEOUT_S = 90.0
ATTRIBUTION = "Rejestr Urbanistyczny, publiczna usługa WFS app-pog; dane bez opłat i ograniczeń dostępu wg GetCapabilities."


def request_params(bbox: Sequence[float], *, swap_axes: bool) -> dict[str, str]:
    minx, miny, maxx, maxy = bbox
    values = (miny, minx, maxy, maxx) if swap_axes else (minx, miny, maxx, maxy)
    return {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": TYPE_NAME,
        "srsName": "EPSG:2180",
        "count": "100",
        "bbox": ",".join(f"{value:.3f}" for value in values) + ",EPSG:2180",
    }


_LEGACY_SRS = re.compile(rb"http://www\.opengis\.net/gml/srs/epsg\.xml#(\d+)")


def normalize_legacy_srs(content: bytes) -> bytes:
    """Rewrites the legacy ``gml/srs/epsg.xml#2180`` srsName used by the RU WFS.

    The production reader ``pog_fetch._parse_gml`` rejects that URL form
    (``pyproj`` cannot resolve it), although the real RU WFS answers with it.
    Only this freezing tool applies the rewrite; the production module is left
    unchanged and the mismatch is reported as a separate finding.
    """
    return _LEGACY_SRS.sub(rb"EPSG:\1", content)


def zones_from_gml(content: bytes) -> list[Zone]:
    document = _parse_gml(normalize_legacy_srs(content))
    zones = []
    for index, feature in enumerate(document.planning_zones, start=1):
        zone_id = first_string_attribute(feature.attributes, FEATURE_ID_KEYS) or f"zone-{index}"
        symbol = first_string_attribute(feature.attributes, SYMBOL_KEYS) or ""
        zones.append(Zone(zone_id, symbol, feature.geometry))
    return zones


def verify_against_corpus(parcel: Any, zones: Sequence[Zone], expected: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Production analyzer shares per symbol versus the corpus expectation."""
    by_id = {zone.zone_id: zone.symbol for zone in zones}
    computed: dict[str, float] = {}
    for item in production_intersections(parcel, zones, "pog"):
        symbol = by_id[item["zone_id"]]
        computed[symbol] = computed.get(symbol, 0.0) + item["pct"]
    expected_by_symbol = {str(z["symbol"]): float(z["share_pct"]) for z in expected if z["symbol"] is not None}
    differences = {
        symbol: abs(computed.get(symbol, 0.0) - share) for symbol, share in expected_by_symbol.items()
    }
    extra = sorted(set(computed) - set(expected_by_symbol))
    worst = max(differences.values()) if differences else None
    ok = worst is not None and worst <= SHARE_TOLERANCE_PP and not any(computed[s] > SHARE_TOLERANCE_PP for s in extra)
    return {
        "status": "verified" if ok else "release_mismatch",
        "computed_share_pct": computed,
        "expected_share_pct": expected_by_symbol,
        "max_abs_difference_pp": worst,
        "unexpected_symbols": extra,
        "tolerance_pp": SHARE_TOLERANCE_PP,
    }


def write_layer(output_dir: Path, case_id: str, zones: Sequence[Zone]) -> tuple[str, str]:
    relative = f"layers/{case_id}.zones.geojson"
    collection = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::2180"}},
        "features": [
            {"type": "Feature", "properties": {"zone_id": zone.zone_id, "symbol": zone.symbol},
             "geometry": mapping(zone.geometry)}
            for zone in sorted(zones, key=lambda z: z.zone_id)
        ],
    }
    payload = (json.dumps(collection, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8")
    target = output_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return relative, hashlib.sha256(payload).hexdigest()


def freeze_layers(
    manifest: Mapping[str, Any],
    corpus_dir: Path,
    output_dir: Path,
    client: httpx.Client,
    url: str = WFS_URL,
    now: datetime | None = None,
) -> dict[str, Any]:
    parcels = {item["case_id"]: item for item in load_parcels(manifest, corpus_dir)}
    layers: dict[str, Any] = {}
    failures: list[dict[str, Any]] = []
    fetched_at = (now or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")
    for row in bounds_from_corpus(manifest):
        if row["layer"] != "pog":
            continue
        case_id = row["case_id"]
        parcel = parcels[case_id]["geometry"]
        expected = [
            {"symbol": symbol, "share_pct": share}
            for symbol, share in zip(row["zone_symbols"], _shares_in_symbol_order(manifest, case_id), strict=False)
        ]
        attempt_log = []
        for swap in (False, True):
            params = request_params(parcel.buffer(BBOX_PAD_M).bounds, swap_axes=swap)
            try:
                response = client.get(url, params=params, timeout=TIMEOUT_S)
                response.raise_for_status()
                zones = zones_from_gml(response.content)
            except Exception as exc:  # noqa: BLE001 - every failure is recorded in the manifest
                attempt_log.append({"swap_axes": swap, "error": f"{type(exc).__name__}: {exc}"})
                continue
            verification = verify_against_corpus(parcel, zones, expected)
            attempt_log.append({"swap_axes": swap, "features": len(zones), "status": verification["status"]})
            if verification["status"] == "verified":
                path, digest = write_layer(output_dir, case_id, zones)
                layers[f"{case_id}:pog"] = {
                    "path": path,
                    "sha256": digest,
                    "response_sha256": hashlib.sha256(response.content).hexdigest(),
                    "feature_count": len(zones),
                    "request": {"url": url, "params": params},
                    "source_release_id": f"ru_pog@{fetched_at[:10]}",
                    "fetched_at": fetched_at,
                    "verification": verification,
                    "status": "verified",
                }
                break
        else:
            failures.append({"case_id": case_id, "layer": "pog", "attempts": attempt_log})
    manifest_out = {
        "schema_version": "1.0.0",
        "attribution": ATTRIBUTION,
        "source": {"url": WFS_URL, "type_name": TYPE_NAME},
        "fetched_at": fetched_at,
        "corpus_id": manifest.get("corpus_id"),
        "layers": layers,
        "failures": failures,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest_out, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest_out


def _shares_in_symbol_order(manifest: Mapping[str, Any], case_id: str) -> list[float]:
    case = next(item for item in manifest["cases"] if item["case_id"] == case_id)
    zones = case["expected"]["pog"]["values"].get("zones", [])
    shares = []
    for zone in zones:
        share = zone.get("share")
        value = share["value"] if isinstance(share, Mapping) and "value" in share else share
        if isinstance(value, (int, float)):
            shares.append(float(value))
    return shares


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Freeze POG zone geometries from the public RU WFS (manual, network).")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--url", default=WFS_URL)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    corpus_path = args.corpus.resolve()
    manifest = json.loads(corpus_path.read_text(encoding="utf-8"))
    with httpx.Client(follow_redirects=False) as client:
        result = freeze_layers(manifest, corpus_path.parent, args.output_dir.resolve(), client, args.url)
    print(f"frozen {len(result['layers'])} layers, {len(result['failures'])} failures -> {args.output_dir.resolve()}")
    return 0 if not result["failures"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
