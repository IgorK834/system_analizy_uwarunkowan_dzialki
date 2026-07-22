"""Deterministyczny generator miniatury mapy PNG dla raportu PDF.

Ścieżka MVP rysuje geometrie na jednolitym tle (``MAP_BACKGROUND_RGB``) bez
pobierania kafelków — raport nie zależy wtedy od sieci. Ścieżka rozszerzona
opcjonalnie nakłada podkład z synchronicznego WMS GetMap (``report_map_basemap``);
przy aktywnym podkładzie wektory używają tej samej afinicznej projekcji co raster
(liniowe mapowanie BBOX→piksele, bez letterboxu rozjeżdżającego obrys).

TODO (ADR-009): PDF offline ze snapshotu; podkład WMS to świadomy wyjątek UX.
Awaria WMS nigdy nie blokuje raportu — fallback MVP + ostrzeżenie po polsku.

Wejściowe geometrie są w WGS84 (EPSG:4326), tak jak w odpowiedzi ``/analyze``.
W ścieżce MVP do rzutowania stosujemy skalowanie równopostaciowe z korektą
``cos(lat)`` na osi długości geograficznej. To wizualizacja, nie warstwa
obliczeniowa — pól powierzchni tu nie liczymy (zgodnie z regułą GIS: pola liczy
się w EPSG:2180 po stronie analizy).
"""

from __future__ import annotations

import base64
import io
import logging
import math
from dataclasses import dataclass, field
from typing import Any

from PIL import Image, ImageDraw

from app.core.report_config import (
    BUILDABLE_AREA_LAYER_STYLE,
    MAP_BACKGROUND_RGB,
    MAP_BBOX_EXPANSION_RATIO,
    MAP_IMAGE_HEIGHT,
    MAP_IMAGE_PADDING_PX,
    MAP_IMAGE_WIDTH,
    NETWORK_LAYER_STYLE,
    PARCEL_LAYER_STYLE,
    PROTECTION_ZONE_LAYER_STYLE,
    RISK_LAYER_STYLE,
    MapLayerStyle,
)
from app.core.settings import settings
from app.services.report_map_basemap import (
    BasemapLayout,
    compute_basemap_layout,
    fetch_report_basemap_png,
)

logger = logging.getLogger(__name__)

BASEMAP_FAILURE_WARNING: str = (
    "Nie udało się pobrać podkładu mapowego. "
    "Miniatura pokazuje tylko geometrię działki."
)

# Minimalny rozmiar rzutowanego BBOX w stopniach, gdy geometria jest punktem
# albo bardzo małym obiektem. Bez tego skala byłaby nieskończona.
_MIN_SPAN_DEGREES = 1e-5


@dataclass
class MapRenderResult:
    """Wynik renderowania miniatury mapy dla raportu PDF."""

    png_bytes: bytes | None
    basemap_used: bool = False
    warning: str | None = None


@dataclass
class _Primitives:
    """Prymitywy geometryczne wyodrębnione z jednego GeoJSON.

    Poligony przechowujemy jako pary (obrys zewnętrzny, lista otworów), aby
    miniatura poprawnie renderowała działki z otworami — nie zakładamy, że
    działka jest pełnym wielokątem bez dziur.
    """

    polygons: list[tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]] = (
        field(default_factory=list)
    )
    lines: list[list[tuple[float, float]]] = field(default_factory=list)
    points: list[tuple[float, float]] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.polygons or self.lines or self.points)


@dataclass
class _MapLayer:
    """Warstwa miniatury: styl domenowy plus jej prymitywy geometryczne."""

    style: MapLayerStyle
    primitives: list[_Primitives] = field(default_factory=list)

    def is_empty(self) -> bool:
        return all(primitive.is_empty() for primitive in self.primitives)


def render_analysis_map_png(
    response: Any,
    *,
    width: int = MAP_IMAGE_WIDTH,
    height: int = MAP_IMAGE_HEIGHT,
) -> MapRenderResult:
    """Renderuje miniaturę mapy działki i warstw analizy jako PNG.

    ``response`` to ``AnalyzeResponse`` (przyjmowany strukturalnie, aby moduł
    był testowalny bez bazy danych). Zwraca ``MapRenderResult`` z bajtami PNG
    albo ``png_bytes=None``, gdy w wyniku nie ma geometrii do narysowania.
    Opcjonalny podkład WMS nie blokuje raportu — przy awarii jest fallback MVP
    i ``warning`` po polsku.
    """
    layers = _collect_map_layers(response)
    drawable = [layer for layer in layers if not layer.is_empty()]
    if not drawable:
        return MapRenderResult(png_bytes=None)

    # Kadr raportu opisuje wybraną działkę, nie zasięg wszystkich danych
    # kontekstowych. Zewnętrzne warstwy (np. długa sieć albo rozległy obszar
    # ochronny) mogą wychodzić daleko poza działkę; nie mogą przez to zmniejszać
    # jej do kilku pikseli. Gdy obrys działki jest dostępny, wyznaczamy BBOX
    # wyłącznie z niego, a pozostałe warstwy Pillow naturalnie przycina do kadru.
    # Dla analiz bez działki zachowujemy bezpieczny fallback do wszystkich warstw.
    parcel_layer = next(
        (layer for layer in drawable if layer.style.layer == PARCEL_LAYER_STYLE.layer),
        None,
    )
    bounds = _compute_bounds([parcel_layer] if parcel_layer is not None else drawable)
    if bounds is None:
        return MapRenderResult(png_bytes=None)

    warning: str | None = None
    basemap_image: Image.Image | None = None
    basemap_used = False
    basemap_enabled = settings.report_map_basemap_enabled
    basemap_layout: BasemapLayout | None = None

    if basemap_enabled:
        basemap_layout = compute_basemap_layout(bounds, width, height)

    basemap_bytes = (
        fetch_report_basemap_png(basemap_layout) if basemap_layout is not None else None
    )
    if basemap_bytes is not None and basemap_layout is not None:
        basemap_image = Image.open(io.BytesIO(basemap_bytes))
        basemap_used = True
        projector: _Projector | _BasemapProjector = _BasemapProjector(basemap_layout)
    else:
        if basemap_enabled:
            warning = BASEMAP_FAILURE_WARNING
        projector = _Projector(bounds, width, height, MAP_IMAGE_PADDING_PX)

    png_bytes = _draw_png(
        drawable,
        projector,
        width,
        height,
        basemap_image=basemap_image,
    )
    return MapRenderResult(
        png_bytes=png_bytes,
        basemap_used=basemap_used,
        warning=warning,
    )


def png_to_data_uri(png_bytes: bytes) -> str:
    """Koduje bajty PNG jako data URI do bezpiecznego osadzenia w HTML."""
    encoded = base64.b64encode(png_bytes).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _collect_map_layers(response: Any) -> list[_MapLayer]:
    """Buduje warstwy miniatury w kolejności rysowania (od spodu do wierzchu).

    Kolejność odwzorowuje ResultPanel frontendu: najpierw powierzchniowe
    ryzyka i strefy ochronne, potem sieci, obszar zabudowy, a na wierzchu
    najważniejszy obrys działki.
    """
    parcel = getattr(response, "parcel", None)
    infrastructure = getattr(response, "infrastructure", []) or []
    risks = getattr(response, "risks", []) or []

    risk_layer = _MapLayer(style=RISK_LAYER_STYLE)
    for risk in risks:
        primitive = _extract_primitives(getattr(risk, "geometry_geojson", None))
        if not primitive.is_empty():
            risk_layer.primitives.append(primitive)

    protection_layer = _MapLayer(style=PROTECTION_ZONE_LAYER_STYLE)
    network_layer = _MapLayer(style=NETWORK_LAYER_STYLE)
    for item in infrastructure:
        protection = _extract_primitives(getattr(item, "protection_zone_geojson", None))
        if not protection.is_empty():
            protection_layer.primitives.append(protection)
        network = _extract_primitives(getattr(item, "network_geometry_geojson", None))
        if not network.is_empty():
            network_layer.primitives.append(network)

    buildable_layer = _MapLayer(style=BUILDABLE_AREA_LAYER_STYLE)
    parcel_layer = _MapLayer(style=PARCEL_LAYER_STYLE)
    if parcel is not None:
        buildable = _extract_primitives(getattr(parcel, "buildable_area_geojson", None))
        if not buildable.is_empty():
            buildable_layer.primitives.append(buildable)
        parcel_primitive = _extract_primitives(getattr(parcel, "geometry_geojson", None))
        if not parcel_primitive.is_empty():
            parcel_layer.primitives.append(parcel_primitive)

    return [
        risk_layer,
        protection_layer,
        network_layer,
        buildable_layer,
        parcel_layer,
    ]


def _extract_primitives(geojson: dict[str, Any] | None) -> _Primitives:
    """Wyodrębnia poligony, linie i punkty z dowolnej struktury GeoJSON.

    Obsługiwane są Feature, FeatureCollection, GeometryCollection oraz typy
    Polygon, MultiPolygon, LineString, MultiLineString, Point i MultiPoint.
    Nieznane albo uszkodzone struktury są pomijane bez wyjątku, aby błąd jednej
    warstwy nie wywracał całej miniatury.
    """
    primitives = _Primitives()
    if not isinstance(geojson, dict):
        return primitives
    _accumulate_geojson(geojson, primitives)
    return primitives


