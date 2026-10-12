"""Zamrożone, rzeczywiste odpowiedzi Rejestru Urbanistycznego (AU-101) i pomocnicy testów.

Pliki w ``fixtures/source_contracts/ru_mpzp_acts`` powstały skryptem ``scripts/capture_ru_mpzp_fixtures.py`` i są
bajt w bajt odpowiedziami usługi z 2026-10-10 (SHA-256 w ``manifest.json``). Testy kierują je przez prawdziwy
``OgcClient`` i adapter, a geometrie działek są rzeczywistymi wielokątami ULDK.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import respx
from shapely import from_wkt
from shapely.geometry.base import BaseGeometry

from app.modules.imports.infrastructure.ogc_client import OgcClient, OgcClientConfig
from app.modules.planning.infrastructure.ru_mpzp_acts import RuMpzpActsAdapter

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures/source_contracts/ru_mpzp_acts"
WFS_URL = "https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe/app-mpzp/wfs"
PUBLIC_IP = "93.184.216.34"

GORA_KALWARIA = "gora_kalwaria_141801_4.0701.23_8"
BIELSKO = "bielsko_biala_246101_1.0056.155_3"
KRAKOW_DRAFT = "krakow_126105_9.0001.540_15"
WARSZAWA_EMPTY = "warszawa_146510_8.0502.1_3"
DYGOWO_EMPTY = "dygowo_321606_2.0029.362"


def manifest() -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / "manifest.json").read_text("utf-8"))


def raw(name: str) -> bytes:
    return (FIXTURE_DIR / name).read_bytes()


def parcel(slug: str) -> BaseGeometry:
    return from_wkt(manifest()["cases"][slug]["parcel_wkt_epsg2180"])


def verify_manifest_hashes() -> list[str]:
    """Nazwy plików, których SHA-256 nie zgadza się z manifestem (zmiana bajtów = błąd)."""
    bad: list[str] = []
    for name, entry in manifest()["files"].items():
        if hashlib.sha256(raw(name)).hexdigest() != entry["sha256"]:
            bad.append(name)
    return bad


def ogc_client(**overrides: object) -> OgcClient:
    defaults: dict[str, object] = {
        "allowed_hosts": frozenset({"rejestr-urbanistyczny.gov.pl"}),
        "retries": 0,
        "backoff_seconds": 0,
        "max_response_bytes": 2_000_000,
        "max_total_bytes": 5_000_000,
    }
    defaults.update(overrides)
    return OgcClient(
        source_id="mpzp_ru",
        config=OgcClientConfig(**defaults),  # type: ignore[arg-type]
        resolver=lambda _host: (PUBLIC_IP,),
        sleep=lambda _seconds: None,
    )


def adapter(**overrides: object) -> RuMpzpActsAdapter:
    return RuMpzpActsAdapter(wfs_url=WFS_URL, client_factory=lambda: ogc_client(**overrides))


def mount_case(router: respx.MockRouter, slug: str) -> None:
    """Rejestruje trasy RU dla przypadku: akty po ``bbox``, dokumenty i rysunki po ``resourceId``."""

    def respond(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        type_name = params.get("typeNames", "")
        if "resourceId" in params:
            kind = "documents" if type_name.endswith("DokumentFormalny") else "drawings"
            name = f"{slug}__{kind}.xml"
        else:
            name = f"{slug}__acts.xml"
        path = FIXTURE_DIR / name
        if not path.exists():
            return httpx.Response(200, content=_empty_collection(), headers={"content-type": "application/gml+xml"})
        return httpx.Response(200, content=path.read_bytes(), headers={"content-type": "application/gml+xml"})

    router.get(WFS_URL).mock(side_effect=respond)


def _empty_collection() -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?><wfs:FeatureCollection '
        'xmlns:wfs="http://www.opengis.net/wfs/2.0" numberMatched="0" numberReturned="0"/>'
    ).encode()


def mounted_adapter(slug: str) -> Callable[[], RuMpzpActsAdapter]:
    """Fabryka adaptera, którego żądania obsługuje ``respx`` (użyć wewnątrz ``respx.mock``)."""
    return lambda: adapter()
