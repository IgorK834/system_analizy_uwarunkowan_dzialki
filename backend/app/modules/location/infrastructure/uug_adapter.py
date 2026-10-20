"""Produkcyjny adapter wyszukiwania adresów przez usługę UUG/EMUiA (GUGiK).

Adapter implementuje port ``AddressSearchProvider``. Respektuje guard katalogu
źródeł (źródło ``emuia_uug`` musi być production_ready), stosuje timeout, retry
dla idempotentnego GET oraz ograniczony cache TTL. Kontrakt dostawcy (parametry,
surowa odpowiedź) nie jest ujawniany publicznie — błędy są mapowane na
``AddressSearchProviderError``.

Usługa zwraca współrzędne w EPSG:2180; adapter przelicza je do WGS84 przed
zwróceniem, ponieważ warstwa domenowa i frontend operują w WGS84.
"""

from __future__ import annotations

import asyncio
import logging
import math
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

import httpx
from pyproj import Transformer

from app.core.data_sources import (
    CatalogError,
    SourceNotRunnableError,
    ensure_source_runnable,
)
from app.modules.location.application.ports import (
    AddressSearchProvider,
    AddressSearchProviderError,
)
from app.modules.location.domain.models import (
    AddressParts,
    AddressQuery,
    GeoPoint,
    RawAddressCandidate,
    ResultType,
)
from app.modules.location.infrastructure.cache import BoundedTTLCache
from app.modules.location.domain.normalization import fold_text, uug_query_variants

logger = logging.getLogger(__name__)

_SOURCE_ID: Final[str] = "emuia_uug"
_UUG_BASE_URL: Final[str] = "https://services.gugik.gov.pl/uug/"
_TIMEOUT_S: Final[float] = 8.0
_MAX_RETRIES: Final[int] = 2
_BACKOFF_S: Final[float] = 0.5
_MAX_RESULTS: Final[int] = 20


@lru_cache(maxsize=1)
def _localities() -> dict[str, str]:
    path = Path(__file__).resolve().parents[3] / "data" / "address_localities.json"
    return {fold_text(name): name for name in json.loads(path.read_text(encoding="utf-8"))["names"]}

# Transformer EPSG:2180 -> WGS84 (lon, lat). always_xy gwarantuje kolejność x,y.
_TO_WGS84: Final[Transformer] = Transformer.from_crs(
    "EPSG:2180", "EPSG:4326", always_xy=True
)


