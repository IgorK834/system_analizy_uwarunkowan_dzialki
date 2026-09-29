from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from xml.etree import ElementTree

import httpx
import pytest
import respx

from app.core.settings import Settings
from app.main import app
from app.routers.map_tiles import (
    _load_tile_until_disconnect,
    get_wms_tile_registry,
)
from app.services.wms_tiles import (
    InvalidTileCoordinatesError,
    WmsPreviewSource,
    WmsTilePreviewRegistry,
    WmsTileProxy,
    WmsTileUnavailableError,
    load_wms_preview_sources,
    web_mercator_tile_bbox,
)

# BK-401: import do PostGIS, analiza lokalnego wydania i dekoder MVT.
import math as _math
from collections.abc import Iterator as _Iterator
from dataclasses import replace as _replace
from datetime import datetime as _datetime, timezone as _timezone
from uuid import uuid4 as _uuid4

from fastapi.testclient import TestClient as _TestClient
from pyproj import Transformer as _Transformer
from shapely import affinity as _affinity, from_wkt as _from_wkt
from shapely.geometry import box as _box
from sqlalchemy import text as _sql_text
from sqlalchemy.orm import Session as _Session

from app.core.data_sources import DataSourceEntry as _DataSourceEntry
from app.core.pog_presentation import load_pog_presentation as _load_presentation
from app.core.settings import settings as _app_settings
from app.db.session import SessionLocal as _SessionLocal, get_db as _get_db
from app.modules.imports.application.common import ImportRelease as _ImportRelease
from app.modules.imports.application.pog_import import (
    PogSourceBatch as _PogSourceBatch,
    run_pog_import as _run_pog_import,
)
from app.modules.imports.domain.pog import (
    PogActRecord as _PogActRecord,
    PogFeatureRecord as _PogFeatureRecord,
    PogNumericValue as _PogNumericValue,
    PogObjectId as _PogObjectId,
    PogPlanningParameters as _PogPlanningParameters,
)
from app.modules.imports.infrastructure.artifacts import (
    LocalArtifactStore as _LocalArtifactStore,
)
from app.modules.imports.infrastructure.pog.reader import (
    assemble_ru_pog_acts as _assemble_ru_pog_acts,
    merge_ru_pog_objects as _merge_ru_pog_objects,
    parse_ru_app_feature_collection as _parse_ru,
)
from app.modules.imports.infrastructure.repository import (
    SqlAlchemyImportRepository as _SqlAlchemyImportRepository,
)
from app.modules.planning.composition import pog_tile_cache as _pog_tile_cache
from app.modules.planning.domain.pog_features import POG_PARAMETER_NAMES
from app.services.analysis_orchestrator import (
    _analyze_pog_local_release as _analyze_local_release,
)
from app.shared.geometry import GeometryPayload as _GeometryPayload
from app.shared.planning_status import inspire_status_uri as _inspire_status_uri
from tests.mvt_decoder import decode_tile

WMS_URL = "https://wms.example.test/kimpzp"
KIUT_URL = "https://wms.example.test/kiut"
PNG = b"\x89PNG\r\n\x1a\nproxy-tile-fixture"


def _settings(cache_dir: Path, **updates: object) -> Settings:
    values: dict[str, object] = {
        "map_tile_cache_dir": str(cache_dir),
        "map_tile_browser_ttl_seconds": 300,
        "map_tile_cache_max_bytes": 1024 * 1024,
        "map_tile_upstream_connect_timeout_seconds": 0.5,
    }
    values.update(updates)
    return Settings(_env_file=None, **values)


def _source(source_key: str = "mpzp", **updates: object) -> WmsPreviewSource:
    values: dict[str, object] = {
        "base_url": WMS_URL,
        "layers": "plany_granice,raster",
        "version": "1.1.1",
        "min_zoom": 11,
        "max_zoom": 18,
        "tile_size": 256,
        "fresh_ttl_s": 3600,
        "stale_ttl_s": 7200,
        "upstream_concurrency": 4,
        "read_timeout_s": 0.5,
        "source_id": "kimpzp",
        "label": "MPZP",
        "attribution": "KIMPZP, GUGiK",
        "legal_note": "Podgląd ma charakter poglądowy.",
        "info_url": WMS_URL,
        "catalog_status": "production",
    }
    values.update(updates)
    return WmsPreviewSource(source_key=source_key, **values)  # type: ignore[arg-type]


def _registry(
    cache_dir: Path,
    sources: dict[str, WmsPreviewSource] | None = None,
) -> WmsTilePreviewRegistry:
    configured_sources = sources or {"mpzp": _source()}
    return WmsTilePreviewRegistry(_settings(cache_dir), configured_sources)


def test_default_registry_defines_all_preview_sources() -> None:
    config = Settings(_env_file=None)

    sources = load_wms_preview_sources(config.wms_preview_sources_path)

    assert set(sources) == {"mpzp", "pog", "kiut"}
    assert sources["kiut"].tile_size == 512
    assert (sources["kiut"].min_zoom, sources["kiut"].max_zoom) == (16, 20)
    assert sources["kiut"].allowed_redirect_host_suffixes == (".gugik.gov.pl",)
    assert sources["kiut"].max_redirects == 3
    assert sources["pog"].source_id == "pog_wms"


def test_kiut_layers_and_zoom_match_getcapabilities_contract() -> None:
    config = Settings(_env_file=None)
    source = load_wms_preview_sources(config.wms_preview_sources_path)["kiut"]
    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "source_contracts"
        / "kiut_getcapabilities.xml"
    )
    root = ElementTree.parse(fixture).getroot()
    layers = {
        name.text: layer
        for layer in root.findall(".//{*}Layer")
        if (name := layer.find("{*}Name")) is not None and name.text
    }

    configured_layers = set(source.layers.split(","))
    assert configured_layers <= set(layers)
    assert "gesut" in layers
    assert all(
        layers[name].attrib.get("queryable") == "1" for name in configured_layers
    )
    assert all(layers[name].attrib.get("cascaded") == "1" for name in configured_layers)
    assert all(
        float(layers[name].findtext("{*}MaxScaleDenominator", default="inf")) <= 1000
        for name in configured_layers
    )
    assert source.min_zoom >= 16


def test_preview_source_rejects_invalid_redirect_configuration() -> None:
    with pytest.raises(ValueError, match="niepoprawne sufiksy"):
        _source(allowed_redirect_host_suffixes=("gugik.gov.pl",))
    with pytest.raises(ValueError, match="limit przekierowań"):
        _source(max_redirects=6)
    with pytest.raises(ValueError, match="niepoprawne sufiksy"):
        _source(allowed_redirect_host_suffixes=(".",))


