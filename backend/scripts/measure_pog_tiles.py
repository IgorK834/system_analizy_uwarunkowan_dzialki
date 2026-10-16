"""Pomiar kafli MVT POG ze stanu krajowego (ADR-026): czasy odpowiedzi i rozmiary na z6, z11, z14 (i opcjonalnie inne).

Skrypt odpytuje działający backend (``--base-url``), losuje deterministycznie kafle pokryte danymi — środki zasięgów aktów
ze ``GET /api/v1/map/pog/state`` — i dla każdego zoomu mierzy:

* **zimne** żądanie (pierwsze po zmianie stanu: kafel liczony w PostGIS) i **ciepłe** (powtórzenie: cache dyskowy/ETag),
* rozmiar kafla (bajty; osobno dla kafli niepustych), liczbę pustych kafli, błędów i odpowiedzi 304,
* p50/p95/max czasu (ms) oraz p50/p95/max rozmiaru.

Wynik to jedna linia JSON na stdout (oraz podsumowanie na stderr). Skrypt jest narzędziem operatora (poza CI): nie jest
używany przez testy i nie zapisuje niczego poza ``--out``.

Użycie::

    python3 backend/scripts/measure_pog_tiles.py --base-url http://127.0.0.1:18000 --zooms 6,11,14 --samples 60 --out wynik.json
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
import time
import urllib.error
import urllib.request
from typing import Any


_MIN_GAP_SECONDS = 0.0
_last_request_at = 0.0


def _get(url: str, *, headers: dict[str, str] | None = None) -> tuple[int, bytes, float, dict[str, str]]:
    # Limit żądań API (RATE_LIMIT_TILES_PER_MINUTE) dotyczy też tego skryptu: odstęp ``--max-rps`` go respektuje.
    global _last_request_at
    wait = _MIN_GAP_SECONDS - (time.perf_counter() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.perf_counter()
    request = urllib.request.Request(url, headers=headers or {})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - adres podaje operator
            body = response.read()
            return response.status, body, (time.perf_counter() - started) * 1000, dict(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), (time.perf_counter() - started) * 1000, dict(exc.headers)


def _tile(lon: float, lat: float, zoom: int) -> tuple[int, int]:
    count = 1 << zoom
    x = int((lon + 180.0) / 360.0 * count)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * count)
    return min(max(x, 0), count - 1), min(max(y, 0), count - 1)


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return float("nan")
    index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def _summary(values: list[float]) -> dict[str, float]:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "p50": round(statistics.median(values), 1),
        "p95": round(_percentile(values, 0.95), 1),
        "max": round(max(values), 1),
    }


def _sample_tiles(areas: list[dict[str, Any]], zoom: int, samples: int, seed: int) -> list[tuple[int, int]]:
    """Różne kafle z obszarów zasięgu aktów: z każdego aktu losowe kafle jego obwiedni (do ``per_act``), potem próbka
    z całej puli — gminy duże (Poznań, Wrocław) wnoszą więcej kafli, ale nie dominują próbki."""
    rng = random.Random(seed + zoom)
    pool: set[tuple[int, int]] = set()
    per_act = max(3, samples // 5)
    for area in areas:
        west, south, east, north = area["bounds"]
        x0, y1 = _tile(west, south, zoom)
        x1, y0 = _tile(east, north, zoom)
        width, height = x1 - x0 + 1, y1 - y0 + 1
        if width * height <= per_act:
            pool.update((x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1))
        else:
            pool.update((rng.randint(x0, x1), rng.randint(y0, y1)) for _ in range(per_act))
    ordered = sorted(pool)
    rng.shuffle(ordered)
    return ordered[:samples]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18000")
    parser.add_argument("--zooms", default="6,11,14")
    parser.add_argument("--samples", type=int, default=60, help="Liczba różnych kafli na zoom.")
    parser.add_argument("--edition", default="binding", choices=("all", "binding", "project"))
    parser.add_argument("--seed", type=int, default=20261013)
    parser.add_argument("--max-rps", type=float, default=15.0, help="Maks. żądań na sekundę (limit API: 1200/min).")
    parser.add_argument("--out")
    args = parser.parse_args()
    global _MIN_GAP_SECONDS
    _MIN_GAP_SECONDS = 1.0 / args.max_rps if args.max_rps > 0 else 0.0

    status, body, _ms, _headers = _get(f"{args.base_url}/api/v1/map/pog/state")
    if status != 200:
        print(f"GET /api/v1/map/pog/state → {status}", file=sys.stderr)
        return 1
    state = json.loads(body)
    areas = [item for item in state["coverage_areas"] if item.get("bounds")]
    template = state["tile_url_template"]
    report: dict[str, Any] = {
        "state_id": state["state_id"],
        "acts": state["act_count"],
        "edition": args.edition,
        "samples_per_zoom": args.samples,
        "zooms": {},
    }
    for zoom in (int(item) for item in args.zooms.split(",")):
        tiles = _sample_tiles(areas, zoom, args.samples, args.seed)
        cold_ms: list[float] = []
        warm_ms: list[float] = []
        sizes: list[float] = []
        cold_nonempty_ms: list[float] = []
        nonempty_sizes: list[float] = []
        empty = errors = not_modified = 0
        for x, y in tiles:
            url = f"{args.base_url}{template.format(z=zoom, x=x, y=y)}?edition={args.edition}"
            code, payload, elapsed, headers = _get(url)
            if code != 200:
                errors += 1
                continue
            cold_ms.append(elapsed)
            sizes.append(len(payload))
            if not payload:
                empty += 1
            else:
                cold_nonempty_ms.append(elapsed)
                nonempty_sizes.append(len(payload))
            code2, payload2, elapsed2, _ = _get(url)
            if code2 == 200:
                warm_ms.append(elapsed2)
            etag = headers.get("etag") or headers.get("ETag")
            if etag:
                code3, _payload3, _elapsed3, _ = _get(url, headers={"If-None-Match": etag})
                not_modified += int(code3 == 304)
        report["zooms"][str(zoom)] = {
            "tiles": len(tiles),
            "errors": errors,
            "empty_tiles": empty,
            "etag_304": not_modified,
            "cold_ms": _summary(cold_ms),
            "cold_ms_nonempty": _summary(cold_nonempty_ms),
            "warm_ms": _summary(warm_ms),
            "size_bytes": _summary(sizes),
            "size_bytes_nonempty": _summary(nonempty_sizes),
            "size_over_500kb": sum(1 for size in sizes if size > 500 * 1024),
        }
        print(f"z{zoom}: {report['zooms'][str(zoom)]}", file=sys.stderr)
    text = json.dumps(report, ensure_ascii=False)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
