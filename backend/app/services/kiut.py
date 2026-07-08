from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final
from xml.etree import ElementTree

import httpx
from shapely.geometry import LineString, MultiLineString
from shapely.geometry.base import BaseGeometry

from app.core.settings import settings
from app.schemas.analyze import SourceMetadata

logger = logging.getLogger(__name__)

KIUT_TIMEOUT_S: Final[float] = 10.0

# Różne powiaty/warstwy WFS nazywają atrybut klasyfikujący sieć inaczej —
# lista w kolejności prawdopodobieństwa wystąpienia w rzeczywistych danych.
_KNOWN_NETWORK_TYPE_ATTRIBUTES: Final[tuple[str, ...]] = (
    "rodzaj",
    "typ",
    "typ_sieci",
    "kind",
    "category",
    "network_type",
)

# Mapowanie słów kluczowych (małe litery) na znormalizowany network_type.
# Dopasowanie przez podłańcuch ('in'), bo wartości bywają pełnymi opisami,
# np. "sieć wodociągowa rozdzielcza", a nie krótkimi kodami.
_NETWORK_TYPE_KEYWORDS: Final[dict[str, str]] = {
    "wod": "water",
    "kanal": "sewage",
    "gaz": "gas",
    "elektr": "power",
    "energet": "power",
    "telekom": "telecoms",
    "teletechn": "telecoms",
    "ciepl": "heating",
    "cieplown": "heating",
}


@dataclass(frozen=True)
class NetworkFeature:
    """
    Pojedyncza sieć uzbrojenia terenu pobrana z KIUT/GESUT.

    Typ sieci może być nieznany, gdy atrybut klasyfikujący nie został
    rozpoznany w danych źródłowych — to nie jest błąd krytyczny, ale wymaga
    ostrzeżenia (sekcja 8 context.md: nie udawaj pewności).
    """

    network_type: str
    geometry: BaseGeometry
    source_metadata: SourceMetadata
    warning: str | None


def bbox_from_geometry(geometry: BaseGeometry) -> tuple[float, float, float, float]:
    """
    Wyznacza BBOX (minx, miny, maxx, maxy) w EPSG:2180 z geometrii działki,
    do użycia w zapytaniu WFS GetFeature.

    Geometria wejściowa musi być już w EPSG:2180 — funkcja nie wykonuje
    żadnej transformacji.
    """
    return geometry.bounds


async def fetch_kiut_networks(
    parcel_bounds: tuple[float, float, float, float],
) -> list[NetworkFeature]:
    """
    Pobiera sieci uzbrojenia terenu z WFS KIUT/GESUT w obrębie BBOX działki.

    BBOX jest przekazywany w EPSG:2180, zgodnie z układem obliczeń metrycznych
    używanym w całym backendzie. Funkcja toleruje brak danych — pusta
    odpowiedź usługi zwraca pustą listę, nie wyjątek. Parser obsługuje
    zarówno GML jak i GeoJSON w zależności od formatu odpowiedzi usługi.
    Różne powiaty mogą zwracać różne nazwy atrybutów klasyfikujących typ
    sieci — nieznany atrybut nie jest błędem, tylko oznaczany jako 'unknown'.
    """
    minx, miny, maxx, maxy = parcel_bounds
    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "bbox": f"{minx},{miny},{maxx},{maxy},EPSG:2180",
    }

    try:
        async with httpx.AsyncClient(timeout=KIUT_TIMEOUT_S) as client:
            response = await client.get(settings.kiut_wfs_base_url, params=params)
            response.raise_for_status()
    except httpx.TimeoutException:
        # Graceful degradation (sekcja 9/10 context.md) — awaria usługi KIUT
        # nie może wywracać całej analizy /analyze.
        logger.warning("Usługa KIUT nie odpowiedziała w wymaganym czasie.")
        return []
    except httpx.HTTPError as exc:
        logger.warning("Usługa KIUT zwróciła błąd: %s", exc)
        return []

    fetched_at = datetime.now(timezone.utc)
    return _parse_kiut_response(response.text, str(response.url), fetched_at)


def _parse_kiut_response(
    text: str, source_url: str, fetched_at: datetime
) -> list[NetworkFeature]:
    stripped = text.strip()
    if not stripped:
        return []
    if stripped.startswith("{"):
        return _parse_geojson_networks(stripped, source_url, fetched_at)
    return _parse_gml_networks(stripped, source_url, fetched_at)