def test_web_mercator_tile_bbox_matches_known_warsaw_tile() -> None:
    bbox = web_mercator_tile_bbox(12, 2287, 1348)

    assert bbox == pytest.approx(
        (
            2338361.5693001114,
            6838973.794731289,
            2348145.508920614,
            6848757.734351791,
        )
    )


@pytest.mark.parametrize(
    ("z", "x", "y"),
    [(-1, 0, 0), (1, -1, 0), (1, 0, -1), (1, 2, 0), (1, 0, 2)],
)
def test_web_mercator_tile_bbox_rejects_invalid_coordinates(
    z: int, x: int, y: int
) -> None:
    with pytest.raises(InvalidTileCoordinatesError):
        web_mercator_tile_bbox(z, x, y)


@pytest.mark.asyncio
@respx.mock
async def test_proxy_fetches_png_once_and_then_uses_disk_cache(tmp_path: Path) -> None:
    route = respx.get(WMS_URL).mock(
        return_value=httpx.Response(
            200,
            content=PNG,
            headers={"content-type": "image/png"},
        )
    )
    proxy = WmsTileProxy(_source(), _settings(tmp_path))

    first = await proxy.get_tile(12, 2287, 1348)
    second = await proxy.get_tile(12, 2287, 1348)

    assert first.content == PNG
    assert first.cache_status == "MISS"
    assert second.cache_status == "HIT"
    assert second.etag == first.etag
    assert route.call_count == 1
    request = route.calls[0].request
    assert request.url.params["request"] == "GetMap"
    assert request.url.params["version"] == "1.1.1"
    assert request.url.params["layers"] == "plany_granice,raster"
    assert request.url.params["srs"] == "EPSG:3857"
    assert request.url.params["width"] == "256"
    assert len(list((tmp_path / "mpzp").rglob("*.png"))) == 1

    await proxy.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_kiut_uses_own_zoom_range_and_512_pixel_tiles(tmp_path: Path) -> None:
    route = respx.get(KIUT_URL).mock(
        return_value=httpx.Response(
            200, content=PNG, headers={"content-type": "image/png"}
        )
    )
    source = _source(
        "kiut",
        base_url=KIUT_URL,
        info_url=KIUT_URL,
        min_zoom=16,
        max_zoom=20,
        tile_size=512,
        source_id="kiut_wms",
        label="Uzbrojenie terenu",
    )
    proxy = WmsTileProxy(source, _settings(tmp_path))

    with pytest.raises(InvalidTileCoordinatesError, match="16–20"):
        await proxy.get_tile(15, 1, 1)
    result = await proxy.get_tile(19, 1, 1)

    assert result.cache_status == "MISS"
    assert route.calls[0].request.url.params["width"] == "512"
    assert route.calls[0].request.url.params["height"] == "512"
    assert len(list((tmp_path / "kiut").rglob("*.png"))) == 1
    await proxy.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_kiut_follows_allowed_redirect_and_caches_response(
    tmp_path: Path,
) -> None:
    redirect_target = "https://integracja02.gugik.gov.pl/cgi-bin/KrajowaIntegracjaUzbrojeniaTerenu?service=WMS&version=1.1.1&request=GetMap"
    kiut_front = respx.get("https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaUzbrojeniaTerenu").mock(
        return_value=httpx.Response(
            302,
            headers={"Location": redirect_target},
        )
    )
    kiut_worker = respx.get(redirect_target).mock(
        return_value=httpx.Response(
            200,
            content=PNG,
            headers={"content-type": "image/png"},
        )
    )
    source = _source(
        "kiut",
        base_url="https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaUzbrojeniaTerenu",
        info_url="https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaUzbrojeniaTerenu",
        min_zoom=16,
        max_zoom=20,
        tile_size=512,
        allowed_redirect_host_suffixes=(".gugik.gov.pl",),
        max_redirects=3,
        source_id="kiut_wms",
        label="Uzbrojenie terenu",
    )
    proxy = WmsTileProxy(source, _settings(tmp_path))

    first = await proxy.get_tile(18, 145001, 89070)
    second = await proxy.get_tile(18, 145001, 89070)

    assert first.content == PNG
    assert first.cache_status == "MISS"
    assert second.cache_status == "HIT"
    assert kiut_front.call_count == 1
    assert kiut_worker.call_count == 1
    await proxy.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_proxy_rejects_redirect_to_unallowed_host(tmp_path: Path) -> None:
    respx.get("https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaUzbrojeniaTerenu").mock(
        return_value=httpx.Response(
            302,
            headers={"Location": "https://evil.example.com/cgi-bin/steal"},
        )
    )
    evil_route = respx.get("https://evil.example.com/cgi-bin/steal").mock(
        return_value=httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
    )
    source = _source(
        "kiut",
        base_url="https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaUzbrojeniaTerenu",
        info_url="https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaUzbrojeniaTerenu",
        min_zoom=16,
        max_zoom=20,
        tile_size=512,
        allowed_redirect_host_suffixes=(".gugik.gov.pl",),
        max_redirects=3,
        source_id="kiut_wms",
        label="Uzbrojenie terenu",
    )
    proxy = WmsTileProxy(source, _settings(tmp_path))

    with pytest.raises(WmsTileUnavailableError, match="niedozwolony adres"):
        await proxy.get_tile(18, 145001, 89070)

    assert evil_route.call_count == 0
    assert not list(tmp_path.rglob("*.png"))
    await proxy.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_proxy_rejects_non_https_redirect(tmp_path: Path) -> None:
    respx.get("https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaUzbrojeniaTerenu").mock(
        return_value=httpx.Response(
            302,
            headers={"Location": "http://integracja02.gugik.gov.pl/cgi-bin/KrajowaIntegracjaUzbrojeniaTerenu"},
        )
    )
    source = _source(
        "kiut",
        base_url="https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaUzbrojeniaTerenu",
        info_url="https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaUzbrojeniaTerenu",
        min_zoom=16,
        max_zoom=20,
        tile_size=512,
        allowed_redirect_host_suffixes=(".gugik.gov.pl",),
        max_redirects=3,
        source_id="kiut_wms",
        label="Uzbrojenie terenu",
    )
    proxy = WmsTileProxy(source, _settings(tmp_path))

    with pytest.raises(WmsTileUnavailableError, match="niedozwolony adres"):
        await proxy.get_tile(18, 145001, 89070)

    await proxy.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_proxy_rejects_lookalike_redirect_host(tmp_path: Path) -> None:
    respx.get(
        "https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaUzbrojeniaTerenu"
    ).mock(
        return_value=httpx.Response(
            302,
            headers={
                "Location": (
                    "https://gugik.gov.pl.evil.com/cgi-bin/"
                    "KrajowaIntegracjaUzbrojeniaTerenu"
                )
            },
        )
    )
    evil_route = respx.get("https://gugik.gov.pl.evil.com/cgi-bin/KrajowaIntegracjaUzbrojeniaTerenu").mock(
        return_value=httpx.Response(
            200, content=PNG, headers={"content-type": "image/png"}
        )
    )
    source = _source(
        "kiut",
        base_url="https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaUzbrojeniaTerenu",
        info_url="https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaUzbrojeniaTerenu",
        min_zoom=16,
        max_zoom=20,
        tile_size=512,
        allowed_redirect_host_suffixes=(".gugik.gov.pl",),
        max_redirects=3,
        source_id="kiut_wms",
        label="Uzbrojenie terenu",
    )
    proxy = WmsTileProxy(source, _settings(tmp_path))

    with pytest.raises(WmsTileUnavailableError, match="niedozwolony adres"):
        await proxy.get_tile(18, 145001, 89070)

    assert evil_route.call_count == 0
    await proxy.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_proxy_fails_when_exceeding_max_redirects(tmp_path: Path) -> None:
    respx.get("https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaUzbrojeniaTerenu").mock(
        return_value=httpx.Response(
            302,
            headers={"Location": "https://integracja01.gugik.gov.pl/step1"},
        )
    )
    respx.get("https://integracja01.gugik.gov.pl/step1").mock(
        return_value=httpx.Response(
            302,
            headers={"Location": "https://integracja02.gugik.gov.pl/step2"},
        )
    )
    respx.get("https://integracja02.gugik.gov.pl/step2").mock(
        return_value=httpx.Response(
            302,
            headers={"Location": "https://integracja03.gugik.gov.pl/step3"},
        )
    )
    respx.get("https://integracja03.gugik.gov.pl/step3").mock(
        return_value=httpx.Response(
            302,
            headers={"Location": "https://integracja04.gugik.gov.pl/step4"},
        )
    )
    source = _source(
        "kiut",
        base_url="https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaUzbrojeniaTerenu",
        info_url="https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaUzbrojeniaTerenu",
        min_zoom=16,
        max_zoom=20,
        tile_size=512,
        allowed_redirect_host_suffixes=(".gugik.gov.pl",),
        max_redirects=3,
        source_id="kiut_wms",
        label="Uzbrojenie terenu",
    )
    proxy = WmsTileProxy(source, _settings(tmp_path))

    with pytest.raises(WmsTileUnavailableError, match="Przekroczono limit przekierowań"):
        await proxy.get_tile(18, 145001, 89070)

    await proxy.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_source_with_zero_max_redirects_rejects_redirect(tmp_path: Path) -> None:
    respx.get(WMS_URL).mock(
        return_value=httpx.Response(
            302,
            headers={"Location": "https://mapy.geoportal.gov.pl/other"},
        )
    )
    followed = respx.get("https://mapy.geoportal.gov.pl/other").mock(
        return_value=httpx.Response(
            200, content=PNG, headers={"content-type": "image/png"}
        )
    )
    source = _source("mpzp", max_redirects=0)
    proxy = WmsTileProxy(source, _settings(tmp_path))

    with pytest.raises(WmsTileUnavailableError):
        await proxy.get_tile(12, 2287, 1348)

    assert followed.call_count == 0
    await proxy.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_concurrent_misses_are_coalesced_to_one_upstream_request(
    tmp_path: Path,
) -> None:
    async def delayed_response(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.03)
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    route = respx.get(WMS_URL).mock(side_effect=delayed_response)
    proxy = WmsTileProxy(_source(), _settings(tmp_path))

    results = await asyncio.gather(*(proxy.get_tile(12, 2287, 1348) for _ in range(20)))

    assert route.call_count == 1
    assert sum(result.cache_status == "MISS" for result in results) == 1
    assert sum(result.cache_status == "HIT" for result in results) == 19
    await proxy.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_slow_kiut_miss_does_not_block_mpzp_miss(tmp_path: Path) -> None:
    kiut_started = asyncio.Event()
    release_kiut = asyncio.Event()

    async def slow_kiut(_: httpx.Request) -> httpx.Response:
        kiut_started.set()
        await release_kiut.wait()
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    respx.get(KIUT_URL).mock(side_effect=slow_kiut)
    respx.get(WMS_URL).mock(
        return_value=httpx.Response(
            200, content=PNG, headers={"content-type": "image/png"}
        )
    )
    sources = {
        "mpzp": _source(upstream_concurrency=1),
        "kiut": _source(
            "kiut",
            base_url=KIUT_URL,
            info_url=KIUT_URL,
            min_zoom=17,
            max_zoom=20,
            tile_size=512,
            upstream_concurrency=1,
            source_id="kiut_wms",
            label="Uzbrojenie terenu",
        ),
    }
    registry = _registry(tmp_path, sources)
    kiut_task = asyncio.create_task(registry.get("kiut").get_tile(19, 1, 1))
    await asyncio.wait_for(kiut_started.wait(), timeout=0.5)

    mpzp = await asyncio.wait_for(
        registry.get("mpzp").get_tile(12, 2287, 1348),
        timeout=0.5,
    )
    release_kiut.set()
    kiut = await kiut_task

    assert mpzp.cache_status == "MISS"
    assert kiut.cache_status == "MISS"
    await registry.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_stale_tile_is_returned_immediately_and_survives_refresh_failure(
    tmp_path: Path,
) -> None:
    route = respx.get(WMS_URL).mock(
        side_effect=[
            httpx.Response(200, content=PNG, headers={"content-type": "image/png"}),
            httpx.TimeoutException("timeout"),
            httpx.TimeoutException("timeout"),
        ]
    )
    proxy = WmsTileProxy(
        _source(fresh_ttl_s=1, stale_ttl_s=60),
        _settings(tmp_path),
    )
    await proxy.get_tile(12, 2287, 1348)
    cache_file = next(tmp_path.rglob("*.png"))
    stale_time = time.time() - 5
    os.utime(cache_file, (stale_time, stale_time))

    stale = await proxy.get_tile(12, 2287, 1348)
    await proxy.wait_for_background_tasks()

    assert stale.content == PNG
    assert stale.cache_status == "STALE"
    assert stale.age_seconds >= 4
    assert cache_file.read_bytes() == PNG
    assert route.call_count == 3
    await proxy.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_proxy_rejects_xml_or_non_png_upstream_response(tmp_path: Path) -> None:
    respx.get(WMS_URL).mock(
        return_value=httpx.Response(
            200,
            text="<ServiceException>bad bbox</ServiceException>",
            headers={"content-type": "application/vnd.ogc.se_xml"},
        )
    )
    proxy = WmsTileProxy(_source(), _settings(tmp_path))

    with pytest.raises(WmsTileUnavailableError):
        await proxy.get_tile(12, 2287, 1348)

    assert not list(tmp_path.rglob("*.png"))
    await proxy.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_http_endpoint_exposes_cache_headers_and_supports_etag(
    tmp_path: Path,
) -> None:
    route = respx.get(WMS_URL).mock(
        return_value=httpx.Response(
            200,
            content=PNG,
            headers={"content-type": "image/png"},
        )
    )
    registry = _registry(tmp_path)
    app.dependency_overrides[get_wms_tile_registry] = lambda: registry
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            first = await client.get("/api/v1/map/tiles/mpzp/12/2287/1348.png")
            unchanged = await client.get(
                "/api/v1/map/tiles/mpzp/12/2287/1348.png",
                headers={"If-None-Match": first.headers["etag"]},
            )
    finally:
        app.dependency_overrides.clear()
        await registry.aclose()

    assert first.status_code == 200
    assert first.content == PNG
    assert first.headers["content-type"] == "image/png"
    assert first.headers["x-tile-cache"] == "MISS"
    assert "max-age=300" in first.headers["cache-control"]
    assert unchanged.status_code == 304
    assert unchanged.content == b""
    assert unchanged.headers["etag"] == first.headers["etag"]
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_preview_sources_endpoint_exposes_safe_frontend_contract(
    tmp_path: Path,
) -> None:
    sources = {
        "mpzp": _source(),
        "pog": _source(
            "pog",
            base_url="https://wms.example.test/pog",
            info_url="https://wms.example.test/pog",
            source_id="pog_wms",
            label="Plany ogólne gmin",
        ),
        "kiut": _source(
            "kiut",
            base_url=KIUT_URL,
            info_url=KIUT_URL,
            min_zoom=16,
            max_zoom=20,
            tile_size=512,
            source_id="kiut_wms",
            label="Uzbrojenie terenu",
        ),
    }
    registry = _registry(tmp_path, sources)
    app.dependency_overrides[get_wms_tile_registry] = lambda: registry
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            response = await client.get("/api/v1/map/preview-sources")
    finally:
        app.dependency_overrides.clear()
        await registry.aclose()

    assert response.status_code == 200
    payload = response.json()
    assert [item["source_key"] for item in payload] == ["mpzp", "pog", "kiut"]
    kiut = payload[2]
    assert kiut["tile_url_template"] == ("/api/v1/map/tiles/kiut/{z}/{x}/{y}.png")
    assert kiut["min_zoom"] == 16
    assert kiut["max_zoom"] == 20
    assert kiut["tile_size"] == 512
    assert "base_url" not in kiut
    assert "layers" not in kiut


