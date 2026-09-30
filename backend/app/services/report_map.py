"""Lokalny renderer map raportu z zamrożonego snapshotu (BK-503, ADR-010).

Renderer dostaje wyłącznie ``analyses.report_map_snapshot`` (albo specyfikację
odtworzoną ze snapshotu analizy dla zapisów sprzed BK-503) i nie wykonuje
żadnych wywołań sieciowych: moduł nie importuje klienta HTTP, a podkład jest
co najwyżej zapisanym artefaktem z SHA-256 (``report_map_basemap``).

Geometrie są w ``EPSG:2180``; piksel ma tę samą długość w terenie na obu osiach
(``frame.meters_per_pixel``), dlatego podziałka jest metrycznie poprawna.
Kolejność warstw, style, tryb tematyczny i font pochodzą ze snapshotu, a nie z
bieżącej konfiguracji. Pola powierzchni nie są tu liczone — to wizualizacja.
"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import PIL
from PIL import Image, ImageDraw, ImageFont

from app.core.report_config import REPORT_MAP_FONT_DIRS, hex_to_rgb
from app.core.settings import settings
from app.modules.reporting.domain.map_snapshot import (
    MapSnapshotError,
    semantic_hash,
    to_pixel,
    validate_snapshot,
    verify_semantic_hash,
)
from app.services.report_map_basemap import load_basemap_image

logger = logging.getLogger(__name__)

Ring = list[tuple[float, float]]


@dataclass(frozen=True)
class RenderedMap:
    id: str
    section: str
    title: str
    mode: str
    mode_label: str
    status: str
    empty_reason: str | None
    png_bytes: bytes | None
    legend: list[dict[str, Any]]
    notes: list[str]
    data_dates: list[str]
    data_release_ids: list[int]
    feature_count: int

    @property
    def png_sha256(self) -> str | None:
        return hashlib.sha256(self.png_bytes).hexdigest() if self.png_bytes else None

    @property
    def data_uri(self) -> str | None:
        return png_to_data_uri(self.png_bytes) if self.png_bytes else None


@dataclass
class ReportMaps:
    maps: list[RenderedMap]
    semantic_sha256: str
    stored_semantic_sha256: str | None
    integrity_ok: bool
    from_snapshot: bool
    config_version: str
    pog_theme: str
    pog_style_version: str | None
    pog_style_from_analysis: bool
    frame: dict[str, Any]
    environment: dict[str, Any]
    basemap: dict[str, Any]
    warnings: list[str] = field(default_factory=list)

    def by_id(self, map_id: str) -> RenderedMap | None:
        return next((item for item in self.maps if item.id == map_id), None)


def png_to_data_uri(png_bytes: bytes) -> str:
    """Koduje bajty PNG jako data URI do bezpiecznego osadzenia w HTML."""
    encoded = base64.b64encode(png_bytes).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def render_report_maps(
    snapshot: Mapping[str, Any],
    *,
    from_snapshot: bool = True,
    basemap_dir: str | None = None,
) -> ReportMaps:
    """Renderuje wszystkie mapy snapshotu; nie wykonuje IO sieciowego."""
    validate_snapshot(snapshot)
    config = snapshot["render_config"]
    frame = snapshot["frame"]
    warnings: list[str] = []
    stored = snapshot.get("semantic_sha256")
    integrity_ok = verify_semantic_hash(snapshot)
    if from_snapshot and not integrity_ok:
        warnings.append(
            "Hash semantyczny zapisanej mapy nie zgadza się z jej treścią — "
            "snapshot mapy mógł zostać zmodyfikowany po zapisie analizy."
        )
    basemap_ref = config.get("basemap") or {"mode": "neutral"}
    basemap_image, basemap_note = load_basemap_image(
        basemap_ref,
        frame,
        basemap_dir if basemap_dir is not None else settings.report_map_basemap_artifact_dir,
    )
    if basemap_note:
        warnings.append(basemap_note)
    fonts = _load_fonts(str(config["font"]["file"]), str(config["font"]["bold_file"]),
                        int(config["font"]["size_px"]))
    maps: list[RenderedMap] = []
    for spec in snapshot["maps"]:
        png = None
        if spec.get("status") == "rendered":
            png = _render_png(spec, config, frame, fonts, basemap_image)
        maps.append(
            RenderedMap(
                id=spec["id"],
                section=spec["section"],
                title=spec["title"],
                mode=spec["mode"],
                mode_label=spec["mode_label"],
                status=spec["status"],
                empty_reason=spec.get("empty_reason"),
                png_bytes=png,
                legend=list(spec.get("legend") or []),
                notes=list(spec.get("notes") or []),
                data_dates=list(spec.get("data_dates") or []),
                data_release_ids=list(spec.get("data_release_ids") or []),
                feature_count=sum(len(layer["features"]) for layer in spec["layers"]),
            )
        )
    pog_config = config.get("pog") or {}
    return ReportMaps(
        maps=maps,
        semantic_sha256=semantic_hash(snapshot),
        stored_semantic_sha256=stored if isinstance(stored, str) else None,
        integrity_ok=integrity_ok,
        from_snapshot=from_snapshot,
        config_version=str(config["config_version"]),
        pog_theme=str(config.get("pog_theme")),
        pog_style_version=pog_config.get("style_version"),
        pog_style_from_analysis=bool(pog_config.get("style_from_analysis")),
        frame=dict(frame),
        environment={
            "pillow": PIL.__version__,
            "font_family": config["font"]["family"],
            "font_file": fonts.path or "wbudowany font Pillow (brak pliku DejaVu)",
            "font_sha256": fonts.sha256,
        },
        basemap={
            **basemap_ref,
            "used": basemap_image is not None,
        },
        warnings=warnings,
    )


# --- Fonty ------------------------------------------------------------------------


@dataclass(frozen=True)
class _Fonts:
    regular: Any
    bold: Any
    path: str | None
    sha256: str | None


@lru_cache(maxsize=8)
def _load_fonts(file_name: str, bold_name: str, size: int) -> _Fonts:
    regular_path = _find_font(file_name)
    bold_path = _find_font(bold_name) or regular_path
    if regular_path is None:
        default = ImageFont.load_default(size=size)
        return _Fonts(default, default, None, None)
    return _Fonts(
        ImageFont.truetype(regular_path, size),
        ImageFont.truetype(bold_path or regular_path, size),
        regular_path,
        hashlib.sha256(Path(regular_path).read_bytes()).hexdigest(),
    )


def _find_font(file_name: str) -> str | None:
    for directory in REPORT_MAP_FONT_DIRS:
        candidate = Path(directory) / file_name
        if candidate.is_file():
            return str(candidate)
    return None


# --- Rysowanie -------------------------------------------------------------------


def _render_png(
    spec: Mapping[str, Any],
    config: Mapping[str, Any],
    frame: Mapping[str, Any],
    fonts: _Fonts,
    basemap_image: Image.Image | None,
) -> bytes:
    width, height = int(frame["width_px"]), int(frame["height_px"])
    if basemap_image is not None:
        base = basemap_image.copy()
    else:
        base = Image.new("RGBA", (width, height), (*hex_to_rgb(config["background"]), 255))

    layers = spec["layers"]
    for layer in layers:
        style = layer["style"]
        if not style.get("fill"):
            continue
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        fill = (*hex_to_rgb(style["fill"]), int(style.get("fill_alpha") or 0))
        for exterior, holes in _polygons(layer, frame):
            draw.polygon(exterior, fill=fill)
            for hole in holes:
                draw.polygon(hole, fill=(0, 0, 0, 0))
        base = Image.alpha_composite(base, overlay)

    for layer in layers:
        pattern = layer["style"].get("pattern")
        if not pattern:
            continue
        mask = Image.new("L", (width, height), 0)
        mask_draw = ImageDraw.Draw(mask)
        for exterior, holes in _polygons(layer, frame):
            mask_draw.polygon(exterior, fill=255)
            for hole in holes:
                mask_draw.polygon(hole, fill=0)
        # Wzór nakładki bez wypełnienia (OUZ/OZS/OSDIS) jest rzadszy i bledszy,
        # aby nie zasłaniał kolorów stref pod spodem.
        overlay_only = not layer["style"].get("fill")
        texture = _pattern_image(
            pattern,
            hex_to_rgb(layer["style"]["outline"]),
            width,
            height,
            alpha=140 if overlay_only else 200,
            spacing=14 if overlay_only else 10,
        )
        clipped = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        clipped.paste(texture, (0, 0), mask)
        base = Image.alpha_composite(base, clipped)

    draw = ImageDraw.Draw(base)
    for layer in layers:
        style = layer["style"]
        color = (*hex_to_rgb(style["outline"]), 255)
        line_width = max(1, int(style.get("line_width") or 1))
        dash = tuple(float(item) for item in style.get("line_dash") or ()) or None
        for feature in layer["features"]:
            for kind, coords in _parts(feature["geometry"], frame):
                if kind == "ring":
                    _draw_ring(draw, coords, color, line_width, dash)
                elif kind == "line" and len(coords) >= 2:
                    if dash:
                        _draw_dashed(draw, coords, color, line_width, dash)
                    else:
                        draw.line(coords, fill=color, width=line_width, joint="curve")
                elif kind == "point":
                    x, y = coords[0]
                    radius = max(3, line_width + 1)
                    draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=color)

    for layer in layers:
        for feature in layer["features"]:
            point = feature.get("label_point")
            if point and feature.get("label"):
                x, y = to_pixel(frame, float(point[0]), float(point[1]))
                draw.text(
                    (x, y),
                    str(feature["label"]),
                    font=fonts.bold,
                    fill=(26, 32, 39, 255),
                    anchor="mm",
                    stroke_width=3,
                    stroke_fill=(255, 255, 255, 255),
                )

    _draw_scale_bar(draw, frame, fonts)
    _draw_north_arrow(draw, width, fonts)
    draw.rectangle([0, 0, width - 1, height - 1], outline=(152, 162, 179, 255), width=1)

    buffer = io.BytesIO()
    base.convert("RGB").save(buffer, format="PNG", optimize=False)
    return buffer.getvalue()


def _parts(geometry: Mapping[str, Any] | None, frame: Mapping[str, Any]) -> Iterator[tuple[str, Ring]]:
    if not geometry:
        return
    kind = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if kind == "GeometryCollection":
        for item in geometry.get("geometries") or []:
            yield from _parts(item, frame)
        return
    if coordinates is None:
        return
    if kind == "Polygon":
        polygons = [coordinates]
    elif kind == "MultiPolygon":
        polygons = coordinates
    else:
        polygons = []
    for polygon in polygons:
        for ring in polygon:
            yield "ring", [to_pixel(frame, x, y) for x, y in ring]
    if kind == "LineString":
        yield "line", [to_pixel(frame, x, y) for x, y in coordinates]
    elif kind == "MultiLineString":
        for line in coordinates:
            yield "line", [to_pixel(frame, x, y) for x, y in line]
    elif kind == "Point":
        yield "point", [to_pixel(frame, coordinates[0], coordinates[1])]
    elif kind == "MultiPoint":
        for x, y in coordinates:
            yield "point", [to_pixel(frame, x, y)]


def _polygons(layer: Mapping[str, Any], frame: Mapping[str, Any]) -> Iterator[tuple[Ring, list[Ring]]]:
    for feature in layer["features"]:
        yield from _feature_polygons(feature["geometry"], frame)


def _feature_polygons(
    geometry: Mapping[str, Any] | None, frame: Mapping[str, Any]
) -> Iterator[tuple[Ring, list[Ring]]]:
    if not geometry:
        return
    kind = geometry.get("type")
    if kind == "GeometryCollection":
        for item in geometry.get("geometries") or []:
            yield from _feature_polygons(item, frame)
        return
    polygons = (
        [geometry["coordinates"]]
        if kind == "Polygon"
        else geometry["coordinates"]
        if kind == "MultiPolygon"
        else []
    )
    for polygon in polygons:
        if not polygon or len(polygon[0]) < 3:
            continue
        exterior = [to_pixel(frame, x, y) for x, y in polygon[0]]
        holes = [[to_pixel(frame, x, y) for x, y in ring] for ring in polygon[1:] if len(ring) >= 3]
        yield exterior, holes


def _draw_ring(
    draw: ImageDraw.ImageDraw,
    pixels: Ring,
    color: tuple[int, int, int, int],
    width: int,
    dash: tuple[float, ...] | None,
) -> None:
    if len(pixels) < 2:
        return
    if pixels[0] != pixels[-1]:
        pixels = [*pixels, pixels[0]]
    if dash:
        _draw_dashed(draw, pixels, color, width, dash)
        return
    draw.line(pixels, fill=color, width=width, joint="curve")


def _draw_dashed(
    draw: ImageDraw.ImageDraw,
    pixels: Ring,
    color: tuple[int, int, int, int],
    width: int,
    dash: tuple[float, ...],
) -> None:
    """Linia przerywana; wzór kresek w jednostkach szerokości linii (jak MapLibre).

    Parzyste pozycje wzoru to kreski, nieparzyste — przerwy; wzór o nieparzystej
    długości jest podwajany (semantyka SVG ``stroke-dasharray``).
    """
    pattern = [max(0.5, value) * width for value in dash]
    if len(pattern) % 2:
        pattern = pattern * 2
    index, remaining = 0, pattern[0]
    for (x0, y0), (x1, y1) in zip(pixels, pixels[1:]):
        length = math.hypot(x1 - x0, y1 - y0)
        position = 0.0
        while position < length:
            step = min(remaining, length - position)
            if index % 2 == 0:
                start, end = position / length, (position + step) / length
                draw.line(
                    [
                        (x0 + (x1 - x0) * start, y0 + (y1 - y0) * start),
                        (x0 + (x1 - x0) * end, y0 + (y1 - y0) * end),
                    ],
                    fill=color,
                    width=width,
                )
            position += step
            remaining -= step
            if remaining <= 1e-9:
                index = (index + 1) % len(pattern)
                remaining = pattern[index]


def _pattern_image(
    pattern: str,
    rgb: tuple[int, int, int],
    width: int,
    height: int,
    *,
    alpha: int = 200,
    spacing: int = 10,
) -> Image.Image:
    """Kafelkowany wzór wypełnienia w kolorze obrysu warstwy."""
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    color = (*rgb, alpha)
    if pattern in {"diagonal-lines", "diagonal-hatch", "cross-hatch"}:
        for offset in range(-height, width, spacing):
            draw.line([(offset, height), (offset + height, 0)], fill=color, width=1)
    if pattern == "cross-hatch":
        for offset in range(0, width + height, spacing):
            draw.line([(offset - height, 0), (offset, height)], fill=color, width=1)
    if pattern == "cross-lines":
        for x in range(0, width, spacing):
            draw.line([(x, 0), (x, height)], fill=color, width=1)
        for y in range(0, height, spacing):
            draw.line([(0, y), (width, y)], fill=color, width=1)
    if pattern == "horizontal-lines":
        for y in range(0, height, spacing // 2 + 2):
            draw.line([(0, y), (width, y)], fill=color, width=1)
    if pattern == "dots":
        for x in range(spacing // 2, width, spacing):
            for y in range(spacing // 2, height, spacing):
                draw.ellipse([x - 1.5, y - 1.5, x + 1.5, y + 1.5], fill=color)
    return image


def format_length_m(value: float) -> str:
    """Długość podziałki po polsku: 20 m, 0,5 m, 1 000 m."""
    if float(value).is_integer():
        return f"{int(value):,}".replace(",", " ") + " m"
    return f"{value:g}".replace(".", ",") + " m"


def _draw_scale_bar(draw: ImageDraw.ImageDraw, frame: Mapping[str, Any], fonts: _Fonts) -> None:
    bar = frame["scale_bar"]
    length_px = float(bar["length_px"])
    height = int(frame["height_px"])
    x0, y0 = 20.0, height - 34.0
    label = format_length_m(float(bar["length_m"]))
    label_box = draw.textbbox((0, 0), label, font=fonts.regular)
    box_w = length_px + (label_box[2] - label_box[0]) + 40
    draw.rectangle([x0 - 8, y0 - 24, x0 + box_w, y0 + 18], fill=(255, 255, 255, 230),
                   outline=(152, 162, 179, 255))
    half = length_px / 2.0
    draw.rectangle([x0, y0, x0 + half, y0 + 8], fill=(26, 32, 39, 255))
    draw.rectangle([x0 + half, y0, x0 + length_px, y0 + 8], fill=(255, 255, 255, 255),
                   outline=(26, 32, 39, 255))
    draw.text((x0, y0 - 4), "0", font=fonts.regular, fill=(26, 32, 39, 255), anchor="lb")
    draw.text((x0 + length_px + 6, y0 + 8), label, font=fonts.regular, fill=(26, 32, 39, 255),
              anchor="lb")


def _draw_north_arrow(draw: ImageDraw.ImageDraw, width: int, fonts: _Fonts) -> None:
    cx, top = width - 30.0, 14.0
    draw.polygon([(cx, top), (cx - 9, top + 26), (cx, top + 20), (cx + 9, top + 26)],
                 fill=(26, 32, 39, 255))
    draw.text((cx, top + 30), "N", font=fonts.bold, fill=(26, 32, 39, 255), anchor="mt",
              stroke_width=2, stroke_fill=(255, 255, 255, 255))


def ensure_valid_snapshot(snapshot: Mapping[str, Any]) -> bool:
    """Czy zapisany snapshot mapy ma obsługiwany schemat (bez wyjątków)."""
    try:
        validate_snapshot(snapshot)
    except MapSnapshotError:
        return False
    return True
