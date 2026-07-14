"""Serwis GDOŚ — formy ochrony przyrody przez przecięcie geometrii.

Formy ochrony przyrody mogą stanowić twarde ograniczenie inwestycyjne. Awaria
usługi nie może więc wyglądać tak samo jak poprawnie sprawdzony brak kolizji.
Pusta lista oznacza wyłącznie "sprawdzono, brak przecięcia", natomiast timeout,
błąd HTTP lub nieparsowalna odpowiedź podnoszą GdosServiceUnavailableError.
Przyszły orchestrator analizy powinien zamienić ten wyjątek na niedostępność
sekcji i ostrzeżenie, zachowując częściowe wyniki pozostałych usług.
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

GDOS_TIMEOUT_S: Final[float] = 10.0
_INTERSECTION_AREA_EPSILON_SQM: Final[float] = 1e-6
_FULL_COVERAGE_RATIO_THRESHOLD: Final[float] = 0.999
_RATIO_HIGH_THRESHOLD: Final[float] = 0.5
_RATIO_MEDIUM_THRESHOLD: Final[float] = 0.1

# Rezerwaty i parki narodowe mają praktycznie całkowity zakaz zabudowy nawet
# przy małym przecięciu. To założenie produktowe można skorygować w jednej stałej.
_ALWAYS_HIGH_PROTECTION_TYPES: Final[frozenset[str]] = frozenset(
    {"rezerwat_przyrody", "park_narodowy"}
)

_KNOWN_PROTECTION_TYPE_ATTRIBUTES: Final[tuple[str, ...]] = (
    "forma_ochrony",
    "typ_ochrony",
    "kategoria",
    "typ",
    "rodzaj",
)
_KNOWN_NAME_ATTRIBUTES: Final[tuple[str, ...]] = (
    "nazwa",
    "name",
    "nazwa_obszaru",
)

# Wartości atrybutów bywają pełnymi opisami, dlatego dopasowanie działa przez
# podłańcuch na tekście znormalizowanym do małych liter i pojedynczych spacji.
_PROTECTION_TYPE_KEYWORDS: Final[dict[str, str]] = {
    "natura 2000": "natura2000",
    "natura2000": "natura2000",
    "rezerwat": "rezerwat_przyrody",
    "park narodowy": "park_narodowy",
    "park krajobrazowy": "park_krajobrazowy",
    "obszar chronionego krajobrazu": "obszar_chronionego_krajobrazu",
    "pomnik przyrody": "pomnik_przyrody",
    "uzytek ekologiczny": "uzytek_ekologiczny",
    "użytek ekologiczny": "uzytek_ekologiczny",
}


class GdosServiceUnavailableError(Exception):
    """Usługa GDOŚ jest niedostępna albo zwróciła nieparsowalną odpowiedź.

    Analogicznie do IsokServiceUnavailableError brak danych o formach ochrony
    przyrody nie może być cicho zamieniony na pustą listę.
    """


@dataclass(frozen=True)
class NatureProtectionFeature:
    """Forma ochrony przyrody przecinająca się z geometrią działki."""

    protection_type: str
    name: str | None
    geometry: BaseGeometry
    intersection_area_sqm: float
    area_ratio: float
    severity: str
    source_metadata: SourceMetadata
    warnings: list[str] = field(default_factory=list)


async def fetch_nature_protection_areas(
    parcel_geometry: BaseGeometry,
    client: httpx.AsyncClient | None = None,
) -> list[NatureProtectionFeature]:
    """Wykrywa rzeczywiste przecięcia działki z formami ochrony przyrody.

    Geometria wejściowa musi być w EPSG:2180, aby pola przecięć i udział
    powierzchni miały znaczenie metryczne. Pusta lista oznacza "sprawdzono,
    brak przecięcia", a nie brak sekcji. Błąd usługi albo parsowania zawsze
    podnosi GdosServiceUnavailableError i nigdy nie jest zamieniany na ``[]``.
    """
    minx, miny, maxx, maxy = bbox_from_geometry(parcel_geometry)
    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "bbox": f"{minx},{miny},{maxx},{maxy},EPSG:2180",
    }

    if client is not None:
        response_text, source_url = await _fetch_gdos_response_text(client, params)
    else:
        async with httpx.AsyncClient() as owned_client:
            response_text, source_url = await _fetch_gdos_response_text(
                owned_client, params
            )

    fetched_at = datetime.now(timezone.utc)
    zone_features = _parse_zone_response(response_text)

    results: list[NatureProtectionFeature] = []
    for zone_geometry, properties in zone_features:
        feature = _build_nature_protection_feature(
            parcel_geometry,
            zone_geometry,
            properties,
            source_url,
            fetched_at,
        )
        if feature is not None:
            results.append(feature)
    return results


async def _fetch_gdos_response_text(
    client: httpx.AsyncClient,
    params: dict[str, str],
) -> tuple[str, str]:
    try:
        response = await client.get(
            settings.gdos_wfs_base_url,
            params=params,
            timeout=GDOS_TIMEOUT_S,
        )
        response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise GdosServiceUnavailableError(
            "Usługa GDOŚ nie odpowiedziała w wymaganym czasie."
        ) from exc
    except httpx.HTTPError as exc:
        raise GdosServiceUnavailableError(f"Usługa GDOŚ zwróciła błąd: {exc}") from exc

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
        # Pusta lista jest zarezerwowana dla potwierdzonego braku przecięć;
        # uszkodzona odpowiedź musi pozostać odróżnialna dla orchestratora.
        raise GdosServiceUnavailableError(
            f"Nie udało się sparsować odpowiedzi GDOŚ: {exc}"
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
            logger.warning("Nieobsługiwany typ geometrii GDOŚ: %s", geometry.geom_type)
            continue

        results.append((geometry, feature.get("properties", {}) or {}))
    return results


def _parse_gml_zones(text: str) -> list[tuple[BaseGeometry, dict]]:
    """Parsuje uproszczone GML Polygon/MultiPolygon bez pierścieni wewnętrznych.

    Schemat WFS GDOŚ nie jest jeszcze udokumentowany w projekcie. Obsługa
    otworów wymagałaby potwierdzenia kontraktu i walidacji topologii, dlatego
    parser zachowuje świadomie ten sam ograniczony zakres co serwis ISOK.
    """
    root = ElementTree.fromstring(text)

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
    # Pierwszy posList jest pierścieniem zewnętrznym w obsługiwanym,
    # uproszczonym zakresie GML bez otworów opisanym w _parse_gml_zones.
    for elem in polygon_elem.iter():
        if _local_name(elem.tag) == "posList" and elem.text:
            values = [float(value) for value in elem.text.split()]
            pairs = list(zip(values[0::2], values[1::2]))
            return pairs if len(pairs) >= 4 else None
    return None


def _extract_gml_properties(feature_elem: ElementTree.Element) -> dict:
    properties: dict[str, str] = {}
    known_attributes = _KNOWN_PROTECTION_TYPE_ATTRIBUTES + _KNOWN_NAME_ATTRIBUTES
    for elem in feature_elem.iter():
        local = _local_name(elem.tag)
        if local.lower() in known_attributes and elem.text:
            properties[local] = elem.text
    return properties


def _normalize_protection_type_value(raw_value: str) -> str:
    lowered = " ".join(raw_value.lower().split())
    for keyword, protection_type in _PROTECTION_TYPE_KEYWORDS.items():
        if keyword in lowered:
            return protection_type
    return "unknown"


def _classify_protection_type(properties: dict) -> tuple[str, str | None]:
    for key in _KNOWN_PROTECTION_TYPE_ATTRIBUTES:
        for prop_key, value in properties.items():
            if prop_key.lower() == key and isinstance(value, str):
                normalized = _normalize_protection_type_value(value)
                if normalized != "unknown":
                    return normalized, None
    return (
        "unknown",
        "Nie rozpoznano typu formy ochrony przyrody na podstawie dostępnych "
        "atrybutów — oznaczono jako unknown.",
    )


def _extract_name(properties: dict) -> str | None:
    for key in _KNOWN_NAME_ATTRIBUTES:
        for prop_key, value in properties.items():
            if (
                prop_key.lower() == key
                and isinstance(value, str)
                and value.strip()
            ):
                return value.strip()
    return None


def _ratio_based_severity(area_ratio: float) -> str:
    if area_ratio >= _RATIO_HIGH_THRESHOLD:
        return "high"
    if area_ratio >= _RATIO_MEDIUM_THRESHOLD:
        return "medium"
    return "low"


def _classify_severity(
    protection_type: str, area_ratio: float
) -> tuple[str, str | None]:
    """Zwraca severity oraz opcjonalne ostrzeżenie o sposobie oszacowania.

    Pełne pokrycie zawsze daje ``high``. Rezerwaty przyrody i parki narodowe
    dają ``high`` nawet przy małym udziale z powodu praktycznie całkowitego
    zakazu zabudowy. Pozostałe i nieznane typy skalują wynik przez area_ratio.
    """
    if area_ratio >= _FULL_COVERAGE_RATIO_THRESHOLD:
        return "high", None
    if protection_type in _ALWAYS_HIGH_PROTECTION_TYPES:
        return "high", None

    severity = _ratio_based_severity(area_ratio)
    if protection_type == "unknown":
        return severity, (
            "Nieznany typ formy ochrony przyrody — severity oszacowano "
            "konserwatywnie na podstawie powierzchni przecięcia (area_ratio)."
        )
    return severity, None


def _build_nature_protection_feature(
    parcel: BaseGeometry,
    zone: BaseGeometry,
    properties: dict,
    source_url: str,
    fetched_at: datetime,
) -> NatureProtectionFeature | None:
    if not parcel.intersects(zone):
        return None

    intersection_geom = parcel.intersection(zone)
    intersection_area_sqm = intersection_geom.area
    parcel_area = parcel.area
    area_ratio = (intersection_area_sqm / parcel_area) if parcel_area > 0 else 0.0

    protection_type, type_warning = _classify_protection_type(properties)
    name = _extract_name(properties)
    warnings: list[str] = []
    if type_warning:
        warnings.append(type_warning)

    if intersection_area_sqm < _INTERSECTION_AREA_EPSILON_SQM:
        # Styk granicą nie jest powierzchniowym ograniczeniem, więc nadpisuje
        # nawet stałą regułę high dla rezerwatów i parków narodowych.
        severity = "low"
        warnings.append(
            "boundary_touch: działka styka się z granicą formy ochrony przyrody, "
            "bez rzeczywistego nakładania powierzchni — nie traktuj jako pełnego "
            "ryzyka."
        )
    else:
        severity, severity_warning = _classify_severity(protection_type, area_ratio)
        if severity_warning:
            warnings.append(severity_warning)

    return NatureProtectionFeature(
        protection_type=protection_type,
        name=name,
        geometry=intersection_geom,
        intersection_area_sqm=intersection_area_sqm,
        area_ratio=area_ratio,
        severity=severity,
        source_metadata=SourceMetadata(
            source_name="GDOS",
            source_url=source_url,
            fetched_at=fetched_at,
            confidence=0.85 if protection_type != "unknown" else 0.4,
            manual_review_required=(protection_type == "unknown"),
        ),
        warnings=warnings,
    )