@pytest.mark.asyncio
async def test_http_endpoint_rejects_source_specific_zoom(tmp_path: Path) -> None:
    kiut_source = _source(
        "kiut",
        base_url=KIUT_URL,
        info_url=KIUT_URL,
        min_zoom=16,
        max_zoom=20,
        tile_size=512,
        source_id="kiut_wms",
        label="Uzbrojenie terenu",
    )
    registry = _registry(tmp_path, {"kiut": kiut_source})
    app.dependency_overrides[get_wms_tile_registry] = lambda: registry
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            response = await client.get("/api/v1/map/tiles/kiut/15/1/1.png")
    finally:
        app.dependency_overrides.clear()
        await registry.aclose()

    assert response.status_code == 422
    assert "16–20" in response.json()["detail"]


@pytest.mark.asyncio
async def test_disconnected_map_client_cancels_pending_tile_work() -> None:
    cancelled = asyncio.Event()

    async def slow_tile(*_: int) -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    request = SimpleNamespace(is_disconnected=AsyncMock(return_value=True))
    proxy = SimpleNamespace(get_tile=slow_tile)

    result = await _load_tile_until_disconnect(request, proxy, 12, 2287, 1348)

    assert result is None
    assert cancelled.is_set()


# --- Wektorowe kafle POG z wersjonowanego wydania (BK-401) --------------------
#
# Testy PostGIS/HTTP importują realne rekordy APP planu ogólnego Sopotu
# (tests/fixtures/ru) do transakcji wycofywanej po teście, a następnie
# porównują zdekodowany kafel MVT z wynikiem analizy tej samej strefy.


