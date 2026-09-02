"""Wydajny i bezpieczny proxy prezentacyjnych kafelków WMS.

Publiczne usługi WMS nie publikują użytecznych nagłówków cache, a każdy
``GetMap`` ma zauważalny koszt. Moduł normalizuje żądania do ``z/x/y``,
przechowuje poprawne PNG na dysku, scala równoczesne cache miss i izoluje
limity współbieżności MPZP, POG oraz KIUT.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import weakref
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final, Literal, Mapping

import httpx

from app.core.settings import Settings, settings

logger = logging.getLogger(__name__)

WEB_MERCATOR_HALF_WORLD_M: Final[float] = 20_037_508.342789244
PNG_SIGNATURE: Final[bytes] = b"\x89PNG\r\n\x1a\n"
MAX_TILE_BYTES: Final[int] = 4 * 1024 * 1024
PRUNE_INTERVAL_SECONDS: Final[float] = 300.0
SOURCE_KEY_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]*$")
SUPPORTED_PREVIEW_SOURCE_KEYS: Final[frozenset[str]] = frozenset(
    {"mpzp", "pog", "kiut"}
)

TileCacheStatus = Literal["HIT", "MISS", "STALE"]


class InvalidTileCoordinatesError(ValueError):
    """Współrzędne kafelka są poza obsługiwanym zakresem."""


class WmsTileUnavailableError(RuntimeError):
    """Nie udało się uzyskać poprawnego kafelka ani wersji z cache."""


class WmsPreviewConfigurationError(RuntimeError):
    """Rejestr źródeł WMS ma nieprawidłowy lub niekompletny kontrakt."""


@dataclass(frozen=True)
class WmsPreviewSource:
    """Zaufana konfiguracja jednego publicznego źródła podglądowego WMS."""

    source_key: str
    base_url: str
    layers: str
    version: str
    min_zoom: int
    max_zoom: int
    tile_size: int
    fresh_ttl_s: int
    stale_ttl_s: int
    upstream_concurrency: int
    read_timeout_s: float
    source_id: str
    label: str
    attribution: str
    legal_note: str
    info_url: str
    catalog_status: str
    allowed_redirect_host_suffixes: tuple[str, ...] = ()
    max_redirects: int = 0

    def __post_init__(self) -> None:
        if not SOURCE_KEY_PATTERN.fullmatch(self.source_key):
            raise ValueError(f"Nieprawidłowy klucz źródła WMS: {self.source_key!r}.")
        if not self.base_url.startswith(("https://", "http://")):
            raise ValueError(f"Źródło {self.source_key} nie ma poprawnego URL WMS.")
        if not self.info_url.startswith(("https://", "http://")):
            raise ValueError(
                f"Źródło {self.source_key} nie ma poprawnego URL informacji."
            )
        if not self.layers.strip():
            raise ValueError(f"Źródło {self.source_key} nie definiuje warstw WMS.")
        if self.version != "1.1.1":
            raise ValueError(
                f"Źródło {self.source_key} musi używać wspieranej wersji WMS 1.1.1."
            )
        if self.min_zoom < 0 or self.max_zoom < self.min_zoom:
            raise ValueError(f"Źródło {self.source_key} ma nieprawidłowy zakres zoomu.")
        if self.tile_size not in {256, 512}:
            raise ValueError(
                f"Źródło {self.source_key} ma nieobsługiwany rozmiar kafla."
            )
        if self.fresh_ttl_s < 0 or self.stale_ttl_s < self.fresh_ttl_s:
            raise ValueError(f"Źródło {self.source_key} ma nieprawidłowe TTL cache.")
        if self.upstream_concurrency < 1 or self.read_timeout_s <= 0:
            raise ValueError(
                f"Źródło {self.source_key} ma nieprawidłowe limity upstreamu."
            )
        if self.max_redirects < 0 or self.max_redirects > 5:
            raise ValueError(
                f"Źródło {self.source_key} ma nieprawidłowy limit przekierowań: {self.max_redirects}."
            )
        if any(
            not suffix.startswith(".") or len(suffix) < 2
            for suffix in self.allowed_redirect_host_suffixes
        ):
            raise ValueError(
                f"Źródło {self.source_key} definiuje niepoprawne sufiksy hostów: "
                f"{self.allowed_redirect_host_suffixes}."
            )
        if not all(
            value.strip()
            for value in (
                self.source_id,
                self.label,
                self.attribution,
                self.legal_note,
                self.catalog_status,
            )
        ):
            raise ValueError(
                f"Źródło {self.source_key} ma niepełne metadane publiczne."
            )

    @classmethod
    def from_mapping(cls, source_key: str, raw: Mapping[str, Any]) -> WmsPreviewSource:
        try:
            data = dict(raw)
            if "allowed_redirect_host_suffixes" in data:
                data["allowed_redirect_host_suffixes"] = tuple(
                    data["allowed_redirect_host_suffixes"]
                )
            return cls(source_key=source_key, **data)
        except (TypeError, ValueError) as exc:
            raise WmsPreviewConfigurationError(
                f"Nieprawidłowa konfiguracja źródła WMS {source_key!r}."
            ) from exc


def load_wms_preview_sources(path: str | Path) -> dict[str, WmsPreviewSource]:
    """Wczytuje i waliduje zamkniętą allowlistę źródeł WMS z pliku JSON."""

    registry_path = Path(path)
    try:
        raw = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WmsPreviewConfigurationError(
            f"Nie udało się wczytać rejestru źródeł WMS: {registry_path}."
        ) from exc
    if not isinstance(raw, dict) or not raw:
        raise WmsPreviewConfigurationError("Rejestr źródeł WMS musi być obiektem JSON.")
    if set(raw) != SUPPORTED_PREVIEW_SOURCE_KEYS:
        raise WmsPreviewConfigurationError(
            "Rejestr WMS musi definiować dokładnie źródła: kiut, mpzp, pog."
        )

    sources: dict[str, WmsPreviewSource] = {}
    for source_key, source_raw in raw.items():
        if not isinstance(source_key, str) or not isinstance(source_raw, dict):
            raise WmsPreviewConfigurationError(
                "Każdy wpis rejestru WMS musi mieć tekstowy klucz i obiekt konfiguracji."
            )
        sources[source_key] = WmsPreviewSource.from_mapping(source_key, source_raw)
    return sources


@dataclass(frozen=True)
class TileResult:
    content: bytes
    etag: str
    cache_status: TileCacheStatus
    age_seconds: int


@dataclass(frozen=True)
class _CachedTile:
    content: bytes
    etag: str
    age_seconds: int
    fresh: bool


def web_mercator_tile_bbox(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """Zwraca BBOX EPSG:3857 kafelka XYZ w kolejności minx,miny,maxx,maxy."""

    if z < 0:
        raise InvalidTileCoordinatesError("Poziom zoom nie może być ujemny.")
    tile_count = 1 << z
    if x < 0 or y < 0 or x >= tile_count or y >= tile_count:
        raise InvalidTileCoordinatesError("Współrzędne x/y są poza zakresem zoomu.")

    span = (2 * WEB_MERCATOR_HALF_WORLD_M) / tile_count
    min_x = -WEB_MERCATOR_HALF_WORLD_M + x * span
    max_x = min_x + span
    max_y = WEB_MERCATOR_HALF_WORLD_M - y * span
    min_y = max_y - span
    return min_x, min_y, max_x, max_y


class WmsTileProxy:
    """Proxy jednego WMS z cache plikowym i własną ochroną upstreamu."""

    def __init__(
        self,
        source: WmsPreviewSource,
        config: Settings = settings,
    ) -> None:
        self.source = source
        self.config = config
        self.cache_dir = Path(config.map_tile_cache_dir)
        self._client: httpx.AsyncClient | None = None
        self._upstream_semaphore = asyncio.Semaphore(source.upstream_concurrency)
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._last_prune_at = 0.0
        self._style_version = hashlib.sha256(
            (
                f"{source.base_url}\n{source.layers}\n{source.tile_size}\n"
                f"{source.version}"
            ).encode("utf-8")
        ).hexdigest()[:16]

    async def get_tile(self, z: int, x: int, y: int) -> TileResult:
        self._validate_zoom(z)
        # Walidacja x/y odbywa się również przed utworzeniem ścieżki cache.
        web_mercator_tile_bbox(z, x, y)
        key = f"{z}/{x}/{y}"
        path = self._tile_path(z, x, y)
        cached = await self._read_cached_tile(path)

        if cached and cached.fresh:
            logger.info(
                "map_tile_cache_hit source=%s z=%s x=%s y=%s",
                self.source.source_key,
                z,
                x,
                y,
            )
            return self._as_result(cached, "HIT")
        if cached:
            logger.info(
                "map_tile_cache_stale source=%s z=%s x=%s y=%s",
                self.source.source_key,
                z,
                x,
                y,
            )
            self._schedule_refresh(key, path, z, x, y)
            return self._as_result(cached, "STALE")

        lock = self._lock_for(key)
        async with lock:
            # Inne żądanie mogło zapisać kafel, gdy czekaliśmy na lock.
            cached = await self._read_cached_tile(path)
            if cached and cached.fresh:
                return self._as_result(cached, "HIT")
            try:
                content = await self._fetch_upstream_tile(z, x, y)
            except WmsTileUnavailableError:
                if cached:
                    return self._as_result(cached, "STALE")
                raise

            await asyncio.to_thread(self._write_tile_atomic, path, content)
            stat = await asyncio.to_thread(path.stat)
            self._schedule_prune()
            logger.info(
                "map_tile_cache_miss source=%s z=%s x=%s y=%s",
                self.source.source_key,
                z,
                x,
                y,
            )
            return TileResult(
                content=content,
                etag=self._etag(path, stat.st_mtime_ns, stat.st_size),
                cache_status="MISS",
                age_seconds=0,
            )

    def response_headers(self, result: TileResult) -> dict[str, str]:
        browser_ttl = min(
            max(0, self.config.map_tile_browser_ttl_seconds),
            self.source.fresh_ttl_s,
        )
        stale_while_revalidate = max(0, self.source.fresh_ttl_s - browser_ttl)
        return {
            "Cache-Control": (
                f"public, max-age={browser_ttl}, "
                f"stale-while-revalidate={stale_while_revalidate}, "
                f"stale-if-error={self.source.stale_ttl_s}"
            ),
            "ETag": result.etag,
            "Age": str(max(0, result.age_seconds)),
            "X-Tile-Cache": result.cache_status,
        }

    async def wait_for_background_tasks(self) -> None:
        tasks = tuple(self._background_tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def aclose(self) -> None:
        tasks = tuple(self._background_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _validate_zoom(self, z: int) -> None:
        if z < self.source.min_zoom or z > self.source.max_zoom:
            raise InvalidTileCoordinatesError(
                f"Obsługiwany zakres zoomu {self.source.source_key.upper()} to "
                f"{self.source.min_zoom}–{self.source.max_zoom}."
            )

    def _tile_path(self, z: int, x: int, y: int) -> Path:
        return (
            self.cache_dir
            / self.source.source_key
            / self._style_version
            / str(z)
            / str(x)
            / f"{y}.png"
        )

    def _lock_for(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    async def _read_cached_tile(self, path: Path) -> _CachedTile | None:
        try:
            stat = await asyncio.to_thread(path.stat)
        except FileNotFoundError:
            return None
        except OSError as exc:
            logger.warning(
                "map_tile_cache_stat_failed error_type=%s", type(exc).__name__
            )
            return None

        age = max(0, int(time.time() - stat.st_mtime))
        if age > self.source.stale_ttl_s:
            await asyncio.to_thread(path.unlink, missing_ok=True)
            return None
        try:
            content = await asyncio.to_thread(path.read_bytes)
        except OSError as exc:
            logger.warning(
                "map_tile_cache_read_failed error_type=%s", type(exc).__name__
            )
            return None
        if not content.startswith(PNG_SIGNATURE):
            await asyncio.to_thread(path.unlink, missing_ok=True)
            return None
        return _CachedTile(
            content=content,
            etag=self._etag(path, stat.st_mtime_ns, stat.st_size),
            age_seconds=age,
            fresh=age <= self.source.fresh_ttl_s,
        )

    async def _fetch_upstream_tile(self, z: int, x: int, y: int) -> bytes:
        bbox = web_mercator_tile_bbox(z, x, y)
        params = {
            "service": "WMS",
            "version": self.source.version,
            "request": "GetMap",
            "layers": self.source.layers,
            "styles": "",
            "format": "image/png",
            "transparent": "true",
            "srs": "EPSG:3857",
            "width": str(self.source.tile_size),
            "height": str(self.source.tile_size),
            "bbox": ",".join(f"{coordinate:.8f}" for coordinate in bbox),
        }

        last_error: Exception | None = None
        for attempt in range(2):
            started = time.perf_counter()
            try:
                async with self._upstream_semaphore:
                    response = await self._get_with_allowed_redirects(
                        self.source.base_url,
                        params,
                    )
                content = response.content
                content_type = response.headers.get("content-type", "").lower()
                if "image/png" not in content_type or not content.startswith(
                    PNG_SIGNATURE
                ):
                    raise WmsTileUnavailableError(
                        "Usługa WMS nie zwróciła poprawnego obrazu PNG."
                    )
                if len(content) > MAX_TILE_BYTES:
                    raise WmsTileUnavailableError(
                        "Kafelek WMS przekracza limit rozmiaru."
                    )
                logger.info(
                    "map_tile_upstream_ok source=%s z=%s x=%s y=%s elapsed_ms=%s",
                    self.source.source_key,
                    z,
                    x,
                    y,
                    round((time.perf_counter() - started) * 1000),
                )
                return content
            except asyncio.CancelledError:
                logger.info(
                    "map_tile_upstream_cancelled source=%s z=%s x=%s y=%s",
                    self.source.source_key,
                    z,
                    x,
                    y,
                )
                raise
            except WmsTileUnavailableError:
                raise
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if exc.response.status_code < 500:
                    break
            except httpx.HTTPError as exc:
                last_error = exc
            if attempt == 0:
                await asyncio.sleep(0.1)

        logger.warning(
            "map_tile_upstream_failed source=%s z=%s x=%s y=%s error_type=%s",
            self.source.source_key,
            z,
            x,
            y,
            type(last_error).__name__ if last_error else "UnknownError",
        )
        raise WmsTileUnavailableError(
            f"Usługa kafelków {self.source.source_key.upper()} jest chwilowo niedostępna."
        ) from last_error

    async def _get_with_allowed_redirects(
        self, url: str, params: dict[str, str] | None
    ) -> httpx.Response:
        """Podąża wyłącznie za HTTPS-owymi przekierowaniami do hostów z allowlisty źródła.

        Front KIUT (integracja.gugik.gov.pl) rozdziela GetMap kodem 302 na węzły
        integracja01/02.gugik.gov.pl. Ogólne follow_redirects=True pozwoliłoby
        upstreamowi skierować proxy na dowolny adres — dlatego allowlista i limit.
        """
        client = self._get_client()
        current_url = url
        current_params = params
        for _ in range(self.source.max_redirects + 1):
            response = await client.get(
                current_url,
                params=current_params,
                follow_redirects=False,
            )
            if response.status_code not in (301, 302, 303, 307, 308):
                response.raise_for_status()
                return response
            location = response.headers.get("location")
            if not location:
                raise WmsTileUnavailableError("Przekierowanie WMS bez nagłówka Location.")
            target = httpx.URL(current_url).join(location)
            host = (target.host or "").lower()
            if target.scheme != "https" or not any(
                host.endswith(suffix)
                for suffix in self.source.allowed_redirect_host_suffixes
            ):
                logger.warning(
                    "map_tile_upstream_redirect_rejected source=%s host=%s target_url=%s",
                    self.source.source_key,
                    host or "?",
                    str(target),
                )
                raise WmsTileUnavailableError(
                    f"Odrzucono przekierowanie WMS na niedozwolony adres: {host or '?'}"
                )
            logger.info(
                "map_tile_upstream_redirected source=%s host=%s",
                self.source.source_key,
                host,
            )
            current_url, current_params = str(target), None
        raise WmsTileUnavailableError("Przekroczono limit przekierowań WMS.")

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            timeout = httpx.Timeout(
                connect=self.config.map_tile_upstream_connect_timeout_seconds,
                read=self.source.read_timeout_s,
                write=self.source.read_timeout_s,
                pool=self.config.map_tile_upstream_connect_timeout_seconds,
            )
            concurrency = self.source.upstream_concurrency
            self._client = httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
                limits=httpx.Limits(
                    max_connections=concurrency,
                    max_keepalive_connections=concurrency,
                ),
                headers={"User-Agent": "dzialki-map-tile-proxy/1.0"},
            )
        return self._client

    def _schedule_refresh(self, key: str, path: Path, z: int, x: int, y: int) -> None:
        task_key = f"refresh:{key}"
        if any(task.get_name() == task_key for task in self._background_tasks):
            return
        task = asyncio.create_task(
            self._refresh_stale_tile(key, path, z, x, y),
            name=task_key,
        )
        self._track_task(task)

    async def _refresh_stale_tile(
        self, key: str, path: Path, z: int, x: int, y: int
    ) -> None:
        lock = self._lock_for(key)
        async with lock:
            try:
                content = await self._fetch_upstream_tile(z, x, y)
                await asyncio.to_thread(self._write_tile_atomic, path, content)
                self._schedule_prune()
            except WmsTileUnavailableError:
                # Stary kafel pozostaje dostępny aż do stale TTL.
                return

    def _schedule_prune(self) -> None:
        now = time.monotonic()
        if now - self._last_prune_at < PRUNE_INTERVAL_SECONDS:
            return
        self._last_prune_at = now
        task = asyncio.create_task(self._prune_cache(), name="map-tile-cache-prune")
        self._track_task(task)

    def _track_task(self, task: asyncio.Task[None]) -> None:
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _prune_cache(self) -> None:
        await asyncio.to_thread(
            self._prune_cache_sync,
            self.cache_dir / self.source.source_key,
            max(0, self.config.map_tile_cache_max_bytes),
            self.source.stale_ttl_s,
        )

    @staticmethod
    def _write_tile_atomic(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
        try:
            temporary.write_bytes(content)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _prune_cache_sync(cache_dir: Path, max_bytes: int, stale_ttl: int) -> None:
        if not cache_dir.exists():
            return
        now = time.time()
        files: list[tuple[Path, os.stat_result]] = []
        total_bytes = 0
        for path in cache_dir.rglob("*.png"):
            try:
                stat = path.stat()
            except OSError:
                continue
            if now - stat.st_mtime > stale_ttl:
                path.unlink(missing_ok=True)
                continue
            files.append((path, stat))
            total_bytes += stat.st_size

        if max_bytes <= 0 or total_bytes <= max_bytes:
            return
        target_bytes = int(max_bytes * 0.9)
        for path, stat in sorted(files, key=lambda item: item[1].st_mtime):
            path.unlink(missing_ok=True)
            total_bytes -= stat.st_size
            if total_bytes <= target_bytes:
                break

    def _etag(self, path: Path, mtime_ns: int, size: int) -> str:
        identity = f"{self._style_version}:{path.as_posix()}:{mtime_ns}:{size}"
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        return f'"{digest}"'

    @staticmethod
    def _as_result(cached: _CachedTile, status: TileCacheStatus) -> TileResult:
        return TileResult(
            content=cached.content,
            etag=cached.etag,
            cache_status=status,
            age_seconds=cached.age_seconds,
        )


class WmsTilePreviewRegistry:
    """Lazy registry zapewniający osobny proxy, klient i semafor per źródło."""

    def __init__(
        self,
        config: Settings = settings,
        sources: Mapping[str, WmsPreviewSource] | None = None,
    ) -> None:
        self.config = config
        if sources is None:
            loaded_sources = load_wms_preview_sources(config.wms_preview_sources_path)
            # Zachowanie istniejących wdrożeń: dotychczasowe zmienne MAP_TILE_*
            # i KIMPZP_WMS_* nadal nadpisują parametry źródła MPZP. POG i KIUT
            # korzystają z odrębnych limitów zapisanych w rejestrze JSON.
            if mpzp := loaded_sources.get("mpzp"):
                loaded_sources["mpzp"] = replace(
                    mpzp,
                    base_url=config.kimpzp_wms_base_url,
                    layers=config.kimpzp_wms_layers,
                    min_zoom=config.map_tile_min_zoom,
                    max_zoom=config.map_tile_max_zoom,
                    fresh_ttl_s=config.map_tile_cache_ttl_seconds,
                    stale_ttl_s=config.map_tile_stale_ttl_seconds,
                    upstream_concurrency=max(
                        1, config.map_tile_upstream_max_concurrency
                    ),
                    read_timeout_s=config.map_tile_upstream_read_timeout_seconds,
                )
            self._sources = loaded_sources
        else:
            self._sources = dict(sources)
        self._proxies: dict[str, WmsTileProxy] = {}

    @property
    def sources(self) -> tuple[WmsPreviewSource, ...]:
        return tuple(self._sources.values())

    def get(self, source_key: str) -> WmsTileProxy:
        try:
            source = self._sources[source_key]
        except KeyError as exc:
            raise KeyError(f"Nieznane źródło podglądu WMS: {source_key}.") from exc
        proxy = self._proxies.get(source_key)
        if proxy is None:
            proxy = WmsTileProxy(source, self.config)
            self._proxies[source_key] = proxy
        return proxy

    async def aclose(self) -> None:
        proxies = tuple(self._proxies.values())
        if proxies:
            await asyncio.gather(
                *(proxy.aclose() for proxy in proxies),
                return_exceptions=True,
            )
        self._proxies.clear()


wms_tile_registry = WmsTilePreviewRegistry()
