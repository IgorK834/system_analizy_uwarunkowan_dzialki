"""Adapter wektorowych danych sieci uzbrojenia KIUT/GESUT (BK-306).

Stan kontraktu (weryfikacja 2026-09-28, szczegóły w katalogu źródeł i
``docs/data_sources/kiut_contracts.md``):

* dawny adres WFS z ``settings.kiut_wfs_base_url`` odpowiada ``HTTP 401
  Unauthorized`` — był placeholderem i został usunięty z konfiguracji;
* publiczny endpoint Krajowej Integracji Uzbrojenia Terenu na zapytanie
  ``service=WFS&request=GetCapabilities`` zwraca wyłącznie
  ``WMS_Capabilities``, czyli nie publikuje kontraktu wektorowego;
* dane GESUT mają dostęp reglamentowany przez starostwa (``kiut_gesut`` ma w
  katalogu status ``contract_required``).

Dlatego domyślnie guard katalogu blokuje adapter **przed wykonaniem
jakiegokolwiek żądania** (``KiutSourceNotRunnableError``), a geometria KIUT nie
wpływa na obliczenia. Pozostaje wyłącznie podgląd WMS i wskaźnik pokrycia
powiatu (``kiut_coverage.py``).

Gdy kontrakt zostanie potwierdzony w katalogu (status ``production`` z zasobem
``networks``), adapter rozróżnia:

* ``available`` z ``relation="no_match"`` — wyłącznie po poprawnej i kompletnej
  odpowiedzi ``FeatureCollection`` bez obiektów (dawne ``available_empty``);
* ``KiutServiceUnavailableError`` — timeout, błąd transportu, status HTTP inny
  niż 200, ``ows:ExceptionReport``, odpowiedź niekompletna albo niepoprawna.

Awaria nigdy nie jest zamieniana na pustą listę, bo pusta lista byłaby
nieodróżnialna od sprawdzonego braku sieci.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Final, Literal
from xml.etree import ElementTree

import httpx
from shapely.geometry import LineString, MultiLineString
from shapely.geometry.base import BaseGeometry

from app.core.data_sources import (
    AccessType,
    CatalogError,
    DataSourceCatalog,
    SourceResource,
    ensure_source_runnable,
)
from app.schemas.analyze import SourceMetadata

logger = logging.getLogger(__name__)

KIUT_SOURCE_ID: Final[str] = "kiut_gesut"
KIUT_SOURCE_NAME: Final[str] = "KIUT"
KIUT_TIMEOUT_S: Final[float] = 10.0
_NETWORKS_ROLE: Final[str] = "networks"

# Kody przyczyn trafiające do ``reason_code`` sekcji i provenance próby.
REASON_VECTOR_SOURCE_NOT_CONFIRMED: Final[str] = "VECTOR_SOURCE_NOT_CONFIRMED"
REASON_SERVICE_TIMEOUT: Final[str] = "SERVICE_TIMEOUT"
REASON_TRANSPORT_ERROR: Final[str] = "TRANSPORT_ERROR"
REASON_HTTP_ERROR: Final[str] = "HTTP_ERROR"
REASON_EXCEPTION_REPORT: Final[str] = "EXCEPTION_REPORT"
REASON_MALFORMED_RESPONSE: Final[str] = "MALFORMED_RESPONSE"
REASON_INCOMPLETE_RESPONSE: Final[str] = "INCOMPLETE_RESPONSE"

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


@dataclass(frozen=True)
class KiutNetworkSection:
    """Wynik kompletnego, poprawnego zapytania do potwierdzonego źródła.

    ``features=[]`` z ``relation="no_match"`` to sprawdzony brak obiektów w
    zasięgu zapytania — powstaje wyłącznie z poprawnej odpowiedzi.
    """

    features: list[NetworkFeature]
    source_metadata: SourceMetadata
    status: Literal["available"] = "available"
    complete: bool = True
    warnings: list[str] = field(default_factory=list)

    @property
    def relation(self) -> Literal["no_match", "features_found"]:
        return "features_found" if self.features else "no_match"


class KiutError(RuntimeError):
    """Bazowy błąd sekcji KIUT z provenance także nieudanej próby."""

    def __init__(
        self,
        message: str,
        *,
        reason_code: str,
        source_metadata: SourceMetadata,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.source_metadata = source_metadata


class KiutSourceNotRunnableError(KiutError):
    """Brak dozwolonego kontraktu wektorowego — żądanie NIE zostało wysłane."""


class KiutServiceUnavailableError(KiutError):
    """Źródło nie dało poprawnej i kompletnej odpowiedzi — to nie jest brak sieci."""


class _MalformedKiutResponse(ValueError):
    def __init__(self, message: str, reason_code: str = REASON_MALFORMED_RESPONSE):
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class KiutWfsContract:
    """Potwierdzony w katalogu kontrakt zasobu ``networks``."""

    url: str
    type_names: tuple[str, ...]
    protocol_version: str


def bbox_from_geometry(geometry: BaseGeometry) -> tuple[float, float, float, float]:
    """
    Wyznacza BBOX (minx, miny, maxx, maxy) w EPSG:2180 z geometrii działki,
    do użycia w zapytaniu WFS GetFeature.

    Geometria wejściowa musi być już w EPSG:2180 — funkcja nie wykonuje
    żadnej transformacji.
    """
    return geometry.bounds


def resolve_kiut_contract(catalog: DataSourceCatalog | None = None) -> KiutWfsContract:
    """Zwraca kontrakt WFS albo podnosi ``KiutSourceNotRunnableError``.

    Guard jest wywoływany przed utworzeniem żądania: niepotwierdzone źródło
    (``contract_required``) nie wykonuje żadnego zapytania sieciowego.
    """
    try:
        entry = ensure_source_runnable(KIUT_SOURCE_ID, catalog)
        resource = _networks_resource(entry.resources)
    except CatalogError as exc:
        logger.info("Wektor KIUT/GESUT zablokowany przez guard katalogu: %s", exc)
        raise KiutSourceNotRunnableError(
            "Brak potwierdzonego, dozwolonego kontraktu wektorowego KIUT/GESUT.",
            reason_code=REASON_VECTOR_SOURCE_NOT_CONFIRMED,
            source_metadata=not_attempted_source_metadata(),
        ) from exc
    return KiutWfsContract(
        url=resource.url,
        type_names=resource.declared_type_names,
        protocol_version=resource.protocol_version or "2.0.0",
    )


def not_attempted_source_metadata() -> SourceMetadata:
    """Provenance decyzji guardu: źródło znane, zapytania nie wykonano."""
    return SourceMetadata(
        source_id=KIUT_SOURCE_ID,
        source_name=KIUT_SOURCE_NAME,
        source_url=None,
        fetched_at=datetime.now(timezone.utc),
        response_status=None,
        confidence=0.0,
        manual_review_required=True,
    )


async def fetch_kiut_network_section(
    parcel_bounds: tuple[float, float, float, float],
    client: httpx.AsyncClient | None = None,
    *,
    catalog: DataSourceCatalog | None = None,
) -> KiutNetworkSection:
    """
    Pobiera sieci uzbrojenia z potwierdzonego WFS w obrębie BBOX działki.

    Najpierw sprawdza guard katalogu — przy braku kontraktu żądanie nie
    powstaje. BBOX jest przekazywany w EPSG:2180. Każda awaria, także pusta,
    niepoprawna albo niekompletna odpowiedź, kończy się
    ``KiutServiceUnavailableError`` z provenance próby.
    """
    contract = resolve_kiut_contract(catalog)
    minx, miny, maxx, maxy = parcel_bounds
    params = {
        "service": "WFS",
        "version": contract.protocol_version,
        "request": "GetFeature",
        "typeNames": ",".join(contract.type_names),
        "bbox": f"{minx},{miny},{maxx},{maxy},EPSG:2180",
    }

    if client is not None:
        return await _fetch_kiut_with_client(client, contract.url, params)

    async with httpx.AsyncClient() as owned_client:
        return await _fetch_kiut_with_client(owned_client, contract.url, params)


async def _fetch_kiut_with_client(
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, str],
) -> KiutNetworkSection:
    attempted_at = datetime.now(timezone.utc)
    try:
        response = await client.get(url, params=params, timeout=KIUT_TIMEOUT_S)
    except httpx.TimeoutException as exc:
        raise _unavailable(
            "Usługa KIUT nie odpowiedziała w wymaganym czasie.",
            REASON_SERVICE_TIMEOUT,
            url,
            attempted_at,
            None,
        ) from exc
    except httpx.HTTPError as exc:
        raise _unavailable(
            f"Błąd transportu KIUT: {type(exc).__name__}.",
            REASON_TRANSPORT_ERROR,
            url,
            attempted_at,
            None,
        ) from exc

    request_url = str(response.request.url) if response.request else url
    if response.status_code != 200:
        raise _unavailable(
            f"Usługa KIUT zwróciła HTTP {response.status_code}.",
            REASON_HTTP_ERROR,
            request_url,
            attempted_at,
            response.status_code,
        )

    try:
        features = _parse_kiut_response(response.text, request_url, attempted_at)
    except _MalformedKiutResponse as exc:
        raise _unavailable(
            f"Odpowiedź KIUT nie spełnia kontraktu: {exc}",
            exc.reason_code,
            request_url,
            attempted_at,
            response.status_code,
        ) from exc

    return KiutNetworkSection(
        features=features,
        source_metadata=SourceMetadata(
            source_id=KIUT_SOURCE_ID,
            source_name=KIUT_SOURCE_NAME,
            source_url=request_url,
            fetched_at=attempted_at,
            response_status=response.status_code,
            confidence=0.8,
            manual_review_required=any(
                feature.network_type == "unknown" for feature in features
            ),
        ),
        warnings=[feature.warning for feature in features if feature.warning],
    )


def _unavailable(
    message: str,
    reason_code: str,
    url: str | None,
    attempted_at: datetime,
    response_status: int | None,
) -> KiutServiceUnavailableError:
    logger.warning("Sekcja KIUT niedostępna (%s): %s", reason_code, message)
    return KiutServiceUnavailableError(
        message,
        reason_code=reason_code,
        source_metadata=SourceMetadata(
            source_id=KIUT_SOURCE_ID,
            source_name=KIUT_SOURCE_NAME,
            source_url=url,
            fetched_at=attempted_at,
            response_status=response_status,
            confidence=0.0,
            manual_review_required=True,
        ),
    )


def _networks_resource(resources: list[SourceResource]) -> SourceResource:
    for resource in resources:
        if resource.role == _NETWORKS_ROLE and resource.access_type is AccessType.WFS:
            return resource
    raise CatalogError(
        "Katalog nie deklaruje zasobu WFS o roli 'networks' dla KIUT/GESUT."
    )


def _parse_kiut_response(
    text: str, source_url: str, fetched_at: datetime | None
) -> list[NetworkFeature]:
    """Parsuje kompletną odpowiedź; każda niejasność podnosi błąd kontraktu."""
    stripped = text.strip()
    if not stripped:
        raise _MalformedKiutResponse("pusta treść odpowiedzi")
    if stripped.startswith("{"):
        return _parse_geojson_networks(stripped, source_url, fetched_at)
    return _parse_gml_networks(stripped, source_url, fetched_at)


def _parse_geojson_networks(
    text: str, source_url: str, fetched_at: datetime | None
) -> list[NetworkFeature]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _MalformedKiutResponse("niepoprawny JSON") from exc
    if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
        raise _MalformedKiutResponse("GeoJSON nie jest FeatureCollection")
    features = data.get("features")
    if not isinstance(features, list):
        raise _MalformedKiutResponse("GeoJSON bez listy features")

    results: list[NetworkFeature] = []
    for feature in features:
        if not isinstance(feature, dict):
            raise _MalformedKiutResponse("element features nie jest obiektem")
        properties = feature.get("properties") or {}
        geometry_dict = feature.get("geometry")
        if not isinstance(geometry_dict, dict):
            raise _MalformedKiutResponse("cecha KIUT bez geometrii")
        shapely_geometry = _geojson_geometry_to_shapely(geometry_dict)
        network_type, warning = _classify_network_type(properties)
        results.append(_feature(network_type, shapely_geometry, warning, source_url, fetched_at))
    return results


def _geojson_geometry_to_shapely(geom_dict: dict) -> BaseGeometry:
    geom_type = geom_dict.get("type")
    coords = geom_dict.get("coordinates")
    try:
        if geom_type == "LineString" and coords:
            return LineString(coords)
        if geom_type == "MultiLineString" and coords:
            return MultiLineString(coords)
    except (TypeError, ValueError) as exc:
        raise _MalformedKiutResponse("niepoprawne współrzędne geometrii") from exc
    raise _MalformedKiutResponse(f"nieobsługiwany typ geometrii {geom_type!r}")


def _parse_gml_networks(
    text: str, source_url: str, fetched_at: datetime | None
) -> list[NetworkFeature]:
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise _MalformedKiutResponse("niepoprawny XML/GML") from exc

    root_name = _local_name(root.tag)
    if root_name in {"ExceptionReport", "ServiceExceptionReport"}:
        raise _MalformedKiutResponse(
            "usługa zwróciła raport wyjątku OGC", REASON_EXCEPTION_REPORT
        )
    if root_name != "FeatureCollection":
        raise _MalformedKiutResponse(f"nieoczekiwany element główny {root_name!r}")

    members = [
        member
        for member in root.iter()
        if _local_name(member.tag) in ("member", "featureMember")
    ]
    _check_wfs_counts(root, len(members))

    results: list[NetworkFeature] = []
    for member in members:
        children = list(member)
        if not children:
            raise _MalformedKiutResponse("pusty element member")
        for feature_elem in children:
            geometry = _extract_gml_geometry(feature_elem)
            if geometry is None:
                raise _MalformedKiutResponse("cecha KIUT bez obsługiwanej geometrii")
            network_type, warning = _classify_network_type_from_xml(feature_elem)
            results.append(_feature(network_type, geometry, warning, source_url, fetched_at))
    return results


def _check_wfs_counts(root: ElementTree.Element, member_count: int) -> None:
    """``numberReturned``/``numberMatched`` muszą potwierdzać kompletność."""
    returned = root.attrib.get("numberReturned")
    matched = root.attrib.get("numberMatched")
    if returned is not None:
        if not returned.isdigit():
            raise _MalformedKiutResponse(f"niepoprawne numberReturned={returned!r}")
        if int(returned) != member_count:
            raise _MalformedKiutResponse(
                "numberReturned nie zgadza się z liczbą elementów member"
            )
    if matched is not None and matched != "unknown":
        if not matched.isdigit():
            raise _MalformedKiutResponse(f"niepoprawne numberMatched={matched!r}")
        if int(matched) > member_count:
            raise _MalformedKiutResponse(
                "odpowiedź zawiera tylko część pasujących obiektów",
                REASON_INCOMPLETE_RESPONSE,
            )


def _feature(
    network_type: str,
    geometry: BaseGeometry,
    warning: str | None,
    source_url: str,
    fetched_at: datetime | None,
) -> NetworkFeature:
    return NetworkFeature(
        network_type=network_type,
        geometry=geometry,
        source_metadata=SourceMetadata(
            source_id=KIUT_SOURCE_ID,
            source_name=KIUT_SOURCE_NAME,
            source_url=source_url,
            fetched_at=fetched_at,
            confidence=0.8 if network_type != "unknown" else 0.4,
            manual_review_required=(network_type == "unknown"),
        ),
        warning=warning,
    )


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
            try:
                values = [float(v) for v in child.text.split()]
            except ValueError as exc:
                raise _MalformedKiutResponse("niepoprawna lista współrzędnych") from exc
            if len(values) % 2:
                raise _MalformedKiutResponse("nieparzysta liczba współrzędnych")
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