RU_FIXTURES = Path(__file__).parent / "fixtures" / "ru"
RU_TYPES = {
    "act": "AktPlanowaniaPrzestrzennego",
    "zone": "StrefaPlanistyczna",
    "ouz": "ObszarUzupelnieniaZabudowy",
    "ozs": "ObszarZabudowySrodmiejskiej",
    "osdis": "ObszarStandardowDostepnosciInfrastrukturySpolecznej",
}
POG_SOURCE_ID = "pog_app"
TILE_ZOOM = 16
ALLOWED_TILE_PROPERTIES = {
    "feature_id", "feature_version", "symbol", "label", "legal_status", "teryt",
    "act_id", "data_release_id", "zone_code", *POG_PARAMETER_NAMES,
    "parameters_informational", "primary_profiles", "additional_profiles",
    "act_version",
}
_TO_WGS84 = _Transformer.from_crs("EPSG:2180", "EPSG:4326", always_xy=True)


def _lonlat_tile(lon: float, lat: float, zoom: int) -> tuple[int, int]:
    n = 1 << zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = _math.radians(lat)
    y = int((1.0 - _math.asinh(_math.tan(lat_rad)) / _math.pi) / 2.0 * n)
    return x, y


def _pog_source_entry() -> _DataSourceEntry:
    return _DataSourceEntry.model_validate(
        {
            "source_id": POG_SOURCE_ID,
            "name": "POG APP (fixture RU)",
            "owner": "Test",
            "status": "production",
            "production_ready": True,
            "contract_confirmed": True,
            "access_type": "app_gml",
            "capabilities_url": None,
            "file_url": "file:///fixture.gml",
            "type_names": ["StrefaPlanistyczna"],
            "layers": None,
            "protocol_version": "fixture/1",
            "source_crs": "EPSG:2180",
            "target_crs": "EPSG:2180",
            "teryt_scope": ["226401"],
            "license": "Fixture RU.",
            "attribution": "Rejestr Urbanistyczny.",
            "expected_update_interval": "never",
            "sla": "test",
            "last_manual_verification": "2026-09-28",
        }
    )


