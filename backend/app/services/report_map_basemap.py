"""Synchroniczne pobieranie podkładu WMS GetMap dla miniatury raportu PDF.

Miniatura raportu potrzebuje jednego obrazu dla całego BBOX (np. 900×600), a nie
kafelków XYZ z ``wms_tiles``. Stos mapy odwzorowuje UI: publiczny podkład
lokalizacyjny (OSM WMS) + opcjonalne przezroczyste nakładki KIMPZP i KIUT.

GetMap używa pełnego kadru miniatury (np. 900×600). Gdy BBOX działki ma inną
proporcję, rozszerzamy zasięg mapy (więcej kontekstu po bokach/górze), zamiast
letterboxa z białymi pasami — proporcje obrazu pozostają poprawne, a wektory
są liczone w tym samym ``BasemapLayout`` co raster WMS.

TODO (ADR-009): raport PDF jest offline ze snapshotu analizy; ten moduł to
świadomy, opcjonalny wyjątek UX — jedyny outbound przy generowaniu PDF. Awaria
WMS nigdy nie blokuje raportu (fallback w ``report_map``). Awaria samej
nakładki KIUT nie blokuje podkładu OSM.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
from PIL import Image
from pyproj import Transformer

from app.core.settings import Settings, settings
from app.services.wms_tiles import WmsPreviewSource

if TYPE_CHECKING:
    from app.services.report_map import _Bounds

logger = logging.getLogger(__name__)

PNG_SIGNATURE: bytes = b"\x89PNG\r\n\x1a\n"
_BASEMAP_SRS = "EPSG:3857"
_TRANSFORMER_WGS84_TO_WEB_MERCATOR = Transformer.from_crs(
    "EPSG:4326",
    _BASEMAP_SRS,
    always_xy=True,
)


@dataclass(frozen=True)
class BasemapLayout:
    """Układ podkładu WMS na kanwie miniatury — wspólny dla GetMap i wektorów."""

    bbox_3857: tuple[float, float, float, float]
    map_width: int
    map_height: int
    offset_x: float
    offset_y: float
    canvas_width: int
    canvas_height: int


def compute_basemap_layout(
    bounds: _Bounds,
    canvas_width: int,
    canvas_height: int,
) -> BasemapLayout:
    """Wyznacza BBOX Web Mercator wypełniający kadr miniatury bez zniekształceń."""
    core_bbox = bounds_to_web_mercator_bbox(bounds)
    canvas_aspect = canvas_width / canvas_height
    fill_bbox = expand_mercator_bbox_to_aspect(core_bbox, canvas_aspect)

    return BasemapLayout(
        bbox_3857=fill_bbox,
        map_width=canvas_width,
        map_height=canvas_height,
        offset_x=0.0,
        offset_y=0.0,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
    )


def expand_mercator_bbox_to_aspect(
    bbox_3857: tuple[float, float, float, float],
    target_aspect: float,
) -> tuple[float, float, float, float]:
    """Poszerza BBOX w Web Mercator, aby proporcje boków = ``target_aspect`` (width/height).

    Oryginalny zasięg pozostaje wycentrowany — działka nie jest obcinana, tylko
    dokładamy kontekst mapy po stronie węższego wymiaru.
    """
    min_x, min_y, max_x, max_y = bbox_3857
    span_x = max(max_x - min_x, 1e-6)
    span_y = max(max_y - min_y, 1e-6)
    aspect = span_x / span_y
    center_x = (min_x + max_x) / 2.0
    center_y = (min_y + max_y) / 2.0

    if aspect >= target_aspect:
        fit_span_x = span_x
        fit_span_y = span_x / target_aspect
    else:
        fit_span_y = span_y
        fit_span_x = span_y * target_aspect

    return (
        center_x - fit_span_x / 2.0,
        center_y - fit_span_y / 2.0,
        center_x + fit_span_x / 2.0,
        center_y + fit_span_y / 2.0,
    )


def bounds_to_web_mercator_bbox(bounds: _Bounds) -> tuple[float, float, float, float]:
    """Przelicza BBOX WGS84 (lon/lat) na Web Mercator minx,miny,maxx,maxy."""
    corner_x, corner_y = _TRANSFORMER_WGS84_TO_WEB_MERCATOR.transform(
        bounds.min_lon,
        bounds.min_lat,
    )
    opposite_x, opposite_y = _TRANSFORMER_WGS84_TO_WEB_MERCATOR.transform(
        bounds.max_lon,
        bounds.max_lat,
    )
    return (
        min(corner_x, opposite_x),
        min(corner_y, opposite_y),
        max(corner_x, opposite_x),
        max(corner_y, opposite_y),
    )


def fetch_report_basemap_png(
    layout: BasemapLayout,
    *,
    config: Settings | None = None,
    kiut_source: WmsPreviewSource | None = None,
) -> bytes | None:
    """Pobiera PNG podkładu (OSM + opcjonalnie KIMPZP i KIUT) wg ``BasemapLayout``."""
    cfg = config or settings
    if not cfg.report_map_basemap_enabled:
        return None
    if not cfg.report_map_wms_base_url or not cfg.report_map_wms_layers:
        logger.warning("report_basemap_skipped reason=missing_wms_config")
        return None

    timeout = _build_timeout(cfg)

    base_image = _fetch_wms_rgba(
        cfg.report_map_wms_base_url,
        cfg.report_map_wms_layers,
        layout.bbox_3857,
        layout.map_width,
        layout.map_height,
        transparent=False,
        timeout=timeout,
        max_bytes=cfg.report_map_wms_max_response_bytes,
    )
    if base_image is None:
        return None

    if cfg.report_map_kimpzp_overlay_enabled:
        overlay = _fetch_wms_rgba(
            cfg.kimpzp_wms_base_url,
            cfg.kimpzp_wms_layers,
            layout.bbox_3857,
            layout.map_width,
            layout.map_height,
            transparent=True,
            timeout=timeout,
            max_bytes=cfg.report_map_wms_max_response_bytes,
        )
        if overlay is not None:
            base_image = Image.alpha_composite(base_image, overlay)

    if cfg.report_map_kiut_overlay_enabled and kiut_source is not None:
        overlay = _fetch_wms_rgba(
            kiut_source.base_url,
            kiut_source.layers,
            layout.bbox_3857,
            layout.map_width,
            layout.map_height,
            transparent=True,
            timeout=timeout,
            max_bytes=cfg.report_map_wms_max_response_bytes,
            allowed_redirect_host_suffixes=kiut_source.allowed_redirect_host_suffixes,
            max_redirects=kiut_source.max_redirects,
        )
        if overlay is not None:
            # KIUT nad planem, pod wektorami działki — jak w UI.
            base_image = Image.alpha_composite(base_image, overlay)

    buffer = io.BytesIO()
    base_image.convert("RGB").save(buffer, format="PNG")
    return buffer.getvalue()


def _build_timeout(cfg: Settings) -> httpx.Timeout:
    return httpx.Timeout(
        connect=min(cfg.report_map_wms_timeout_seconds, 2.0),
        read=cfg.report_map_wms_timeout_seconds,
        write=cfg.report_map_wms_timeout_seconds,
        pool=min(cfg.report_map_wms_timeout_seconds, 2.0),
    )


def _fetch_wms_rgba(
    base_url: str,
    layers: str,
    bbox_3857: tuple[float, float, float, float],
    width: int,
    height: int,
    *,
    transparent: bool,
    timeout: httpx.Timeout,
    max_bytes: int,
    allowed_redirect_host_suffixes: tuple[str, ...] = (),
    max_redirects: int = 0,
) -> Image.Image | None:
    if not base_url or not layers:
        return None

    params: dict[str, str] = {
        "service": "WMS",
        "version": "1.1.1",
        "request": "GetMap",
        "layers": layers,
        "styles": "",
        "format": "image/png",
        "srs": _BASEMAP_SRS,
        "width": str(width),
        "height": str(height),
        "bbox": ",".join(f"{coordinate:.8f}" for coordinate in bbox_3857),
    }
    if transparent:
        params["transparent"] = "true"

    try:
        with httpx.Client(
            timeout=timeout,
            headers={"User-Agent": "dzialki-report-map/1.0"},
            follow_redirects=False,
        ) as client:
            response = _get_with_allowed_redirects(
                client,
                base_url,
                params,
                allowed_redirect_host_suffixes=allowed_redirect_host_suffixes,
                max_redirects=max_redirects,
            )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "report_basemap_fetch_failed status=%s error_type=%s",
            exc.response.status_code,
            type(exc).__name__,
        )
        return None
    except httpx.HTTPError as exc:
        logger.warning(
            "report_basemap_fetch_failed error_type=%s",
            type(exc).__name__,
        )
        return None

    content = response.content
    content_type = response.headers.get("content-type", "").lower()
    if "image/png" not in content_type or not content.startswith(PNG_SIGNATURE):
        logger.warning(
            "report_basemap_invalid_content content_type=%s",
            content_type or "unknown",
        )
        return None
    if len(content) > max_bytes:
        logger.warning("report_basemap_response_too_large size=%s", len(content))
        return None

    try:
        image = Image.open(io.BytesIO(content)).convert("RGBA")
    except OSError:
        logger.warning("report_basemap_invalid_png")
        return None

    if image.size != (width, height):
        image = image.resize((width, height), Image.Resampling.LANCZOS)
    return image


_REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})


def _get_with_allowed_redirects(
    client: httpx.Client,
    url: str,
    params: dict[str, str] | None,
    *,
    allowed_redirect_host_suffixes: tuple[str, ...],
    max_redirects: int,
) -> httpx.Response:
    """Podąża wyłącznie za HTTPS-owymi przekierowaniami z allowlisty źródła.

    Front KIUT rozdziela GetMap kodem 302 na węzły integracja01/02. Ogólne
    ``follow_redirects=True`` otworzyłoby SSRF przez nagłówek Location.
    """
    current_url = url
    current_params = params
    for _ in range(max_redirects + 1):
        response = client.get(
            current_url, params=current_params, follow_redirects=False
        )
        if response.status_code not in _REDIRECT_STATUS_CODES:
            return response
        location = response.headers.get("location")
        if not location:
            raise httpx.HTTPError("Przekierowanie WMS bez nagłówka Location.")
        target = httpx.URL(current_url).join(location)
        host = (target.host or "").lower()
        if target.scheme != "https" or not any(
            host.endswith(suffix) for suffix in allowed_redirect_host_suffixes
        ):
            logger.warning(
                "report_basemap_redirect_rejected host=%s",
                host or "?",
            )
            raise httpx.HTTPError(
                f"Odrzucono przekierowanie WMS na niedozwolony adres: {host or '?'}"
            )
        logger.info("report_basemap_redirected host=%s", host)
        current_url, current_params = str(target), None
    raise httpx.HTTPError("Przekroczono limit przekierowań WMS.")
