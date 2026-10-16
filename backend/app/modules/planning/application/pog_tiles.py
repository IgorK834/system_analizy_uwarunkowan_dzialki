"""Przypadek użycia: kafle MVT POG przypięte do stanu krajowego (BK-401, ADR-026)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol

from app.modules.planning.domain.pog_tiles import (
    PogStateInfo,
    PogTile,
    PogTileRequest,
    tile_etag,
)


class PogStateNotFoundError(LookupError):
    """Stan krajowy POG nie istnieje (HTTP 404)."""


class PogTileTooLargeError(RuntimeError):
    """Kafel przekracza limit liczby cech albo bajtów (HTTP 413)."""

    def __init__(self, message: str, *, limit: str) -> None:
        super().__init__(message)
        self.limit = limit


@dataclass(frozen=True)
class EncodedTile:
    """Wynik kodowania w PostGIS: bajty protobuf i liczności warstw (do kontroli limitu)."""

    content: bytes
    counts: dict[str, int]

    @property
    def feature_count(self) -> int:
        return sum(self.counts.values())


class PogTileRepository(Protocol):
    def state_info(self, state_id: int) -> PogStateInfo | None:
        """Metadane stanu krajowego albo ``None``, gdy stan nie istnieje."""

    def current_state_id(self) -> int | None:
        """Najnowszy numer stanu krajowego (``None`` — rejestr jest pusty)."""

    def encode_tile(self, request: PogTileRequest, *, max_features: int) -> EncodedTile:
        """Koduje warstwy kafla jednym zapytaniem ``ST_AsMVT`` z tabel ``pog_map_*`` (pusty kafel = ``b""``).

        Każda warstwa jest ograniczona do ``max_features + 1`` obiektów, żeby przekroczenie limitu było wykrywalne.
        """


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

    def current_state(self) -> PogStateInfo:
        state_id = self._repository.current_state_id()
        if state_id is None:
            raise PogStateNotFoundError("Brak lokalnych danych POG: rejestr stanu krajowego jest pusty.")
        return self.state(state_id)

    def state(self, state_id: int) -> PogStateInfo:
        info = self._repository.state_info(state_id)
        if info is None:
            raise PogStateNotFoundError(f"Stan krajowy POG {state_id} nie istnieje.")
        return info

    def get_tile(self, request: PogTileRequest) -> PogTile:
        """Zwraca kafel z cache albo koduje go z PostGIS.

        Kafel jest deterministyczną funkcją numeru stanu, edycji i adresu, więc cache nie wymaga unieważniania —
        każda zmiana rejestru tworzy nowy numer stanu (wariant „unieważnij całość”: klucz zawiera numer stanu).
        """
        request.validate(min_zoom=self._limits.min_zoom, max_zoom=self._limits.max_zoom)
        key = request.cache_key
        cached = self._cache.get(key)
        if cached is not None:
            return replace(cached, cache_status="HIT")

        if self._repository.state_info(request.state_id) is None:
            raise PogStateNotFoundError(f"Stan krajowy POG {request.state_id} nie istnieje.")
        encoded = self._repository.encode_tile(request, max_features=self._limits.max_features)
        if any(count > self._limits.max_features for count in encoded.counts.values()):
            raise PogTileTooLargeError(
                "Kafel zawiera więcej obiektów POG niż dozwolony limit "
                f"({self._limits.max_features} na warstwę); przybliż mapę.",
                limit="features",
            )
        if len(encoded.content) > self._limits.max_bytes:
            raise PogTileTooLargeError(
                f"Kafel ma {len(encoded.content)} B i przekracza limit "
                f"{self._limits.max_bytes} B; przybliż mapę.",
                limit="bytes",
            )
        tile = PogTile(
            content=encoded.content,
            etag=tile_etag(key, encoded.content),
            feature_count=encoded.feature_count,
            cache_status="MISS",
            state_id=request.state_id,
            edition=request.edition,
        )
        self._cache.put(key, tile)
        return tile