def _sopot_act() -> _PogActRecord:
    """Realny akt Sopotu ze strefą 1POG-100SU oraz OUZ/OZS/OSDIS z fixtur RU.

    Obszary OUZ/OZS/OSDIS są przesunięte (bez zmiany kształtu i atrybutów) tak,
    by ich środek leżał w środku strefy — dzięki temu jeden kafel zawiera
    wszystkie pięć warstw. OSDIS pochodzi z innej gminy i ma podmienione
    odwołanie do aktu, tak jak w teście mapowania RU.
    """
    parts = []
    for name, feature_type in RU_TYPES.items():
        payload = (RU_FIXTURES / f"wfs_pog_getfeature_{name}.xml").read_bytes()
        if name == "osdis":
            payload = payload.replace(
                b"PL.ZIPPZP.10067/240203-POG/1POG", b"PL.ZIPPZP.10011/226401-POG/1POG"
            )
        parts.append(_parse_ru(payload, expected_type=feature_type, source_reference="fixture"))
    act = _assemble_ru_pog_acts(_merge_ru_pog_objects(tuple(parts)))[0]
    zone = next(f for f in act.features if f.feature_type == "planning_zone")
    center = _from_wkt(zone.geometry.wkt).centroid
    features = []
    for feature in act.features:
        if feature.feature_type == "planning_zone":
            features.append(feature)
            continue
        shape = _from_wkt(feature.geometry.wkt)
        moved = _affinity.translate(
            shape, center.x - shape.centroid.x, center.y - shape.centroid.y
        )
        features.append(feature.with_geometry(_GeometryPayload(moved.wkt)))
    return _replace(act, features=tuple(features))


def _project_act(zone_wkt: str, *, height_m: float | None) -> _PogActRecord:
    """Syntetyczny projekt aktu obok strefy Sopotu: 0% ≠ brak wartości."""
    shape = _affinity.translate(_from_wkt(zone_wkt), 0, 0)
    minx, miny, maxx, maxy = shape.bounds
    square = _box(maxx + 5, miny, maxx + 55, miny + 50)
    namespace = "PL.ZIPPZP.99999/226401-POG"
    return _PogActRecord(
        act_identifier=f"{namespace}/2POG",
        resolution_number=None,
        resolution_date=None,
        teryt="226401",
        name="Projekt zmiany planu ogólnego (syntetyczny)",
        legal_status="project",
        raw_legal_status=_inspire_status_uri("project"),  # type: ignore[arg-type]
        boundary=_GeometryPayload(square.buffer(1).wkt),
        object_id=_PogObjectId(namespace, "2POG", "20260901T000000"),
        features=(
            _PogFeatureRecord(
                feature_type="planning_zone",
                geometry=_GeometryPayload(square.wkt),
                raw_attributes={"symbol": "SJ", "geometria": "x" * 5000},
                object_id=_PogObjectId(namespace, "2POG-1SJ", "20260901T000000"),
                symbol="SJ",
                label="strefa wielofunkcyjna z zabudową mieszkaniową jednorodzinną",
                parameters=_PogPlanningParameters(
                    max_overground_floor_area_ratio=_PogNumericValue(0.0, "1"),
                    max_building_height=(
                        _PogNumericValue(height_m, "m") if height_m is not None else None
                    ),
                    max_building_coverage=_PogNumericValue(0.0, "%"),
                    min_biologically_active=None,
                ),
            ),
        ),
    )


class _StaticReader:
    def __init__(self, *acts: _PogActRecord, content: bytes) -> None:
        self.acts = acts
        self.content = content

    def read(self) -> _PogSourceBatch:
        return _PogSourceBatch(self.content, "pog.gml", "application/gml+xml", self.acts)


def _publish(session: _Session, tmp_path: Path, *acts: _PogActRecord, label: str) -> int:
    repository = _SqlAlchemyImportRepository(
        session, _pog_source_entry(), _LocalArtifactStore(tmp_path)
    )
    outcome = _run_pog_import(
        _StaticReader(*acts, content=f"pog-{label}-{_uuid4().hex}".encode()),
        POG_SOURCE_ID,
        repository,
        release=_ImportRelease(
            POG_SOURCE_ID,
            label,
            _datetime(2026, 9, 28, tzinfo=_timezone.utc),
            publication_allowed=True,
            dry_run=False,
            teryt_scope=("226401",),
        ),
    )
    assert outcome.status == "succeeded", outcome
    return int(outcome.data_release_id)


@pytest.fixture
def pog_session() -> _Iterator[_Session]:
    db = _SessionLocal()
    _pog_tile_cache().clear()
    try:
        yield db
    finally:
        db.rollback()
        db.close()
        _pog_tile_cache().clear()


@pytest.fixture
def pog_client(pog_session: _Session) -> _Iterator[_TestClient]:
    app.dependency_overrides[_get_db] = lambda: pog_session
    try:
        yield _TestClient(app)
    finally:
        app.dependency_overrides.pop(_get_db, None)


@pytest.fixture
def sopot_release(pog_session: _Session, tmp_path: Path) -> dict[str, object]:
    act = _sopot_act()
    zone = next(f for f in act.features if f.feature_type == "planning_zone")
    release_id = _publish(
        pog_session, tmp_path, act, _project_act(zone.geometry.wkt, height_m=None), label="A"
    )
    zone_shape = _from_wkt(zone.geometry.wkt)
    lon, lat = _TO_WGS84.transform(*zone_shape.representative_point().coords[0])
    x, y = _lonlat_tile(lon, lat, TILE_ZOOM)
    return {"release_id": release_id, "zone": zone, "zone_shape": zone_shape, "tile": (x, y)}


def _tile_url(release_id: int, x: int, y: int, z: int = TILE_ZOOM, edition: str | None = None) -> str:
    query = f"?edition={edition}" if edition else ""
    return f"/api/v1/map/pog/releases/{release_id}/{z}/{x}/{y}.mvt{query}"


