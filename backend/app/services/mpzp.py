"""Wstępne rozpoznanie MPZP przez wielopunktowe WMS GetFeatureInfo.

``discover_mpzp`` działa best-effort i nie podnosi błędów zapytań na poziomie
całej funkcji: awaria jednego lub wszystkich punktów próbki trafia do ostrzeżeń.
Centroid nigdy nie jest jedynym planowanym punktem — zawsze uwzględniamy także
``representative_point``, a dla dużych i wieloczęściowych działek dalsze próbki.

Parser obsługuje faktyczną odpowiedź HTML zbiorczej warstwy
``plany_granice`` oraz zachowuje zgodność z odpowiedzią GeoJSON używaną przez
część usług gminnych i testów kontraktowych. Wynik pozostaje discovery, nie
finalnym przecięciem geometrii wektorowej MPZP.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Final, Literal

import httpx
from bs4 import BeautifulSoup
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from app.core.settings import settings
from app.schemas.analyze import SourceMetadata

logger = logging.getLogger(__name__)

KIMPZP_TIMEOUT_S: Final[float] = 10.0

# Dla dużej działki pojedyncze punkty są mniej reprezentatywne, dlatego próg
# uruchamia dodatkowe próbki ćwiartek. To heurystyka produktowa, nie prawna.
_LARGE_PARCEL_AREA_THRESHOLD_SQM: Final[float] = 2000.0

_KNOWN_SYMBOL_ATTRIBUTES: Final[tuple[str, ...]] = (
    "symbol",
    "zone_symbol",
    "symbol_strefy",
    "oznaczenie",
    "rodzaj oznaczenia",
)
_KNOWN_PLAN_ID_ATTRIBUTES: Final[tuple[str, ...]] = (
    "plan_id",
    "id_planu",
    "numer_uchwaly",
    "uchwalenie",
    "uchwała",
    "numer planu",
    "identyfikator",
)
_KNOWN_URL_ATTRIBUTES: Final[tuple[str, ...]] = (
    "uchwala_url",
    "url_uchwaly",
    "link",
    "www",
    "treść uchwały",
    "rysunek planu",
    "usługa przeglądania",
)


@dataclass(frozen=True)
class _PointQueryResult:
    found: bool
    plan_id: str | None
    zone_symbol: str | None
    uchwala_url: str | None
    vector_available: bool


@dataclass(frozen=True)
class MpzpDiscoveryResult:
    """Wstępne rozpoznanie MPZP na próbce punktów działki.

    ``is_discovery_only`` jest zawsze True, analogicznie do
    ``is_technical_approximation`` w ``TechnicalSetbackResult``. Dalsze warstwy
    nie mogą prezentować wyniku jako finalnego przecięcia geometrii wektorowej;
    takie przypisanie stref wykona Task 4.5.
    """

    plan_id: str | None
    candidate_zone_symbols: list[str]
    uchwala_url: str | None
    brak_wektorow: bool
    status: Literal["found", "no_mpzp", "raster_only"]
    is_discovery_only: bool
    source_metadata: SourceMetadata
    warnings: list[str] = field(default_factory=list)


async def discover_mpzp(parcel_geometry: BaseGeometry) -> MpzpDiscoveryResult:
    """Rozpoznaje wstępnie MPZP dla geometrii działki w EPSG:2180.

    Wynik jest discovery na próbce punktów, a NIE finalnym przecięciem danych
    wektorowych — finalne przypisanie stref wykona Task 4.5. Centroid nigdy nie
    jest jedynym planowanym punktem: zawsze sprawdzamy też ``representative_point``
    gwarantowany wewnątrz poligonu, a duże i wieloczęściowe działki dostają
    dodatkowe próbki.

    Funkcja nie podnosi błędów zapytań na poziomie całości. Awaria pojedynczego
    lub wszystkich punktów jest tolerowana i opisana w ``warnings``.
    """
    sample_points = _build_sample_points(parcel_geometry)
    fetched_at = datetime.now(timezone.utc)

    async with httpx.AsyncClient(timeout=KIMPZP_TIMEOUT_S) as client:
        point_results = await asyncio.gather(
            *(_query_point_safe(client, x, y) for x, y in sample_points)
        )

    (
        candidate_symbols,
        plan_id,
        uchwala_url,
        brak_wektorow,
        saw_any_feature,
        warnings,
    ) = _aggregate_point_results(point_results)

    if brak_wektorow:
        status: Literal["found", "no_mpzp", "raster_only"] = "raster_only"
    elif saw_any_feature:
        status = "found"
    else:
        status = "no_mpzp"

    if status == "no_mpzp":
        warnings.append(
            "Nie znaleziono miejscowego planu zagospodarowania przestrzennego "
            "dla żadnego z próbkowanych punktów działki."
        )
    if status == "raster_only":
        warnings.append(
            "Gmina nie udostępnia wektorowych danych MPZP przez KIMPZP dla "
            "próbkowanych punktów — wymagana ręczna weryfikacja treści planu."
        )
    warnings.append(
        "To jest wstępne rozpoznanie (discovery) na próbce punktów, nie finalne "
        "przecięcie geometrii wektorowej działki ze strefami MPZP."
    )

    return MpzpDiscoveryResult(
        plan_id=plan_id,
        candidate_zone_symbols=candidate_symbols,
        uchwala_url=uchwala_url,
        brak_wektorow=brak_wektorow,
        status=status,
        is_discovery_only=True,
        source_metadata=SourceMetadata(
            source_name="KIMPZP",
            source_url=settings.kimpzp_wms_base_url,
            fetched_at=fetched_at,
            confidence=0.6 if status == "found" else 0.3,
            manual_review_required=True,
        ),
        warnings=warnings,
    )


def _build_sample_points(parcel: BaseGeometry) -> list[tuple[float, float]]:
    """Buduje wielopunktową próbkę do zapytań GetFeatureInfo.

    Centroid oraz ``representative_point`` są dodawane niezależnie, a następnie
    deduplikowane, gdy geometrycznie wypadają w tym samym miejscu. MultiPolygon
    dodaje punkt każdej części, a działka od 2000 m² także próbki ćwiartek BBOX.
    """
    points: list[tuple[float, float]] = []
    centroid = parcel.centroid
    points.append((round(centroid.x, 3), round(centroid.y, 3)))

    representative_point = parcel.representative_point()
    points.append(
        (round(representative_point.x, 3), round(representative_point.y, 3))
    )

    if parcel.geom_type == "MultiPolygon":
        for part in parcel.geoms:
            part_point = part.representative_point()
            points.append((round(part_point.x, 3), round(part_point.y, 3)))

    if parcel.area >= _LARGE_PARCEL_AREA_THRESHOLD_SQM:
        points.extend(_grid_sample_points(parcel))

    return list(dict.fromkeys(points))


def _grid_sample_points(parcel: BaseGeometry) -> list[tuple[float, float]]:
    minx, miny, maxx, maxy = parcel.bounds
    midx, midy = (minx + maxx) / 2, (miny + maxy) / 2
    quadrants = [
        box(minx, miny, midx, midy),
        box(midx, miny, maxx, midy),
        box(minx, midy, midx, maxy),
        box(midx, midy, maxx, maxy),
    ]

    points: list[tuple[float, float]] = []
    for quadrant in quadrants:
        clipped = parcel.intersection(quadrant)
        if not clipped.is_empty and clipped.area > 0:
            representative_point = clipped.representative_point()
            points.append(
                (
                    round(representative_point.x, 3),
                    round(representative_point.y, 3),
                )
            )
    return points


def _build_get_feature_info_params(x: float, y: float) -> dict[str, str]:
    """Buduje GetFeatureInfo dla centralnego piksela BBOX 1x1 m wokół punktu.

    WMS 1.1.1 zachowuje kolejność x/y dla EPSG:2180. Obraz 2x2 i środkowy
    piksel pozwalają odpytać dokładnie próbkę bez ryzyka odwrócenia osi przez
    reguły WMS 1.3.0.
    """
    half = 0.5
    return {
        "service": "WMS",
        "version": "1.1.1",
        "request": "GetFeatureInfo",
        "layers": "plany_granice",
        "query_layers": "plany_granice",
        "srs": "EPSG:2180",
        "bbox": f"{x - half},{y - half},{x + half},{y + half}",
        "width": "2",
        "height": "2",
        "x": "1",
        "y": "1",
        "info_format": "text/html",
        "feature_count": "5",
    }


async def _query_point_safe(
    client: httpx.AsyncClient, x: float, y: float
) -> _PointQueryResult | Exception:
    """Odpytuje jeden punkt, zwracając błąd jako wartość do agregacji."""
    try:
        params = _build_get_feature_info_params(x, y)
        response = await client.get(settings.kimpzp_wms_base_url, params=params)
        response.raise_for_status()
        return _parse_get_feature_info_response(response.text)
    except httpx.HTTPError as exc:
        return exc
    except (json.JSONDecodeError, ValueError) as exc:
        return exc


def _parse_get_feature_info_response(text: str) -> _PointQueryResult:
    stripped = text.strip()
    if stripped.startswith("<"):
        return _parse_html_get_feature_info_response(stripped)

    data = json.loads(stripped) if stripped else {}
    features = data.get("features", [])
    vector_available = bool(data.get("vector_available", True))

    if not features:
        return _PointQueryResult(
            found=False,
            plan_id=None,
            zone_symbol=None,
            uchwala_url=None,
            vector_available=vector_available,
        )

    properties = features[0].get("properties", {}) or {}
    return _PointQueryResult(
        found=True,
        plan_id=_first_matching_attribute(properties, _KNOWN_PLAN_ID_ATTRIBUTES),
        zone_symbol=_first_matching_attribute(properties, _KNOWN_SYMBOL_ATTRIBUTES),
        uchwala_url=_first_matching_attribute(properties, _KNOWN_URL_ATTRIBUTES),
        vector_available=True,
    )


def _parse_html_get_feature_info_response(text: str) -> _PointQueryResult:
    """Normalizuje tabele HTML zwracane przez zbiorczą usługę KIMPZP."""
    soup = BeautifulSoup(text, "html.parser")
    records: list[dict[str, str]] = []
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue

        header_cells = rows[0].find_all("th")
        if len(header_cells) > 1:
            headers = [cell.get_text(" ", strip=True) for cell in header_cells]
            for row in rows[1:]:
                values = [cell.get_text(" ", strip=True) for cell in row.find_all("td")]
                if values:
                    records.append(dict(zip(headers, values, strict=False)))
            continue

        record: dict[str, str] = {}
        for row in rows:
            key_cell = row.find("th")
            value_cell = row.find("td")
            if key_cell is not None and value_cell is not None:
                record[key_cell.get_text(" ", strip=True)] = value_cell.get_text(
                    " ", strip=True
                )
        if record:
            records.append(record)

    if not records:
        return _PointQueryResult(
            found=False,
            plan_id=None,
            zone_symbol=None,
            uchwala_url=None,
            vector_available=True,
        )

    selected = next(
        (
            record
            for record in records
            if _first_matching_attribute(record, _KNOWN_SYMBOL_ATTRIBUTES)
        ),
        records[0],
    )
    zone_symbol = _first_matching_attribute(selected, _KNOWN_SYMBOL_ATTRIBUTES)
    informatization = next(
        (
            value.casefold()
            for key, value in selected.items()
            if key.casefold() == "poziom informatyzacji"
        ),
        "",
    )
    vector_available = zone_symbol is not None or "wektor" in informatization
    if "raster" in informatization:
        vector_available = False

    return _PointQueryResult(
        found=True,
        plan_id=_first_matching_attribute(selected, _KNOWN_PLAN_ID_ATTRIBUTES),
        zone_symbol=zone_symbol,
        uchwala_url=_first_matching_attribute(selected, _KNOWN_URL_ATTRIBUTES),
        vector_available=vector_available,
    )


def _first_matching_attribute(
    properties: dict, candidate_keys: tuple[str, ...]
) -> str | None:
    """Wybiera pierwszy niepusty atrybut spośród znanych wariantów gminnych."""
    for key in candidate_keys:
        for property_key, value in properties.items():
            if (
                property_key.lower() == key
                and isinstance(value, str)
                and value.strip()
            ):
                return value.strip()
    return None


def _aggregate_point_results(
    point_results: list[_PointQueryResult | Exception],
) -> tuple[list[str], str | None, str | None, bool, bool, list[str]]:
    """Agreguje wiele punktów bez uprzywilejowania wyniku centroidu."""
    candidate_symbols: list[str] = []
    plan_id: str | None = None
    uchwala_url: str | None = None
    saw_vector_available_false = False
    saw_any_feature = False
    warnings: list[str] = []

    for result in point_results:
        if isinstance(result, Exception):
            warnings.append(
                "Zapytanie GetFeatureInfo do KIMPZP nie powiodło się dla jednego "
                f"z punktów próbki: {result}"
            )
            continue

        if not result.vector_available:
            saw_vector_available_false = True
        if not result.found:
            continue

        saw_any_feature = True
        if result.zone_symbol and result.zone_symbol not in candidate_symbols:
            candidate_symbols.append(result.zone_symbol)
        if plan_id is None:
            plan_id = result.plan_id
        elif result.plan_id and result.plan_id != plan_id:
            warnings.append(
                "Punkty próbki wskazują na różne plany miejscowe "
                f"({plan_id!r} i {result.plan_id!r}) — działka może przecinać "
                "więcej niż jeden plan MPZP."
            )
        if uchwala_url is None:
            uchwala_url = result.uchwala_url

    brak_wektorow = saw_vector_available_false and not saw_any_feature
    return (
        candidate_symbols,
        plan_id,
        uchwala_url,
        brak_wektorow,
        saw_any_feature,
        warnings,
    )
