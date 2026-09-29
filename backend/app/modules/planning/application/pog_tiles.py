"""Przypadek użycia: kafle MVT POG przypięte do wydania danych (BK-401)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Protocol

from app.modules.planning.domain.pog_tiles import (
    PogReleaseInfo,
    PogTile,
    PogTileCandidate,
    PogTileRequest,
    tile_etag,
    tile_feature_properties,
)


class PogReleaseNotFoundError(LookupError):
    """Wydanie nie istnieje albo nie zawiera aktów POG (HTTP 404)."""


class PogTileTooLargeError(RuntimeError):
    """Kafel przekracza limit liczby cech albo bajtów (HTTP 413)."""

    def __init__(self, message: str, *, limit: str) -> None:
        super().__init__(message)
        self.limit = limit


class PogTileRepository(Protocol):
    def release_info(self, release_id: int) -> PogReleaseInfo | None:
        """Metadane wydania albo ``None``, gdy nie jest wydaniem POG."""

    def active_release_id(self, source_id: str) -> int | None:
        """Identyfikator aktywnego wydania źródła POG."""

    def tile_candidates(
        self, request: PogTileRequest, *, limit: int
    ) -> list[PogTileCandidate]:
        """Obiekty czterech warstw przecinające kafel (selekcja GiST w 2180)."""

    def encode_tile(
        self,
        request: PogTileRequest,
        features: Sequence[dict[str, object]],
        *,
        boundary_limit: int,
    ) -> bytes:
        """Koduje warstwy kafla przez ``ST_AsMVT`` (pusty kafel = ``b""``)."""


class PogTileCache(Protocol):
    def get(self, key: str) -> PogTile | None: ...

    def put(self, key: str, tile: PogTile) -> None: ...


@dataclass(frozen=True)
class PogTileLimits:
    min_zoom: int
    max_zoom: int
    max_features: int
    max_bytes: int


class PogTileService:
    def __init__(
        self,
        repository: PogTileRepository,
        cache: PogTileCache,
        limits: PogTileLimits,
        *,
        source_id: str,
    ) -> None:
        self._repository = repository
        self._cache = cache
        self._limits = limits
        self._source_id = source_id

    @property
    def limits(self) -> PogTileLimits:
        return self._limits

    def active_release(self) -> PogReleaseInfo:
        release_id = self._repository.active_release_id(self._source_id)
        if release_id is None:
            raise PogReleaseNotFoundError("Brak aktywnego lokalnego wydania POG.")
        return self.release(release_id)

    def release(self, release_id: int) -> PogReleaseInfo:
        info = self._repository.release_info(release_id)
        if info is None:
            raise PogReleaseNotFoundError(
                f"Wydanie {release_id} nie istnieje albo nie zawiera aktów POG."
            )
        return info

    def get_tile(self, request: PogTileRequest) -> PogTile:
        """Zwraca kafel z cache albo koduje go z PostGIS.

        Kafel jest deterministyczną funkcją wydania, edycji i adresu, więc
        cache nie wymaga unieważniania — nowe wydanie ma inny ``release_id``.
        """
        request.validate(min_zoom=self._limits.min_zoom, max_zoom=self._limits.max_zoom)
        key = request.cache_key
        cached = self._cache.get(key)
        if cached is not None:
            return replace(cached, cache_status="HIT")

        self.release(request.release_id)
        candidates = self._repository.tile_candidates(
            request, limit=self._limits.max_features + 1
        )
        if len(candidates) > self._limits.max_features:
            raise PogTileTooLargeError(
                "Kafel zawiera więcej obiektów POG niż dozwolony limit "
                f"({self._limits.max_features}); przybliż mapę.",
                limit="features",
            )
        features = [
            tile_feature_properties(candidate, release_id=request.release_id)
            for candidate in candidates
        ]
        content = self._repository.encode_tile(
            request, features, boundary_limit=self._limits.max_features
        )
        if len(content) > self._limits.max_bytes:
            raise PogTileTooLargeError(
                f"Kafel ma {len(content)} B i przekracza limit "
                f"{self._limits.max_bytes} B; przybliż mapę.",
                limit="bytes",
            )
        tile = PogTile(
            content=content,
            etag=tile_etag(key, content),
            feature_count=len(features),
            cache_status="MISS",
            release_id=request.release_id,
            edition=request.edition,
        )
        self._cache.put(key, tile)
        return tile