@pytest.mark.integration
def test_pog_tile_has_five_layers_and_same_parameters_as_analysis(
    pog_session: _Session, pog_client: _TestClient, sopot_release: dict[str, object]
) -> None:
    release_id = int(sopot_release["release_id"])  # type: ignore[call-overload]
    zone_shape = sopot_release["zone_shape"]
    x, y = sopot_release["tile"]  # type: ignore[misc]
    parcel = zone_shape.representative_point().buffer(3, cap_style="square")  # type: ignore[attr-defined]

    analysis = _analyze_local_release(pog_session, parcel, "2264011")
    assert analysis is not None
    pog = analysis[0]
    assert pog.source is not None and pog.source.data_release_id == release_id
    analysed_zone = pog.zones[0]

    response = pog_client.get(_tile_url(release_id, x, y))
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.mapbox-vector-tile"
    layers = decode_tile(response.content)
    assert set(layers) == {
        "zones", "ouz", "downtown", "social_infrastructure_standard", "act_boundary"
    }
    assert all(layer.extent == 4096 and layer.version == 2 for layer in layers.values())

    tile_zone = next(
        feature for feature in layers["zones"].features
        if feature.properties["feature_id"] == analysed_zone.id
    )
    props = tile_zone.properties
    for name in POG_PARAMETER_NAMES:
        assert props[name] == getattr(analysed_zone, name), name
    assert (props["zone_code"], props["symbol"], props["label"]) == (
        analysed_zone.type, analysed_zone.symbol, analysed_zone.label
    )
    assert props["feature_version"] == analysed_zone.feature_version
    assert (props["legal_status"], props["teryt"], props["data_release_id"]) == (
        "binding", "226401", release_id
    )
    assert analysed_zone.primary_profile, "profile muszą przejść import → analiza"
    assert props["primary_profiles"] == ",".join(
        profile.code for profile in analysed_zone.primary_profile
    )
    assert props["additional_profiles"] == ",".join(
        profile.code for profile in analysed_zone.additional_profiles
    )
    assert tile_zone.geometry_type == "POLYGON" and tile_zone.id is not None

    # Kafel nie publikuje surowego XML ani nadmiarowych pól.
    for layer in layers.values():
        for feature in layer.features:
            assert set(feature.properties) <= ALLOWED_TILE_PROPERTIES
            assert all(len(str(value)) <= 256 for value in feature.properties.values())
    boundary = layers["act_boundary"].features[0].properties
    assert boundary["act_id"] == "PL.ZIPPZP.10011/226401-POG/1POG"
    assert boundary["legal_status"] == "binding"


@pytest.mark.integration
def test_pog_tile_keeps_null_distinct_from_zero_and_marks_project(
    pog_client: _TestClient, sopot_release: dict[str, object]
) -> None:
    release_id = int(sopot_release["release_id"])  # type: ignore[call-overload]
    x, y = sopot_release["tile"]  # type: ignore[misc]
    layers = decode_tile(pog_client.get(_tile_url(release_id, x, y)).content)
    project = next(
        feature.properties for feature in layers["zones"].features
        if feature.properties["legal_status"] == "project"
    )
    assert project["max_building_coverage_pct"] == 0.0
    assert project["max_overground_floor_area_ratio"] == 0.0
    assert "max_building_height_m" not in project
    assert "min_biologically_active_pct" not in project
    assert project["zone_code"] == "SJ"

    binding_only = decode_tile(
        pog_client.get(_tile_url(release_id, x, y, edition="binding")).content
    )
    project_only = decode_tile(
        pog_client.get(_tile_url(release_id, x, y, edition="project")).content
    )
    assert {f.properties["legal_status"] for f in binding_only["zones"].features} == {"binding"}
    assert {f.properties["legal_status"] for f in project_only["zones"].features} == {"project"}
    assert {f.properties["legal_status"] for f in project_only["act_boundary"].features} == {
        "project"
    }


@pytest.mark.integration
def test_pog_empty_tile_is_200_with_valid_empty_protobuf(
    pog_client: _TestClient, sopot_release: dict[str, object]
) -> None:
    release_id = int(sopot_release["release_id"])  # type: ignore[call-overload]
    # Kafel na Atlantyku — poprawny adres, brak danych.
    response = pog_client.get(_tile_url(release_id, 20000, 20000))
    assert response.status_code == 200
    assert response.content == b""
    assert decode_tile(response.content) == {}
    assert response.headers["x-pog-tile-features"] == "0"
    assert response.headers["etag"]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("z", "x", "y", "edition"),
    [
        (19, 0, 0, None),
        (-1, 0, 0, None),
        (3, 8, 0, None),
        (3, 0, 8, None),
        (3, -1, 0, None),
        (3, 0, 0, "wszystko"),
    ],
)
def test_pog_tile_rejects_invalid_coordinates_with_422(
    pog_client: _TestClient,
    sopot_release: dict[str, object],
    z: int,
    x: int,
    y: int,
    edition: str | None,
) -> None:
    release_id = int(sopot_release["release_id"])  # type: ignore[call-overload]
    response = pog_client.get(_tile_url(release_id, x, y, z=z, edition=edition))
    assert response.status_code == 422


@pytest.mark.integration
def test_pog_tile_rejects_non_integer_path_and_unknown_release(
    pog_client: _TestClient, sopot_release: dict[str, object]
) -> None:
    assert pog_client.get("/api/v1/map/pog/releases/1/abc/0/0.mvt").status_code == 422
    missing = pog_client.get(_tile_url(2_000_000_000, 0, 0, z=0))
    assert missing.status_code == 404
    assert pog_client.get("/api/v1/map/pog/releases/2000000000").status_code == 404


@pytest.mark.integration
def test_pog_tile_etag_returns_304_and_cache_hit(
    pog_client: _TestClient, sopot_release: dict[str, object]
) -> None:
    release_id = int(sopot_release["release_id"])  # type: ignore[call-overload]
    x, y = sopot_release["tile"]  # type: ignore[misc]
    first = pog_client.get(_tile_url(release_id, x, y))
    assert first.headers["x-tile-cache"] == "MISS"
    assert first.headers["cache-control"].startswith("public, max-age=")
    etag = first.headers["etag"]

    second = pog_client.get(_tile_url(release_id, x, y), headers={"If-None-Match": etag})
    assert second.status_code == 304
    assert second.content == b""
    assert second.headers["etag"] == etag
    assert second.headers["x-tile-cache"] == "HIT"
    weak = pog_client.get(_tile_url(release_id, x, y), headers={"If-None-Match": f"W/{etag}"})
    assert weak.status_code == 304
    other = pog_client.get(_tile_url(release_id, x, y), headers={"If-None-Match": '"other"'})
    assert other.status_code == 200 and other.content == first.content


