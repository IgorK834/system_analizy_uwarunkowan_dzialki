"""Pobiera zamrożone odpowiedzi Rejestru Urbanistycznego (WFS ``app-mpzp``) do testów kontraktowych (AU-101).

Uruchomienie z katalogu ``backend`` w obrazie backendu (łańcuch certyfikatów RU jest zaufany w obrazie, ale
nie w systemowym Pythonie macOS)::

    python scripts/capture_ru_mpzp_fixtures.py [katalog_docelowy] [--force]

Domyślny katalog: ``tests/fixtures/source_contracts/ru_mpzp_acts``. Dla każdego przypadku skrypt:

1. pobiera geometrię działki — z korpusu referencyjnego (``tests/fixtures/reference_corpus``) albo z ULDK
   (``GetParcelById``, EPSG:2180);
2. odpytuje ``AktPlanowaniaPrzestrzennego`` dla ``bbox`` działki parametrami identycznymi z adapterem
   (``bbox`` w kolejności północ,wschód dla URN EPSG:2180);
3. dla zwróconych aktów pobiera ``DokumentFormalny`` i ``RysunekAktuPlanowaniaPrzestrzennego`` po ``resourceId``.

Odpowiedzi są zapisywane bajt w bajt; ``manifest.json`` zawiera URL, czas pobrania, status HTTP, typ treści,
rozmiar i SHA-256 każdego pliku oraz WKT działki w EPSG:2180. Skrypt nie nadpisuje istniejących plików bez
``--force``. Zapytania są tylko do odczytu, a ruch jest niewielki (kilkanaście żądań GET).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

ULDK_URL = "https://uldk.gugik.gov.pl/"
CORPUS = BACKEND_DIR / "tests/fixtures/reference_corpus"

# (slug, gmina, identyfikator działki, id przypadku korpusu albo None, opis)
CASES: tuple[tuple[str, str, str, str | None, str], ...] = (
    ("gora_kalwaria_141801_4.0701.23_8", "Góra Kalwaria", "141801_4.0701.23/8", None,
     "raport OnGeo: IV/30/2024 (cz. I) jest w KIMPZP, a RU zwraca tylko projekt XXIV/302/2026 (cz. II), "
     "którego granica leży poza działką"),
    ("bielsko_biala_246101_1.0056.155_3", "Bielsko-Biała", "246101_1.0056.155/3", "real-009-246101-1-0056-155-3",
     "KIMPZP zwraca tu błąd usługi, a RU wskazuje obowiązujący plan „Starówka” z dokumentem, zmianą i rysunkami"),
    ("krakow_126105_9.0001.540_15", "Kraków", "126105_9.0001.540/15", "real-007-126105-9-0001-540-15",
     "RU zwraca wyłącznie projekt planu (status „w opracowaniu”)"),
    ("warszawa_146510_8.0502.1_3", "Warszawa", "146510_8.0502.1/3", "real-001-146510-8-0502-1-3",
     "RU odpowiada poprawnie i nie zwraca żadnego aktu dla bbox działki"),
    ("dygowo_321606_2.0029.362", "Dygowo", "321606_2.0029.362", None,
     "gmina poza KIMPZP („brak serwisu”) — RU też nie zwraca aktu"),
)


def _polygon_bbox_wkt(wkt: str) -> tuple[float, float, float, float]:
    from shapely import from_wkt

    return tuple(from_wkt(wkt).bounds)  # type: ignore[return-value]


def _parcel_wkt(client, identifier: str, corpus_case: str | None) -> str:  # noqa: ANN001 - httpx.Client
    from shapely.geometry import shape

    if corpus_case:
        feature = json.loads((CORPUS / "artifacts/parcels" / f"{corpus_case}.geojson").read_text("utf-8"))
        geometry = feature["geometry"] if feature.get("type") == "Feature" else feature
        return shape(geometry).wkt
    response = client.get(
        ULDK_URL,
        params={"request": "GetParcelById", "id": identifier, "result": "geom_wkt", "srid": "2180"},
    )
    response.raise_for_status()
    lines = response.text.strip().splitlines()
    if len(lines) < 2 or not lines[0].startswith("0"):
        raise RuntimeError(f"ULDK nie zwróciło geometrii {identifier}: {response.text[:120]!r}")
    return lines[1].split(";", 1)[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("target", nargs="?", default=str(BACKEND_DIR / "tests/fixtures/source_contracts/ru_mpzp_acts"))
    parser.add_argument("--force", action="store_true", help="nadpisz istniejące pliki")
    args = parser.parse_args()
    target = Path(args.target).resolve()

    os.chdir(BACKEND_DIR)  # app.core.settings czyta względny .env przy imporcie
    import httpx

    from app.modules.planning.infrastructure.ru_mpzp_acts import (
        SRS_URN_2180,
        TYPE_ACT,
        TYPE_DOCUMENT,
        TYPE_DRAWING,
        _bbox_param,
        gml_id_for,
        parse_act,
    )

    wfs_url = "https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe/app-mpzp/wfs"
    target.mkdir(parents=True, exist_ok=True)
    manifest_path = target / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8")) if manifest_path.exists() else {"cases": {}, "files": {}}

    def fetch(client, params: dict[str, str]):  # noqa: ANN001, ANN202
        base = {"service": "WFS", "version": "2.0.0", "request": "GetFeature",
                "outputFormat": "application/gml+xml; version=3.2", "srsName": SRS_URN_2180,
                "count": "100", "startIndex": "0"}
        return client.get(wfs_url, params={**base, **params})

    def save(name: str, response, meta: dict) -> None:  # noqa: ANN001
        path = target / name
        if path.exists() and not args.force:
            print(f"pominięto (istnieje): {name}")
            return
        path.write_bytes(response.content)
        manifest["files"][name] = {
            **meta,
            "request_url": str(response.request.url),
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "http_status": response.status_code,
            "content_type": response.headers.get("content-type"),
            "size_bytes": len(response.content),
            "sha256": hashlib.sha256(response.content).hexdigest(),
        }
        print(f"{name}: HTTP {response.status_code}, {len(response.content)} B")

    with httpx.Client(timeout=90.0, follow_redirects=False) as client:
        for slug, municipality, identifier, corpus_case, description in CASES:
            wkt = _parcel_wkt(client, identifier, corpus_case)
            bbox = _polygon_bbox_wkt(wkt)
            existing = manifest["cases"].get(slug, {})
            manifest["cases"][slug] = {
                **existing,
                "municipality": municipality,
                "parcel_identifier": identifier,
                "description": description,
                "parcel_wkt_epsg2180": existing.get("parcel_wkt_epsg2180") or wkt,
                "bbox_epsg2180": [round(value, 3) for value in bbox],
            }
            acts_name = f"{slug}__acts.xml"
            acts_path = target / acts_name
            if acts_path.exists() and not args.force:
                payload = acts_path.read_bytes()
            else:
                response = fetch(client, {"typeNames": TYPE_ACT, "bbox": _bbox_param(bbox)})
                save(acts_name, response, {"case": slug, "kind": "acts"})
                payload = response.content
            # Dokumenty i rysunki wskazane przez akty z odpowiedzi.
            root = ElementTree.fromstring(payload)
            document_ids: list[str] = []
            drawing_ids: list[str] = []
            for member in root:
                for child in member:
                    act = parse_act(ElementTree.tostring(child, encoding="utf-8"))
                    if act is None:
                        continue
                    document_ids += [gml_id_for(path) for _, path in act.document_refs]
                    drawing_ids += [gml_id_for(path) for path in act.drawing_refs]
            for kind, type_name, ids in (
                ("documents", TYPE_DOCUMENT, document_ids),
                ("drawings", TYPE_DRAWING, drawing_ids),
            ):
                unique = list(dict.fromkeys(ids))
                if not unique:
                    continue
                name = f"{slug}__{kind}.xml"
                if (target / name).exists() and not args.force:
                    print(f"pominięto (istnieje): {name}")
                    continue
                response = fetch(client, {"typeNames": type_name, "resourceId": ",".join(unique)})
                save(name, response, {"case": slug, "kind": kind, "resource_ids": unique})
        # Realny wyjątek usługi: nieznany typ obiektu (kontrola, że błąd nie wygląda jak „brak aktów”).
        name = "ows_exception_unknown_type.xml"
        if not (target / name).exists() or args.force:
            response = fetch(client, {"typeNames": "app-mpzp:NieistniejacyTyp"})
            save(name, response, {"kind": "exception_report", "description": "nieznany typ obiektu"})
    manifest["files"] = dict(sorted(manifest["files"].items()))
    manifest["cases"] = dict(sorted(manifest["cases"].items()))
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", "utf-8")


if __name__ == "__main__":
    main()
