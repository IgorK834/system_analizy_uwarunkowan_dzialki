#!/usr/bin/env python3
"""Narzędzie diagnostyczne do sondowania kafelków WMS przez proxy.

Przykład użycia:
    python scripts/probe_wms_preview.py --source kiut --lon 19.12903 --lat 49.85377 --min-zoom 15 --max-zoom 19
"""

from __future__ import annotations

import argparse
import asyncio
import io
import math
import sys
import time
from pathlib import Path

from PIL import Image

# Dodaj backend do PYTHONPATH, jeśli uruchamiane bezpośrednio ze ścieżki
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.services.wms_tiles import wms_tile_registry  # noqa: E402


def lon_lat_to_tile(lon: float, lat: float, zoom: int) -> tuple[int, int]:
    """Konwertuje współrzędne WGS84 na indeksy kafelka (x, y) w standardzie Web Mercator / Slippy Map."""
    n = 1 << zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    # Zabezpieczenie przed wyjściem poza zakres
    x = max(0, min(x, n - 1))
    y = max(0, min(y, n - 1))
    return x, y


def count_opaque_pixels(png_bytes: bytes) -> int:
    """Zwraca liczbę pikseli o kanale alfa > 0."""
    try:
        image = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
        alpha = image.getchannel("A")
        return sum(1 for p in alpha.tobytes() if p > 0)
    except Exception:
        return -1


async def probe_source(
    source_key: str,
    lon: float,
    lat: float,
    min_zoom: int,
    max_zoom: int,
) -> None:
    print(
        f"=== Sonda WMS dla źródła '{source_key}' (lon: {lon}, lat: {lat}, zoom: {min_zoom}..{max_zoom}) ==="
    )
    print(
        f"{'Zoom':<6} {'X':<8} {'Y':<8} {'Status':<10} {'Cache':<8} {'Rozmiar':<10} {'Piksele z treścią':<20} {'Czas (ms)':<10}"
    )
    print("-" * 88)

    try:
        proxy = wms_tile_registry.get(source_key)
    except KeyError as exc:
        print(f"BŁĄD: {exc}")
        return

    for z in range(min_zoom, max_zoom + 1):
        x, y = lon_lat_to_tile(lon, lat, z)
        t0 = time.perf_counter()
        try:
            tile_result = await proxy.get_tile(z, x, y)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            opaque_px = count_opaque_pixels(tile_result.content)
            size_kb = len(tile_result.content) / 1024
            print(
                f"{z:<6} {x:<8} {y:<8} {'200 OK':<10} {tile_result.cache_status:<8} {f'{size_kb:.1f} KB':<10} {opaque_px:<20} {f'{elapsed_ms:.1f}':<10}"
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            err_msg = f"{type(exc).__name__}: {exc}"
            print(
                f"{z:<6} {x:<8} {y:<8} {'ERROR':<10} {'-':<8} {'-':<10} {err_msg[:20]:<20} {f'{elapsed_ms:.1f}':<10}"
            )

    await proxy.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sonda kafelków WMS dla zadanego punktu WGS84."
    )
    parser.add_argument(
        "--source",
        "-s",
        default="kiut",
        help="Klucz źródła (kiut, mpzp, pog) - domyślnie: kiut",
    )
    parser.add_argument(
        "--lon",
        type=float,
        default=19.12903,
        help="Długość geograficzna WGS84 (domyślnie: 19.12903 - Bielsko-Biała)",
    )
    parser.add_argument(
        "--lat",
        type=float,
        default=49.85377,
        help="Szerokość geograficzna WGS84 (domyślnie: 49.85377 - Bielsko-Biała)",
    )
    parser.add_argument(
        "--min-zoom",
        type=int,
        default=15,
        help="Minimalny zoom do przetestowania (domyślnie: 15)",
    )
    parser.add_argument(
        "--max-zoom",
        type=int,
        default=19,
        help="Maksymalny zoom do przetestowania (domyślnie: 19)",
    )

    args = parser.parse_args()
    asyncio.run(
        probe_source(
            source_key=args.source,
            lon=args.lon,
            lat=args.lat,
            min_zoom=args.min_zoom,
            max_zoom=args.max_zoom,
        )
    )


if __name__ == "__main__":
    main()