@pytest.mark.integration
def test_pog_tile_cache_separates_releases_and_pinned_url_reproduces_release_a(
    pog_session: _Session,
    pog_client: _TestClient,
    sopot_release: dict[str, object],
    tmp_path: Path,
) -> None:
    release_a = int(sopot_release["release_id"])  # type: ignore[call-overload]
    x, y = sopot_release["tile"]  # type: ignore[misc]
    zone = sopot_release["zone"]
    tile_a = pog_client.get(_tile_url(release_a, x, y))

    # Wydanie B: ta sama strefa, zmieniony projekt (wysokość 12 m zamiast braku).
    release_b = _publish(
        pog_session,
        tmp_path,
        _sopot_act(),
        _project_act(zone.geometry.wkt, height_m=12.0),  # type: ignore[attr-defined]
        label="B",
    )
    assert release_b != release_a
    tile_b = pog_client.get(_tile_url(release_b, x, y))
    tile_a_again = pog_client.get(_tile_url(release_a, x, y))

    assert tile_a.headers["etag"] != tile_b.headers["etag"]
    assert tile_a_again.headers["etag"] == tile_a.headers["etag"]
    assert tile_a_again.content == tile_a.content
    heights = {
        release: next(
            f.properties.get("max_building_height_m")
            for f in decode_tile(tile.content)["zones"].features
            if f.properties["legal_status"] == "project"
        )
        for release, tile in ((release_a, tile_a), (release_b, tile_b))
    }
    assert heights == {release_a: None, release_b: 12.0}
    edition_tile = pog_client.get(_tile_url(release_b, x, y, edition="binding"))
    assert edition_tile.headers["etag"] != tile_b.headers["etag"]

    active = pog_client.get("/api/v1/map/pog/releases/active").json()
    assert active["release_id"] == release_b and active["is_active"] is True
    assert active["tile_url_template"] == (
        f"/api/v1/map/pog/releases/{release_b}/{{z}}/{{x}}/{{y}}.mvt"
    )
    pinned = pog_client.get(f"/api/v1/map/pog/releases/{release_a}").json()
    assert pinned["is_active"] is False
    assert pinned["tile_url_template"].startswith(f"/api/v1/map/pog/releases/{release_a}/")


@pytest.mark.integration
def test_pog_release_metadata_exposes_style_bounds_and_statuses(
    pog_client: _TestClient, sopot_release: dict[str, object]
) -> None:
    release_id = int(sopot_release["release_id"])  # type: ignore[call-overload]
    body = pog_client.get(f"/api/v1/map/pog/releases/{release_id}").json()
    presentation = _load_presentation()
    assert body["style_version"] == presentation.style_version
    assert body["style_sha256"] == presentation.sha256
    assert body["layers"] == [
        "zones", "ouz", "downtown", "social_infrastructure_standard", "act_boundary"
    ]
    assert body["editions"] == ["all", "binding", "project"]
    assert body["acts_by_legal_status"] == {"binding": 1, "project": 1}
    min_lon, min_lat, max_lon, max_lat = body["bounds"]
    assert 18.4 < min_lon < max_lon < 18.7 and 54.3 < min_lat < max_lat < 54.6
    assert (body["min_zoom"], body["max_zoom"]) == (
        _app_settings.pog_tile_min_zoom, _app_settings.pog_tile_max_zoom
    )


