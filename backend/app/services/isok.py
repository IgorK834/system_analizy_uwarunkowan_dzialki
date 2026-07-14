"""Serwis ISOK — wykrywanie ryzyka powodziowego przez rzeczywiste przecięcie geometrii.

Ryzyko powodziowe jest twardym ograniczeniem inwestycyjnym, nie kosmetyczną
informacją poboczną — dlatego ten moduł traktujemy jako bezpieczeństwo-krytyczny,
w odróżnieniu np. od kiut.py.

W kiut.py fetch_kiut_networks celowo połyka timeout/błąd HTTP i zwraca [] —
to bezpieczne, bo brak danych o sieciach uzbrojenia to tylko utrata informacji
pomocniczej. Dla ryzyka powodziowego jest to NIEBEZPIECZNE: pusta lista
RiskFeature przy awarii usługi ISOK wygląda identycznie jak "sprawdzono, brak
zagrożenia", co jest fałszywym poczuciem bezpieczeństwa dla użytkownika
podejmującego decyzję inwestycyjną. Dlatego fetch_flood_risks musi odróżnić
"sprawdzono, brak stref w BBOX" (zwróć []) od "nie udało się sprawdzić"
(podnieś IsokServiceUnavailableError). Wywołujący kod (przyszły orchestrator)
złapie ten wyjątek i ustawi status sekcji na 'unavailable' zamiast HTTP 500 —
ale ta integracja nie jest częścią tego modułu.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Final
from xml.etree import ElementTree

import httpx
from shapely.geometry import MultiPolygon, Polygon, shape as shapely_shape
from shapely.geometry.base import BaseGeometry

from app.core.settings import settings
from app.schemas.analyze import SourceMetadata
from app.services.kiut import _local_name, bbox_from_geometry

logger = logging.getLogger(__name__)

ISOK_TIMEOUT_S: Final[float] = 10.0

# Domyślna, ostrożnościowa klasyfikacja severity gdy atrybut prawdopodobieństwa
# jest nierozpoznany albo brak go w danych źródłowych. Dla ryzyka bezpieczeństwa
# krytycznego lepiej ostrzec nadmiarowo niż zaniżyć ryzyko przy niepewnych danych.
# To założenie produktowe, nie twardy wymóg — zmiana decyzji jest jednolinijkowa.
UNKNOWN_PROBABILITY_SEVERITY: Final[str] = "medium"

# Różne warstwy WFS ISOK mogą nazywać atrybut klasy prawdopodobieństwa inaczej.
_KNOWN_PROBABILITY_ATTRIBUTES: Final[tuple[str, ...]] = (
    "prawdopodobienstwo",
    "prawdopodobieństwo",
    "klasa_prawdopodobienstwa",
    "p",
    "probability",
)

# Sprawdzane w tej kolejności (od najbardziej specyficznych tokenów), żeby
# uniknąć fałszywych dopasowań substringów — np. token '1%' nie może dopasować
# się wewnątrz '10%', dlatego '10%' jest sprawdzane wcześniej niż '1%'.
_FLOOD_PROBABILITY_SEVERITY_RULES: Final[tuple[tuple[str, str], ...]] = (
    ("0,2%", "low"),
    ("0.2%", "low"),
    ("10%", "medium"),
    ("1%", "high"),
)

# Próg poniżej którego przecięcie traktujemy jako czysto brzegowe (styk),
# nie powierzchniowe — działki dotykające granicy strefy mają matematycznie
# niezerowe, ale nieistotne pole przecięcia wynikające z precyzji Shapely.
_INTERSECTION_AREA_EPSILON_SQM: Final[float] = 1e-6


class IsokServiceUnavailableError(Exception):
    """
    Usługa ISOK nie odpowiedziała, zwróciła błąd HTTP, lub zwróciła odpowiedź
    niemożliwą do sparsowania.

    W odróżnieniu od KIUT, brak danych o ryzyku powodziowym NIE może być cicho
    zamieniony na pustą listę — patrz uzasadnienie w docstringu modułu.
    """


@dataclass(frozen=True)
class RiskFeature:
    """
    Pojedyncza strefa zagrożenia powodziowego przecinająca się z działką.

    Zawiera tylko strefy faktycznie stykające się lub nakładające z geometrią
    działki — strefy obecne w BBOX zapytania WFS, ale geometrycznie rozłączne
    z działką, nie są tu reprezentowane (patrz _build_risk_feature).
    """

    risk_type: str
    severity: str
    geometry: BaseGeometry
    intersection_area_sqm: float
    area_ratio: float
    probability_class: str | None
    source_metadata: SourceMetadata
    # Celowo lista, nie pojedynczy str — dla ryzyka powodziowego mogą wystąpić
    # jednocześnie np. boundary_touch i nierozpoznana klasa prawdopodobieństwa.
    warnings: list[str] = field(default_factory=list)


async def fetch_flood_risks(
    parcel_geometry: BaseGeometry,
    client: httpx.AsyncClient | None = None,
) -> list[RiskFeature]:
    """
    Wykrywa ryzyko powodziowe dla działki przez rzeczywiste przecięcie geometrii
    ze strefami zagrożenia powodziowego ISOK.

    Wejściem jest geometria działki w EPSG:2180 (nie BBOX — BBOX jest liczony
    wewnętrznie przez bbox_from_geometry). Funkcja wykrywa RZECZYWISTE
    przecięcie geometrii ze strefami zagrożenia, nie tylko obecność danych w
    BBOX zapytania WFS — strefa obecna w odpowiedzi usługi, ale geometrycznie
    rozłączna z działką, jest odrzucana.

    Pusta lista oznacza "sprawdzono, brak stref w sąsiedztwie działki". Błąd
    usługi lub nieparsowalna odpowiedź podnosi IsokServiceUnavailableError —
    te dwa przypadki NIGDY nie są mylone, w odróżnieniu od fetch_kiut_networks
    (patrz uzasadnienie w docstringu modułu), bo dla ryzyka powodziowego pusta
    lista przy awarii usługi byłaby fałszywym poczuciem bezpieczeństwa.

    WMS (settings.isok_wms_fallback_url) jest wyłącznie linkiem referencyjnym
    do ręcznej weryfikacji wizualnej w konfiguracji — nieużywanym jako aktywne
    źródło danych w tej funkcji. Analiza rastra WMS nie jest zaimplementowana.
    """
    minx, miny, maxx, maxy = bbox_from_geometry(parcel_geometry)
    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "bbox": f"{minx},{miny},{maxx},{maxy},EPSG:2180",
    }

    if client is not None:
        response_text, source_url = await _fetch_isok_response_text(client, params)
    else:
        async with httpx.AsyncClient() as owned_client:
            response_text, source_url = await _fetch_isok_response_text(
                owned_client, params
            )

    fetched_at = datetime.now(timezone.utc)
    zone_features = _parse_zone_response(response_text)

    results: list[RiskFeature] = []
    for zone_geometry, properties in zone_features:
        risk = _build_risk_feature(
            parcel_geometry, zone_geometry, properties, source_url, fetched_at
        )
        if risk is not None:
            results.append(risk)
    return results


async def _fetch_isok_response_text(
    client: httpx.AsyncClient,
    params: dict[str, str],
) -> tuple[str, str]:
    try:
        response = await client.get(
            settings.isok_wfs_base_url,
            params=params,
            timeout=ISOK_TIMEOUT_S,
        )
        response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise IsokServiceUnavailableError(
            "Usługa ISOK nie odpowiedziała w wymaganym czasie."
        ) from exc
    except httpx.HTTPError as exc:
        raise IsokServiceUnavailableError(f"Usługa ISOK zwróciła błąd: {exc}") from exc

    return response.text, str(response.url)


def _parse_zone_response(text: str) -> list[tuple[BaseGeometry, dict]]:
    stripped = text.strip()
    if not stripped:
        return []
    try:
        if stripped.startswith("{"):
            return _parse_geojson_zones(stripped)
        return _parse_gml_zones(stripped)
    except (json.JSONDecodeError, ElementTree.ParseError, ValueError) as exc:
        # W odróżnieniu od kiut.py: błąd parsowania podnosi wyjątek, nie
        # zwraca [] — patrz critical_design_deviation w docstringu modułu.
        raise IsokServiceUnavailableError(
            f"Nie udało się sparsować odpowiedzi ISOK: {exc}"
        ) from exc


def _parse_geojson_zones(text: str) -> list[tuple[BaseGeometry, dict]]:
    data = json.loads(text)
    features = data.get("features", [])

    results: list[tuple[BaseGeometry, dict]] = []
    for feature in features:
        geometry_dict = feature.get("geometry")
        if not geometry_dict:
            continue

        geometry = shapely_shape(geometry_dict)
        if geometry.geom_type not in ("Polygon", "MultiPolygon"):
            logger.warning("Nieobsługiwany typ geometrii ISOK: %s", geometry.geom_type)
            continue

        results.append((geometry, feature.get("properties", {}) or {}))
    return results


def _parse_gml_zones(text: str) -> list[tuple[BaseGeometry, dict]]:
    """
    Parsuje uproszczoną strukturę GML dla stref powodziowych.

    Analogicznie do kiut.py, rzeczywisty schemat ISOK nie jest w pełni znany.
    Dziury w poligonach (gml:interior) NIE są obsługiwane w tej wersji — świadome
    uproszczenie zakresu, bo strefy zagrożenia powodziowego z otworami są
    rzadkim przypadkiem, a obsługa interior ringów wymagałaby dodatkowej
    walidacji topologii bez pełnej dokumentacji schematu ISOK.
    """
    root = ElementTree.fromstring(text)  # ParseError propaguje się do _parse_zone_response

    results: list[tuple[BaseGeometry, dict]] = []
    for member in root.iter():
        if _local_name(member.tag) not in ("member", "featureMember"):
            continue
        for feature_elem in list(member):
            geometry = _extract_gml_polygon(feature_elem)
            if geometry is None:
                continue
            properties = _extract_gml_properties(feature_elem)
            results.append((geometry, properties))
    return results


def _extract_gml_polygon(feature_elem: ElementTree.Element) -> BaseGeometry | None:
    for elem in feature_elem.iter():
        local = _local_name(elem.tag)
        if local == "Polygon":
            ring = _find_exterior_ring_coords(elem)
            return Polygon(ring) if ring else None
        if local in ("MultiPolygon", "MultiSurface"):
            polygons = []
            for poly_elem in elem.iter():
                if _local_name(poly_elem.tag) == "Polygon":
                    ring = _find_exterior_ring_coords(poly_elem)
                    if ring:
                        polygons.append(Polygon(ring))
            return MultiPolygon(polygons) if polygons else None
    return None


def _find_exterior_ring_coords(
    polygon_elem: ElementTree.Element,
) -> list[tuple[float, float]] | None:
    # Bierze pierwszy napotkany posList w elemencie Polygon jako pierścień
    # zewnętrzny — wystarczające dla uproszczonego, udokumentowanego zakresu
    # bez dziur w poligonach (patrz docstring _parse_gml_zones).
    for elem in polygon_elem.iter():
        if _local_name(elem.tag) == "posList" and elem.text:
            values = [float(v) for v in elem.text.split()]
            pairs = list(zip(values[0::2], values[1::2]))
            return pairs if len(pairs) >= 4 else None
    return None


def _extract_gml_properties(feature_elem: ElementTree.Element) -> dict:
    properties: dict[str, str] = {}
    for elem in feature_elem.iter():
        local = _local_name(elem.tag)
        if local.lower() in _KNOWN_PROBABILITY_ATTRIBUTES and elem.text:
            properties[local] = elem.text
    return properties


def _classify_flood_probability(properties: dict) -> tuple[str | None, str, str | None]:
    """Zwraca (surowa_wartość_lub_None, severity, warning_lub_None)."""
    for key in _KNOWN_PROBABILITY_ATTRIBUTES:
        for prop_key, value in properties.items():
            if prop_key.lower() == key and isinstance(value, str):
                lowered = value.lower().replace(" ", "")
                for token, severity in _FLOOD_PROBABILITY_SEVERITY_RULES:
                    if token in lowered:
                        return value, severity, None
                return value, UNKNOWN_PROBABILITY_SEVERITY, (
                    f"Nierozpoznana klasa prawdopodobieństwa powodzi: {value!r} — "
                    f"przyjęto severity={UNKNOWN_PROBABILITY_SEVERITY} ostrożnościowo."
                )
    return None, UNKNOWN_PROBABILITY_SEVERITY, (
        "Brak atrybutu klasy prawdopodobieństwa powodzi w danych źródłowych — "
        f"przyjęto severity={UNKNOWN_PROBABILITY_SEVERITY} ostrożnościowo."
    )


def _build_risk_feature(
    parcel: BaseGeometry,
    zone: BaseGeometry,
    properties: dict,
    source_url: str,
    fetched_at: datetime,
) -> RiskFeature | None:
    """
    Zwraca None gdy strefa jest rozłączna z działką (rzeczywiste sprawdzenie
    geometryczne, nie tylko obecność w BBOX).

    Rozróżnia styk brzegowy (zerowe pole przecięcia) od rzeczywistego
    nakładania powierzchni — styk brzegowy dostaje severity='low' i warning
    'boundary_touch' NIEZALEŻNIE od klasy prawdopodobieństwa strefy, żeby nie
    sugerować fałszywie pełnego ryzyka tam, gdzie nie ma rzeczywistego
    nakładania powierzchni.
    """
    if not parcel.intersects(zone):
        return None

    intersection_geom = parcel.intersection(zone)
    intersection_area_sqm = intersection_geom.area
    parcel_area = parcel.area
    area_ratio = (intersection_area_sqm / parcel_area) if parcel_area > 0 else 0.0

    probability_class, probability_severity, probability_warning = (
        _classify_flood_probability(properties)
    )
    warnings: list[str] = []
    if probability_warning:
        warnings.append(probability_warning)

    if intersection_area_sqm < _INTERSECTION_AREA_EPSILON_SQM:
        # Styk brzegowy nadpisuje severity z klasyfikacji prawdopodobieństwa —
        # inaczej użytkownik zobaczyłby np. severity='high' dla działki, która
        # w rzeczywistości tylko dotyka granicy strefy zagrożenia.
        severity = "low"
        warnings.append(
            "boundary_touch: działka styka się z granicą strefy zagrożenia "
            "powodziowego, bez rzeczywistego nakładania powierzchni — nie "
            "traktuj jako pełnego ryzyka."
        )
    else:
        severity = probability_severity

    return RiskFeature(
        risk_type="flood",
        severity=severity,
        geometry=intersection_geom,
        intersection_area_sqm=intersection_area_sqm,
        area_ratio=area_ratio,
        probability_class=probability_class,
        source_metadata=SourceMetadata(
            source_name="ISOK",
            source_url=source_url,
            fetched_at=fetched_at,
            confidence=0.85 if probability_class else 0.4,
            manual_review_required=(probability_class is None),
        ),
        warnings=warnings,
    )
