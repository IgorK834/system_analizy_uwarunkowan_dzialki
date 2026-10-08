"""Pobiera zamrożone odpowiedzi KIMPZP GetFeatureInfo do testów kontraktowych (AU-004).

Uruchomienie z katalogu głównego repozytorium::

    python backend/scripts/capture_kimpzp_fixtures.py [katalog_docelowy]

Domyślny katalog: ``backend/tests/fixtures/source_contracts/kimpzp``. Dla działki
skrypt pobiera geometrię z ULDK (EPSG:2180) i odpytuje ``representative_point``
— ten sam punkt, który ``discover_mpzp`` zawsze uwzględnia w próbce — parametrami
z ``_build_get_feature_info_params``. Odpowiedzi zapisywane są bajt w bajt;
``manifest.json`` zawiera URL, czas pobrania, status HTTP, typ treści i SHA-256.
Skrypt nie nadpisuje istniejących plików bez ``--force``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

ULDK_URL = "https://uldk.gugik.gov.pl/"

# (plik, gmina, identyfikator działki albo None, punkt EPSG:2180 albo None, opis)
CASES: tuple[tuple[str, str, str | None, tuple[float, float] | None, str], ...] = (
    ("gora_kalwaria_141801_4.0701.23_8.html", "Góra Kalwaria", "141801_4.0701.23/8", None,
     "dwa bloki „Obowiązujące MPZP” z linkami i tabelą „Zmiany tekstowe” (raport OnGeo)"),
    ("krakow_126105_9.0001.580_4.html", "Kraków", "126105_9.0001.580/4", None,
     "trzy tabele atrybutów Esri (th + wiersze) z tym samym aktem i symbolem strefy"),
    ("bielsko_biala_246101_1.0056.155_3.html", "Bielsko-Biała", "246101_1.0056.155/3", None,
     "ServiceExceptionReport (LayerNotDefined) z HTTP 200 — błąd usługi gminnej"),
    ("pisz_281603_4.0001.496_5.html", "Pisz", "281603_4.0001.496/5", None,
     "QGIS Server: warstwy mpzp_meta, dod_info_*, mpzp z zagnieżdżonymi tabelami obiektów"),
    ("legnica_026201_1.0009.1319_4.html", "Legnica", "026201_1.0009.1319/4", None,
     "pionowe pary th/td (numer, symbol, przeznaczenie)"),
    ("warszawa_146510_8.0502.1_3.html", "Warszawa", "146510_8.0502.1/3", None,
     "<oms_error> z HTTP 200 — błąd usługi gminnej"),
    ("dygowo_321606_2.0029.362.html", "Dygowo", "321606_2.0029.362", None,
     "„brak serwisu dla wskazanego obszaru” — gmina poza KIMPZP"),
    ("ruciane_nida_281604_5.0011.107.html", "Ruciane-Nida", "281604_5.0011.107", None,
     "„<gmina>: brak wyniku dla wskazanego obszaru” — usługa bez wyniku"),
    ("kalety_punkt_500000_300000.html", "Kalety", None, (500000.0, 300000.0),
     "iGeoMap: dwa akty rastrowe w punkcie + segment „brak wyniku” drugiej usługi"),
    ("inowroclaw_punkt_450000_550000.html", "Inowrocław", None, (450000.0, 550000.0),
     "GeoServer APP: numer uchwały wyłącznie w opisie dokumentuchwalajacy, data w formacie USA"),
)


def _parcel_point(client, parcel_id: str) -> tuple[float, float]:  # noqa: ANN001 - httpx.Client
    from shapely import from_wkt

    response = client.get(
        ULDK_URL,
        params={"request": "GetParcelById", "id": parcel_id, "result": "geom_wkt", "srid": "2180"},
    )
    response.raise_for_status()
    lines = response.text.strip().splitlines()
    if len(lines) < 2 or not lines[0].startswith("0"):
        raise RuntimeError(f"ULDK nie zwróciło geometrii {parcel_id}: {response.text[:120]!r}")
    point = from_wkt(lines[1].split(";", 1)[1]).representative_point()
    return round(point.x, 3), round(point.y, 3)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("target", nargs="?", default=str(BACKEND_DIR / "tests/fixtures/source_contracts/kimpzp"))
    parser.add_argument("--force", action="store_true", help="nadpisz istniejące pliki")
    args = parser.parse_args()
    target = Path(args.target).resolve()

    os.chdir(BACKEND_DIR)  # app.core.settings czyta względny .env przy imporcie
    import httpx

    from app.core.settings import settings
    from app.services.mpzp import _build_get_feature_info_params

    target.mkdir(parents=True, exist_ok=True)
    manifest_path = target / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8")) if manifest_path.exists() else {"files": {}}
    with httpx.Client(timeout=60.0) as client:
        for filename, municipality, parcel_id, point, description in CASES:
            path = target / filename
            if path.exists() and not args.force:
                print(f"pominięto (istnieje): {filename}")
                continue
            x, y = point if point is not None else _parcel_point(client, parcel_id or "")
            response = client.get(
                settings.kimpzp_wms_base_url, params=_build_get_feature_info_params(x, y)
            )
            path.write_bytes(response.content)
            manifest["files"][filename] = {
                "municipality": municipality,
                "parcel_identifier": parcel_id,
                "point_epsg2180": [x, y],
                "description": description,
                "request_url": str(response.request.url),
                "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "http_status": response.status_code,
                "content_type": response.headers.get("content-type"),
                "size_bytes": len(response.content),
                "sha256": hashlib.sha256(response.content).hexdigest(),
            }
            print(f"{filename}: HTTP {response.status_code}, {len(response.content)} B")
    manifest["files"] = dict(sorted(manifest["files"].items()))
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", "utf-8")


if __name__ == "__main__":
    main()