@pytest.mark.integration
def test_pog_tile_limits_return_413(
    pog_client: _TestClient,
    sopot_release: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_id = int(sopot_release["release_id"])  # type: ignore[call-overload]
    x, y = sopot_release["tile"]  # type: ignore[misc]
    monkeypatch.setattr(_app_settings, "pog_tile_max_features", 1)
    too_many = pog_client.get(_tile_url(release_id, x, y))
    assert too_many.status_code == 413
    assert too_many.headers["x-pog-tile-limit"] == "features"

    monkeypatch.setattr(_app_settings, "pog_tile_max_features", 100)
    monkeypatch.setattr(_app_settings, "pog_tile_max_bytes", 10)
    too_big = pog_client.get(_tile_url(release_id, x, y))
    assert too_big.status_code == 413
    assert too_big.headers["x-pog-tile-limit"] == "bytes"


@pytest.mark.integration
def test_pog_tile_matches_analysis_for_raw_alias_attributes(
    pog_session: _Session, pog_client: _TestClient, tmp_path: Path
) -> None:
    """Cecha bez kolumn kanonicznych (np. import z GPKG) — wąski podzbiór SQL
    surowych atrybutów daje te same wartości co analiza pełnego rekordu."""
    square = _box(470000, 732000, 470080, 732080)
    raw = {
        "OZNACZENIE": "Strefa usługowa",
        "MAKSYMALNA_WYSOKOŚĆ_ZABUDOWY": "12,5",
        "maksymalny_udzial_powierzchni_zabudowy": {"value": "40"},
        "Zrodlo_Parametrow": "uzasadnienie (PDF)",
        "geometria": "470000 732000 " * 400,
    }
    act = _PogActRecord(
        act_identifier="PL.ZIPPZP.88888/226401-POG/1POG",
        resolution_number=None,
        resolution_date=None,
        teryt="226401",
        name="Akt z surowymi atrybutami",
        legal_status="binding",
        raw_legal_status=_inspire_status_uri("binding"),  # type: ignore[arg-type]
        boundary=_GeometryPayload(square.buffer(2).wkt),
        features=(
            _PogFeatureRecord(
                feature_type="planning_zone",
                geometry=_GeometryPayload(square.wkt),
                raw_attributes=raw,
            ),
        ),
    )
    release_id = _publish(pog_session, tmp_path, act, label="raw")
    parcel = _box(470030, 732030, 470040, 732040)
    pog = _analyze_local_release(pog_session, parcel, "2264011")[0]  # type: ignore[index]
    zone = pog.zones[0]
    lon, lat = _TO_WGS84.transform(470035, 732035)
    x, y = _lonlat_tile(lon, lat, TILE_ZOOM)
    props = decode_tile(pog_client.get(_tile_url(release_id, x, y)).content)["zones"].features[0].properties

    assert (zone.type, zone.max_building_height_m, zone.max_building_coverage_pct) == (
        "SU", 12.5, 40.0
    )
    for name in POG_PARAMETER_NAMES:
        assert props.get(name) == getattr(zone, name), name
    assert props["zone_code"] == zone.type
    assert props["parameters_informational"] is True
    assert "geometria" not in props



@pytest.mark.integration
def test_new_release_is_complete_snapshot_including_unchanged_act(
    pog_session: _Session,
    pog_client: _TestClient,
    sopot_release: dict[str, object],
    tmp_path: Path,
) -> None:
    """BK-104/ADR-008: akt bez zmian treści trafia do nowego wydania.

    Wydanie B różni się od A tylko projektem; niezmieniony, obowiązujący akt
    Sopotu musi być w B (baza, analiza i kafel MVT), a A pozostaje odtwarzalne.
    """
    release_a = int(sopot_release["release_id"])  # type: ignore[call-overload]
    zone = sopot_release["zone"]
    x, y = sopot_release["tile"]  # type: ignore[misc]
    release_b = _publish(
        pog_session,
        tmp_path,
        _sopot_act(),
        _project_act(zone.geometry.wkt, height_m=12.0),  # type: ignore[attr-defined]
        label="B",
    )
    assert release_b != release_a

    def acts(release_id: int) -> list[tuple[str, str, int, bool]]:
        rows = pog_session.execute(
            _sql_text(
                """
                SELECT pa.act_identifier, pav.legal_status,
                       (SELECT count(*) FROM planning_features pf
                         WHERE pf.planning_act_version_id = pav.id) AS features,
                       EXISTS (SELECT 1 FROM plan_boundaries pb
                                WHERE pb.planning_act_version_id = pav.id) AS boundary
                FROM planning_act_versions pav
                JOIN planning_acts pa ON pa.id = pav.planning_act_id
                WHERE pav.data_release_id = :release
                ORDER BY pa.act_identifier
                """
            ),
            {"release": release_id},
        ).all()
        return [tuple(row) for row in rows]  # type: ignore[misc]

    sopot = "PL.ZIPPZP.10011/226401-POG/1POG"
    project = "PL.ZIPPZP.99999/226401-POG/2POG"
    assert acts(release_b) == acts(release_a) == [
        (sopot, "binding", 4, True),
        (project, "project", 1, True),
    ]
    hashes = pog_session.execute(
        _sql_text(
            """
            SELECT pav.data_release_id, pav.content_hash, pav.valid_to IS NULL
            FROM planning_act_versions pav
            JOIN planning_acts pa ON pa.id = pav.planning_act_id
            WHERE pa.act_identifier = :act ORDER BY pav.data_release_id
            """
        ),
        {"act": sopot},
    ).all()
    assert [(row[0], row[2]) for row in hashes] == [(release_a, False), (release_b, True)]
    assert hashes[0][1] == hashes[1][1], "przeniesiony akt zachowuje content_hash"
    run_stats = pog_session.execute(
        _sql_text(
            "SELECT stats FROM import_runs WHERE data_release_id = :release "
            "ORDER BY id DESC LIMIT 1"
        ),
        {"release": release_b},
    ).scalar_one()
    assert (run_stats["changed"], run_stats["unchanged"], run_stats["carried_forward"]) == (1, 1, 1)

    # Analiza aktywnego wydania B widzi obowiązującą strefę Sopotu.
    parcel = sopot_release["zone_shape"].representative_point().buffer(3, cap_style="square")  # type: ignore[attr-defined]
    pog = _analyze_local_release(pog_session, parcel, "2264011")[0]  # type: ignore[index]
    assert pog.source is not None and pog.source.data_release_id == release_b
    assert pog.legal_status == "binding"
    assert [(zone.type, zone.max_building_height_m) for zone in pog.zones] == [("SU", 4.0)]

    # Kafel wydania B zawiera akt Sopotu (strefa, OUZ/OZS/OSDIS i granica).
    layers = decode_tile(pog_client.get(_tile_url(release_b, x, y)).content)
    assert set(layers) == {
        "zones", "ouz", "downtown", "social_infrastructure_standard", "act_boundary"
    }
    zone_ids = {feature.properties["feature_id"] for feature in layers["zones"].features}
    assert "PL.ZIPPZP.10011/226401-POG/1POG-100SU" in zone_ids
    assert {f.properties["data_release_id"] for f in layers["zones"].features} == {release_b}
    assert sopot in {f.properties["act_id"] for f in layers["act_boundary"].features}
    metadata = pog_client.get("/api/v1/map/pog/releases/active").json()
    assert metadata["release_id"] == release_b
    assert metadata["acts_by_legal_status"] == {"binding": 1, "project": 1}


@pytest.mark.integration
def test_reimport_of_identical_artifact_adds_no_versions_and_repairs_nothing_twice(
    pog_session: _Session, tmp_path: Path
) -> None:
    """Idempotencja: identyczny artefakt = to samo wydanie i żadnych nowych wersji."""
    from app.modules.imports.application.common import ImportRelease
    from app.modules.imports.infrastructure.artifacts import LocalArtifactStore
    from app.modules.imports.infrastructure.repository import SqlAlchemyImportRepository

    act = _sopot_act()
    zone = next(f for f in act.features if f.feature_type == "planning_zone")
    acts = (act, _project_act(zone.geometry.wkt, height_m=None))
    repository = SqlAlchemyImportRepository(
        pog_session, _pog_source_entry(), LocalArtifactStore(tmp_path)
    )
    content = f"pog-identical-{_uuid4().hex}".encode()

    def run(label: str):
        return _run_pog_import(
            _StaticReader(*acts, content=content),
            POG_SOURCE_ID,
            repository,
            release=ImportRelease(
                POG_SOURCE_ID, label, _datetime(2026, 9, 28, tzinfo=_timezone.utc),
                publication_allowed=True, dry_run=False, teryt_scope=("226401",),
            ),
        )

    def version_count() -> int:
        return int(pog_session.execute(_sql_text("SELECT count(*) FROM planning_act_versions")).scalar_one())

    first = run("first")
    count = version_count()
    again = run("again")
    assert again.data_release_id == first.data_release_id
    assert (again.stats["new"], again.stats["changed"], again.stats["unchanged"]) == (0, 0, 2)
    assert again.stats["carried_forward"] == 0
    assert version_count() == count
