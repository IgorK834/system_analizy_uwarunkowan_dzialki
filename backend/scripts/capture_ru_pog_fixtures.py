#!/usr/bin/env python3
"""Zamraża rzeczywiste odpowiedzi WFS ``app-pog`` Rejestru Urbanistycznego dla korpusu referencyjnego (AU-105).

Dla każdej działki korpusu (``tests/fixtures/reference_corpus``) skrypt uruchamia **produkcyjny** adapter
``RuPogFeaturesAdapter`` na żywej usłudze i zapisuje bajt w bajt każdą odpowiedź HTTP (strona po stronie) wraz z
parametrami żądania: typ obiektu, kafel ``bbox`` i ``startIndex``. Testy odtwarzają je przez ``respx`` i
prawdziwego ``OgcClient`` — nie ma ręcznie uproszczonych odpowiedzi.

Uruchomienie z katalogu ``backend`` w obrazie backendu (łańcuch certyfikatów RU jest zaufany w obrazie)::

    python scripts/capture_ru_pog_fixtures.py [katalog_docelowy] [--force]

Domyślny katalog: ``tests/fixtures/source_contracts/ru_pog_features``. Pliki są kompresowane gzipem (granica aktu
Bielska-Białej ma ponad 300 kB GML); ``manifest.json`` zawiera SHA-256 nieskompresowanej treści, URL, czas pobrania
i rozmiar każdego pliku oraz mapowanie ``przypadek → żądania``. Identyczne odpowiedzi (np. ten sam akt dla wielu
kafli) są zapisane raz. Skrypt nie nadpisuje istniejących plików bez ``--force``; ruch to kilkaset zapytań GET
tylko do odczytu (cztery na działkę).
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

BACKEND_DIR = Path(__file__).resolve().parents[1]
os.chdir(BACKEND_DIR)  # Settings czyta ``.env`` względem bieżącego katalogu.
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

CORPUS = BACKEND_DIR / "tests/fixtures/reference_corpus"
DEFAULT_OUTPUT = BACKEND_DIR / "tests/fixtures/source_contracts/ru_pog_features"
SCHEMA_VERSION = "ru-pog-features/1"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output", nargs="?", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--force", action="store_true", help="Nadpisz istniejące pliki.")
    args = parser.parse_args()

    from shapely.geometry import shape

    from app.core.settings import settings
    from app.modules.imports.infrastructure.ogc_client import OgcClient, OgcClientConfig
    from app.modules.planning.domain.pog_discovery import POG_FEATURE_TYPES, snap_bbox_outward
    from app.modules.planning.infrastructure.ru_pog_features import RuPogFeaturesAdapter

    wfs_url = "https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe/app-pog/wfs"
    output: Path = args.output
    if (output / "manifest.json").exists() and not args.force:
        print(f"{output / 'manifest.json'} już istnieje; użyj --force.", file=sys.stderr)
        return 2
    output.mkdir(parents=True, exist_ok=True)

    recorded: list[dict[str, Any]] = []

    def on_response(response: httpx.Response) -> None:
        response.read()
        query = parse_qs(urlsplit(str(response.request.url)).query)
        recorded.append(
            {
                "type_name": query.get("typeNames", [""])[0].removeprefix("app-pog:"),
                "bbox": query.get("bbox", [""])[0],
                "start_index": int(query.get("startIndex", ["0"])[0]),
                "status": response.status_code,
                "content_type": response.headers.get("content-type", ""),
                "content": response.content,
            }
        )

    manifest = json.loads((CORPUS / "manifest.json").read_text("utf-8"))
    files: dict[str, dict[str, Any]] = {}
    cases: dict[str, Any] = {}
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for case in manifest["cases"]:
        case_id = case["case_id"]
        geometry = shape(json.loads((CORPUS / "artifacts/parcels" / f"{case_id}.geojson").read_text("utf-8"))["geometry"])
        tile = snap_bbox_outward(geometry.bounds, settings.ru_pog_tile_size_m)
        entries: list[dict[str, Any]] = []
        for feature_type in POG_FEATURE_TYPES:
            recorded.clear()
            http = httpx.Client(timeout=60.0, follow_redirects=False, event_hooks={"response": [on_response]})
            client = OgcClient(
                source_id="pog_app",
                config=OgcClientConfig(
                    allowed_hosts=frozenset({"rejestr-urbanistyczny.gov.pl"}),
                    total_timeout_seconds=120.0,
                    read_timeout_seconds=60.0,
                    max_response_bytes=settings.ru_pog_max_response_bytes,
                    max_total_bytes=settings.ru_pog_max_response_bytes * 2,
                    retries=1,
                ),
                client=http,
            )
            try:
                RuPogFeaturesAdapter(wfs_url=wfs_url, client_factory=lambda _hook, c=client: c).fetch_features(
                    feature_type, tile
                )
            finally:
                http.close()
            for item in recorded:
                if item["status"] != 200:
                    raise RuntimeError(f"{case_id} {feature_type}: HTTP {item['status']} — zamrażamy wyłącznie poprawne odpowiedzi.")
                digest = hashlib.sha256(item["content"]).hexdigest()
                name = f"{digest[:16]}.xml.gz"
                path = output / name
                if not path.exists() or args.force:
                    path.write_bytes(gzip.compress(item["content"], compresslevel=9, mtime=0))
                files[name] = {
                    "sha256": digest,
                    "size_bytes": len(item["content"]),
                    "content_type": item["content_type"],
                }
                entries.append(
                    {
                        "type_name": feature_type,
                        "bbox": item["bbox"],
                        "start_index": item["start_index"],
                        "file": name,
                    }
                )
        cases[case_id] = {"teryt": case["teryt"], "tile": list(tile), "requests": entries}
        print(f"{case_id}: {len(entries)} odpowiedzi", file=sys.stderr)

    (output / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "source": {
                    "service": "Rejestr Urbanistyczny, WFS 2.0.0 app-pog",
                    "url": wfs_url,
                    "tile_size_m": settings.ru_pog_tile_size_m,
                    "axis_order": "north,east (urn:ogc:def:crs:EPSG::2180)",
                    "fetched_at": fetched_at,
                    "capture": "scripts/capture_ru_pog_fixtures.py (produkcyjny RuPogFeaturesAdapter)",
                },
                "files": dict(sorted(files.items())),
                "cases": cases,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    total = sum(entry["size_bytes"] for entry in files.values())
    print(f"Zapisano {len(files)} unikalnych odpowiedzi ({total} B przed kompresją).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