class UugAddressSearchProvider(AddressSearchProvider):
    """Adapter UUG/EMUiA zwracający surowych kandydatów adresowych."""

    def __init__(
        self,
        base_url: str = _UUG_BASE_URL,
        cache: BoundedTTLCache[list[RawAddressCandidate]] | None = None,
    ) -> None:
        self._base_url = base_url
        self._cache: BoundedTTLCache[list[RawAddressCandidate]] = (
            cache
            if cache is not None
            else BoundedTTLCache(max_entries=512, ttl_seconds=300.0)
        )

    async def search(self, query: AddressQuery) -> list[RawAddressCandidate]:
        self._ensure_source_allowed()

        cache_key = self._cache_key(query)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        params = {
            "request": "GetAddress",
            # UUG domyślnie wymaga bardzo dokładnego dopasowania i dokładnego
            # numeru budynku. Dla sugestii podczas pisania korzystamy z
            # oficjalnego dolnego progu podobieństwa i dopuszczamy wynik ulicy,
            # zanim użytkownik poda pełny numer.
            "accuracy": "0.5",
            "exact_number": "0",
        }
        candidates: list[RawAddressCandidate] = []
        try:
            variants = uug_query_variants(query.q, _localities())
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise AddressSearchProviderError("Słownik nazw miejscowości jest niedostępny.") from exc
        for variant in variants:
            response = await self._request_with_retry(params | {"address": variant})
            try:
                candidates = self._parse(response.json())
            except (ValueError, TypeError) as exc:
                raise AddressSearchProviderError("Nieoczekiwany format odpowiedzi UUG.") from exc
            if candidates:
                break
        self._cache.set(cache_key, candidates)
        return candidates

    def _ensure_source_allowed(self) -> None:
        # Adapter produkcyjny może użyć wyłącznie źródła production_ready
        # potwierdzonego w katalogu (jedyne źródło prawdy). Błąd katalogu lub
        # niedozwolone źródło jest błędem dostawcy (fail-closed).
        try:
            ensure_source_runnable(_SOURCE_ID)
        except SourceNotRunnableError as exc:
            raise AddressSearchProviderError(
                f"Źródło {_SOURCE_ID} nie jest dopuszczone do uruchomienia."
            ) from exc
        except CatalogError as exc:
            raise AddressSearchProviderError(
                "Katalog źródeł danych jest niedostępny."
            ) from exc

    @staticmethod
    def _cache_key(query: AddressQuery) -> str:
        bias = f"{query.bias.lon:.4f},{query.bias.lat:.4f}" if query.bias else "-"
        bbox = (
            f"{query.bbox.min_lon:.4f},{query.bbox.min_lat:.4f},"
            f"{query.bbox.max_lon:.4f},{query.bbox.max_lat:.4f}"
            if query.bbox
            else "-"
        )
        types = ",".join(sorted(result_type.value for result_type in query.type_filter))
        return f"{query.q.strip().casefold()}|{query.limit}|{bias}|{bbox}|{types}"

    async def _request_with_retry(self, params: dict[str, str]) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
                    response = await client.get(self._base_url, params=params)
                    response.raise_for_status()
                    return response
            except httpx.TimeoutException as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(_BACKOFF_S * (attempt + 1))
            except httpx.HTTPStatusError as exc:
                # Retry tylko dla przejściowych błędów serwera (idempotentny GET).
                if exc.response.status_code >= 500 and attempt < _MAX_RETRIES:
                    last_exc = exc
                    await asyncio.sleep(_BACKOFF_S * (attempt + 1))
                    continue
                logger.warning("UUG zwróciło błąd HTTP %s", exc.response.status_code)
                raise AddressSearchProviderError(
                    "Usługa wyszukiwania adresów zwróciła błąd."
                ) from exc
            except httpx.RequestError as exc:
                last_exc = exc
                break

        raise AddressSearchProviderError(
            "Usługa wyszukiwania adresów jest niedostępna."
        ) from last_exc

    def _parse(self, data: Any) -> list[RawAddressCandidate]:
        if not isinstance(data, dict):
            raise AddressSearchProviderError("Nieoczekiwany format odpowiedzi.")
        if "results" not in data or (data["results"] is not None and not isinstance(data["results"], dict)):
            raise AddressSearchProviderError("Nieoczekiwany format wyników UUG.")
        results = data["results"]
        # Odpowiedzi UUG dla dopasowań częściowych (np. type=street) potrafią
        # zawierać "found objects" i poprawny słownik "results", ale pomijać
        # pole "returned objects". To wyniki, nie licznik, są źródłem prawdy.
        if not isinstance(results, dict) or not results:
            return []

        candidates: list[RawAddressCandidate] = []
        for result in list(results.values())[:_MAX_RESULTS]:
            candidate = self._build_candidate(result)
            if candidate is not None:
                candidates.append(candidate)
        if not candidates:
            raise AddressSearchProviderError("UUG zwróciło wyniki bez poprawnych współrzędnych.")
        return candidates

    def _build_candidate(self, result: Any) -> RawAddressCandidate | None:
        if not isinstance(result, dict):
            return None
        try:
            x = float(result["x"])
            y = float(result["y"])
        except (KeyError, TypeError, ValueError):
            return None

        lon, lat = _TO_WGS84.transform(x, y)
        if not all(math.isfinite(value) for value in (lon, lat)) or not (-180 <= lon <= 180 and -90 <= lat <= 90):
            return None
        city = _clean(result.get("city"))
        street = _clean(result.get("street"))
        number = _clean(result.get("number"))
        parts = AddressParts(
            country="Polska",
            city=city,
            street=street,
            house_number=number,
        )
        try:
            confidence = max(0.0, min(1.0, float(result.get("accuracy", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0

        return RawAddressCandidate(
            label=_build_label(city, street, number, result.get("teryt")),
            point=GeoPoint(lon=lon, lat=lat),
            parts=parts,
            result_type=_derive_result_type(city, street, number),
            provider_confidence=confidence,
            source_identifier=None,
        )


def _clean(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _derive_result_type(
    city: str | None, street: str | None, number: str | None
) -> ResultType:
    if number:
        return ResultType.HOUSE_NUMBER
    if street:
        return ResultType.STREET
    if city:
        return ResultType.CITY
    return ResultType.MUNICIPALITY


def _build_label(
    city: str | None, street: str | None, number: str | None, teryt: Any
) -> str:
    if city and street and number:
        return f"{city}, {street} {number}"
    if city and street:
        return f"{city}, {street}"
    if city:
        return city
    return _clean(teryt) or "nieznana lokalizacja"
