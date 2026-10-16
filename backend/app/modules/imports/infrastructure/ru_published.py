"""Klient publicznego API Rejestru Urbanistycznego: wykaz aktów POG i pliki GML aktów (ADR-026).

Dwa endpointy (zweryfikowane 2026-10-13, ``docs/data_sources/ru_contracts.md``):

* ``POST /api/public/published/query`` — wykaz opublikowanych aktów (strony po ≤ 100 wierszy; ``publishedActVersions``
  ``CURRENT`` = bieżące wersje, ``ALL`` = także historyczne);
* ``GET /api/public/published/{publishedActId}/download`` — plik GML całego aktu (akt, strefy, OUZ/OZS/OSDIS, dokumenty).

RU odpowiada HTTP 429 (``Request blocked Exceeded requests/sec limit``) powyżej kilku żądań na sekundę, dlatego klient
rozstawia **wszystkie** żądania w czasie (``min_interval_seconds``) niezależnie od współbieżności przetwarzania i przy 429
odczekuje (``Retry-After`` albo rosnący backoff). Transport (HTTPS, allowlista hosta, DNS/IP, limity bajtów i czasu,
brak przekierowań) zapewnia wspólny ``OgcClient``.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Final

from app.modules.imports.domain.ru_catalog import (
    ListingPage,
    RuCatalogError,
    RuListing,
    assemble_listing,
    parse_content_disposition_filename,
    parse_listing_page,
)
from app.modules.imports.infrastructure.ogc_client import (
    HttpResult,
    OgcClient,
    OgcContractError,
    OgcTransportError,
)

PAGE_SIZE: Final[int] = 100
MAX_PAGES: Final[int] = 200
RATE_LIMIT_BACKOFF_SECONDS: Final[tuple[float, ...]] = (5.0, 15.0, 45.0, 120.0)


class RuRateLimitedError(OgcTransportError):
    """RU nie przestało odpowiadać 429 po kolejnych odczekaniach (przyczyna: ``rate_limited``)."""


_UTF8_BOM = b"\xef\xbb\xbf"


class RuDownloadError(RuntimeError):
    """Plik GML aktu nie został pobrany; ``code`` to stały kod przyczyny (do raportu i kolejki zleceń)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class RuDownload:
    published_act_id: int
    content: bytes
    filename: str | None
    url: str
    fetched_at: datetime
    sha256: str


class Throttle:
    """Wspólny rozstawiacz żądań: kolejne żądanie startuje najwcześniej ``interval`` po poprzednim (wątkowo-bezpieczny)."""

    def __init__(
        self, interval: float, *, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep
    ) -> None:
        self._interval = max(interval, 0.0)
        self._clock = clock
        self._sleep = sleep
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = self._clock()
            delay = max(self._next - now, 0.0)
            self._next = max(now, self._next) + self._interval
        if delay > 0:
            self._sleep(delay)