def _parse_geojson_networks(
    text: str, source_url: str, fetched_at: datetime
) -> list[NetworkFeature]:
    data = json.loads(text)
    features = data.get("features", [])
    if not features:
        return []

    results: list[NetworkFeature] = []
    for feature in features:
        properties = feature.get("properties", {}) or {}
        network_type, warning = _classify_network_type(properties)

        geometry_dict = feature.get("geometry")
        if not geometry_dict:
            logger.warning("Cecha KIUT bez geometrii — pominięto.")
            continue

        shapely_geometry = _geojson_geometry_to_shapely(geometry_dict)
        if shapely_geometry is None:
            continue

        results.append(
            NetworkFeature(
                network_type=network_type,
                geometry=shapely_geometry,
                source_metadata=SourceMetadata(
                    source_name="KIUT",
                    source_url=source_url,
                    fetched_at=fetched_at,
                    confidence=0.8 if network_type != "unknown" else 0.4,
                    manual_review_required=(network_type == "unknown"),
                ),
                warning=warning,
            )
        )
    return results


def _geojson_geometry_to_shapely(geom_dict: dict) -> BaseGeometry | None:
    geom_type = geom_dict.get("type")
    coords = geom_dict.get("coordinates")
    if geom_type == "LineString" and coords:
        return LineString(coords)
    if geom_type == "MultiLineString" and coords:
        return MultiLineString(coords)
    logger.warning("Nieobsługiwany typ geometrii KIUT: %s", geom_type)
    return None


def _parse_gml_networks(
    text: str, source_url: str, fetched_at: datetime
) -> list[NetworkFeature]:
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        logger.warning("Nie udało się sparsować GML z KIUT: %s", exc)
        return []

    results: list[NetworkFeature] = []
    for member in root.iter():
        if _local_name(member.tag) not in ("member", "featureMember"):
            continue
        for feature_elem in list(member):
            network_type, warning = _classify_network_type_from_xml(feature_elem)
            geometry = _extract_gml_geometry(feature_elem)
            if geometry is None:
                continue
            results.append(
                NetworkFeature(
                    network_type=network_type,
                    geometry=geometry,
                    source_metadata=SourceMetadata(
                        source_name="KIUT",
                        source_url=source_url,
                        fetched_at=fetched_at,
                        confidence=0.8 if network_type != "unknown" else 0.4,
                        manual_review_required=(network_type == "unknown"),
                    ),
                    warning=warning,
                )
            )
    return results


def _local_name(tag: str) -> str:
    """
    Zwraca lokalną nazwę tagu XML bez prefiksu przestrzeni nazw.

    Różne serwery WFS mogą używać różnych prefiksów dla tej samej struktury
    GML, więc dopasowanie po lokalnej nazwie jest odporniejsze niż porównanie
    pełnego tagu z przestrzenią nazw.
    """
    return tag.split("}")[-1] if "}" in tag else tag


def _extract_gml_geometry(feature_elem: ElementTree.Element) -> BaseGeometry | None:
    for elem in feature_elem.iter():
        local = _local_name(elem.tag)
        if local == "LineString":
            coords = _parse_pos_list(elem)
            return LineString(coords) if coords else None
        if local == "MultiLineString":
            lines = []
            for line_elem in elem.iter():
                if _local_name(line_elem.tag) == "LineString":
                    coords = _parse_pos_list(line_elem)
                    if coords:
                        lines.append(coords)
            return MultiLineString(lines) if lines else None
    return None


def _parse_pos_list(
    line_string_elem: ElementTree.Element,
) -> list[tuple[float, float]] | None:
    # gml:posList to płaska lista współrzędnych "x1 y1 x2 y2 ..." — para (x, y)
    # w EPSG:2180 zgodnie z atrybutem srsName elementu LineString.
    for child in line_string_elem.iter():
        if _local_name(child.tag) == "posList" and child.text:
            values = [float(v) for v in child.text.split()]
            pairs = list(zip(values[0::2], values[1::2]))
            return pairs if len(pairs) >= 2 else None
    return None


def _classify_network_type(properties: dict) -> tuple[str, str | None]:
    for key in _KNOWN_NETWORK_TYPE_ATTRIBUTES:
        for prop_key, value in properties.items():
            if prop_key.lower() == key and isinstance(value, str):
                normalized = _normalize_network_type_value(value)
                if normalized != "unknown":
                    return normalized, None
    return (
        "unknown",
        "Nie rozpoznano typu sieci na podstawie dostępnych atrybutów — "
        "oznaczono jako unknown.",
    )


def _classify_network_type_from_xml(
    feature_elem: ElementTree.Element,
) -> tuple[str, str | None]:
    for elem in feature_elem.iter():
        local = _local_name(elem.tag).lower()
        if local in _KNOWN_NETWORK_TYPE_ATTRIBUTES and elem.text:
            normalized = _normalize_network_type_value(elem.text)
            if normalized != "unknown":
                return normalized, None
    return (
        "unknown",
        "Nie rozpoznano typu sieci na podstawie dostępnych atrybutów GML — "
        "oznaczono jako unknown.",
    )


def _normalize_network_type_value(raw_value: str) -> str:
    lowered = raw_value.lower()
    for keyword, network_type in _NETWORK_TYPE_KEYWORDS.items():
        if keyword in lowered:
            return network_type
    return "unknown"
