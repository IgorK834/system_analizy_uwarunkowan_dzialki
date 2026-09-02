"""Sprawdzenie pokrycia powiatu w publicznej warstwie WMS KIUT ``gesut``.

Usługa rozdziela informację o dostępności podglądu od analizy wektorowej
GESUT. Wynik ``not_covered`` powstaje wyłącznie po poprawnej odpowiedzi WMS,
która jednoznacznie nie zawiera cechy. Timeout, błąd HTTP lub nierozpoznawalna
odpowiedź zawsze dają ``unknown`` — nigdy fałszywe ``not_covered``.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final, Literal
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup
from pyproj import Transformer
from shapely.geometry.base import BaseGeometry

from app.schemas.analyze import UtilitiesPreviewResult
from app.schemas.source import SourceMetadata
from app.services.wms_tiles import WmsPreviewSource, wms_tile_registry

CoverageStatus = Literal["covered", "not_covered", "unknown"]

KIUT_COVERAGE_CACHE_TTL_S: Final[float] = 24 * 60 * 60
KIUT_COVERAGE_UNKNOWN_CACHE_TTL_S: Final[float] = 5 * 60
_TO_PUWG = Transformer.from_crs("EPSG:4326", "EPSG:2180", always_xy=True)
_NO_FEATURE_PHRASES: Final[tuple[str, ...]] = (
    "brak wyników",
    "brak obiektów",
    "nie znaleziono obiektów",
    "no features",
    "no results",
    "search returned no results",
)
_COUNTY_FIELD_MARKERS: Final[tuple[str, ...]] = (
    "nazwa powiatu",
    "nazwa_powiatu",
    "powiat",
    "jpt_nazwa",
    "nazwa",
)


@dataclass(frozen=True)
class _ParsedCoverage:
    status: Literal["covered", "not_covered"]
    county_name: str | None = None


@dataclass(frozen=True)
class _CacheEntry:
    expires_at: float
    result: UtilitiesPreviewResult


_coverage_cache: dict[str, _CacheEntry] = {}
_coverage_cache_lock = asyncio.Lock()


async def check_kiut_coverage_wgs84(
    lon: float,
    lat: float,
    *,
    county_teryt: str | None = None,
    client: httpx.AsyncClient | None = None,
    source: WmsPreviewSource | None = None,
) -> UtilitiesPreviewResult:
    """Sprawdza pokrycie KIUT dla punktu WGS84."""

    x, y = _TO_PUWG.transform(lon, lat)
    return await check_kiut_coverage_projected(
        x,
        y,
        county_teryt=county_teryt,
        client=client,
        source=source,
    )


async def check_kiut_coverage_for_geometry(
    parcel_geometry: BaseGeometry,
    *,
    county_teryt: str | None = None,
    client: httpx.AsyncClient | None = None,
    source: WmsPreviewSource | None = None,
) -> UtilitiesPreviewResult:
    """Sprawdza reprezentatywny punkt działki zapisanej w EPSG:2180."""

    point = parcel_geometry.representative_point()
    return await check_kiut_coverage_projected(
        point.x,
        point.y,
        county_teryt=county_teryt,
        client=client,
        source=source,
    )


async def check_kiut_coverage_projected(
    x: float,
    y: float,
    *,
    county_teryt: str | None = None,
    client: httpx.AsyncClient | None = None,
    source: WmsPreviewSource | None = None,
) -> UtilitiesPreviewResult:
    """Odpytuje GetFeatureInfo warstwy ``gesut`` w EPSG:2180.

    Cache jest współdzielony dla sześciocyfrowych kodów TERYT należących do
    tego samego powiatu. Endpoint publiczny, który nie zna TERYT, nie zapisuje
    wyniku do cache powiatowego.
    """

    cache_key = _county_cache_key(county_teryt)
    cached = await _get_cached(cache_key)
    if cached is not None:
        return cached

    preview_source = source or wms_tile_registry.get("kiut").source
    if client is not None:
        result = await _fetch_coverage(client, preview_source, x, y)
    else:
        timeout = httpx.Timeout(
            connect=2.0,
            read=preview_source.read_timeout_s,
            write=5.0,
            pool=5.0,
        )
        async with httpx.AsyncClient(timeout=timeout) as owned_client:
            result = await _fetch_coverage(owned_client, preview_source, x, y)

    await _store_cached(cache_key, result)
    return result


async def clear_kiut_coverage_cache() -> None:
    """Czyści cache; publiczne wyłącznie dla deterministycznych testów."""

    async with _coverage_cache_lock:
        _coverage_cache.clear()


def unknown_kiut_coverage_result(
    source: WmsPreviewSource | None = None,
) -> UtilitiesPreviewResult:
    """Buduje bezpieczny fallback dla granic orkiestracji."""

    preview_source = source or wms_tile_registry.get("kiut").source
    return _result(
        "unknown",
        preview_source,
        datetime.now(timezone.utc),
        None,
    )


async def _fetch_coverage(
    client: httpx.AsyncClient,
    source: WmsPreviewSource,
    x: float,
    y: float,
) -> UtilitiesPreviewResult:
    fetched_at = datetime.now(timezone.utc)
    last_status: int | None = None

    for info_format in ("text/xml", "text/html"):
        try:
            response = await client.get(
                source.base_url,
                params=_get_feature_info_params(x, y, info_format),
                timeout=source.read_timeout_s,
            )
            last_status = response.status_code
        except httpx.RequestError:
            return _result("unknown", source, fetched_at, None)

        if response.status_code >= 500:
            return _result("unknown", source, fetched_at, response.status_code)
        if response.status_code >= 400:
            # KIUT deklaruje text/xml i text/html; część węzłów na HTML
            # oddaje XML, a część odrzuca jeden z formatów kodem 4xx.
            if info_format == "text/xml":
                continue
            return _result("unknown", source, fetched_at, response.status_code)

        text = response.text.strip()
        if (
            text.startswith("<?xml")
            or "<msgmloutput" in text.casefold()
            or info_format == "text/xml"
        ):
            parsed = _parse_xml(response.text) or _parse_html(response.text)
        else:
            parsed = _parse_html(response.text) or _parse_xml(response.text)

        if parsed is not None:
            return _result(
                parsed.status,
                source,
                fetched_at,
                response.status_code,
                county_name=parsed.county_name,
            )

    return _result("unknown", source, fetched_at, last_status)


def _get_feature_info_params(
    x: float,
    y: float,
    info_format: str,
) -> dict[str, str]:
    half = 0.5
    return {
        "service": "WMS",
        "version": "1.1.1",
        "request": "GetFeatureInfo",
        "layers": "gesut",
        "query_layers": "gesut",
        "styles": "",
        "srs": "EPSG:2180",
        "bbox": f"{x - half},{y - half},{x + half},{y + half}",
        "width": "2",
        "height": "2",
        "x": "1",
        "y": "1",
        "feature_count": "1",
        "info_format": info_format,
        "format": "image/png",
    }


def _parse_html(text: str) -> _ParsedCoverage | None:
    stripped = text.strip()
    if not stripped:
        return _ParsedCoverage("not_covered")

    soup = BeautifulSoup(stripped, "html.parser")
    visible_text = " ".join(soup.stripped_strings)
    normalized_text = _normalize(visible_text)
    if any(phrase in normalized_text for phrase in _NO_FEATURE_PHRASES):
        return _ParsedCoverage("not_covered")
    if soup.find(string=re.compile(r"ServiceException", re.IGNORECASE)):
        return None

    records: list[dict[str, str]] = []
    saw_data_cell = False
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        headers = [cell.get_text(" ", strip=True) for cell in rows[0].find_all("th")]
        if headers:
            for row in rows[1:]:
                values = [cell.get_text(" ", strip=True) for cell in row.find_all("td")]
                if values:
                    saw_data_cell = True
                    records.append(dict(zip(headers, values, strict=False)))
        for row in rows:
            key_cell = row.find("th")
            value_cell = row.find("td")
            if key_cell is not None and value_cell is not None:
                value = value_cell.get_text(" ", strip=True)
                if value:
                    saw_data_cell = True
                    records.append({key_cell.get_text(" ", strip=True): value})

    if saw_data_cell:
        return _ParsedCoverage("covered", _county_from_records(records))
    if soup.find("table") is not None:
        return _ParsedCoverage("not_covered")
    # Nietabelaryczny HTML nie stanowi wystarczającego dowodu żadnego stanu.
    return None


def _parse_xml(text: str) -> _ParsedCoverage | None:
    stripped = text.strip()
    if not stripped:
        return _ParsedCoverage("not_covered")
    try:
        root = ElementTree.fromstring(stripped)
    except ElementTree.ParseError:
        return None

    root_name = _local_name(root.tag).casefold()
    if "exception" in root_name or any(
        "exception" in _local_name(element.tag).casefold() for element in root.iter()
    ):
        return None

    feature_nodes = [
        element
        for element in root.iter()
        if (
            _local_name(element.tag).casefold() in {"featuremember", "member", "fields"}
            or _local_name(element.tag).casefold().endswith("_feature")
        )
    ]
    if feature_nodes:
        return _ParsedCoverage("covered", _county_from_xml(root))

    if root_name in {"featurecollection", "msgmloutput", "featureinforesponse"}:
        return _ParsedCoverage("not_covered")
    return None


def _county_from_records(records: list[dict[str, str]]) -> str | None:
    for marker in _COUNTY_FIELD_MARKERS:
        for record in records:
            for key, value in record.items():
                if marker in _normalize(key):
                    return _clean_county_name(value)
    return None


def _county_from_xml(root: ElementTree.Element) -> str | None:
    for marker in _COUNTY_FIELD_MARKERS:
        for element in root.iter():
            local_name = _normalize(_local_name(element.tag))
            if marker in local_name and element.text:
                if county := _clean_county_name(element.text):
                    return county
            for key, value in element.attrib.items():
                if marker in _normalize(_local_name(key)):
                    if county := _clean_county_name(value):
                        return county
    return None


def _clean_county_name(value: str) -> str | None:
    cleaned = " ".join(value.split()).strip(" -–—")
    if not cleaned or cleaned.casefold() in {"null", "none", "brak"}:
        return None
    return cleaned[:200]


def _result(
    status: CoverageStatus,
    source: WmsPreviewSource,
    fetched_at: datetime,
    response_status: int | None,
    *,
    county_name: str | None = None,
) -> UtilitiesPreviewResult:
    notes = {
        "covered": (
            "Powiat publikuje dane GESUT w KIUT. Brak obiektów na podglądzie "
            "może nadal oznaczać brak sieci albo brak danych cyfrowych dla tego "
            "fragmentu. Podgląd nie służy do obliczania odległości."
        ),
        "not_covered": (
            "KIUT nie potwierdził publikacji danych GESUT przez ten powiat. "
            "Pusty podgląd nie jest dowodem braku sieci; informację należy "
            "zweryfikować u gestora lub w powiecie."
        ),
        "unknown": (
            "Nie udało się sprawdzić, czy powiat publikuje dane GESUT w KIUT. "
            "Pusty podgląd nie oznacza braku sieci. Podgląd nie służy do "
            "obliczania odległości."
        ),
    }
    return UtilitiesPreviewResult(
        coverage_status=status,
        county_name=county_name,
        layer_available=status == "covered",
        note=notes[status],
        source=SourceMetadata(
            source_name="KIUT (GUGiK)",
            source_url=source.base_url,
            fetched_at=fetched_at,
            response_status=response_status,
            confidence=0.9 if status != "unknown" else 0.0,
            manual_review_required=status == "unknown",
        ),
    )


def _county_cache_key(teryt: str | None) -> str | None:
    if not teryt:
        return None
    digits = "".join(character for character in teryt if character.isdigit())
    return digits[:4] if len(digits) >= 4 else None


async def _get_cached(cache_key: str | None) -> UtilitiesPreviewResult | None:
    if cache_key is None:
        return None
    async with _coverage_cache_lock:
        entry = _coverage_cache.get(cache_key)
        if entry is None:
            return None
        if entry.expires_at <= time.monotonic():
            _coverage_cache.pop(cache_key, None)
            return None
        return entry.result.model_copy(deep=True)


async def _store_cached(
    cache_key: str | None,
    result: UtilitiesPreviewResult,
) -> None:
    if cache_key is None:
        return
    ttl = (
        KIUT_COVERAGE_UNKNOWN_CACHE_TTL_S
        if result.coverage_status == "unknown"
        else KIUT_COVERAGE_CACHE_TTL_S
    )
    async with _coverage_cache_lock:
        _coverage_cache[cache_key] = _CacheEntry(
            expires_at=time.monotonic() + ttl,
            result=result.model_copy(deep=True),
        )


def _normalize(value: str) -> str:
    return " ".join(value.casefold().replace("_", " ").split())


def _local_name(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag
