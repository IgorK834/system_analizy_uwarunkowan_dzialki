"""Wspólny parser odpowiedzi WFS (GML 3.2 / GeoJSON) dla geometrii powierzchniowych i punktowych.

Modułu nie ma po to, żeby „ładniej rozdzielić kod”. Powstał, bo dwa
bezpieczeństwo-krytyczne źródła (ISOK i GDOŚ) mają identyczne wymagania wobec
parsowania oraz identyczne pułapki, potwierdzone realnymi zapytaniami do usług
2026-07-30. Duplikowanie tej logiki w dwóch adapterach oznaczałoby, że poprawka
w jednym miejscu cicho nie działa w drugim.

Trzy ustalenia z realnych odpowiedzi, które wymuszają kształt tego parsera:

1. **HTTP 200 dla błędu.** Obie usługi zwracają ``ows:ExceptionReport`` ze
   statusem 200, więc ``raise_for_status()`` niczego nie wykryje. Taka odpowiedź
   jest poprawnym XML-em bez ``wfs:member``, czyli naiwny parser zwróciłby pustą
   listę cech. Dla źródeł, w których pusta lista znaczy „sprawdzono, brak
   kolizji”, byłoby to fałszywe poczucie bezpieczeństwa. Dlatego
   ``ExceptionReport`` jest rozpoznawany jawnie i podnosi
   ``OwsExceptionReportError``.

2. **Kolejność osi EPSG:2180 zależy od formy zapisu ``srsName``.** Forma URN
   (``urn:ogc:def:crs:EPSG::2180``) oraz forma HTTP
   (``http://www.opengis.net/def/crs/EPSG/0/2180``) wymuszają kolejność osi z
   rejestru EPSG, czyli dla 2180 ``(northing, easting)``. Skrót ``EPSG:2180``
   oznacza w praktyce kolejność „GIS”, czyli ``(easting, northing)``. Geometria
   kanoniczna systemu pochodzi z ULDK i jest w kolejności
   ``(easting, northing)`` — potwierdzone: ULDK dla ``xy=637000,486000,2180``
   zwraca działkę w Warszawie z WKT rozpoczynającym się od 637343 (easting).
   Bez zamiany osi ``parcel.intersects(zone)`` byłoby ZAWSZE fałszem, a sekcja
   raportowałaby brak kolizji.

3. **Pierścienie wewnętrzne są w tych danych normą, nie wyjątkiem.** Jedna cecha
   „Kampinoski Park Narodowy” ma 229 poligonów i 199 pierścieni wewnętrznych, a
   jedna cecha ``nz-core:HazardArea`` — kilkaset. Pominięcie dziur oznaczałoby
   uznanie działki leżącej w enklawie (wyłączonej z parku) za leżącą w parku.
   Dlatego ``gml:interior`` jest obsługiwany, a nie świadomie pomijany.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Final
from xml.etree import ElementTree

from pyproj import CRS, Transformer
from shapely.geometry import MultiPoint, MultiPolygon, Point, Polygon, shape as shapely_shape
from shapely.geometry.base import BaseGeometry

logger = logging.getLogger(__name__)

# Kanoniczny układ obliczeniowy systemu. Parser celowo nie reprojektuje —
# nieoczekiwany układ jest błędem kontraktu, a nie czymś do cichego naprawienia,
# bo milcząca reprojekcja ukryłaby zmianę zachowania usługi.
CANONICAL_EPSG_CODE: Final[int] = 2180

# Formy zapisu ``srsName``. Rozróżnienie jest istotne wyłącznie z powodu
# kolejności osi — kod EPSG jest w obu przypadkach ten sam.
_URN_CRS_RE: Final[re.Pattern[str]] = re.compile(r"^urn:ogc:def:crs:EPSG:[^:]*:(\d+)$", re.IGNORECASE)
_HTTP_CRS_RE: Final[re.Pattern[str]] = re.compile(
    r"^https?://(?:www\.)?opengis\.net/def/crs/EPSG/[^/]*/(\d+)$", re.IGNORECASE
)
_SHORT_CRS_RE: Final[re.Pattern[str]] = re.compile(r"^EPSG:+(\d+)$", re.IGNORECASE)
_LEGACY_GML_CRS_RE: Final[re.Pattern[str]] = re.compile(
    r"^https?://(?:www\.)?opengis\.net/gml/srs/epsg\.xml#(\d+)$",
    re.IGNORECASE,
)

# Elementy, poniżej których nie zbieramy atrybutów opisowych — inaczej listy
# współrzędnych i narożniki kopert trafiłyby do właściwości cechy.
_GEOMETRY_CONTAINERS: Final[frozenset[str]] = frozenset(
    {
        "Polygon",
        "MultiSurface",
        "MultiPolygon",
        "Surface",
        "Envelope",
        "LineString",
        "MultiLineString",
        "Curve",
        "Point",
        "MultiPoint",
        "boundedBy",
    }
)

_FEATURE_MEMBER_NAMES: Final[frozenset[str]] = frozenset({"member", "featureMember"})

# Minimalna liczba wierzchołków zamkniętego pierścienia w GML.
_MIN_RING_VERTICES: Final[int] = 4


class GmlResponseError(ValueError):
    """Odpowiedzi usługi nie da się zinterpretować jako kolekcji cech."""


class OwsExceptionReportError(GmlResponseError):
    """Usługa zwróciła ``ows:ExceptionReport`` (także ze statusem HTTP 200)."""


class UnsupportedCrsError(GmlResponseError):
    """Geometria jest w układzie innym niż kanoniczny EPSG:2180."""


@dataclass(frozen=True)
class GmlFeature:
    """Pojedyncza cecha powierzchniowa sprowadzona do konwencji systemu.

    ``layer`` to lokalna nazwa elementu cechy, czyli w praktyce nazwa warstwy
    WFS (np. ``ParkiNarodowe`` dla ``GDOS:ParkiNarodowe``). Dla źródeł, w których
    klasyfikacja wynika z odpytanej warstwy, a nie z atrybutów cechy, jest to
    jedyna wiarygodna informacja o rodzaju obiektu. Dla GeoJSON pozostaje pusta.

    ``geometry`` jest zawsze w kolejności ``(easting, northing)`` EPSG:2180.
    ``feature_id`` to unikalny identyfikator obiektu nadany przez źródło
    (``gml:id`` albo ``id`` GeoJSON) — pozwala nie liczyć tej samej cechy dwa
    razy i powiązać wynik z obiektem źródłowym. ``None``, gdy źródło go nie podało.
    """

    layer: str
    geometry: BaseGeometry
    properties: dict[str, str] = field(default_factory=dict)
    feature_id: str | None = None


def local_name(tag: str) -> str:
    """Zwraca lokalną nazwę tagu XML bez prefiksu przestrzeni nazw.

    Różne serwery WFS używają różnych prefiksów dla tej samej struktury GML,
    więc dopasowanie po nazwie lokalnej jest odporniejsze niż porównanie pełnego
    tagu z przestrzenią nazw.
    """
    return tag.split("}")[-1] if "}" in tag else tag


def parse_feature_collection(text: str, *, reproject: bool = False) -> list[GmlFeature]:
    """Parsuje odpowiedź WFS (GML albo GeoJSON) na listę cech powierzchniowych.

    Pusta lista oznacza poprawną odpowiedź bez cech. Każdy problem z odczytem —
    ``ows:ExceptionReport``, nieparsowalny XML/JSON, nieobsługiwany układ
    współrzędnych — podnosi ``GmlResponseError``, aby wywołujący adapter mógł
    odróżnić „sprawdzono, brak cech” od „nie udało się sprawdzić”.

    ``reproject=True`` (pliki GML aktów pobrane z Rejestru Urbanistycznego, ADR-026) przyjmuje jawnie zadeklarowane
    układy z listy ``REPROJECTABLE_EPSG`` — RU zapisuje akt w układzie strefy PL-2000 gminy (2176–2179) — i przelicza
    je do EPSG:2180 z uwzględnieniem kolejności osi z rejestru EPSG. Domyślnie (usługi ISOK/GDOŚ, WFS z 2180) parser
    nadal nie reprojektuje.
    """
    stripped = text.strip()
    if not stripped:
        return []
    if stripped.startswith("{"):
        return _parse_geojson(stripped)
    return _parse_gml(stripped, reproject=reproject)


# --- GML ---------------------------------------------------------------------


def _parse_gml(text: str, *, reproject: bool = False) -> list[GmlFeature]:
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise GmlResponseError(f"Odpowiedź nie jest poprawnym XML: {exc}") from exc

    _raise_if_exception_report(root)

    features: list[GmlFeature] = []
    transforms = _TransformCache() if reproject else None
    for member in root.iter():
        if local_name(member.tag) not in _FEATURE_MEMBER_NAMES:
            continue
        for feature_elem in list(member):
            geometry = _extract_geometry(feature_elem, None, transforms)
            if geometry is None:
                continue
            features.append(
                GmlFeature(
                    layer=local_name(feature_elem.tag),
                    geometry=geometry,
                    properties=_extract_properties(feature_elem),
                    feature_id=_feature_id(feature_elem),
                )
            )
    return features


def _feature_id(feature_elem: ElementTree.Element) -> str | None:
    """Zwraca ``gml:id`` cechy (niezależnie od prefiksu przestrzeni nazw)."""
    for key, value in feature_elem.attrib.items():
        if local_name(key) == "id" and value.strip():
            return value.strip()
    return None


def _raise_if_exception_report(root: ElementTree.Element) -> None:
    """Rozpoznaje raport wyjątku OWS zwrócony ze statusem HTTP 200."""
    if local_name(root.tag) != "ExceptionReport":
        return
    codes = [elem.get("exceptionCode", "") for elem in root.iter() if local_name(elem.tag) == "Exception"]
    texts = [
        (elem.text or "").strip()
        for elem in root.iter()
        if local_name(elem.tag) == "ExceptionText" and (elem.text or "").strip()
    ]
    detail = "; ".join(filter(None, [", ".join(filter(None, codes)), " ".join(texts)]))
    raise OwsExceptionReportError(f"Usługa zwróciła ows:ExceptionReport: {detail or 'brak szczegółów'}")


def _extract_geometry(
    elem: ElementTree.Element, inherited_srs: str | None, transforms: "_TransformCache | None" = None
) -> BaseGeometry | None:
    """Zbiera geometrię powierzchniową cechy, dziedziczą ``srsName`` w dół drzewa.

    Dziedziczenie jest konieczne, bo GDOŚ deklaruje ``srsName`` na
    ``gml:MultiSurface``, a ISOK na ``gml:Polygon``. ElementTree nie ma wskaźnika
    na rodzica, więc kolejność osi trzeba przekazywać podczas zejścia w dół.
    """
    polygons = _collect_polygons(elem, inherited_srs, transforms)
    if polygons:
        if len(polygons) == 1:
            return polygons[0]
        return MultiPolygon(polygons)

    points = _collect_points(elem, inherited_srs, transforms)
    if not points:
        return None
    if len(points) == 1:
        return points[0]
    return MultiPoint(points)


def _collect_points(
    elem: ElementTree.Element,
    inherited_srs: str | None,
    transforms: "_TransformCache | None" = None,
) -> list[Point]:
    """Zbiera ``gml:Point``/``gml:MultiPoint`` z zachowaniem reguł osi CRS."""
    srs = elem.get("srsName") or inherited_srs
    if local_name(elem.tag) == "Point":
        point = _parse_point(elem, srs, transforms)
        return [point] if point is not None else []

    points: list[Point] = []
    for child in elem:
        points.extend(_collect_points(child, srs, transforms))
    return points


def _parse_point(
    point_elem: ElementTree.Element,
    srs_name: str | None,
    transforms: "_TransformCache | None" = None,
) -> Point | None:
    swap_axes = False
    transform = None
    if transforms is not None:
        swap_axes, transform = transforms.resolve(srs_name)
    else:
        swap_axes = _requires_axis_swap(srs_name)

    for elem in point_elem.iter():
        if local_name(elem.tag) != "pos" or not elem.text:
            continue
        try:
            values = [float(value) for value in elem.text.split()]
        except ValueError as exc:
            raise GmlResponseError(f"Współrzędne gml:pos są nieliczbowe: {exc}") from exc
        if len(values) < 2:
            raise GmlResponseError("gml:pos punktu musi zawierać co najmniej dwie współrzędne.")
        x, y = (values[1], values[0]) if swap_axes else (values[0], values[1])
        if transform is not None:
            x, y = transform.transform(x, y)
        return Point(x, y)
    return None


def _collect_polygons(
    elem: ElementTree.Element, inherited_srs: str | None, transforms: "_TransformCache | None" = None
) -> list[Polygon]:
    srs = elem.get("srsName") or inherited_srs
    if local_name(elem.tag) == "Polygon":
        polygon = _parse_polygon(elem, srs, transforms)
        return [polygon] if polygon is not None else []

    polygons: list[Polygon] = []
    for child in elem:
        polygons.extend(_collect_polygons(child, srs, transforms))
    return polygons


def _parse_polygon(
    polygon_elem: ElementTree.Element, srs_name: str | None, transforms: "_TransformCache | None" = None
) -> Polygon | None:
    swap_axes = False
    transform = None
    if transforms is not None:
        swap_axes, transform = transforms.resolve(srs_name)
    else:
        swap_axes = _requires_axis_swap(srs_name)
    shell: list[tuple[float, float]] | None = None
    holes: list[list[tuple[float, float]]] = []

    for child in polygon_elem:
        role = local_name(child.tag)
        if role == "exterior":
            shell = _ring_coordinates(child, swap_axes, transform)
        elif role == "interior":
            hole = _ring_coordinates(child, swap_axes, transform)
            if hole is not None:
                holes.append(hole)

    if shell is None:
        return None
    return Polygon(shell, holes)


def _ring_coordinates(
    ring_parent: ElementTree.Element, swap_axes: bool, transform: "Transformer | None" = None
) -> list[tuple[float, float]] | None:
    for elem in ring_parent.iter():
        if local_name(elem.tag) != "posList" or not elem.text:
            continue
        try:
            values = [float(value) for value in elem.text.split()]
        except ValueError as exc:
            raise GmlResponseError(f"Lista współrzędnych gml:posList jest nieliczbowa: {exc}") from exc
        first, second = values[0::2], values[1::2]
        pairs = list(zip(second, first)) if swap_axes else list(zip(first, second))
        if transform is not None and pairs:
            easting, northing = transform.transform([x for x, _y in pairs], [y for _x, y in pairs])
            pairs = list(zip(easting, northing))
        return pairs if len(pairs) >= _MIN_RING_VERTICES else None
    return None


# Układy, które parser przyjmuje przy ``reproject=True``: PL-2000 (strefy 15°, 18°, 21°, 24°), PL-1992 (kanoniczny),
# ETRS89 geograficzny, WGS 84 i Web Mercator. Każdy inny kod jest błędem kontraktu, nie powodem do zgadywania.
REPROJECTABLE_EPSG: Final[frozenset[int]] = frozenset({2176, 2177, 2178, 2179, 2180, 4258, 4326, 3857})
_ANY_CRS_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:urn:ogc:def:crs:EPSG:[^:]*:(\d+)|https?://(?:www\.)?opengis\.net/def/crs/EPSG/[^/]*/(\d+)"
    r"|EPSG:+(\d+)|https?://(?:www\.)?opengis\.net/gml/srs/epsg\.xml#(\d+))$",
    re.IGNORECASE,
)


class _TransformCache:
    """Przeliczenie do EPSG:2180 per ``srsName`` (jeden ``Transformer`` na układ w obrębie jednego parsowania)."""

    def __init__(self) -> None:
        self._items: dict[str, tuple[bool, Transformer | None]] = {}

    def resolve(self, srs_name: str | None) -> tuple[bool, "Transformer | None"]:
        if not srs_name:
            raise UnsupportedCrsError("Geometria APP nie ma jawnego srsName; CRS nie będzie zgadywany.")
        key = srs_name.strip()
        cached = self._items.get(key)
        if cached is not None:
            return cached
        match = _ANY_CRS_RE.match(key)
        if match is None:
            raise UnsupportedCrsError(f"Nierozpoznany zapis układu współrzędnych srsName={srs_name!r}.")
        code = int(next(group for group in match.groups() if group))
        if code not in REPROJECTABLE_EPSG:
            raise UnsupportedCrsError(
                f"Geometria jest w EPSG:{code}, którego parser nie przyjmuje (dozwolone: {sorted(REPROJECTABLE_EPSG)})."
            )
        # Skrót ``EPSG:code`` oznacza kolejność „GIS” (x = wschód); formy URN i HTTP — kolejność z rejestru EPSG.
        short_form = key.upper().startswith("EPSG:")
        source = CRS.from_epsg(code)
        first_axis = source.axis_info[0].direction.lower()
        swap = (not short_form) and first_axis == "north"
        transformer = (
            None
            if code == CANONICAL_EPSG_CODE
            else Transformer.from_crs(source, CRS.from_epsg(CANONICAL_EPSG_CODE), always_xy=True)
        )
        self._items[key] = (swap, transformer)
        return swap, transformer


def _requires_axis_swap(srs_name: str | None) -> bool:
    """Czy współrzędne trzeba zamienić, aby uzyskać ``(easting, northing)``.

    Brak ``srsName`` traktujemy jako kolejność GIS bez zamiany — tak zapisane są
    proste, uproszczone odpowiedzi i taka interpretacja nie zmienia zachowania
    dla danych, które już są w kolejności kanonicznej.
    """
    if not srs_name:
        return False

    candidate = srs_name.strip()
    for pattern, swap in (
        (_URN_CRS_RE, True),
        (_HTTP_CRS_RE, True),
        (_SHORT_CRS_RE, False),
        # Oficjalny RU APP 3.0 używa starszej składni GML i praktycznej
        # kolejności GIS (easting, northing), potwierdzonej próbkami WFS.
        (_LEGACY_GML_CRS_RE, False),
    ):
        match = pattern.match(candidate)
        if match is None:
            continue
        code = int(match.group(1))
        if code != CANONICAL_EPSG_CODE:
            raise UnsupportedCrsError(
                f"Geometria jest w EPSG:{code}, a wymagany jest "
                f"EPSG:{CANONICAL_EPSG_CODE}. Parser nie reprojektuje, aby nie "
                "ukryć zmiany kontraktu usługi."
            )
        return swap

    raise UnsupportedCrsError(f"Nierozpoznany zapis układu współrzędnych srsName={srs_name!r}.")


def _extract_properties(feature_elem: ElementTree.Element) -> dict[str, str]:
    """Zbiera tekstowe atrybuty cechy, pomijając wnętrze geometrii.

    Zwraca wszystkie liście z niepustym tekstem, kluczowane lokalną nazwą.
    Adapter sam decyduje, które nazwy rozpoznaje — dzięki temu parser nie musi
    znać słownika pojęć konkretnego źródła.
    """
    properties: dict[str, str] = {}
    _collect_properties(feature_elem, properties)
    return properties


def _collect_properties(elem: ElementTree.Element, properties: dict[str, str]) -> None:
    for child in elem:
        name = local_name(child.tag)
        if name in _GEOMETRY_CONTAINERS:
            continue
        text = (child.text or "").strip()
        if len(child) == 0:
            if text:
                properties.setdefault(name, text)
            continue
        _collect_properties(child, properties)


# --- GeoJSON -----------------------------------------------------------------


def _parse_geojson(text: str) -> list[GmlFeature]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GmlResponseError(f"Odpowiedź nie jest poprawnym JSON: {exc}") from exc

    features: list[GmlFeature] = []
    for feature in data.get("features", []) or []:
        geometry_dict = feature.get("geometry")
        if not geometry_dict:
            continue
        geometry = shapely_shape(geometry_dict)
        if geometry.geom_type not in ("Polygon", "MultiPolygon", "Point", "MultiPoint"):
            logger.warning(
                "Pominięto nieobsługiwany typ geometrii GeoJSON: %s",
                geometry.geom_type,
            )
            continue
        properties = {key: str(value) for key, value in (feature.get("properties") or {}).items() if value is not None}
        raw_id = feature.get("id")
        features.append(
            GmlFeature(
                layer="",
                geometry=geometry,
                properties=properties,
                feature_id=str(raw_id) if raw_id not in (None, "") else None,
            )
        )
    return features
