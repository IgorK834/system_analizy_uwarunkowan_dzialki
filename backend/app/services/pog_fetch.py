"""Bezpieczne pobieranie i parsowanie gminnych danych wektorowych POG.

Obsługiwane są GML/APP, GeoJSON oraz ZIP zawierający te formaty. Pobieranie
ponownie używa zabezpieczeń modułu MPZP: walidacji URL/DNS przed każdym hopem,
ręcznych redirectów, limitu rozmiaru i kontroli ścieżek ZIP. Archiwa pozostają
wyłącznie w pamięci.
"""

from __future__ import annotations

import io
import json
import logging
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Final, Literal, Sequence
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup
from pyproj import CRS, Transformer
from shapely import make_valid
from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

from app.schemas.source import SourceMetadata, WarningMessage
from app.services.mpzp_fetch import (
    MPZP_FETCH_TIMEOUT_S,
    MPZP_MAX_DOCUMENT_SIZE_BYTES,
    MpzpDocumentError,
    MpzpDocumentFetchError,
    MpzpDocumentSecurityError,
    _fetch_with_redirect_validation,
    _safe_zip_member_path,
)

logger = logging.getLogger(__name__)

POG_TARGET_CRS: Final[str] = "EPSG:2180"
POG_MAX_DISCOVERED_LINKS: Final[int] = 20
_VECTOR_EXTENSIONS: Final[tuple[str, ...]] = (".gml", ".xml", ".geojson", ".json")
_ZIP_EXTENSIONS: Final[tuple[str, ...]] = (".zip",)

PogVectorStatus = Literal["available", "partial", "wms_fallback_required"]
PogVectorLayer = Literal[
    "planning_zone",
    "ouz",
    "downtown_area",
    "social_infrastructure_standard",
    "app_metadata",
]


class PogVectorError(Exception):
    """Bazowy kontrolowany błąd danych wektorowych POG."""


class PogVectorSecurityError(PogVectorError):
    """Plik POG został odrzucony ze względów bezpieczeństwa."""


class PogVectorParseError(PogVectorError):
    """Plik POG został pobrany, ale nie można go wiarygodnie sparsować."""


@dataclass(frozen=True)
class PogVectorFeature:
    """Pojedynczy obiekt POG z geometrią znormalizowaną do EPSG:2180."""

    geometry: BaseGeometry
    attributes: dict[str, object]
    source_crs: str
    layer_type: PogVectorLayer


@dataclass(frozen=True)
class PogVectorData:
    """Rozdzielone warstwy wektorowe POG gotowe do analizy powierzchniowej."""

    planning_zones: list[PogVectorFeature]
    ouz_areas: list[PogVectorFeature]
    downtown_areas: list[PogVectorFeature]
    app_metadata: dict[str, object]
    status: PogVectorStatus
    wms_fallback_required: bool
    source_metadata: SourceMetadata
    social_infrastructure_standard_areas: list[PogVectorFeature] = field(default_factory=list)
    warnings: list[WarningMessage] = field(default_factory=list)


@dataclass(frozen=True)
class _ParsedVectorDocument:
    planning_zones: list[PogVectorFeature]
    ouz_areas: list[PogVectorFeature]
    downtown_areas: list[PogVectorFeature]
    app_metadata: dict[str, object]
    warnings: list[WarningMessage]
    social_infrastructure_standard_areas: list[PogVectorFeature] = field(default_factory=list)


