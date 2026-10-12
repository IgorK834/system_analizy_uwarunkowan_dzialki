"""Adapter Rejestru Urbanistycznego (WFS ``app-mpzp``) dla aktów MPZP — kanał ``mpzp_ru`` (AU-101).

Adapter pobiera przez ``OgcClient`` (HTTPS, allowlista hostów, limity bajtów, stron i obiektów, ponowienia):

* ``app-mpzp:AktPlanowaniaPrzestrzennego`` z filtrem ``bbox`` — **kolejność osi dla URN EPSG:2180 to
  północ, wschód** (zweryfikowana 2026-10-05 i ponownie 2026-10-10 na żywej usłudze): zarówno parametr ``bbox``
  (``N_min,E_min,N_max,E_max,urn:ogc:def:crs:EPSG::2180``), jak i współrzędne ``gml:posList`` w odpowiedzi;
* ``app-mpzp:DokumentFormalny`` (numer uchwały, link do tekstu, daty, relacje) i
  ``app-mpzp:RysunekAktuPlanowaniaPrzestrzennego`` (GeoTIFF, legenda) po ``resourceId`` — obiekty, do których
  odwołuje się akt przez ``xlink:href``.

Paginacja: serwer zwraca strony po 10 obiektów, a ``OgcClient.fetch_wfs`` liczy ``startIndex`` z liczby już
pobranych obiektów. RU nie zawiera wydzieleń stref — adapter dostarcza wyłącznie granicę aktu, dokument i
rysunek (guard ``vector_zones`` w katalogu źródeł).

Adapter nie zgłasza wyjątków sieciowych: błąd źródła wraca jako ``RuActsResponse.error_code``, żeby wywołujący
nie mógł pomylić go z pustą odpowiedzią (``no_match``).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable, Sequence
from contextlib import contextmanager
from datetime import date, datetime
from typing import Final, Iterator
from xml.etree import ElementTree

import numpy as np
from shapely.geometry import MultiPolygon, Polygon

from app.core.ru_contracts import RuContractError, parse_xml_root
from app.modules.imports.composition import (
    OgcClient,
    OgcContractError,
    OgcError,
    OgcExceptionReportError,
    OgcLimitError,
    OgcTransportError,
)
from app.modules.planning.application.ports import (
    RuActsResponse,
    RuRelatedResponse,
)
from app.modules.planning.domain.kimpzp_discovery import parse_source_date
from app.modules.planning.domain.mpzp_acts import (
    RuActRecord,
    RuDocumentRecord,
    RuDrawingRecord,
    document_number,
)
from app.shared.provenance import Provenance

logger = logging.getLogger(__name__)

NS_APP: Final[str] = "https://www.gov.pl/static/zagospodarowanieprzestrzenne/schemas/app/3.0"
NS_GML: Final[str] = "http://www.opengis.net/gml/3.2"
NS_XLINK: Final[str] = "http://www.w3.org/1999/xlink"
NS_GMD: Final[str] = "http://www.isotc211.org/2005/gmd"
NS_GCO: Final[str] = "http://www.isotc211.org/2005/gco"

TYPE_ACT: Final[str] = "app-mpzp:AktPlanowaniaPrzestrzennego"
TYPE_DOCUMENT: Final[str] = "app-mpzp:DokumentFormalny"
TYPE_DRAWING: Final[str] = "app-mpzp:RysunekAktuPlanowaniaPrzestrzennego"
SRS_URN_2180: Final[str] = "urn:ogc:def:crs:EPSG::2180"

# Kody błędów kanału (mapowane przez serwis na ``unavailable``/``unknown``).
RU_TRANSPORT_ERROR: Final[str] = "MPZP_RU_TRANSPORT_ERROR"
RU_SERVICE_EXCEPTION: Final[str] = "MPZP_RU_SERVICE_EXCEPTION"
RU_CONTRACT_ERROR: Final[str] = "MPZP_RU_CONTRACT_ERROR"
RU_LIMIT_EXCEEDED: Final[str] = "MPZP_RU_LIMIT_EXCEEDED"
RU_PARTIAL_RESPONSE: Final[str] = "MPZP_RU_PARTIAL_RESPONSE"
RU_GEOMETRY_UNUSABLE: Final[str] = "MPZP_RU_GEOMETRY_UNUSABLE"
RU_GEOMETRY_AXIS_SUSPECT: Final[str] = "MPZP_RU_GEOMETRY_AXIS_SUSPECT"

# Dokumenty/rysunki pobierane po ``resourceId`` w paczkach (serwer RU zwraca do 10 obiektów na stronę).
_RESOURCE_CHUNK: Final[int] = 8
_VERSION_SEGMENT = re.compile(r"^\d{8}T\d{6}$")
_DOCUMENT_RELATIONS: Final[tuple[str, ...]] = (
    "uchwala",
    "przystapienie",
    "zmienia",
    "uchyla",
    "uniewaznia",
)
_ACT_DOCUMENT_ROLES: Final[tuple[str, ...]] = (
    "dokument",
    "dokumentPrzystepujacy",
    "dokumentUchwalajacy",
    "dokumentZmieniajacy",
    "dokumentUchylajacy",
    "dokumentUniewazniajacy",
)


def _q(namespace: str, name: str) -> str:
    return f"{{{namespace}}}{name}"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(element: ElementTree.Element | None, name: str, namespace: str = NS_APP) -> str | None:
    if element is None:
        return None
    child = element.find(_q(namespace, name))
    if child is None or child.text is None:
        return None
    value = child.text.strip()
    return value or None


def _date(value: str | None) -> date | None:
    return parse_source_date(value)


def _datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _href(element: ElementTree.Element) -> str | None:
    value = element.attrib.get(_q(NS_XLINK, "href"))
    return value.strip() if value and value.strip() else None


# --- identyfikatory ----------------------------------------------------------------------------------------


def reference_path(href: str | None, type_name: str) -> str | None:
    """Ścieżka identyfikatora z ``xlink:href`` (``…/<Typ>/<przestrzenNazw>/<lokalnyId>[/<wersja>]``)."""
    if not href or f"/{type_name}/" not in href:
        return None
    path = href.split(f"/{type_name}/", 1)[1].strip("/")
    return path or None


def without_version(path: str) -> str:
    parts = path.split("/")
    if parts and _VERSION_SEGMENT.match(parts[-1]):
        parts = parts[:-1]
    return "/".join(parts)


def gml_id_for(path: str) -> str:
    """``gml:id`` obiektu RU: segmenty identyfikatora połączone podkreśleniem (zweryfikowane na żywej usłudze)."""
    return path.replace("/", "_")


def _identifier(element: ElementTree.Element) -> tuple[str, str | None] | None:
    identifier = element.find(f"{_q(NS_APP, 'idIIP')}/{_q(NS_APP, 'Identyfikator')}")
    if identifier is None:
        return None
    namespace = _text(identifier, "przestrzenNazw")
    local = _text(identifier, "lokalnyId")
    if not namespace or not local:
        return None
    return f"{namespace}/{local}", _text(identifier, "wersjaId")


# --- geometria ---------------------------------------------------------------------------------------------


def _pos_values(element: ElementTree.Element, dimension: int) -> np.ndarray | None:
    pos_list = element.find(_q(NS_GML, "posList"))
    if pos_list is not None and pos_list.text:
        tokens = pos_list.text.split()
    else:
        tokens = [token for pos in element.findall(_q(NS_GML, "pos")) for token in (pos.text or "").split()]
    if not tokens or len(tokens) % dimension:
        return None
    try:
        values = np.asarray(tokens, dtype=float).reshape(-1, dimension)
    except ValueError:
        return None
    if not np.isfinite(values).all():
        return None
    return values[:, :2]


def _ring(element: ElementTree.Element | None, dimension: int, northing_first: bool) -> list[tuple[float, float]] | None:
    if element is None:
        return None
    linear = element.find(_q(NS_GML, "LinearRing"))
    if linear is None:
        return None
    values = _pos_values(linear, dimension)
    if values is None or len(values) < 4:
        return None
    if northing_first:
        values = values[:, ::-1]
    return [(float(x), float(y)) for x, y in values]


def _srs_dimension(element: ElementTree.Element, default: int = 2) -> int:
    raw = element.attrib.get("srsDimension")
    return int(raw) if raw and raw.isdigit() and int(raw) in {2, 3} else default


def parse_extent(
    zasieg: ElementTree.Element | None, *, northing_first: bool = True
) -> tuple[MultiPolygon | None, str | None]:
    """Zasięg przestrzenny aktu jako ``MultiPolygon`` w (wschód, północ) albo powód nieużyteczności.

    ``northing_first=True`` odpowiada kolejności osi, którą RU stosuje dla EPSG:2180 (północ, wschód); dane są
    odwracane do kolejności (x = wschód, y = północ) przyjętej w całym systemie.
    """
    if zasieg is None:
        return None, "missing"
    polygons: list[Polygon] = []
    for polygon in zasieg.iter():
        if _local(polygon.tag) not in {"Polygon", "PolygonPatch"}:
            continue
        dimension = _srs_dimension(polygon, _srs_dimension(zasieg))
        exterior = _ring(polygon.find(_q(NS_GML, "exterior")), dimension, northing_first)
        if exterior is None:
            continue
        holes = [
            ring
            for node in polygon.findall(_q(NS_GML, "interior"))
            if (ring := _ring(node, dimension, northing_first)) is not None
        ]
        polygons.append(Polygon(exterior, holes))
    if not polygons:
        return None, "unparseable"
    return MultiPolygon(polygons), None


def _bbox_axis_consistent(geometry: MultiPolygon, bbox: tuple[float, float, float, float]) -> bool:
    """Czy obwiednia zasięgu (po odwróceniu osi) przecina ``bbox`` zapytania w (wschód, północ).

    Serwer filtruje po obwiedni, więc akt zwrócony dla ``bbox`` ma obwiednię nachodzącą na ``bbox``, ale jego
    wielokąt wcale nie musi go dotykać (rzeczywisty przypadek: projekt planu Wzgórza Wawelskiego dla działki
    540/15 w Krakowie). Rozjazd obwiedni oznacza, że usługa zmieniła kolejność osi albo odpowiada innym układem —
    wtedy wynik odrzucenia aktu byłby fałszywym „brak przecięcia”.
    """
    min_x, min_y, max_x, max_y = bbox
    g_min_x, g_min_y, g_max_x, g_max_y = geometry.bounds
    return g_min_x <= max_x and g_max_x >= min_x and g_min_y <= max_y and g_max_y >= min_y


# --- parsery obiektów --------------------------------------------------------------------------------------


def parse_act(
    feature: bytes,
    *,
    request_bbox: tuple[float, float, float, float] | None = None,
    northing_first: bool = True,
) -> RuActRecord | None:
    """Parsuje ``AktPlanowaniaPrzestrzennego``; ``None`` dla elementu o innej strukturze."""
    root = parse_xml_root(feature)
    if _local(root.tag) != "AktPlanowaniaPrzestrzennego":
        return None
    parsed_id = _identifier(root)
    gml_id = root.attrib.get(_q(NS_GML, "id"))
    if parsed_id is None or not gml_id:
        return None
    identifier, version_id = parsed_id
    status = root.find(_q(NS_APP, "status"))
    plan_type = root.find(_q(NS_APP, "typPlanu"))
    geometry, issue = parse_extent(root.find(_q(NS_APP, "zasiegPrzestrzenny")), northing_first=northing_first)
    if geometry is not None and request_bbox is not None and not _bbox_axis_consistent(geometry, request_bbox):
        geometry, issue = None, "axis_suspect"
    refs: list[tuple[str, str]] = []
    for role in _ACT_DOCUMENT_ROLES:
        for node in root.findall(_q(NS_APP, role)):
            path = reference_path(_href(node), "DokumentFormalny")
            if path:
                refs.append((role, path))
    drawings = tuple(
        path
        for node in root.findall(_q(NS_APP, "rysunek"))
        if (path := reference_path(_href(node), "RysunekAktuPlanowaniaPrzestrzennego"))
    )
    amendment = _text(root, "zmiana")
    return RuActRecord(
        identifier=identifier,
        gml_id=gml_id,
        version_id=version_id,
        title=" ".join((_text(root, "tytul") or "").split()) or None,
        plan_type=plan_type.attrib.get(_q(NS_XLINK, "title")) if plan_type is not None else None,
        effective_from=_date(_text(root, "obowiazujeOd")),
        effective_to=_date(_text(root, "obowiazujeDo")),
        status_code=(_href(status) or "").rsplit("/", 1)[-1] or None if status is not None else None,
        status_title=status.attrib.get(_q(NS_XLINK, "title")) if status is not None else None,
        version_started=_datetime(_text(root, "poczatekWersjiObiektu")),
        version_ended=_datetime(_text(root, "koniecWersjiObiektu")),
        amendment_count=int(amendment) if amendment and amendment.isdigit() else None,
        geometry=geometry,
        geometry_issue=issue,
        document_refs=tuple(dict.fromkeys(refs)),
        drawing_refs=tuple(dict.fromkeys(drawings)),
    )


def parse_document(feature: bytes) -> RuDocumentRecord | None:
    root = parse_xml_root(feature)
    if _local(root.tag) != "DokumentFormalny":
        return None
    parsed_id = _identifier(root)
    if parsed_id is None:
        return None
    title = " ".join((_text(root, "tytul") or "").split()) or None
    number, basis = document_number(title, _text(root, "numerIdentyfikacyjny"))
    document_date = None
    date_node = root.find(f"{_q(NS_APP, 'data')}/{_q(NS_GMD, 'CI_Date')}/{_q(NS_GMD, 'date')}/{_q(NS_GCO, 'Date')}")
    if date_node is not None:
        document_date = _date(date_node.text)
    relations: list[tuple[str, str]] = []
    for relation in _DOCUMENT_RELATIONS:
        for node in root.findall(_q(NS_APP, relation)):
            path = reference_path(_href(node), "AktPlanowaniaPrzestrzennego")
            if path:
                relations.append((relation, without_version(path)))
    journal = root.find(_q(NS_APP, "dziennikUrzedowy"))
    return RuDocumentRecord(
        identifier=parsed_id[0],
        title=title,
        number=number,
        number_basis=basis,  # type: ignore[arg-type]
        document_date=document_date,
        effective_from=_date(_text(root, "dataWejsciaWZycie")),
        repealed_on=_date(_text(root, "dataUchylenia")),
        links=tuple(
            link.text.strip()
            for link in root.findall(_q(NS_APP, "lacze"))
            if link.text and link.text.strip()
        ),
        journal=journal.attrib.get(_q(NS_XLINK, "title")) if journal is not None else None,
        relations=tuple(dict.fromkeys(relations)),
    )


def parse_drawing(feature: bytes) -> RuDrawingRecord | None:
    root = parse_xml_root(feature)
    if _local(root.tag) != "RysunekAktuPlanowaniaPrzestrzennego":
        return None
    parsed_id = _identifier(root)
    if parsed_id is None:
        return None
    identifier, version = parsed_id
    scale = _text(root, "rozdzielczoscPrzestrzenna")
    return RuDrawingRecord(
        identifier=f"{identifier}/{version}" if version else identifier,
        title=" ".join((_text(root, "tytul") or "").split()) or None,
        link=_text(root, "lacze"),
        legend=_text(root, "legenda"),
        crs=_text(root, "ukladOdniesieniaPrzestrzennego"),
        scale_denominator=int(scale) if scale and scale.isdigit() else None,
        description=_text(root, "opis"),
    )


# --- adapter -----------------------------------------------------------------------------------------------


def _bbox_param(bbox: tuple[float, float, float, float]) -> str:
    """``bbox`` w kolejności osi RU dla URN EPSG:2180: północ, wschód (``N_min,E_min,N_max,E_max``)."""
    min_x, min_y, max_x, max_y = bbox
    return f"{min_y:.3f},{min_x:.3f},{max_y:.3f},{max_x:.3f},{SRS_URN_2180}"


def _error_code(error: OgcError) -> str:
    if isinstance(error, OgcLimitError):
        return RU_LIMIT_EXCEEDED
    if isinstance(error, OgcExceptionReportError):
        return RU_SERVICE_EXCEPTION
    if isinstance(error, OgcContractError):
        # ``ExceptionReport`` bywa zgłaszany przez ``OgcClient`` jako błąd kontraktu z treścią wyjątku.
        return RU_SERVICE_EXCEPTION if "ExceptionReport" in str(error) else RU_CONTRACT_ERROR
    if isinstance(error, OgcTransportError):
        return RU_TRANSPORT_ERROR
    return RU_CONTRACT_ERROR


class RuMpzpActsAdapter:
    """Adapter portu ``RuMpzpActsSource`` na ``OgcClient``.

    ``client_factory`` zwraca nowy klient dla każdego wywołania (klient jest synchroniczny i zamykany po
    użyciu); w testach wstrzykuje się klienta z ``respx``/``MockTransport``.
    """

    def __init__(self, *, wfs_url: str, client_factory: Callable[[], OgcClient]) -> None:
        self._wfs_url = wfs_url
        self._client_factory = client_factory

    @contextmanager
    def _client(self) -> Iterator[OgcClient]:
        client = self._client_factory()
        try:
            yield client
        finally:
            client.close()

    def fetch_acts(self, bbox: tuple[float, float, float, float]) -> RuActsResponse:
        """Akty, których zasięg przecina ``bbox`` (x_min, y_min, x_max, y_max w EPSG:2180, x = wschód)."""
        try:
            with self._client() as client:
                result = client.fetch_wfs(
                    self._wfs_url,
                    type_name=TYPE_ACT,
                    srs_name=SRS_URN_2180,
                    extra_params={"bbox": _bbox_param(bbox)},
                )
        except OgcError as exc:
            logger.warning("ru_mpzp_acts_fetch_failed", extra={"error": type(exc).__name__})
            return RuActsResponse(
                acts=(),
                complete=False,
                source=exc.source,
                error_code=_error_code(exc),
                error_message=str(exc),
            )
        acts: list[RuActRecord] = []
        issues: list[str] = []
        for feature in result.features:
            try:
                act = parse_act(feature, request_bbox=bbox)
            except RuContractError:
                issues.append(RU_CONTRACT_ERROR)
                continue
            if act is None:
                issues.append(RU_CONTRACT_ERROR)
                continue
            if act.geometry_issue == "axis_suspect":
                issues.append(RU_GEOMETRY_AXIS_SUSPECT)
            elif act.geometry_issue:
                issues.append(RU_GEOMETRY_UNUSABLE)
            acts.append(act)
        if not result.complete:
            issues.append(RU_PARTIAL_RESPONSE)
        acts.sort(key=lambda item: (item.identifier, item.version_id or ""))
        return RuActsResponse(
            acts=tuple(acts),
            complete=result.complete,
            source=result.source,
            issues=tuple(dict.fromkeys(issues)),
        )

    def fetch_related(
        self, document_paths: Sequence[str], drawing_paths: Sequence[str]
    ) -> RuRelatedResponse:
        """Dokumenty formalne i rysunki wskazane przez akty (po ``resourceId``)."""
        documents: dict[str, RuDocumentRecord] = {}
        drawings: dict[str, RuDrawingRecord] = {}
        provenance: list[Provenance] = []
        errors: list[str] = []
        try:
            with self._client() as client:
                self._collect(client, TYPE_DOCUMENT, document_paths, parse_document, documents, provenance)
                self._collect(client, TYPE_DRAWING, drawing_paths, parse_drawing, drawings, provenance)
        except OgcError as exc:
            logger.warning("ru_mpzp_related_fetch_failed", extra={"error": type(exc).__name__})
            errors.append(_error_code(exc))
            provenance.append(exc.source)
        except RuContractError:
            errors.append(RU_CONTRACT_ERROR)
        unresolved_documents = tuple(
            path for path in dict.fromkeys(document_paths) if without_version(path) not in documents
        )
        unresolved_drawings = tuple(
            path for path in dict.fromkeys(drawing_paths) if path not in drawings
        )
        return RuRelatedResponse(
            documents=documents,
            drawings=drawings,
            unresolved_documents=unresolved_documents,
            unresolved_drawings=unresolved_drawings,
            sources=tuple(provenance),
            error_codes=tuple(dict.fromkeys(errors)),
        )

    def _collect(
        self,
        client: OgcClient,
        type_name: str,
        paths: Iterable[str],
        parse: Callable[[bytes], object],
        into: dict,
        provenance: list[Provenance],
    ) -> None:
        unique = list(dict.fromkeys(paths))
        for start in range(0, len(unique), _RESOURCE_CHUNK):
            chunk = unique[start : start + _RESOURCE_CHUNK]
            result = client.fetch_wfs(
                self._wfs_url,
                type_name=type_name,
                srs_name=SRS_URN_2180,
                extra_params={"resourceId": ",".join(gml_id_for(path) for path in chunk)},
            )
            provenance.append(result.source)
            for feature in result.features:
                record = parse(feature)
                if record is not None:
                    into[record.identifier] = record  # type: ignore[attr-defined]