def _accumulate_geojson(node: Any, primitives: _Primitives) -> None:
    if not isinstance(node, dict):
        return
    node_type = node.get("type")

    if node_type == "FeatureCollection":
        for feature in node.get("features", []) or []:
            _accumulate_geojson(feature, primitives)
        return
    if node_type == "Feature":
        _accumulate_geojson(node.get("geometry"), primitives)
        return
    if node_type == "GeometryCollection":
        for geometry in node.get("geometries", []) or []:
            _accumulate_geojson(geometry, primitives)
        return

    coordinates = node.get("coordinates")
    if coordinates is None:
        return

    try:
        _accumulate_geometry(node_type, coordinates, primitives)
    except (TypeError, ValueError, IndexError):
        # Uszkodzona geometria pojedynczej warstwy nie może zablokować raportu.
        logger.warning("Pominięto uszkodzoną geometrię typu %s w miniaturze mapy", node_type)


def _accumulate_geometry(
    geometry_type: str | None,
    coordinates: Any,
    primitives: _Primitives,
) -> None:
    if geometry_type == "Point":
        primitives.points.append(_as_point(coordinates))
    elif geometry_type == "MultiPoint":
        primitives.points.extend(_as_point(point) for point in coordinates)
    elif geometry_type == "LineString":
        primitives.lines.append(_as_line(coordinates))
    elif geometry_type == "MultiLineString":
        primitives.lines.extend(_as_line(line) for line in coordinates)
    elif geometry_type == "Polygon":
        primitives.polygons.append(_as_polygon(coordinates))
    elif geometry_type == "MultiPolygon":
        primitives.polygons.extend(_as_polygon(polygon) for polygon in coordinates)


def _as_point(coordinate: Any) -> tuple[float, float]:
    return (float(coordinate[0]), float(coordinate[1]))


def _as_line(coordinates: Any) -> list[tuple[float, float]]:
    return [_as_point(coordinate) for coordinate in coordinates]


def _as_polygon(
    rings: Any,
) -> tuple[list[tuple[float, float]], list[list[tuple[float, float]]]]:
    exterior = _as_line(rings[0]) if rings else []
    holes = [_as_line(ring) for ring in rings[1:]] if len(rings) > 1 else []
    return exterior, holes


@dataclass(frozen=True)
class _Bounds:
    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float


def _compute_bounds(layers: list[_MapLayer]) -> _Bounds | None:
    """Wyznacza BBOX przekazanych geometrii i dodaje 10% marginesu na stronę."""
    min_lon = math.inf
    min_lat = math.inf
    max_lon = -math.inf
    max_lat = -math.inf

    for layer in layers:
        for primitive in layer.primitives:
            for exterior, holes in primitive.polygons:
                for ring in (exterior, *holes):
                    for lon, lat in ring:
                        min_lon, max_lon = min(min_lon, lon), max(max_lon, lon)
                        min_lat, max_lat = min(min_lat, lat), max(max_lat, lat)
            for line in primitive.lines:
                for lon, lat in line:
                    min_lon, max_lon = min(min_lon, lon), max(max_lon, lon)
                    min_lat, max_lat = min(min_lat, lat), max(max_lat, lat)
            for lon, lat in primitive.points:
                min_lon, max_lon = min(min_lon, lon), max(max_lon, lon)
                min_lat, max_lat = min(min_lat, lat), max(max_lat, lat)

    if not math.isfinite(min_lon) or not math.isfinite(min_lat):
        return None

    span_lon = max(max_lon - min_lon, _MIN_SPAN_DEGREES)
    span_lat = max(max_lat - min_lat, _MIN_SPAN_DEGREES)
    # Rozszerzamy BBOX o 10% z każdej strony, aby geometria nie dotykała krawędzi.
    margin_lon = span_lon * MAP_BBOX_EXPANSION_RATIO
    margin_lat = span_lat * MAP_BBOX_EXPANSION_RATIO
    center_lon = (min_lon + max_lon) / 2.0
    center_lat = (min_lat + max_lat) / 2.0

    return _Bounds(
        min_lon=center_lon - span_lon / 2.0 - margin_lon,
        min_lat=center_lat - span_lat / 2.0 - margin_lat,
        max_lon=center_lon + span_lon / 2.0 + margin_lon,
        max_lat=center_lat + span_lat / 2.0 + margin_lat,
    )


class _BasemapProjector:
    """Rzutuje WGS84 na piksele zgodnie z kadrem podkładu WMS (Web Mercator).

    BBOX i wymiary muszą pochodzić z tego samego ``BasemapLayout`` co raster GetMap.
    """

    def __init__(self, layout: BasemapLayout) -> None:
        from pyproj import Transformer

        self._layout = layout
        self._transformer = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
        self._min_x, self._min_y, self._max_x, self._max_y = layout.bbox_3857
        self._span_x = max(self._max_x - self._min_x, 1e-6)
        self._span_y = max(self._max_y - self._min_y, 1e-6)

    def to_pixel(self, lon: float, lat: float) -> tuple[float, float]:
        x_m, y_m = self._transformer.transform(lon, lat)
        x = self._layout.offset_x + (x_m - self._min_x) / self._span_x * self._layout.map_width
        y = self._layout.offset_y + (self._max_y - y_m) / self._span_y * self._layout.map_height
        return (x, y)