async def fetch_pog_vector_data(pog_links: Sequence[str]) -> PogVectorData:
    """Pobiera i normalizuje dane APP/GML lub GeoJSON do EPSG:2180.

    Linki pochodzą z discovery konkretnej gminy i są traktowane jako
    niezaufane. ZIP nie jest zapisywany na dysku, a każda ścieżka wpisu jest
    sprawdzana przed odczytem. Geometrie z innym CRS są transformowane przez
    pyproj z ``always_xy=True``. Dane służą do obliczeń POG/OUZ, ale w MVP nie
    weryfikujemy podpisu cyfrowego ani formalnej autentyczności aktu, dlatego
    wynik zawsze wymaga ręcznej weryfikacji źródła.

    Gdy nie uda się pozyskać żadnej geometrii, funkcja zwraca kontrolowany
    ``wms_fallback_required``. WMS pozostaje wtedy jedynie warstwą poglądową,
    a nie podstawą precyzyjnego przecięcia.
    """
    links_to_visit = list(dict.fromkeys(link for link in pog_links if link.strip()))
    visited: set[str] = set()
    warnings: list[WarningMessage] = []
    documents: list[_ParsedVectorDocument] = []
    successful_urls: list[str] = []

    async with httpx.AsyncClient(
        timeout=MPZP_FETCH_TIMEOUT_S,
        follow_redirects=False,
    ) as client:
        while links_to_visit and len(visited) < POG_MAX_DISCOVERED_LINKS:
            url = links_to_visit.pop(0)
            if url in visited:
                continue
            visited.add(url)
            try:
                (
                    content,
                    content_type,
                    final_url,
                ) = await _fetch_with_redirect_validation(client, url)
                if content_type == "text/html":
                    discovered = _find_vector_links_in_html(content, final_url)
                    links_to_visit.extend(
                        link
                        for link in discovered
                        if link not in visited and link not in links_to_visit
                    )
                    if not discovered:
                        warnings.append(
                            _warning(
                                "POG_VECTOR_LINK_NOT_FOUND",
                                "Strona źródłowa nie zawiera rozpoznanego odnośnika do GML, GeoJSON ani ZIP.",
                            )
                        )
                    continue

                entries = _vector_entries(content, content_type, final_url)
                for filename, entry_content in entries:
                    documents.append(_parse_vector_document(entry_content, filename))
                successful_urls.append(final_url)
            except (MpzpDocumentSecurityError, PogVectorSecurityError):
                warnings.append(
                    _warning(
                        "POG_VECTOR_SECURITY_BLOCK",
                        "Źródło danych POG zostało odrzucone przez reguły bezpieczeństwa.",
                        severity="error",
                    )
                )
            except (MpzpDocumentFetchError, PogVectorParseError):
                warnings.append(
                    _warning(
                        "POG_VECTOR_UNAVAILABLE",
                        "Nie udało się pobrać lub sparsować jednego ze źródeł wektorowych POG.",
                        severity="warning",
                    )
                )
            except MpzpDocumentError:
                warnings.append(
                    _warning(
                        "POG_VECTOR_UNAVAILABLE",
                        "Źródło wektorowe POG jest niedostępne.",
                        severity="warning",
                    )
                )

    planning_zones = [feature for doc in documents for feature in doc.planning_zones]
    ouz_areas = [feature for doc in documents for feature in doc.ouz_areas]
    downtown_areas = [feature for doc in documents for feature in doc.downtown_areas]
    social_areas = [
        feature
        for doc in documents
        for feature in doc.social_infrastructure_standard_areas
    ]
    app_metadata: dict[str, object] = {}
    for document in documents:
        app_metadata.update(document.app_metadata)
        warnings.extend(document.warnings)

    has_geometry = bool(planning_zones or ouz_areas or downtown_areas or social_areas)
    if not has_geometry:
        warnings.append(
            _warning(
                "POG_WMS_FALLBACK_REQUIRED",
                "Brak danych wektorowych POG. WMS może służyć tylko do prezentacji, a wynik wymaga ręcznej weryfikacji.",
                severity="error",
            )
        )
        status: PogVectorStatus = "wms_fallback_required"
    elif warnings:
        status = "partial"
    else:
        status = "available"

    # Pełna walidacja podpisu elektronicznego aktu nie należy do MVP. Ostrzeżenie
    # jest jawne nawet dla poprawnie sparsowanego pliku, aby nie mylić poprawności
    # technicznej z formalną autentycznością dokumentu.
    if has_geometry:
        warnings.append(
            _warning(
                "POG_AUTHENTICITY_NOT_VERIFIED",
                "Nie wykonano formalnej weryfikacji podpisu ani autentyczności pliku APP; sprawdź publikację w źródle urzędowym.",
                severity="info",
            )
        )

    source_url = (
        successful_urls[0] if successful_urls else (pog_links[0] if pog_links else None)
    )
    return PogVectorData(
        planning_zones=planning_zones,
        ouz_areas=ouz_areas,
        downtown_areas=downtown_areas,
        app_metadata=app_metadata,
        status=status,
        wms_fallback_required=not has_geometry,
        source_metadata=SourceMetadata(
            source_id="pog_app",
            source_name="POG_APP_VECTOR",
            source_url=source_url,
            fetched_at=datetime.now(timezone.utc),
            response_status=200 if successful_urls else None,
            confidence=0.75 if has_geometry else 0.15,
            manual_review_required=True,
        ),
        social_infrastructure_standard_areas=social_areas,
        warnings=warnings,
    )


