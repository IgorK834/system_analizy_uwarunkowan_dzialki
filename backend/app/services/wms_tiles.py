"""Wydajny i bezpieczny proxy rastrowych kafelków KIMPZP.

Publiczny WMS Geoportalu nie publikuje użytecznych nagłówków cache, a każdy
``GetMap`` ma zauważalny koszt. Ten moduł normalizuje żądania do ``z/x/y``,
przechowuje poprawne PNG na dysku, scala równoczesne cache miss i może podać
stary kafel podczas przejściowej awarii upstreamu.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

import httpx

from app.core.settings import Settings, settings

logger = logging.getLogger(__name__)

WEB_MERCATOR_HALF_WORLD_M: Final[float] = 20_037_508.342789244
WMS_TILE_SIZE: Final[int] = 256
PNG_SIGNATURE: Final[bytes] = b"\x89PNG\r\n\x1a\n"
MAX_TILE_BYTES: Final[int] = 4 * 1024 * 1024
PRUNE_INTERVAL_SECONDS: Final[float] = 300.0

TileCacheStatus = Literal["HIT", "MISS", "STALE"]


class InvalidTileCoordinatesError(ValueError):
    """Współrzędne kafelka są poza obsługiwanym zakresem."""


class WmsTileUnavailableError(RuntimeError):
    """Nie udało się uzyskać poprawnego kafelka ani wersji z cache."""


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
    """Proxy KIMPZP z cache plikowym i ochroną upstreamu przed lawiną żądań."""

    def __init__(self, config: Settings = settings) -> None:
        self.config = config
        self.cache_dir = Path(config.map_tile_cache_dir)
        self._client: httpx.AsyncClient | None = None
        self._upstream_semaphore = asyncio.Semaphore(
            max(1, config.map_tile_upstream_max_concurrency)
        )
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._last_prune_at = 0.0
        self._style_version = hashlib.sha256(
            (
                f"{config.kimpzp_wms_base_url}\n{config.kimpzp_wms_layers}\n"
                f"{WMS_TILE_SIZE}"
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
            logger.info("map_tile_cache_hit source=mpzp z=%s x=%s y=%s", z, x, y)
            return self._as_result(cached, "HIT")
        if cached:
            logger.info("map_tile_cache_stale source=mpzp z=%s x=%s y=%s", z, x, y)
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
            logger.info("map_tile_cache_miss source=mpzp z=%s x=%s y=%s", z, x, y)
            return TileResult(
                content=content,
                etag=self._etag(path, stat.st_mtime_ns, stat.st_size),
                cache_status="MISS",
                age_seconds=0,
            )

    def response_headers(self, result: TileResult) -> dict[str, str]:
        browser_ttl = max(0, self.config.map_tile_browser_ttl_seconds)
        stale_while_revalidate = max(
            0, self.config.map_tile_cache_ttl_seconds - browser_ttl
        )
        return {
            "Cache-Control": (
                f"public, max-age={browser_ttl}, "
                f"stale-while-revalidate={stale_while_revalidate}, "
                f"stale-if-error={max(0, self.config.map_tile_stale_ttl_seconds)}"
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
        if z < self.config.map_tile_min_zoom or z > self.config.map_tile_max_zoom:
            raise InvalidTileCoordinatesError(
                "Obsługiwany zakres zoomu MPZP to "
                f"{self.config.map_tile_min_zoom}–{self.config.map_tile_max_zoom}."
            )

    def _tile_path(self, z: int, x: int, y: int) -> Path:
        return self.cache_dir / "mpzp" / self._style_version / str(z) / str(x) / f"{y}.png"

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
            logger.warning("map_tile_cache_stat_failed error_type=%s", type(exc).__name__)
            return None

        age = max(0, int(time.time() - stat.st_mtime))
        if age > self.config.map_tile_stale_ttl_seconds:
            await asyncio.to_thread(path.unlink, missing_ok=True)
            return None
        try:
            content = await asyncio.to_thread(path.read_bytes)
        except OSError as exc:
            logger.warning("map_tile_cache_read_failed error_type=%s", type(exc).__name__)
            return None
        if not content.startswith(PNG_SIGNATURE):
            await asyncio.to_thread(path.unlink, missing_ok=True)
            return None
        return _CachedTile(
            content=content,
            etag=self._etag(path, stat.st_mtime_ns, stat.st_size),
            age_seconds=age,
            fresh=age <= self.config.map_tile_cache_ttl_seconds,
        )

    async def _fetch_upstream_tile(self, z: int, x: int, y: int) -> bytes:
        bbox = web_mercator_tile_bbox(z, x, y)
        params = {
            "service": "WMS",
            "version": "1.1.1",
            "request": "GetMap",
            "layers": self.config.kimpzp_wms_layers,
            "styles": "",
            "format": "image/png",
            "transparent": "true",
            "srs": "EPSG:3857",
            "width": str(WMS_TILE_SIZE),
            "height": str(WMS_TILE_SIZE),
            "bbox": ",".join(f"{coordinate:.8f}" for coordinate in bbox),
        }

        last_error: Exception | None = None
        for attempt in range(2):
            started = time.perf_counter()
            try:
                async with self._upstream_semaphore:
                    response = await self._get_client().get(
                        self.config.kimpzp_wms_base_url,
                        params=params,
                    )
                response.raise_for_status()
                content = response.content
                content_type = response.headers.get("content-type", "").lower()
                if "image/png" not in content_type or not content.startswith(PNG_SIGNATURE):
                    raise WmsTileUnavailableError(
                        "Usługa WMS nie zwróciła poprawnego obrazu PNG."
                    )
                if len(content) > MAX_TILE_BYTES:
                    raise WmsTileUnavailableError("Kafelek WMS przekracza limit rozmiaru.")
                logger.info(
                    "map_tile_upstream_ok source=mpzp z=%s x=%s y=%s elapsed_ms=%s",
                    z,
                    x,
                    y,
                    round((time.perf_counter() - started) * 1000),
                )
                return content
            except asyncio.CancelledError:
                logger.info(
                    "map_tile_upstream_cancelled source=mpzp z=%s x=%s y=%s",
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
            "map_tile_upstream_failed source=mpzp z=%s x=%s y=%s error_type=%s",
            z,
            x,
            y,
            type(last_error).__name__ if last_error else "UnknownError",
        )
        raise WmsTileUnavailableError("Usługa kafelków MPZP jest chwilowo niedostępna.") from last_error

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            timeout = httpx.Timeout(
                connect=self.config.map_tile_upstream_connect_timeout_seconds,
                read=self.config.map_tile_upstream_read_timeout_seconds,
                write=self.config.map_tile_upstream_read_timeout_seconds,
                pool=self.config.map_tile_upstream_connect_timeout_seconds,
            )
            concurrency = max(1, self.config.map_tile_upstream_max_concurrency)
            self._client = httpx.AsyncClient(
                timeout=timeout,
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
            self.cache_dir,
            max(0, self.config.map_tile_cache_max_bytes),
            max(0, self.config.map_tile_stale_ttl_seconds),
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


wms_tile_proxy = WmsTileProxy()
