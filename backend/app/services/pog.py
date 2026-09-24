"""Best-effort discovery Planu Ogólnego Gminy ze źródeł per gmina i RU.

Adresy gminne są przekazywane przez wywołującego, a potwierdzony krajowy WMS
Rejestru Urbanistycznego jest odczytywany wyłącznie z katalogu źródeł.

WMS służy wyłącznie do rozpoznania dostępności aktu i odnośników. Wynik nie
jest precyzyjną geometrią stref, OUZ ani obszaru zabudowy śródmiejskiej.

Status prawny (BK-106) pochodzi wyłącznie z urzędowego kodu: jawnego atrybutu
statusu albo nazwy warstwy RU kodującej krok procesu (np.
``APP.POG.SJ.PrawnieWiazacyLubRealizowany``). Obecność obiektu bez kodu daje
``unknown``, a pusta odpowiedź opisuje wyłącznie pokrycie, nie status aktu.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Final, Literal, Sequence
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from shapely.geometry.base import BaseGeometry

from app.core.data_sources import AccessType, CatalogError, ensure_source_runnable
from app.modules.imports.infrastructure.ogc_client import OgcClient, OgcError
from app.schemas.source import SourceMetadata, WarningMessage
from app.services.mpzp import _build_sample_points
from app.services.mpzp_fetch import (
    MpzpDocumentError,
    _fetch_with_redirect_validation,
)
from app.shared.planning_status import (
    LegalStatus,
    StatusEvidence,
    normalize_official_legal_status,
    official_status_code,
)

POG_DISCOVERY_TIMEOUT_S: Final[float] = 10.0
_WMS_PIXEL_SIZE_M: Final[float] = 1.0
_WMS_IMAGE_SIZE_PX: Final[int] = 2
_WMS_QUERY_PIXEL: Final[int] = 1
_WMS_FEATURE_COUNT: Final[int] = 10

# Stan odpowiedzi warstwy WMS; to fakt operacyjny/pokrycia, nie status prawny.
PogLayerAvailability = Literal["found", "empty", "unavailable"]
PogLogicalLayer = Literal[
    "planning_act",
    "downtown_area",
    "ouz",
    "planning_zones",
]


@dataclass(frozen=True)
class PogLayerNames:
    """Konfigurowalne kandydaty nazw warstw logicznych gminnego WMS."""

    planning_act: tuple[str, ...] = (
        "aktPlanowaniaprzestrzennego",
        "akt_planowania_przestrzennego",
        "AktPlanowaniaPrzestrzennego",
        "pog",
    )
    downtown_area: tuple[str, ...] = (
        "obszarZabSrodmiejskiej",
        "obszar_zabudowy_srodmiejskiej",
        "ObszarZabudowySrodmiejskiej",
    )
    ouz: tuple[str, ...] = (
        "obszarUzupelnieniaZabudowy",
        "obszar_uzupelnienia_zabudowy",
        "ObszarUzupelnieniaZabudowy",
        "ouz",
    )
    planning_zones: tuple[str, ...] = (
        "strefaPlanistyczna",
        "strefa_planistyczna",
        "StrefaPlanistyczna",
        "strefy_planistyczne",
    )

    def for_logical_layer(self, logical_layer: PogLogicalLayer) -> tuple[str, ...]:
        """Zwraca kandydatów WMS dla wskazanej warstwy logicznej."""
        return getattr(self, logical_layer)


@dataclass(frozen=True)
class PogGminaSources:
    """Znane źródła POG jednej gminy, przekazywane jawnie przez wywołującego."""

    wms_url: str | None = None
    bip_url: str | None = None
    teryt: str | None = None
    layer_names: PogLayerNames = field(default_factory=PogLayerNames)


@dataclass(frozen=True)
class PogLayerSection:
    """Status jednej z czterech wymaganych warstw logicznych POG."""

    logical_layer: PogLogicalLayer
    status: PogLayerAvailability
    matched_wms_layer: str | None = None
    feature_count: int = 0
    raw_legal_status: str | None = None


@dataclass(frozen=True)
class PogDiscoveryResult:
    """Wynik rozpoznania POG, gotowy do mapowania na kontrakt API.

    ``is_discovery_only`` zawsze ma wartość True. Geometrie z WMS nie są
    używane do obliczeń; do analizy powierzchniowej wymagane są wektory APP/GML.
    ``legal_status`` jest ustalany wyłącznie z urzędowego kodu statusu, a
    ``source_responded`` odróżnia pustą odpowiedź od awarii źródła.
    """

    legal_status: LegalStatus
    uchwala_nr: str | None
    uchwala_date: str | None
    links: list[str]
    planning_act: PogLayerSection
    downtown_area: PogLayerSection
    ouz: PogLayerSection
    planning_zones: PogLayerSection
    is_discovery_only: bool
    source_metadata: SourceMetadata
    warnings: list[WarningMessage] = field(default_factory=list)
    source_responded: bool = False
    raw_legal_status: str | None = None
    legal_evidence: StatusEvidence | None = None

    @property
    def status(self) -> LegalStatus:
        """Zgodność wsteczna: ``status`` jest aliasem ``legal_status``."""
        return self.legal_status

    @property
    def act_found(self) -> bool:
        return self.planning_act.status == "found"

    @property
    def preview_features_found(self) -> int:
        return sum(
            section.feature_count
            for section in (self.planning_zones, self.ouz, self.downtown_area)
            if section.status == "found"
        )


@dataclass(frozen=True)
class _WmsFeatureInfo:
    raw_legal_status: str | None
    properties: dict[str, object]
    links: tuple[str, ...]
    feature_count: int


_LOGICAL_LAYERS: Final[tuple[PogLogicalLayer, ...]] = (
    "planning_act",
    "downtown_area",
    "ouz",
    "planning_zones",
)
_KNOWN_RESOLUTION_ATTRIBUTES: Final[tuple[str, ...]] = (
    "uchwala_nr",
    "numer_uchwaly",
    "nr_uchwaly",
    "uchwala",
)
_KNOWN_RESOLUTION_DATE_ATTRIBUTES: Final[tuple[str, ...]] = (
    "uchwala_date",
    "data_uchwaly",
    "data_uchwalenia",
)
_KNOWN_STATUS_ATTRIBUTES: Final[tuple[str, ...]] = (
    "status",
    "status_aktu",
    "etap",
)
_KNOWN_LINK_ATTRIBUTES: Final[tuple[str, ...]] = (
    "url",
    "link",
    "app_url",
    "gml_url",
    "geojson_url",
    "zip_url",
    "uchwala_url",
)
_APP_LINK_SUFFIXES: Final[tuple[str, ...]] = (".gml", ".geojson", ".json", ".zip")


async def discover_pog(
    parcel_geometry: BaseGeometry,
    gmina_sources: PogGminaSources | Sequence[PogGminaSources] | None,
) -> PogDiscoveryResult:
    """Rozpoznaje POG dla działki w EPSG:2180 ze źródeł konkretnej gminy.

    Funkcja próbkuje co najmniej centroid i punkt reprezentatywny, a dla dużych
    lub wieloczęściowych działek także dalsze punkty. Gminny WMS jest wyłącznie
    warstwą discovery: wynik nie przesądza powierzchniowego położenia działki
    w strefie ani OUZ. POG jest aktem wpływającym na nowe planowanie i decyzje
    WZ; jego relacji z MPZP nie rozstrzygamy automatycznie.

    Brak skonfigurowanego źródła uruchamia potwierdzony w katalogu Rejestr
    Urbanistyczny. Niedostępność źródeł daje ``unknown`` z
    ``source_responded=False`` i wymaga ręcznej weryfikacji, zamiast być
    interpretowana jako brak POG.
    """
    sources = _normalize_sources(gmina_sources)
    configured_sources = [
        source for source in sources if source.wms_url or source.bip_url
    ]

    if not configured_sources:
        fallback_teryt = next(
            (source.teryt for source in sources if source.teryt), None
        )
        return await _discover_from_catalog(parcel_geometry, fallback_teryt)

    partial_results = [
        await _discover_from_gmina_source(parcel_geometry, source)
        for source in configured_sources
    ]
    return _merge_discovery_results(partial_results)


def _normalize_sources(
    gmina_sources: PogGminaSources | Sequence[PogGminaSources] | None,
) -> list[PogGminaSources]:
    if gmina_sources is None:
        return []
    if isinstance(gmina_sources, PogGminaSources):
        return [gmina_sources]
    return list(gmina_sources)


async def _discover_from_gmina_source(
    parcel_geometry: BaseGeometry,
    source: PogGminaSources,
) -> PogDiscoveryResult:
    if source.wms_url:
        result = await _discover_from_wms(parcel_geometry, source)
        if source.bip_url and source.bip_url not in result.links:
            result.links.append(source.bip_url)
        return result
    if source.bip_url:
        return await _discover_from_bip(source.bip_url)
    return _unknown_result(None, "POG_GMINA")


async def _discover_from_wms(
    parcel_geometry: BaseGeometry,
    source: PogGminaSources,
    *,
    source_name: str = "POG_GMINA_WMS",
) -> PogDiscoveryResult:
    assert source.wms_url is not None
    fetched_at = datetime.now(timezone.utc)
    try:
        client = OgcClient.for_urls(
            source_id="pog_app",
            urls=(source.wms_url,),
            config_overrides={
                "connect_timeout_seconds": 3.0,
                "read_timeout_seconds": POG_DISCOVERY_TIMEOUT_S,
                "total_timeout_seconds": POG_DISCOVERY_TIMEOUT_S,
                "max_response_bytes": 2 * 1024 * 1024,
                "retries": 1,
            },
        )
    except ValueError:
        return _unknown_result(
            source.wms_url,
            "POG_GMINA_WMS",
            code="POG_SOURCE_BLOCKED",
            message="Adres gminnego WMS POG został odrzucony przez reguły bezpieczeństwa.",
        )

    sample_points = _build_sample_points(parcel_geometry)
    with client:
        sections_and_info = await asyncio.gather(
            *(
                _discover_logical_layer(
                    client,
                    source.wms_url,
                    logical_layer,
                    source.layer_names.for_logical_layer(logical_layer),
                    sample_points,
                )
                for logical_layer in _LOGICAL_LAYERS
            )
        )

    sections = {section.logical_layer: section for section, _ in sections_and_info}
    infos = [info for _, info in sections_and_info if info is not None]
    responded = any(section.status != "unavailable" for section in sections.values())
    raw_status = _first_raw_status(sections)
    legal_status = normalize_official_legal_status(raw_status)
    properties = [info.properties for info in infos]
    links = list(dict.fromkeys(link for info in infos for link in info.links))
    warnings: list[WarningMessage] = []
    if not responded:
        warnings.append(
            _warning(
                "POG_WMS_UNAVAILABLE",
                "Nie udało się potwierdzić danych POG w skonfigurowanym WMS gminy.",
                "error",
            )
        )
    elif not any(section.status == "found" for section in sections.values()):
        warnings.append(
            _warning(
                "POG_NOT_FOUND_IN_SOURCE",
                "Usługa WMS odpowiedziała, ale nie zwróciła obiektów POG dla "
                "próbkowanych punktów. Pusta odpowiedź nie oznacza braku planu.",
                "warning",
            )
        )
    elif raw_status is None or official_status_code(raw_status) is None:
        warnings.append(
            _warning(
                "POG_STATUS_NOT_OFFICIAL",
                "Obiekty POG nie niosą urzędowego kodu statusu; status prawny pozostaje nieustalony.",
                "warning",
            )
        )
    warnings.append(_discovery_only_warning())

    evidence = (
        StatusEvidence(
            source_name=source_name,
            official=True,
            reference=source.wms_url,
            raw_value=raw_status,
            confirmed_at=fetched_at,
        )
        if official_status_code(raw_status) is not None
        else None
    )
    return PogDiscoveryResult(
        legal_status=legal_status,
        uchwala_nr=_first_attribute_from_many(properties, _KNOWN_RESOLUTION_ATTRIBUTES),
        uchwala_date=_first_attribute_from_many(
            properties, _KNOWN_RESOLUTION_DATE_ATTRIBUTES
        ),
        links=links,
        planning_act=sections["planning_act"],
        downtown_area=sections["downtown_area"],
        ouz=sections["ouz"],
        planning_zones=sections["planning_zones"],
        is_discovery_only=True,
        source_metadata=SourceMetadata(
            source_name=source_name,
            source_url=source.wms_url,
            fetched_at=fetched_at,
            confidence=0.65 if legal_status != "unknown" else 0.3,
            manual_review_required=True,
        ),
        warnings=warnings,
        source_responded=responded,
        raw_legal_status=raw_status,
        legal_evidence=evidence,
    )


def _first_raw_status(
    sections: dict[PogLogicalLayer, PogLayerSection],
) -> str | None:
    """Kod statusu aktu ma pierwszeństwo przed kodem obiektów stref."""
    for name in ("planning_act", "planning_zones", "ouz", "downtown_area"):
        raw = sections[name].raw_legal_status  # type: ignore[index]
        if raw and official_status_code(raw) is not None:
            return raw
    return None


async def _discover_logical_layer(
    client: OgcClient,
    wms_url: str,
    logical_layer: PogLogicalLayer,
    candidates: tuple[str, ...],
    sample_points: list[tuple[float, float]],
) -> tuple[PogLayerSection, _WmsFeatureInfo | None]:
    saw_valid_response = False
    best_info: _WmsFeatureInfo | None = None
    matched_layer: str | None = None

    for candidate in candidates:
        point_results = await asyncio.gather(
            *(
                _query_wms_point(client, wms_url, candidate, x, y)
                for x, y in sample_points
            )
        )
        valid_results = [result for result in point_results if result is not None]
        if valid_results:
            saw_valid_response = True
        feature_results = [result for result in valid_results if result.feature_count]
        if feature_results:
            best_info = _merge_wms_infos(feature_results)
            matched_layer = candidate
            break

    status: PogLayerAvailability
    raw_status: str | None = None
    if best_info is not None:
        status = "found"
        feature_count = best_info.feature_count
        # Jawny atrybut statusu ma pierwszeństwo; nazwa warstwy RU koduje ten
        # sam urzędowy krok procesu (np. ...PrawnieWiazacyLubRealizowany).
        raw_status = best_info.raw_legal_status or (
            matched_layer if official_status_code(matched_layer) else None
        )
    elif saw_valid_response:
        status = "empty"
        feature_count = 0
    else:
        status = "unavailable"
        feature_count = 0
    return (
        PogLayerSection(
            logical_layer=logical_layer,
            status=status,
            matched_wms_layer=matched_layer,
            feature_count=feature_count,
            raw_legal_status=raw_status,
        ),
        best_info,
    )


async def _query_wms_point(
    client: OgcClient,
    wms_url: str,
    layer_name: str,
    x: float,
    y: float,
) -> _WmsFeatureInfo | None:
    try:
        half_pixel = _WMS_PIXEL_SIZE_M / 2
        response = await asyncio.to_thread(
            client.fetch_wms_feature_info,
            wms_url,
            layer=layer_name,
            bbox=(
                x - half_pixel,
                y - half_pixel,
                x + half_pixel,
                y + half_pixel,
            ),
            crs="EPSG:2180",
            width=_WMS_IMAGE_SIZE_PX,
            height=_WMS_IMAGE_SIZE_PX,
            i=_WMS_QUERY_PIXEL,
            j=_WMS_QUERY_PIXEL,
            feature_count=_WMS_FEATURE_COUNT,
        )
        return _parse_wms_response(response.artifact.decode("utf-8"))
    except (OgcError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _build_get_feature_info_params(
    layer_name: str,
    x: float,
    y: float,
) -> dict[str, str]:
    half_pixel = _WMS_PIXEL_SIZE_M / 2
    return {
        "service": "WMS",
        "version": "1.3.0",
        "request": "GetFeatureInfo",
        "layers": layer_name,
        "query_layers": layer_name,
        "crs": "EPSG:2180",
        "bbox": f"{x - half_pixel},{y - half_pixel},{x + half_pixel},{y + half_pixel}",
        "width": str(_WMS_IMAGE_SIZE_PX),
        "height": str(_WMS_IMAGE_SIZE_PX),
        "i": str(_WMS_QUERY_PIXEL),
        "j": str(_WMS_QUERY_PIXEL),
        "info_format": "application/json",
        "feature_count": str(_WMS_FEATURE_COUNT),
    }


def _parse_wms_response(text: str) -> _WmsFeatureInfo:
    stripped = text.strip()
    if not stripped:
        return _WmsFeatureInfo(None, {}, (), 0)
    data = json.loads(stripped)
    if not isinstance(data, dict):
        raise TypeError("Odpowiedź GetFeatureInfo nie jest obiektem JSON.")
    features = data.get("features", [])
    if not isinstance(features, list):
        raise TypeError("Pole features nie jest listą.")
    if not features:
        return _WmsFeatureInfo(None, {}, (), 0)

    properties: dict[str, object] = {}
    links: list[str] = []
    statuses: list[str] = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        feature_properties = feature.get("properties", {}) or {}
        if not isinstance(feature_properties, dict):
            continue
        properties.update(feature_properties)
        if raw := _first_attribute(feature_properties, _KNOWN_STATUS_ATTRIBUTES):
            statuses.append(raw)
        links.extend(_links_from_properties(feature_properties))
    return _WmsFeatureInfo(
        raw_legal_status=_preferred_raw_status(statuses),
        properties=properties,
        links=tuple(dict.fromkeys(links)),
        feature_count=len(features),
    )


def _merge_wms_infos(infos: list[_WmsFeatureInfo]) -> _WmsFeatureInfo:
    properties: dict[str, object] = {}
    for info in infos:
        properties.update(info.properties)
    return _WmsFeatureInfo(
        raw_legal_status=_preferred_raw_status(
            [info.raw_legal_status for info in infos if info.raw_legal_status]
        ),
        properties=properties,
        links=tuple(dict.fromkeys(link for info in infos for link in info.links)),
        feature_count=sum(info.feature_count for info in infos),
    )


async def _discover_from_bip(bip_url: str) -> PogDiscoveryResult:
    fetched_at = datetime.now(timezone.utc)
    try:
        async with httpx.AsyncClient(
            timeout=POG_DISCOVERY_TIMEOUT_S,
            follow_redirects=False,
        ) as client:
            content, content_type, final_url = await _fetch_with_redirect_validation(
                client, bip_url
            )
    except MpzpDocumentError:
        return _unknown_result(
            bip_url,
            "POG_GMINA_BIP",
            code="POG_BIP_UNAVAILABLE",
            message="Nie udało się zweryfikować strony BIP gminy z danymi POG.",
        )

    links: list[str] = []
    if content_type == "text/html":
        soup = BeautifulSoup(content, "html.parser")
        for anchor in soup.find_all("a", href=True):
            href = str(anchor["href"]).strip()
            path = href.lower().split("?", maxsplit=1)[0]
            if path.endswith(_APP_LINK_SUFFIXES):
                links.append(urljoin(final_url, href))
    elif final_url.lower().split("?", maxsplit=1)[0].endswith(_APP_LINK_SUFFIXES):
        # Pole bip_url może wskazywać bezpośrednio na urzędowy artefakt APP,
        # a nie tylko stronę HTML. Zachowujemy taki URL dla Task 5.2 nawet bez
        # heurystycznego wnioskowania o formalnym statusie aktu.
        links.append(final_url)

    sections = _empty_sections("unavailable")
    sections["planning_act"] = PogLayerSection(
        "planning_act", "found" if links else "empty"
    )
    warnings = [
        _warning(
            "POG_BIP_DISCOVERY_LIMITED",
            "Strona BIP pozwala rozpoznać odnośniki, ale nie jest źródłem statusu "
            "prawnego ani geometrii POG; wymagana weryfikacja w Rejestrze Urbanistycznym.",
            "warning",
        ),
        _discovery_only_warning(),
    ]
    # Tekst strony (np. słowo „uchwalony”) nie jest urzędowym kodem statusu —
    # BIP służy wyłącznie do odnalezienia odnośników.
    return PogDiscoveryResult(
        legal_status="unknown",
        uchwala_nr=None,
        uchwala_date=None,
        links=list(dict.fromkeys(links)),
        planning_act=sections["planning_act"],
        downtown_area=sections["downtown_area"],
        ouz=sections["ouz"],
        planning_zones=sections["planning_zones"],
        is_discovery_only=True,
        source_metadata=SourceMetadata(
            source_name="POG_GMINA_BIP",
            source_url=final_url,
            fetched_at=fetched_at,
            response_status=200,
            confidence=0.25,
            manual_review_required=True,
        ),
        warnings=warnings,
        source_responded=True,
    )


async def _discover_from_catalog(
    parcel_geometry: BaseGeometry,
    teryt: str | None,
) -> PogDiscoveryResult:
    """Uruchamia krajowy WMS wyłącznie po przejściu guardu katalogu."""

    try:
        source = ensure_source_runnable("pog_app")
        resource = next(
            item
            for item in source.resources
            if item.role == "ru_wms_preview" and item.access_type is AccessType.WMS
        )
    except (CatalogError, StopIteration):
        return _unknown_result(
            None,
            "REJESTR_URBANISTYCZNY",
            code="POG_NO_CONFIRMED_SOURCE",
            message=(
                "Katalog nie udostępnia potwierdzonego kanału RU. "
                "Nie oznacza to braku POG."
            ),
        )

    layer_names = tuple(resource.layers)
    catalog_source = PogGminaSources(
        wms_url=resource.url,
        teryt=teryt,
        layer_names=PogLayerNames(
            planning_act=tuple(layer for layer in layer_names if layer.count(".") == 2),
            downtown_area=tuple(
                layer
                for layer in layer_names
                if ".ObszarZabudowySrodmiejskiej." in layer
            ),
            ouz=tuple(
                layer
                for layer in layer_names
                if ".ObszarUzupelnieniaZabudowy." in layer
            ),
            planning_zones=tuple(
                layer
                for layer in layer_names
                if layer.startswith("APP.POG.S") and ".ObszarStandardow" not in layer
            ),
        ),
    )
    result = await _discover_from_wms(
        parcel_geometry, catalog_source, source_name="REJESTR_URBANISTYCZNY_WMS"
    )
    result.warnings.insert(
        0,
        _warning(
            "POG_RU_CATALOG_SOURCE",
            "Discovery użyło potwierdzonego endpointu WMS z katalogu źródeł.",
            "info",
            source_name="Rejestr Urbanistyczny",
        ),
    )
    return result


_LEGAL_RANK: Final[dict[str, int]] = {
    "unknown": 0,
    "superseded": 1,
    "in_progress": 2,
    "project": 3,
    "binding": 4,
}


def _merge_discovery_results(results: list[PogDiscoveryResult]) -> PogDiscoveryResult:
    best = max(
        results,
        key=lambda result: (
            result.legal_evidence is not None,
            _LEGAL_RANK[result.legal_status],
            result.source_responded,
        ),
    )
    best.links[:] = list(
        dict.fromkeys(link for result in results for link in result.links)
    )
    best.warnings[:] = [warning for result in results for warning in result.warnings]
    return best


def _preferred_raw_status(values: Sequence[str | None]) -> str | None:
    """Wybiera urzędowy kod o najwyższej randze; nierozpoznane kody na końcu."""
    official = [value for value in values if value and official_status_code(value)]
    if official:
        return max(official, key=lambda value: _LEGAL_RANK[official_status_code(value) or "unknown"])
    return next((value for value in values if value), None)


def _links_from_properties(properties: dict[str, object]) -> list[str]:
    links: list[str] = []
    for key, value in properties.items():
        if key.lower() not in _KNOWN_LINK_ATTRIBUTES:
            continue
        if isinstance(value, str) and value.strip().startswith(("http://", "https://")):
            links.append(value.strip())
        elif isinstance(value, list):
            links.extend(
                item.strip()
                for item in value
                if isinstance(item, str)
                and item.strip().startswith(("http://", "https://"))
            )
    return links


def _first_attribute(
    properties: dict[str, object],
    candidates: tuple[str, ...],
) -> str | None:
    for candidate in candidates:
        for key, value in properties.items():
            if key.lower() == candidate and value is not None:
                text = str(value).strip()
                if text:
                    return text
    return None


def _first_attribute_from_many(
    properties_list: list[dict[str, object]],
    candidates: tuple[str, ...],
) -> str | None:
    for properties in properties_list:
        value = _first_attribute(properties, candidates)
        if value:
            return value
    return None


def _empty_sections(
    status: PogLayerAvailability,
) -> dict[PogLogicalLayer, PogLayerSection]:
    return {
        logical_layer: PogLayerSection(logical_layer, status)
        for logical_layer in _LOGICAL_LAYERS
    }


def _unknown_result(
    source_url: str | None,
    source_name: str,
    *,
    code: str = "POG_SOURCE_UNAVAILABLE",
    message: str = "Nie udało się ustalić statusu POG; wynik wymaga ręcznej weryfikacji.",
) -> PogDiscoveryResult:
    sections = _empty_sections("unavailable")
    return PogDiscoveryResult(
        legal_status="unknown",
        uchwala_nr=None,
        uchwala_date=None,
        links=[],
        planning_act=sections["planning_act"],
        downtown_area=sections["downtown_area"],
        ouz=sections["ouz"],
        planning_zones=sections["planning_zones"],
        is_discovery_only=True,
        source_metadata=SourceMetadata(
            source_name=source_name,
            source_url=source_url,
            fetched_at=datetime.now(timezone.utc),
            confidence=0.0,
            manual_review_required=True,
        ),
        warnings=[_warning(code, message, "error"), _discovery_only_warning()],
    )


def _warning(
    code: str,
    message: str,
    severity: Literal["info", "warning", "error"],
    *,
    source_name: str = "pog",
) -> WarningMessage:
    return WarningMessage(
        code=code,
        message=message,
        severity=severity,
        source_name=source_name,
    )


def _discovery_only_warning() -> WarningMessage:
    return _warning(
        "POG_DISCOVERY_ONLY",
        "WMS/BIP służy tylko do rozpoznania POG; obliczenia wymagają danych wektorowych APP/GML.",
        "info",
    )
