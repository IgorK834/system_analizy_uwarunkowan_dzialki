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
