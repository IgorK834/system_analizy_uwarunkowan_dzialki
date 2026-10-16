#!/usr/bin/env python3
"""Niezależny pomiar GDAL (nie QGIS) wartości oczekiwanych drogi i zabudowy dla działek korpusu (Task 23.2–23.3).

Wartości z ``tests/fixtures/bdot10k/<powiat>/expected.json`` zostały policzone Shapely z ``gml:posList``. Ten skrypt powtarza
pomiar zupełnie innym łańcuchem narzędzi: GDAL (sterownik GML czyta mini-paczki z fixtures) oraz funkcje przestrzenne
dialektu SQLite w ``ogr2ogr`` (``ST_Distance``, ``ST_Buffer``, ``ST_Intersection`` — GEOS wywoływany przez GDAL). Nie importuje
niczego z aplikacji, Shapely ani PostGIS; działka jest budowana jako WKT wprost z GeoJSON korpusu.

Nie jest to pomiar w QGIS: QGIS nie był dostępny w środowisku wykonania. Zakres porównania: odległość, relacja i identyfikator
najbliższej osi jezdni (promień do 500 m), liczba budynków na działce i pole ich części na działce oraz — dla buforów 50/100/200 m
(pierścień ``buffer(działka, r, 16 odcinków na ćwiartkę) − działka``) — liczba budynków, suma pól części w pierścieniu i minimalna
odległość. Pole sumy (union) i statystyki wielkości obrysów weryfikują testy Shapely.

Uruchomienie w obrazie backendu (ma ``ogr2ogr`` z GDAL 3.10)::

    python scripts/verify_bdot10k_with_gdal.py [--powiat 1261 ...] [--output docs/evaluation/results/bdot10k-gdal-cross-check]

Kod wyjścia: 0 — wszystkie porównania w tolerancji; 1 — jakakolwiek rozbieżność; 2 — brak narzędzia GDAL.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parent.parent
FIXTURES = BACKEND_DIR / "tests" / "fixtures" / "bdot10k"
CORPUS = BACKEND_DIR / "tests" / "fixtures" / "reference_corpus"
DISTANCE_TOLERANCE_M = 0.011
AREA_TOLERANCE_SQM = 0.05
AREA_EPSILON_SQM = 0.01
QUAD_SEGS = 16
ROAD_RADII = (25.0, 50.0, 100.0, 250.0, 500.0)
BUFFERS = (50.0, 100.0, 200.0)


def _ring(coords: list[list[float]]) -> str:
    return "(" + ", ".join(f"{x!r} {y!r}" for x, y, *_ in coords) + ")"


def parcel_wkt(case_id: str) -> str:
    """WKT działki wprost z GeoJSON korpusu (EPSG:2180, kolejność easting northing)."""
    geometry = json.loads((CORPUS / "artifacts" / "parcels" / f"{case_id}.geojson").read_text(encoding="utf-8"))["geometry"]
    polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    parts = ", ".join("(" + ", ".join(_ring(ring) for ring in polygon) + ")" for polygon in polygons)
    return f"MULTIPOLYGON({parts})"


def ogr_rows(gml: Path, sql: str) -> list[dict[str, str]]:
    """Wynik zapytania SQL (dialekt SQLite GDAL) na pliku GML jako lista wierszy tekstowych."""
    completed = subprocess.run(
        ["ogr2ogr", "-f", "CSV", "/vsistdout/", str(gml), "-dialect", "SQLite", "-sql", sql],
        capture_output=True, text=True, check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"ogr2ogr zakończył się błędem: {completed.stderr.strip()[:300]}")
    return list(csv.DictReader(io.StringIO(completed.stdout)))


def _flag(value: str) -> bool:
    return value.strip() in {"1", "true", "True"}


def measure_road(gml: Path, wkt: str) -> dict[str, Any]:
    parcel = f"ST_GeomFromText('{wkt}')"
    rows = ogr_rows(
        gml,
        "SELECT lokalnyId AS id, kategoriaZarzadzania AS cat, klasaDrogi AS cls, "
        f"ST_Distance(geometria, {parcel}) AS d, ST_Intersects(geometria, {parcel}) AS i, ST_Touches(geometria, {parcel}) AS t "
        "FROM OT_SKJZ_L WHERE kategoriaIstnienia = 'eksploatowany' ORDER BY d, id LIMIT 200",
    )
    if not rows:
        return {"status": "not_found_within_radius"}
    ranked = sorted(rows, key=lambda row: (round(float(row["d"]) / 1e-3), 0 if _flag(row["i"]) and not _flag(row["t"]) else 1 if _flag(row["t"]) else 2, row["id"]))
    best = ranked[0]
    distance = float(best["d"])
    radius = next((r for r in ROAD_RADII if distance <= r), None)
    if radius is None:
        return {"status": "not_found_within_radius"}
    relation = "touches" if _flag(best["t"]) else "intersects" if _flag(best["i"]) else "disjoint"
    return {
        "status": "available", "search_radius_m": radius, "distance_m": round(distance, 2), "relation": relation,
        "road_id": best["id"], "management_category": best["cat"], "road_class": best["cls"],
    }


def measure_buildings(gml: Path, wkt: str) -> dict[str, Any]:
    parcel = f"ST_GeomFromText('{wkt}')"
    on_parcel = ogr_rows(
        gml,
        "SELECT count(*) AS n, coalesce(sum(a), 0) AS area FROM ("
        f"SELECT ST_Area(ST_Intersection(geometria, {parcel})) AS a FROM OT_BUBD_A WHERE kategoriaIstnienia = 'eksploatowany') "
        f"WHERE a > {AREA_EPSILON_SQM}",
    )[0]
    result: dict[str, Any] = {"on_parcel": {"count": int(on_parcel["n"]), "area_sqm": float(on_parcel["area"])}, "buffers": {}}
    for radius in BUFFERS:
        ring = f"ST_Difference(ST_Buffer({parcel}, {radius}, {QUAD_SEGS}), {parcel})"
        row = ogr_rows(
            gml,
            "SELECT count(*) AS n, coalesce(sum(a), 0) AS clipped, min(d) AS mind FROM ("
            f"SELECT ST_Area(ST_Intersection(geometria, {ring})) AS a, ST_Distance(geometria, {parcel}) AS d "
            "FROM OT_BUBD_A WHERE kategoriaIstnienia = 'eksploatowany') "
            f"WHERE a > {AREA_EPSILON_SQM}",
        )[0]
        result["buffers"][str(int(radius))] = {
            "count": int(row["n"]), "clipped_sqm": float(row["clipped"]),
            "min_distance_m": float(row["mind"]) if row["mind"] not in {"", None} else None,
        }
    return result


def compare_case(case_id: str, want: dict[str, Any], road: dict[str, Any], buildings: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    expected_road = want["roads"]
    if road["status"] != expected_road["status"]:
        problems.append(f"droga: status {road['status']} ≠ {expected_road['status']}")
    elif road["status"] == "available":
        for key in ("search_radius_m", "relation", "road_id", "management_category", "road_class"):
            if road[key] != expected_road[key]:
                problems.append(f"droga: {key} {road[key]!r} ≠ {expected_road[key]!r}")
        if abs(road["distance_m"] - expected_road["distance_m"]) > DISTANCE_TOLERANCE_M:
            problems.append(f"droga: odległość {road['distance_m']} ≠ {expected_road['distance_m']}")
    on_parcel = want["buildings"]["on_parcel"]
    if buildings["on_parcel"]["count"] != on_parcel["count"]:
        problems.append(f"budynki na działce: {buildings['on_parcel']['count']} ≠ {on_parcel['count']}")
    if abs(buildings["on_parcel"]["area_sqm"] - on_parcel["union_area_in_parcel_sqm"]) > AREA_TOLERANCE_SQM and on_parcel["count"] <= 1:
        problems.append("pole budynku na działce poza tolerancją")
    for radius, target in want["buildings"]["buffers"].items():
        got = buildings["buffers"][radius]
        if got["count"] != target["count"]:
            problems.append(f"bufor {radius} m: liczba {got['count']} ≠ {target['count']}")
        if abs(got["clipped_sqm"] - target["footprint_area_in_ring_sqm"]) > AREA_TOLERANCE_SQM:
            problems.append(f"bufor {radius} m: pole {got['clipped_sqm']:.3f} ≠ {target['footprint_area_in_ring_sqm']}")
        wanted = target["min_distance_m"]
        if (got["min_distance_m"] is None) != (wanted is None):
            problems.append(f"bufor {radius} m: odległość minimalna null vs {wanted}")
        elif wanted is not None and abs(got["min_distance_m"] - wanted) > DISTANCE_TOLERANCE_M:
            problems.append(f"bufor {radius} m: min. odległość {got['min_distance_m']:.3f} ≠ {wanted}")
    return problems


def verify_powiat(teryt: str, only: set[str] | None) -> list[dict[str, Any]]:
    expected = json.loads((FIXTURES / teryt / "expected.json").read_text(encoding="utf-8"))
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix=f"bdot10k-gdal-{teryt}-") as temporary:
        with zipfile.ZipFile(FIXTURES / teryt / f"{teryt}_GML.zip") as archive:
            archive.extractall(temporary)  # własne, zamrożone fixtures repozytorium (nie niezaufana paczka)
        roads = next(Path(temporary).rglob("*__OT_SKJZ_L.xml"))
        buildings = next(Path(temporary).rglob("*__OT_BUBD_A.xml"))
        for case_id, want in sorted(expected["cases"].items()):
            if only and case_id not in only:
                continue
            wkt = parcel_wkt(case_id)
            road, built = measure_road(roads, wkt), measure_buildings(buildings, wkt)
            results.append({"case_id": case_id, "powiat": teryt, "road": road, "buildings": built, "problems": compare_case(case_id, want, road, built)})
    return results


def render_markdown(results: list[dict[str, Any]], gdal_version: str) -> str:
    bad = [item for item in results if item["problems"]]
    lines = [
        "# Niezależny pomiar GDAL — droga i zabudowa sąsiednia (korpus)",
        "",
        f"Narzędzie: `ogr2ogr` / GDAL ({gdal_version}), dialekt SQLite (funkcje GEOS). **Nie był to pomiar w QGIS.** "
        "Wartości oczekiwane: `backend/tests/fixtures/bdot10k/*/expected.json` (Shapely, `gml:posList`). "
        f"Tolerancje: odległość {DISTANCE_TOLERANCE_M} m, pole {AREA_TOLERANCE_SQM} m².",
        "",
        f"**Wynik: {'ZGODNE' if not bad else 'ROZBIEŻNOŚCI'}** — {len(results) - len(bad)} z {len(results)} działek zgodnych.",
        "",
        "| działka | powiat | droga [m] / relacja | budynki na działce | bufor 50 m | bufor 100 m | bufor 200 m | zgodność |",
        "|---|---|---|---:|---:|---:|---:|---|",
    ]
    for item in results:
        road, built = item["road"], item["buildings"]
        road_cell = f"{road['distance_m']:.2f} / {road['relation']}" if road["status"] == "available" else road["status"]
        buffers = [f"{built['buffers'][r]['count']} / {built['buffers'][r]['clipped_sqm']:.1f} m²" for r in ("50", "100", "200")]
        lines.append(
            f"| `{item['case_id']}` | {item['powiat']} | {road_cell} | {built['on_parcel']['count']} | "
            f"{buffers[0]} | {buffers[1]} | {buffers[2]} | {'tak' if not item['problems'] else '; '.join(item['problems'])} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--powiat", action="append", help="TERYT powiatu (domyślnie wszystkie z fixtures)")
    parser.add_argument("--case", action="append", help="identyfikator przypadku korpusu (domyślnie wszystkie)")
    parser.add_argument("--output", type=Path, help="prefiks plików wynikowych (.json i .md)")
    args = parser.parse_args(argv)
    if shutil.which("ogr2ogr") is None:
        print("Brak ogr2ogr (GDAL) w PATH.", file=sys.stderr)
        return 2
    version = subprocess.run(["ogr2ogr", "--version"], capture_output=True, text=True, check=False).stdout.strip()
    powiats = args.powiat or sorted(path.name for path in FIXTURES.iterdir() if path.is_dir())
    results: list[dict[str, Any]] = []
    for teryt in powiats:
        results += verify_powiat(teryt, set(args.case) if args.case else None)
    markdown = render_markdown(results, version)
    print(markdown)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.with_suffix(".md").write_text(markdown, encoding="utf-8")
        args.output.with_suffix(".json").write_text(
            json.dumps({"gdal": version, "results": results}, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return 1 if any(item["problems"] for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
