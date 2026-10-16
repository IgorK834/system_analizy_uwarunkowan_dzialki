#!/usr/bin/env python3
"""Porównuje wynik live smoke (żywe API z zaimportowanymi paczkami BDOT10k) z wartościami niezależnymi (Task 23.2–23.3).

Wejście: raport JSON ``live_smoke_corpus.py`` (pola ``road_distance_m``, ``road_status``, ``buildings_on_parcel``,
``buildings_status`` każdego przypadku) i ``tests/fixtures/bdot10k/<powiat>/expected.json`` (Shapely z ``gml:posList``).
To sprawdzenie od końca do końca: pełna paczka powiatu → import → PostGIS → API kontra pomiar wykonany bez aplikacji.
Odległość drogi porównuje z tolerancją 0,05 m (API zaokrągla do 0,01 m), liczbę budynków na działce — dokładnie.
Nie łączy się z siecią.

    python3 -I backend/scripts/compare_live_smoke_bdot.py docs/evaluation/results/live-smoke/2026-10-13.json \\
        --output docs/evaluation/results/bdot10k/live-vs-independent

Kod wyjścia: 0 — wszystkie zgodne, 1 — rozbieżność lub brak przypadku, 2 — błąd użycia.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parent.parent
FIXTURES = BACKEND_DIR / "tests" / "fixtures" / "bdot10k"
DISTANCE_TOLERANCE_M = 0.05


def load_expected(fixtures: Path = FIXTURES) -> dict[str, dict[str, Any]]:
    expected: dict[str, dict[str, Any]] = {}
    for path in sorted(fixtures.glob("*/expected.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for case_id, case in data["cases"].items():
            expected[case_id] = {"powiat": data["teryt_powiat"], **case}
    return expected


def compare(live_cases: list[dict[str, Any]], expected: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {case["case_id"]: case for case in live_cases}
    rows: list[dict[str, Any]] = []
    for case_id, want in sorted(expected.items()):
        got = by_id.get(case_id)
        problems: list[str] = []
        if got is None:
            problems.append("brak przypadku w raporcie live smoke")
            got = {}
        else:
            if got.get("road_status") != "available" or got.get("buildings_status") != "available":
                problems.append(f"statusy: droga {got.get('road_status')}, zabudowa {got.get('buildings_status')}")
            distance = got.get("road_distance_m")
            if distance is None or abs(distance - want["roads"]["distance_m"]) > DISTANCE_TOLERANCE_M:
                problems.append(f"odległość {distance} ≠ {want['roads']['distance_m']}")
            on_parcel = want["buildings"]["on_parcel"]["count"]
            if got.get("buildings_on_parcel") != on_parcel:
                problems.append(f"budynki na działce {got.get('buildings_on_parcel')} ≠ {on_parcel}")
        rows.append({
            "case_id": case_id, "powiat": want["powiat"],
            "live_distance_m": got.get("road_distance_m"), "expected_distance_m": want["roads"]["distance_m"],
            "live_buildings_on_parcel": got.get("buildings_on_parcel"),
            "expected_buildings_on_parcel": want["buildings"]["on_parcel"]["count"],
            "problems": problems,
        })
    return rows


def render_markdown(rows: list[dict[str, Any]], source: str) -> str:
    bad = [row for row in rows if row["problems"]]
    lines = [
        "# Żywe API a pomiar niezależny — droga i zabudowa (korpus)",
        "",
        f"Źródło: `{source}` (pełne paczki powiatowe zaimportowane do PostGIS, odpowiedzi `POST /analyze`) wobec "
        "`backend/tests/fixtures/bdot10k/*/expected.json` (Shapely z `gml:posList`, bez aplikacji). "
        f"Tolerancja odległości: {DISTANCE_TOLERANCE_M} m; liczba budynków na działce: równość.",
        "",
        f"**Wynik: {'ZGODNE' if not bad else 'ROZBIEŻNOŚCI'}** — {len(rows) - len(bad)} z {len(rows)} działek zgodnych.",
        "",
        "| działka | powiat | odległość API / niezależna [m] | budynki na działce API / niezależnie | zgodność |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| `{row['case_id']}` | {row['powiat']} | {row['live_distance_m']} / {row['expected_distance_m']} | "
            f"{row['live_buildings_on_parcel']} / {row['expected_buildings_on_parcel']} | "
            f"{'tak' if not row['problems'] else '; '.join(row['problems'])} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("report", type=Path, help="raport JSON live_smoke_corpus.py")
    parser.add_argument("--output", type=Path, help="prefiks plików wynikowych (.json i .md)")
    args = parser.parse_args(argv)
    try:
        live = json.loads(args.report.read_text(encoding="utf-8"))["cases"]
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"Nieczytelny raport live smoke: {exc}", file=sys.stderr)
        return 2
    rows = compare(live, load_expected())
    markdown = render_markdown(rows, args.report.name)
    print(markdown)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.with_suffix(".md").write_text(markdown, encoding="utf-8")
        args.output.with_suffix(".json").write_text(
            json.dumps({"source": args.report.name, "results": rows}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return 1 if any(row["problems"] for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