def _find_vector_links_in_html(content: bytes, base_url: str) -> list[str]:
    soup = BeautifulSoup(content, "html.parser")
    links: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = str(anchor["href"]).strip()
        path = urlparse(href).path.lower()
        if path.endswith(_VECTOR_EXTENSIONS + _ZIP_EXTENSIONS):
            links.append(urljoin(base_url, href))
    return list(dict.fromkeys(links))


def _vector_entries(
    content: bytes,
    content_type: str,
    url: str,
) -> list[tuple[str, bytes]]:
    path = urlparse(url).path
    filename = path.rsplit("/", maxsplit=1)[-1] or "pog-data"
    is_zip = (
        content.startswith(b"PK\x03\x04")
        or content_type in {"application/zip", "application/x-zip-compressed"}
        or path.lower().endswith(_ZIP_EXTENSIONS)
    )
    if is_zip:
        return _extract_vector_entries_from_zip(content)
    return [(filename, content)]


def _extract_vector_entries_from_zip(content: bytes) -> list[tuple[str, bytes]]:
    """Czyta bezpieczne wpisy GML/GeoJSON z ZIP wyłącznie w pamięci."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries: list[tuple[str, bytes]] = []
            total_uncompressed_size = 0
            for info in archive.infolist():
                safe_name = _safe_zip_member_path(info.filename)
                if safe_name is None:
                    raise PogVectorSecurityError(
                        "Archiwum POG zawiera ścieżkę mogącą prowadzić do Zip Slip."
                    )
                if info.is_dir() or not safe_name.lower().endswith(_VECTOR_EXTENSIONS):
                    continue
                total_uncompressed_size += info.file_size
                if total_uncompressed_size > MPZP_MAX_DOCUMENT_SIZE_BYTES:
                    raise PogVectorSecurityError(
                        "Rozpakowane dane POG przekraczają dozwolony limit rozmiaru."
                    )
                entry_content = archive.read(info)
                if len(entry_content) > MPZP_MAX_DOCUMENT_SIZE_BYTES:
                    raise PogVectorSecurityError(
                        "Wpis danych POG przekracza dozwolony limit rozmiaru."
                    )
                entries.append((safe_name, entry_content))
            if not entries:
                raise PogVectorParseError(
                    "Archiwum nie zawiera pliku GML, XML, GeoJSON ani JSON."
                )
            return entries
    except zipfile.BadZipFile as exc:
        raise PogVectorParseError("Archiwum danych POG jest uszkodzone.") from exc


def _parse_vector_document(content: bytes, filename: str) -> _ParsedVectorDocument:
    stripped = content.lstrip()
    try:
        if filename.lower().endswith((".geojson", ".json")) or stripped.startswith(
            (b"{", b"[")
        ):
            return _parse_geojson(content)
        return _parse_gml(content)
    except (json.JSONDecodeError, ElementTree.ParseError, TypeError, ValueError) as exc:
        raise PogVectorParseError(f"Nieprawidłowy dokument wektorowy: {exc}") from exc


def _parse_geojson(content: bytes) -> _ParsedVectorDocument:
    data = json.loads(content.decode("utf-8-sig"))
    if not isinstance(data, dict):
        raise TypeError("GeoJSON nie jest obiektem.")
    if data.get("type") == "Feature":
        features = [data]
    else:
        features = data.get("features", [])
    if not isinstance(features, list):
        raise TypeError("Pole features GeoJSON nie jest listą.")

    source_crs, assumed_crs = _geojson_crs(data)
    warnings: list[WarningMessage] = []
    if assumed_crs:
        warnings.append(
            _warning(
                "POG_GEOJSON_CRS_ASSUMED",
                "GeoJSON nie deklaruje CRS; zgodnie ze standardem przyjęto EPSG:4326.",
                severity="warning",
            )
        )
    buckets = _empty_feature_buckets()
    raw_metadata = data.get("app_metadata", {}) or {}
    metadata: dict[str, object] = (
        dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
    )
    for feature in features:
        if not isinstance(feature, dict):
            continue
        properties = feature.get("properties", {}) or {}
        if not isinstance(properties, dict):
            properties = {}
        layer_type = _classify_layer("", properties)
        if layer_type == "app_metadata":
            metadata.update(properties)
            continue
        if not feature.get("geometry"):
            continue
        geometry = _normalize_geometry(shape(feature["geometry"]), source_crs)
        if geometry is None or layer_type is None:
            continue
        buckets[layer_type].append(
            PogVectorFeature(geometry, properties, source_crs, layer_type)
        )
    return _document_from_buckets(buckets, metadata, warnings)


def _geojson_crs(data: dict[str, object]) -> tuple[str, bool]:
    crs = data.get("crs")
    if isinstance(crs, dict):
        properties = crs.get("properties")
        if isinstance(properties, dict) and properties.get("name"):
            return _canonical_crs(str(properties["name"])), False
    return "EPSG:4326", True


def _parse_gml(content: bytes) -> _ParsedVectorDocument:
    root = ElementTree.fromstring(content)
    default_crs = _first_srs_name(root)
    if default_crs is None:
        raise PogVectorParseError(
            "GML POG nie deklaruje srsName; CRS nie może być zgadywany."
        )
    warnings: list[WarningMessage] = []

    buckets = _empty_feature_buckets()
    metadata: dict[str, object] = {}
    for feature_elem in _iter_gml_features(root):
        properties = _gml_properties(feature_elem)
        layer_type = _classify_layer(_local_name(feature_elem.tag), properties)
        if layer_type == "app_metadata":
            metadata.update(properties)
            continue
        geometry_and_crs = _gml_geometry(feature_elem, default_crs)
        if geometry_and_crs is None or layer_type is None:
            continue
        geometry, source_crs = geometry_and_crs
        normalized = _normalize_geometry(geometry, source_crs)
        if normalized is None:
            continue
        buckets[layer_type].append(
            PogVectorFeature(normalized, properties, source_crs, layer_type)
        )
    return _document_from_buckets(buckets, metadata, warnings)


def _iter_gml_features(root: ElementTree.Element) -> list[ElementTree.Element]:
    features: list[ElementTree.Element] = []
    for elem in root.iter():
        if _local_name(elem.tag) in {"member", "featureMember"}:
            features.extend(list(elem))
    if features:
        return features
    return [
        elem
        for elem in list(root)
        if _classify_layer(_local_name(elem.tag), {}) is not None
    ]


def _gml_geometry(
    feature_elem: ElementTree.Element,
    default_crs: str,
) -> tuple[BaseGeometry, str] | None:
    for elem in feature_elem.iter():
        local = _local_name(elem.tag)
        if local == "Polygon":
            polygon = _polygon_from_gml(elem)
            if polygon is not None:
                return polygon, _canonical_crs(elem.attrib.get("srsName", default_crs))
        if local in {"MultiPolygon", "MultiSurface"}:
            polygons = [
                polygon
                for polygon_elem in elem.iter()
                if _local_name(polygon_elem.tag) == "Polygon"
                if (polygon := _polygon_from_gml(polygon_elem)) is not None
            ]
            if polygons:
                return MultiPolygon(polygons), _canonical_crs(
                    elem.attrib.get("srsName", default_crs)
                )
    return None


def _polygon_from_gml(element: ElementTree.Element) -> Polygon | None:
    exterior: list[tuple[float, float]] | None = None
    interiors: list[list[tuple[float, float]]] = []
    for boundary in element.iter():
        local = _local_name(boundary.tag)
        if local not in {"exterior", "interior"}:
            continue
        coordinates = _coordinates_from_boundary(boundary)
        if not coordinates:
            continue
        if local == "exterior" and exterior is None:
            exterior = coordinates
        elif local == "interior":
            interiors.append(coordinates)
    if exterior is None:
        coordinates = _coordinates_from_boundary(element)
        exterior = coordinates or None
    return Polygon(exterior, interiors) if exterior else None


def _coordinates_from_boundary(
    boundary: ElementTree.Element,
) -> list[tuple[float, float]]:
    for elem in boundary.iter():
        local = _local_name(elem.tag)
        if local == "posList" and elem.text:
            values = [float(value) for value in elem.text.split()]
            dimension = int(elem.attrib.get("srsDimension", "2"))
            if dimension < 2 or len(values) % dimension:
                raise ValueError("Nieprawidłowy wymiar gml:posList.")
            return [
                (values[index], values[index + 1])
                for index in range(0, len(values), dimension)
            ]
        if local == "coordinates" and elem.text:
            return [
                (float(pair.split(",")[0]), float(pair.split(",")[1]))
                for pair in elem.text.split()
            ]
    positions = [
        [float(value) for value in elem.text.split()]
        for elem in boundary.iter()
        if _local_name(elem.tag) == "pos" and elem.text
    ]
    return [(position[0], position[1]) for position in positions if len(position) >= 2]


def _gml_properties(feature_elem: ElementTree.Element) -> dict[str, object]:
    properties: dict[str, object] = {}
    for child in list(feature_elem):
        if any(
            _local_name(descendant.tag) in {"Polygon", "MultiPolygon", "MultiSurface"}
            for descendant in child.iter()
        ):
            continue
        key = _local_name(child.tag)
        text = " ".join(child.itertext()).strip()
        href = next(
            (
                value
                for attr, value in child.attrib.items()
                if _local_name(attr) == "href"
            ),
            None,
        )
        value = href or text
        if value:
            properties[key] = value
    return properties


def _classify_layer(
    feature_name: str,
    properties: dict[str, object],
) -> PogVectorLayer | None:
    hints = [feature_name]
    for key in ("layer", "layer_type", "feature_type", "typ_obiektu", "typ"):
        value = properties.get(key)
        if value is not None:
            hints.append(str(value))
    normalized = " ".join(_normalize_name(hint) for hint in hints)
    if "strefaplanistyczna" in normalized or "planningzone" in normalized:
        return "planning_zone"
    if "obszaruzupelnieniazabudowy" in normalized or "ouz" in normalized.split():
        return "ouz"
    if "obszarzabudowysrodmiejskiej" in normalized or "downtown" in normalized:
        return "downtown_area"
    if "obszarstandardowdostepnosciinfrastrukturyspolecznej" in normalized:
        return "social_infrastructure_standard"
    if "aktplanowaniaprzestrzennego" in normalized or "appmetadata" in normalized:
        return "app_metadata"
    # Atrybut ustawowego kodu strefy jest mocniejszą wskazówką niż lokalna nazwa
    # typu obiektu, która w gminnych eksportach bywa całkowicie niestandardowa.
    if any(key.lower() in {"zone_type", "symbol", "oznaczenie"} for key in properties):
        return "planning_zone"
    return None


def _normalize_geometry(
    geometry: BaseGeometry,
    source_crs: str,
) -> BaseGeometry | None:
    if geometry.is_empty:
        return None
    canonical_source = _canonical_crs(source_crs)
    if canonical_source != POG_TARGET_CRS:
        transformer = Transformer.from_crs(
            canonical_source,
            POG_TARGET_CRS,
            always_xy=True,
        )
        geometry = transform(transformer.transform, geometry)
    if not geometry.is_valid:
        geometry = make_valid(geometry)
    if geometry.geom_type == "Polygon":
        return geometry
    if geometry.geom_type == "MultiPolygon":
        return geometry
    if geometry.geom_type == "GeometryCollection":
        polygons = [part for part in geometry.geoms if part.geom_type == "Polygon"]
        if polygons:
            return MultiPolygon(polygons) if len(polygons) > 1 else polygons[0]
    return None


def _canonical_crs(raw_crs: str) -> str:
    try:
        return CRS.from_user_input(raw_crs).to_string()
    except Exception as exc:
        raise ValueError(f"Nierozpoznany CRS danych POG: {raw_crs!r}.") from exc


def _first_srs_name(root: ElementTree.Element) -> str | None:
    for elem in root.iter():
        if elem.attrib.get("srsName"):
            return _canonical_crs(elem.attrib["srsName"])
    return None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", maxsplit=1)[-1].rsplit(":", maxsplit=1)[-1]


def _normalize_name(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.lower())
    return "".join(
        char
        for char in decomposed
        if not unicodedata.combining(char) and char.isalnum()
    )


def _empty_feature_buckets() -> dict[PogVectorLayer, list[PogVectorFeature]]:
    return {
        "planning_zone": [],
        "ouz": [],
        "downtown_area": [],
        "social_infrastructure_standard": [],
        "app_metadata": [],
    }


def _document_from_buckets(
    buckets: dict[PogVectorLayer, list[PogVectorFeature]],
    metadata: dict[str, object],
    warnings: list[WarningMessage],
) -> _ParsedVectorDocument:
    return _ParsedVectorDocument(
        planning_zones=buckets["planning_zone"],
        ouz_areas=buckets["ouz"],
        downtown_areas=buckets["downtown_area"],
        app_metadata=metadata,
        warnings=warnings,
        social_infrastructure_standard_areas=buckets["social_infrastructure_standard"],
    )


def _warning(
    code: str,
    message: str,
    *,
    severity: Literal["info", "warning", "error"] = "warning",
) -> WarningMessage:
    return WarningMessage(
        code=code,
        message=message,
        severity=severity,
        source_name="pog",
    )