class RuPublishedClient:
    """Wykaz aktów i pobieranie GML; ``base_url`` bez końcowego ukośnika (``…/api/public/published``)."""

    def __init__(
        self,
        client: OgcClient,
        *,
        base_url: str,
        min_interval_seconds: float = 1.2,
        max_gml_bytes: int = 160 * 1024 * 1024,
        timeout_seconds: float = 180.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        rate_limit_backoff: tuple[float, ...] = RATE_LIMIT_BACKOFF_SECONDS,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._throttle = Throttle(min_interval_seconds, clock=clock, sleep=sleep)
        self._max_gml_bytes = max_gml_bytes
        self._timeout = timeout_seconds
        self._sleep = sleep
        self._backoff = rate_limit_backoff
        self._requests = 0
        self._rate_limited = 0
        self._lock = threading.Lock()

    def close(self) -> None:
        self._client.close()

    @property
    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"requests": self._requests, "rate_limited": self._rate_limited}

    def _call(self, **kwargs: Any) -> HttpResult:
        """Żądanie przez przepustnicę z ponowieniem po 429; po wyczerpaniu odczekań zgłasza ``RuRateLimitedError``."""
        for attempt in range(len(self._backoff) + 1):
            self._throttle.wait()
            with self._lock:
                self._requests += 1
            result = self._client.fetch_resource(readable_error_statuses=frozenset({429}), **kwargs)
            if result.status_code != 429:
                return result
            with self._lock:
                self._rate_limited += 1
            if attempt >= len(self._backoff):
                break
            retry_after = result.headers.get("retry-after", "")
            delay = float(retry_after) if retry_after.replace(".", "", 1).isdigit() else self._backoff[attempt]
            self._sleep(min(max(delay, self._backoff[attempt]), 300.0))
        raise RuRateLimitedError(
            "Rejestr Urbanistyczny odpowiada HTTP 429 mimo odczekań.", source=result.source
        )

    # --- wykaz ---------------------------------------------------------------------------------------------

    def list_acts(self, versions: str = "CURRENT") -> RuListing:
        """Pobiera cały wykaz aktów POG (``CURRENT`` albo ``ALL``); niekompletny wykaz jest oznaczony, nie ukrywany."""
        if versions not in {"CURRENT", "ALL"}:
            raise ValueError("Wersje wykazu: CURRENT albo ALL.")
        pages: list[ListingPage] = []
        number = 1
        while number <= MAX_PAGES:
            body = {
                "page": {"number": number, "size": PAGE_SIZE, "sort": {"sortBy": "publishDate", "direction": "desc"}},
                "criteria": {
                    "query": None,
                    "parcelIdentifier": None,
                    "statusList": [],
                    "publishedActVersions": versions,
                    "tercCodeList": [],
                    "publishDateFrom": None,
                    "publishDateTo": None,
                    "planValidFrom": None,
                    "planValidTo": None,
                    "planTypeList": ["MUNICIPAL_GENERAL_PLAN"],
                    "plansGroup": "MUNICIPAL_GENERAL_PLAN",
                },
            }
            result = self._call(
                url=f"{self._base_url}/query",
                method="POST",
                json_body=body,
                operation="RU:published/query",
                byte_limit=8 * 1024 * 1024,
                total_timeout_seconds=self._timeout,
            )
            try:
                page = parse_listing_page(json.loads(result.content))
            except (json.JSONDecodeError, RuCatalogError, UnicodeDecodeError) as exc:
                raise OgcContractError(f"Wykaz RU ma niepoprawny kształt: {exc}", source=result.source) from exc
            pages.append(page)
            if number >= page.total_pages or not page.acts:
                break
            number += 1
        return assemble_listing(pages)

    # --- GML -----------------------------------------------------------------------------------------------

    def download_gml(self, published_act_id: int) -> RuDownload:
        if published_act_id <= 0:
            raise ValueError("publishedActId musi być dodatni.")
        url = f"{self._base_url}/{published_act_id}/download"
        try:
            result = self._call(
                url=url,
                operation="RU:published/download",
                byte_limit=self._max_gml_bytes,
                total_timeout_seconds=self._timeout,
            )
        except RuRateLimitedError as exc:
            raise RuDownloadError("rate_limited", str(exc)) from exc
        except OgcTransportError as exc:
            raise RuDownloadError("transport", str(exc)) from exc
        except OgcContractError as exc:
            raise RuDownloadError("contract", str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - np. OgcLimitError: plik większy niż limit
            code = "too_large" if type(exc).__name__ == "OgcLimitError" else "download_failed"
            raise RuDownloadError(code, f"{type(exc).__name__}: {exc}") from exc
        # Część plików RU zaczyna się od BOM UTF-8 (np. Jejkowice, 2026-10-13) — to nadal dokument XML.
        if not result.content.removeprefix(_UTF8_BOM).lstrip().startswith(b"<"):
            raise RuDownloadError("not_xml", "Odpowiedź RU nie jest dokumentem XML/GML.")
        return RuDownload(
            published_act_id=published_act_id,
            content=result.content,
            filename=parse_content_disposition_filename(result.headers.get("content-disposition")),
            url=url,
            fetched_at=result.source.fetched_at or datetime.now(timezone.utc),
            sha256=result.source.content_hash or "",
        )
