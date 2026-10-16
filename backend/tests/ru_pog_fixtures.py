"""Zamrożone, rzeczywiste odpowiedzi Rejestru Urbanistycznego (WFS ``app-pog``, AU-105) i pomocnicy testów.

Pliki w ``fixtures/source_contracts/ru_pog_features`` powstały skryptem ``scripts/capture_ru_pog_fixtures.py``: to
bajt w bajt odpowiedzi usługi z 2026-10-12 (SHA-256 nieskompresowanej treści w ``manifest.json``), pobrane przez
produkcyjny adapter dla 30 działek korpusu referencyjnego, strona po stronie. Testy kierują je przez prawdziwy
``OgcClient`` i adapter, a geometrie działek są rzeczywistymi wielokątami ULDK z korpusu.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
import respx
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from app.modules.imports.infrastructure.ogc_client import OgcClient, OgcClientConfig
from app.modules.planning.domain.pog_discovery import Bbox, snap_bbox_outward
from app.modules.planning.infrastructure.ru_pog_features import BeforeRequest, RuPogFeaturesAdapter

TESTS_DIR = Path(__file__).resolve().parent
FIXTURE_DIR = TESTS_DIR / "fixtures/source_contracts/ru_pog_features"
CORPUS_DIR = TESTS_DIR / "fixtures/reference_corpus"
WFS_URL = "https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe/app-pog/wfs"
WMS_URL = "https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe/wms-pog/wms"
PUBLIC_IP = "93.184.216.34"

BIELSKO = "real-009-246101-1-0056-155-3"  # binding: akt, strefa, bez OUZ/OZS na działce
BIELSKO_WITH_OUZ = "real-010-246101-1-0055-68-13"  # binding: akt, strefa, OUZ i obszar zabudowy śródmiejskiej
LEGNICA_TWO_PAGES = "real-013-026201-1-0009-1319-4"  # binding; strefy wymagają drugiej strony (5 żądań)
KRAKOW_NO_POG = "real-005-126105-9-0001-580-4"  # RU odpowiada poprawnie i nie zwraca żadnego obiektu
WARSZAWA_NO_POG = "real-001-146510-8-0502-1-3"


@lru_cache(maxsize=1)
def manifest() -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / "manifest.json").read_text("utf-8"))


def body(name: str) -> bytes:
    return gzip.decompress((FIXTURE_DIR / name).read_bytes())


def verify_manifest_hashes() -> list[str]:
    """Nazwy plików, których SHA-256 nie zgadza się z manifestem (zmiana bajtów = błąd)."""
    return [
        name
        for name, entry in manifest()["files"].items()
        if hashlib.sha256(body(name)).hexdigest() != entry["sha256"]
    ]


def case_ids() -> list[str]:
    return sorted(manifest()["cases"])


def parcel(case_id: str) -> BaseGeometry:
    feature = json.loads((CORPUS_DIR / "artifacts/parcels" / f"{case_id}.geojson").read_text("utf-8"))
    return shape(feature["geometry"])


def tile(case_id: str) -> Bbox:
    return snap_bbox_outward(parcel(case_id).bounds)


@lru_cache(maxsize=1)
def _index() -> dict[tuple[str, str, int], str]:
    index: dict[tuple[str, str, int], str] = {}
    for case in manifest()["cases"].values():
        for item in case["requests"]:
            index[(item["type_name"], item["bbox"], int(item["start_index"]))] = item["file"]
    return index


def ogc_client(before_request: BeforeRequest | None = None, **overrides: object) -> OgcClient:
    defaults: dict[str, object] = {
        "allowed_hosts": frozenset({"rejestr-urbanistyczny.gov.pl"}),
        "retries": 0,
        "backoff_seconds": 0,
        "max_response_bytes": 2_000_000,
        "max_total_bytes": 5_000_000,
    }
    defaults.update(overrides)
    return OgcClient(
        source_id="pog_app",
        config=OgcClientConfig(**defaults),  # type: ignore[arg-type]
        resolver=lambda _host: (PUBLIC_IP,),
        sleep=lambda _seconds: None,
        before_request=before_request,
    )


def adapter(**overrides: object) -> RuPogFeaturesAdapter:
    return RuPogFeaturesAdapter(
        wfs_url=WFS_URL, client_factory=lambda hook: ogc_client(hook, **overrides)
    )


def respond_from_fixtures(unexpected: list[str] | None = None) -> Callable[[httpx.Request], httpx.Response]:
    """Odpowiedź z zamrożonego manifestu po (typ, kafel, startIndex); brak wpisu to błąd testu, nie „pusta odpowiedź”."""

    def respond(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        key = (
            params.get("typeNames", "").removeprefix("app-pog:"),
            params.get("bbox", ""),
            int(params.get("startIndex", "0")),
        )
        name = _index().get(key)
        if name is None:
            if unexpected is not None:
                unexpected.append(str(request.url))
            return httpx.Response(599, text="brak zamrożonej odpowiedzi dla tego żądania")
        return httpx.Response(200, content=body(name), headers={"content-type": "application/gml+xml; version=3.2"})

    return respond


def mount(router: respx.MockRouter, unexpected: list[str] | None = None) -> respx.Route:
    """Trasa WFS RU obsługiwana z zamrożonych odpowiedzi; ``route.call_count`` to liczba żądań HTTP."""
    return router.get(WFS_URL).mock(side_effect=respond_from_fixtures(unexpected))