class _Projector:
    """Rzutuje współrzędne WGS84 na piksele z zachowaniem proporcji i wycentrowaniem."""

    def __init__(self, bounds: _Bounds, width: int, height: int, padding: int) -> None:
        self._bounds = bounds
        self._padding = padding
        mean_lat_rad = math.radians((bounds.min_lat + bounds.max_lat) / 2.0)
        # Korekta cos(lat): stopień długości jest krótszy niż stopień szerokości
        # w polskich szerokościach, więc bez tego działka byłaby rozciągnięta.
        self._kx = max(math.cos(mean_lat_rad), 1e-6)

        proj_w = (bounds.max_lon - bounds.min_lon) * self._kx
        proj_h = bounds.max_lat - bounds.min_lat
        available_w = width - 2 * padding
        available_h = height - 2 * padding
        # Wspólna skala dla obu osi zachowuje proporcje (letterbox).
        self._scale = min(available_w / proj_w, available_h / proj_h)

        draw_w = proj_w * self._scale
        draw_h = proj_h * self._scale
        self._offset_x = (width - draw_w) / 2.0
        self._offset_y = (height - draw_h) / 2.0

    def to_pixel(self, lon: float, lat: float) -> tuple[float, float]:
        x = self._offset_x + (lon - self._bounds.min_lon) * self._kx * self._scale
        # Oś pikseli rośnie w dół, a szerokość geograficzna rośnie w górę.
        y = self._offset_y + (self._bounds.max_lat - lat) * self._scale
        return (x, y)


def _draw_png(
    layers: list[_MapLayer],
    projector: _Projector | _BasemapProjector,
    width: int,
    height: int,
    *,
    basemap_image: Image.Image | None = None,
) -> bytes:
    if basemap_image is not None:
        base = basemap_image.convert("RGBA")
    else:
        base = Image.new("RGBA", (width, height), (*MAP_BACKGROUND_RGB, 255))

    # Najpierw wypełnienia (od spodu do wierzchu), każde na osobnej nakładce,
    # aby otwory jednej warstwy nie kasowały wypełnień warstw pod spodem.
    for layer in layers:
        style = layer.style
        if style.fill_rgb is None:
            continue
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        fill = (*style.fill_rgb, style.fill_alpha)
        for primitive in layer.primitives:
            for exterior, holes in primitive.polygons:
                if len(exterior) < 3:
                    continue
                draw.polygon([projector.to_pixel(*pt) for pt in exterior], fill=fill)
                for hole in holes:
                    if len(hole) >= 3:
                        # Otwór kasujemy do pełnej przezroczystości na tej nakładce.
                        draw.polygon(
                            [projector.to_pixel(*pt) for pt in hole],
                            fill=(0, 0, 0, 0),
                        )
        base = Image.alpha_composite(base, overlay)

    # Następnie obrysy i linie na wierzchu, w tej samej kolejności warstw.
    outline_draw = ImageDraw.Draw(base)
    for layer in layers:
        style = layer.style
        line_color = (*style.line_rgb, 255)
        line_width = max(1, style.line_width)
        for primitive in layer.primitives:
            for exterior, holes in primitive.polygons:
                _draw_ring(outline_draw, projector, exterior, line_color, line_width)
                for hole in holes:
                    _draw_ring(outline_draw, projector, hole, line_color, line_width)
            for line in primitive.lines:
                if len(line) >= 2:
                    outline_draw.line(
                        [projector.to_pixel(*pt) for pt in line],
                        fill=line_color,
                        width=line_width,
                        joint="curve",
                    )
            for point in primitive.points:
                px, py = projector.to_pixel(*point)
                radius = max(3, line_width + 1)
                outline_draw.ellipse(
                    [px - radius, py - radius, px + radius, py + radius],
                    fill=line_color,
                )

    buffer = io.BytesIO()
    base.convert("RGB").save(buffer, format="PNG")
    return buffer.getvalue()


def _draw_ring(
    draw: ImageDraw.ImageDraw,
    projector: _Projector,
    ring: list[tuple[float, float]],
    color: tuple[int, int, int, int],
    width: int,
) -> None:
    if len(ring) < 2:
        return
    pixels = [projector.to_pixel(*pt) for pt in ring]
    # Domykamy obrys, aby wielokąt był narysowany jako zamknięta pętla.
    if pixels[0] != pixels[-1]:
        pixels.append(pixels[0])
    draw.line(pixels, fill=color, width=width, joint="curve")
