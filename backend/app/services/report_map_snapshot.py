"""Zamrażanie map raportu przy zapisie analizy (BK-503, ADR-010).

Snapshot mapy przechowuje wszystko, co decyduje o znaczeniu obrazu: obrys
działki i geometrie analizowanych stref/ryzyk w ``EPSG:2180`` (przycięte do
kadru, zaokrąglone do 1 cm), kadr metryczny, podziałkę, tryb tematyczny,
kolejność warstw, style (z wersją stylu POG), font oraz identyfikatory wydań i
daty danych. Raport renderuje mapę wyłącznie z tego zapisu — publikacja nowego
wydania ani zmiana bieżącej konfiguracji nie zmienia starego PDF.

Wejściem są GeoJSON-y ``EPSG:4326`` z ``AnalyzeResponse`` (tak zapisuje je
analiza) oraz oryginalna geometria działki ``EPSG:2180``. Przeliczenie do
``EPSG:2180`` odbywa się raz, przy zamrożeniu.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any

import shapely
from pyproj import Transformer
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

from app.modules.analysis.domain.landcover import CLASS_COLORS
from app.core.report_config import (
    MAP_BBOX_EXPANSION_RATIO,
    MAP_IMAGE_HEIGHT,
    MAP_IMAGE_WIDTH,
    MAP_MIN_SPAN_M,
    MAP_SCALE_BAR_MAX_FRACTION,
    report_map_render_config,
)
from app.core.settings import settings
from app.modules.reporting.domain.map_snapshot import (
    REPORT_MAP_SNAPSHOT_SCHEMA,
    choose_scale_bar,
    fit_frame,
    round_coordinates,
    semantic_hash,
    theme_class_for,
)
from app.services.report_map_basemap import select_basemap_artifact

logger = logging.getLogger(__name__)

_TO_2180 = Transformer.from_crs("EPSG:4326", "EPSG:2180", always_xy=True)

MAP_TITLES: dict[str, str] = {
    "parcel": "Mapa 1. Działka, obszar po odsunięciu i przybliżenia techniczne",
    "mpzp": "Mapa 3. Strefy MPZP przecinające działkę",
    "mpzp_acts": "Mapa 3a. Granice aktów MPZP i część działki objęta aktem",
    "pog": "Mapa 4. Strefy POG oraz OUZ, OZS i OSDIS",
    "environment": "Mapa 5. Zagrożenie powodziowe i formy ochrony przyrody",
}
_PATTERN_LABELS: dict[str, str] = {
    "diagonal-lines": "ukośne kreskowanie",
    "dots": "wypełnienie z kropek",
    "cross-lines": "kratka pionowo-pozioma",
    "cross-hatch": "kratka ukośna",
    "diagonal-hatch": "ukośne kreskowanie (brak wartości)",
    "horizontal-lines": "poziome kreskowanie",
}
_OVERLAY_ORDER: tuple[tuple[str, str], ...] = (
    ("ouz", "ouz"),
    ("downtown", "downtown_areas"),
    ("social_infrastructure_standard", "social_infrastructure_standard_areas"),
)


def build_report_map_snapshot(
    response: Any,
    parcel_geometry: BaseGeometry,
    *,
    pog_theme: str | None = None,
    basemap_dir: str | None = None,
    frozen_at: datetime | None = None,
) -> dict[str, Any]:
    """Zamrożona specyfikacja wszystkich map raportu dla jednej analizy."""
    if parcel_geometry.is_empty:
        raise ValueError("Nie można zamrozić mapy bez geometrii działki.")
    theme_id = pog_theme or settings.report_map_pog_theme
    cover = getattr(response, "land_cover", None)
    context_bounds = parcel_geometry.buffer(200, quad_segs=16).bounds if cover and cover.features_geojson else parcel_geometry.bounds
    frame = fit_frame(
        context_bounds,
        width_px=MAP_IMAGE_WIDTH,
        height_px=MAP_IMAGE_HEIGHT,
        margin_ratio=MAP_BBOX_EXPANSION_RATIO,
        min_span_m=MAP_MIN_SPAN_M,
    )
    frame["crs"] = "EPSG:2180"  # type: ignore[assignment]
    frame["scale_bar"] = choose_scale_bar(  # type: ignore[assignment]
        frame["meters_per_pixel"], MAP_IMAGE_WIDTH * MAP_SCALE_BAR_MAX_FRACTION
    )
    basemap = select_basemap_artifact(
        frame, basemap_dir if basemap_dir is not None else settings.report_map_basemap_artifact_dir
    )
    pog = getattr(response, "pog", None)
    config = report_map_render_config(pog, theme_id, basemap)
    clip = (frame["min_x"], frame["min_y"], frame["max_x"], frame["max_y"])
    parcel_feature = _feature("parcel", "obrys działki", _clip(parcel_geometry, clip))
    parcel_layer = _layer("parcel", config["layer_styles"]["parcel"], [parcel_feature])

    maps = [
        _parcel_map(response, config, clip, parcel_layer),
        _mpzp_map(response, config, clip, parcel_layer),
        # Mapa 3a istnieje tylko wtedy, gdy wynik ma akty MPZP (AU-101); analizy bez aktów mają ten sam zestaw
        # i ten sam hash semantyczny map co przed zmianą.
        *(
            [_mpzp_acts_map(response, config, clip, parcel_layer)]
            if getattr(response, "mpzp_acts", None)
            else []
        ),
        _pog_map(pog, config, clip, parcel_layer),
        _environment_map(response, config, clip, parcel_layer),
    ]
    for item in maps:
        item["data_dates"], item["data_release_ids"] = _map_data_provenance(response, item["id"])
    parcel = getattr(response, "parcel", None)
    snapshot: dict[str, Any] = {
        "schema": REPORT_MAP_SNAPSHOT_SCHEMA,
        "render_config": config,
        "frame": frame,
        "data": {
            "parcel_identifier": getattr(parcel, "parcel_identifier", None),
            "analyzed_at": _iso(getattr(response, "analyzed_at", None)),
            "data_release_ids": _data_release_ids(response),
        },
        "maps": maps,
        "frozen_at": _iso(frozen_at or datetime.now(timezone.utc)),
    }
    snapshot["semantic_sha256"] = semantic_hash(snapshot)
    return snapshot


# --- Mapy ----------------------------------------------------------------------


def _parcel_map(
    response: Any, config: Mapping[str, Any], clip: tuple[float, ...], parcel_layer: dict[str, Any]
) -> dict[str, Any]:
    styles = config["layer_styles"]
    parcel = getattr(response, "parcel", None)
    infrastructure = list(getattr(response, "infrastructure", None) or [])
    protection = [
        _feature(
            f"protection:{index}",
            f"bufor {item.network_type} (przybliżenie)",
            _clip_geojson(item.protection_zone_geojson, clip),
        )
        for index, item in enumerate(infrastructure)
    ]
    networks = [
        _feature(
            f"network:{index}",
            f"sieć {item.network_type} (przybliżenie)",
            _clip_geojson(item.network_geometry_geojson, clip),
        )
        for index, item in enumerate(infrastructure)
    ]
    buildable = _feature(
        "buildable_area",
        "obszar po technicznym odsunięciu od granic",
        _clip_geojson(getattr(parcel, "buildable_area_geojson", None), clip),
    )
    layers = [
        _layer("protection_zone", styles["protection_zone"], protection, kind="approximation"),
        _layer("network", styles["network"], networks, kind="approximation"),
        _layer("buildable_area", styles["buildable_area"], [buildable], kind="approximation"),
        parcel_layer,
    ]
    return _map("parcel", "parcel_outline", "Obrys działki i przybliżenia techniczne", layers, None)


def _mpzp_map(
    response: Any, config: Mapping[str, Any], clip: tuple[float, ...], parcel_layer: dict[str, Any]
) -> dict[str, Any]:
    zones = list(getattr(response, "mpzp_zones", None) or [])
    palette = config["mpzp_palette"]
    layers: list[dict[str, Any]] = []
    without_geometry: list[str] = []
    for index, zone in enumerate(zones):
        geometry = _clip_geojson(zone.intersection_geojson, clip)
        if geometry is None:
            without_geometry.append(zone.zone_symbol)
            continue
        fill, outline = palette[index % len(palette)]
        style = {
            "layer": f"mpzp_zone:{index}",
            "label": f"{zone.zone_symbol} — {zone.primary_use or 'przeznaczenie nie określono'}",
            "outline": outline,
            "line_width": 2,
            "fill": fill,
            "fill_alpha": config["mpzp_fill_alpha"],
            "pattern": None,
            "line_dash": None,
        }
        feature = _feature(zone.zone_id or f"mpzp:{index}", zone.zone_symbol, geometry)
        feature["label_point"] = _label_point(geometry)
        layers.append(_layer(f"mpzp_zone:{index}", style, [feature]))
    reason = None
    if not layers:
        reason = (
            "Brak geometrii stref MPZP w zapisanym wyniku"
            + (
                f" (strefy bez wektora: {', '.join(without_geometry)} — przypisanie dokumentowe lub ręczne)"
                if without_geometry
                else f" ({_mpzp_state_phrase(response)})"
            )
            + ". Brak strefy na mapie nie oznacza braku planu."
        )
    notes = (
        [f"Bez geometrii (nienarysowane): {', '.join(without_geometry)}."]
        if layers and without_geometry
        else []
    )
    return _map("mpzp", "mpzp_zones", "Strefy MPZP (symbol podpisany na mapie)", [*layers, parcel_layer],
                reason, notes)


def _mpzp_state_phrase(response: Any) -> str:
    """Stan ustalenia MPZP (AU-103) zamiast jednego zdania „nie znaleziono albo nie sprawdzono”."""
    state = getattr(response, "mpzp_status", None)
    if state is None:
        return "stan ustalenia MPZP nieznany — zapis sprzed wprowadzenia stanów"
    return f"stan ustalenia MPZP: {state.label}"


def _clip_boundary_lines(geojson: Mapping[str, Any] | None, clip: tuple[float, ...]) -> dict[str, Any] | None:
    """Obrys aktu jako linie (bez sztucznych krawędzi na brzegu kadru) w EPSG:2180, przycięty do kadru."""
    geometry = _geojson_geometry(geojson)
    if geometry is None:
        return None
    try:
        projected = transform(_TO_2180.transform, shape(geometry))
    except (ValueError, TypeError, AttributeError, KeyError):
        logger.warning("report_map_snapshot_invalid_geometry")
        return None
    if not projected.is_valid and projected.geom_type in {"Polygon", "MultiPolygon"}:
        projected = shapely.make_valid(projected)
    lines = projected.boundary if projected.geom_type in {"Polygon", "MultiPolygon"} else projected
    clipped = shapely.clip_by_rect(lines, *clip)
    if clipped.is_empty:
        return None
    data = mapping(clipped)
    return {"type": data["type"], "coordinates": round_coordinates(data["coordinates"])}


def _mpzp_acts_map(
    response: Any, config: Mapping[str, Any], clip: tuple[float, ...], parcel_layer: dict[str, Any]
) -> dict[str, Any]:
    """Mapa 3a: granice aktów MPZP (linie przerywane) i część działki objęta aktem (AU-101).

    Akt nie jest strefą, więc mapa nie podpisuje przeznaczeń; legenda wymienia numer uchwały. Akt bez
    granicy w źródle (wyłącznie KIMPZP) nie jest rysowany i jest wymieniony w uwadze.
    """
    acts = list(getattr(response, "mpzp_acts", None) or [])
    palette = config["mpzp_palette"]
    layers: list[dict[str, Any]] = []
    undrawn: list[str] = []
    for index, act in enumerate(acts):
        number = act.resolution_number or act.act_identifier or f"akt {index + 1}"
        fill, outline = palette[index % len(palette)]
        in_force = act.legal_status in {"binding", "unknown"}
        intersection = _clip_geojson(act.intersection_geojson, clip)
        boundary = _clip_boundary_lines(act.boundary_geojson, clip)
        if intersection is None and boundary is None:
            undrawn.append(number)
            continue
        if boundary is not None:
            layers.append(
                _layer(
                    f"mpzp_act_boundary:{index}",
                    {
                        "layer": f"mpzp_act_boundary:{index}",
                        "label": f"Granica aktu {number}" + ("" if in_force else " (projekt lub bez mocy)"),
                        "outline": outline,
                        "line_width": 2,
                        "fill": None,
                        "fill_alpha": None,
                        "pattern": None,
                        "line_dash": [6, 4],
                    },
                    [_feature(f"mpzp-act-boundary:{index}", None, boundary)],
                )
            )
        if intersection is not None:
            feature = _feature(f"mpzp-act:{index}", number, intersection)
            feature["label_point"] = _label_point(intersection)
            layers.append(
                _layer(
                    f"mpzp_act:{index}",
                    {
                        "layer": f"mpzp_act:{index}",
                        "label": f"Część działki objęta aktem {number}",
                        "outline": outline,
                        "line_width": 2,
                        "fill": fill if in_force else None,
                        "fill_alpha": config["mpzp_fill_alpha"] if in_force else None,
                        "pattern": None if in_force else "diagonal-lines",
                        "line_dash": None,
                    },
                    [feature],
                )
            )
    reason = None
    if not layers:
        reason = (
            "Akty znane tylko z KIMPZP nie mają granicy w źródle, więc mapy granic nie narysowano "
            "(udział działki nie został ustalony)."
            if undrawn
            else f"Brak aktu z granicą w zapisanym wyniku ({_mpzp_state_phrase(response)}). "
            "Brak aktu na mapie nie oznacza braku planu."
        )
    notes = [f"Bez granicy w źródle (nienarysowane): {', '.join(undrawn)}."] if layers and undrawn else []
    return _map(
        "mpzp_acts",
        "mpzp_acts",
        "Akty MPZP (granica aktu i część działki; akt nie jest strefą)",
        [*layers, parcel_layer],
        reason,
        notes,
    )


def _pog_map(
    pog: Any, config: Mapping[str, Any], clip: tuple[float, ...], parcel_layer: dict[str, Any]
) -> dict[str, Any]:
    pog_config = config.get("pog")
    theme_id = config["pog_theme"]
    if pog is None or pog_config is None:
        reason = (
            "Snapshot nie zawiera wyniku POG — mapy nie narysowano; brak wyniku nie oznacza braku planu."
            if pog is None
            else "Brak zapisanego stylu POG — mapy nie narysowano."
        )
        return _map("pog", f"pog:{theme_id}", "Plan ogólny gminy", [parcel_layer], reason)
    theme = pog_config["theme"]
    legal = pog_config["legal_statuses"].get(pog.legal_status) or pog_config["legal_statuses"]["unknown"]
    non_binding_pattern = legal.get("pattern")
    dash = legal.get("line_dasharray")
    layers: list[dict[str, Any]] = []
    by_key: dict[str, dict[str, Any]] = {}
    for zone in pog.zones:
        geometry = _clip_geojson(zone.geometry_geojson, clip)
        if geometry is None:
            continue
        style, key, value, class_label = _pog_zone_style(zone, pog_config, theme)
        if non_binding_pattern and not style.get("pattern"):
            style["pattern"] = non_binding_pattern
        style["line_dash"] = list(dash) if dash else None
        feature = _feature(zone.id, zone.symbol or zone.type, geometry)
        feature["label_point"] = _label_point(geometry)
        feature["value"] = value
        feature["class_label"] = class_label
        layer = by_key.get(key)
        if layer is None:
            layer = _layer(f"pog_zone:{key}", style, [])
            by_key[key] = layer
            layers.append(layer)
        layer["features"].append(feature)
    for overlay_id, attribute in _OVERLAY_ORDER:
        overlay = pog_config["overlays"][overlay_id]
        features = [
            _feature(item.id, item.symbol or item.label or item.id, _clip_geojson(item.geometry_geojson, clip))
            for item in getattr(pog, attribute)
        ]
        style = {
            "layer": f"pog_overlay:{overlay_id}",
            "label": f"{overlay['short_label']} — {overlay['label']}",
            "outline": overlay["outline"],
            "line_width": max(2, int(round(float(overlay["line_width"])))),
            "fill": None,
            "fill_alpha": 0,
            "pattern": overlay.get("pattern"),
            "line_dash": overlay.get("line_dasharray"),
        }
        layers.append(_layer(f"pog_overlay:{overlay_id}", style, features))
    reason = None
    if not any(layer["features"] for layer in layers):
        reason = (
            "Wynik POG nie zawiera geometrii stref ani obszarów przecinających działkę "
            f"(zakres danych: {pog.coverage_status}). Brak geometrii nie oznacza braku planu."
        )
    notes = []
    if legal.get("badge"):
        notes.append(f"Status aktu: {legal['label']} — {legal['badge']}.")
    return _map(
        "pog",
        f"pog:{theme_id}",
        f"Tryb tematyczny: {theme['label']}" + _theme_unit(theme),
        [*layers, parcel_layer],
        reason,
        notes,
        theme=theme,
        legal_status=pog.legal_status,
    )


def _theme_unit(theme: Mapping[str, Any]) -> str:
    """Jednostka tematu: symbol (m, %) albo opis wartości bezwymiarowej."""
    if theme.get("kind") != "numeric":
        return ""
    unit = theme.get("unit")
    return f" [{unit}]" if unit and unit != "1" else f" [{theme['unit_label']}]"


def _pog_zone_style(
    zone: Any, pog_config: Mapping[str, Any], theme: Mapping[str, Any]
) -> tuple[dict[str, Any], str, float | None, str | None]:
    zones = pog_config["zones"]
    if theme.get("kind") == "numeric":
        value = getattr(zone, theme["property"], None)
        match = theme_class_for(value, theme.get("classes", ()))
        if match is None:
            null = pog_config["null_style"]
            style = {
                "layer": "pog_null",
                "label": null["label"],
                "outline": null["outline"],
                "line_width": 2,
                "fill": null["fill"],
                "fill_alpha": pog_config["fill_alpha"],
                "pattern": null.get("pattern"),
            }
            return style, "null", value, null["label"]
        style = {
            "layer": f"pog_class:{match['label']}",
            "label": f"{match['label']}",
            "outline": "#5c5c5c",
            "line_width": 2,
            "fill": match["color"],
            "fill_alpha": pog_config["fill_alpha"],
            "pattern": None,
        }
        return style, f"class:{match['label']}", value, match["label"]
    entry = zones.get(zone.type)
    if entry is None:
        unknown = pog_config["unknown_zone"]
        style = {
            "layer": f"pog_zone:{zone.type or 'unknown'}",
            "label": unknown["label"],
            "outline": unknown["outline"],
            "line_width": 2,
            "fill": unknown["fill"],
            "fill_alpha": pog_config["fill_alpha"],
            "pattern": unknown.get("pattern"),
        }
        return style, "unknown", None, unknown["label"]
    style = {
        "layer": f"pog_zone:{zone.type}",
        "label": f"{zone.type} — {entry['label']}",
        "outline": entry["outline"],
        "line_width": 2,
        "fill": entry["fill"],
        "fill_alpha": pog_config["fill_alpha"],
        "pattern": None,
    }
    return style, f"zone:{zone.type}", None, zone.type


def _environment_map(
    response: Any, config: Mapping[str, Any], clip: tuple[float, ...], parcel_layer: dict[str, Any]
) -> dict[str, Any]:
    styles = config["layer_styles"]
    features: dict[str, list[dict[str, Any]]] = {"flood": [], "nature": []}
    for index, risk in enumerate(getattr(response, "risks", None) or []):
        section = risk.section or ("flood" if risk.risk_type in {"flood", "flood_zone"} else "nature")
        label = (
            risk.probability_class or risk.risk_type
            if section == "flood"
            else risk.name or risk.protection_type or risk.risk_type
        )
        features[section].append(
            _feature(risk.feature_id or f"risk:{index}", label, _clip_geojson(risk.geometry_geojson, clip))
        )
    layers = [
        _layer("flood", styles["flood"], features["flood"]),
        _layer("nature", styles["nature"], features["nature"]),
    ]
    cover = getattr(response, "land_cover", None)
    if cover is not None:
        for index, code in enumerate(sorted({f.get("properties", {}).get("class_code", "") for f in cover.features_geojson})):
            items = [f for f in cover.features_geojson if f.get("properties", {}).get("class_code") == code]
            style = {**styles["nature"], "label": items[0].get("properties", {}).get("label", code),
                     "fill": CLASS_COLORS.get(code, "#888888"), "outline": CLASS_COLORS.get(code, "#888888"),
                     "line_dash": None, "pattern": None, "fill_alpha": 100}
            layers.append(_layer("land_cover:" + code, style,
                [_feature(f"cover:{index}:{j}", code, _clip_geojson(f, clip)) for j, f in enumerate(items)], kind="computed"))
        for b in cover.forest.buffers:
            style = {**styles["nature"], "label": f"Las — orientacyjny bufor {b.radius_m:g} m",
                     "fill": "#34732d", "fill_alpha": 30, "pattern": None}
            layers.append(_layer(f"forest_buffer:{b.radius_m:g}", style,
                [_feature(f"forest:{b.radius_m:g}", "bufor lasu", _clip_geojson(b.geometry_geojson, clip))], kind="approximation"))
    if cover is not None and cover.features_geojson:
        buildings = getattr(response, "neighbour_buildings", None)
        if buildings:
            layers.append(_layer("neighbour_buildings", {**styles["nature"], "label": "Budynki BDOT10k",
                "fill": "#897468", "outline": "#514237", "pattern": None},
                [_feature(b.building_id, b.general_function, _clip_geojson(b.geometry_geojson, clip)) for b in buildings.buildings]))
        road = getattr(response, "road_context", None)
        if road and road.nearest:
            layers.append(_layer("road_context", {**styles["network"], "label": "Najbliższa oś jezdni BDOT10k",
                "outline": "#bd7900", "fill": None, "pattern": None},
                [_feature(road.nearest.road_id, "oś jezdni", _clip_geojson(road.nearest.road_geojson, clip))]))
    reason = None
    if not any(layer["features"] for layer in layers):
        statuses = {
            section.section: section.status for section in getattr(response, "risk_sections", None) or []
        }
        reason = (
            "Brak obiektów ISOK/GDOŚ w kadrze mapy "
            f"(status sekcji: powódź — {statuses.get('flood', 'unknown')}, "
            f"ochrona przyrody — {statuses.get('nature', 'unknown')}). "
            "Przy statusie innym niż „sprawdzono” brak obiektów nie oznacza braku ograniczeń."
        )
    result = _map("environment", "environment_risk", "Zagrożenie powodziowe i formy ochrony przyrody",
                  [*layers, parcel_layer], reason)
    if cover is not None and cover.features_geojson:
        result["title"] = "Mapa 5. Pokrycie terenu, lasy, zabudowa i środowisko w otoczeniu działki"
        result["notes"] = ["Pokrycie w promieniu 200 m. Bufory lasu 8/12/16 m to orientacyjne przybliżenie geometryczne, bez interpretacji przepisów."]
        if getattr(response, "road_context", None):
            result["notes"].append(response.road_context.interpretation_note)
    return result


# --- Pomocnicze ------------------------------------------------------------------


def _map(
    map_id: str,
    mode: str,
    mode_label: str,
    layers: list[dict[str, Any]],
    empty_reason: str | None,
    notes: list[str] | None = None,
    *,
    theme: Mapping[str, Any] | None = None,
    legal_status: str | None = None,
) -> dict[str, Any]:
    kept = [layer for layer in layers if layer["features"]]
    return {
        "id": map_id,
        "section": map_id,
        "title": MAP_TITLES[map_id],
        "mode": mode,
        "mode_label": mode_label,
        "layers": kept,
        "legend": _legend(kept, theme),
        "status": "empty" if empty_reason else "rendered",
        "empty_reason": empty_reason,
        "notes": list(notes or []),
        "legal_status": legal_status,
    }


def _legend(layers: Iterable[Mapping[str, Any]], theme: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for layer in layers:
        style = layer["style"]
        pattern = style.get("pattern")
        entries.append(
            {
                "label": style["label"],
                "fill": style.get("fill"),
                "outline": style["outline"],
                "pattern": pattern,
                "pattern_label": _PATTERN_LABELS.get(pattern or "", "wypełnienie jednolite")
                if style.get("fill") or pattern
                else "linia",
                "line_dash": style.get("line_dash"),
                "kind": layer.get("kind", "source"),
                "feature_count": len(layer["features"]),
            }
        )
    if theme is not None and theme.get("kind") == "numeric":
        entries.append(
            {
                "label": f"skala: {theme['scale_description']} Jednostka: {theme['unit_label']}.",
                "fill": None,
                "outline": "#5c5c5c",
                "pattern": None,
                "pattern_label": "opis skali",
                "line_dash": None,
                "kind": "note",
                "feature_count": 0,
            }
        )
    return entries


def _layer(
    layer_id: str,
    style: Mapping[str, Any],
    features: list[dict[str, Any]],
    *,
    kind: str = "source",
) -> dict[str, Any]:
    return {
        "id": layer_id,
        "kind": kind,
        "style": dict(style),
        "features": [item for item in features if item["geometry"] is not None],
    }


def _feature(feature_id: str, label: str | None, geometry: dict[str, Any] | None) -> dict[str, Any]:
    return {"id": str(feature_id), "label": label, "geometry": geometry}


def _clip(geometry: BaseGeometry, clip: tuple[float, ...]) -> dict[str, Any] | None:
    if geometry.is_empty:
        return None
    clipped = shapely.clip_by_rect(geometry, *clip)
    if clipped.is_empty:
        return None
    data = mapping(clipped)
    return {"type": data["type"], "coordinates": round_coordinates(data["coordinates"])}


def _clip_geojson(geojson: Mapping[str, Any] | None, clip: tuple[float, ...]) -> dict[str, Any] | None:
    geometry = _geojson_geometry(geojson)
    if geometry is None:
        return None
    try:
        projected = transform(_TO_2180.transform, shape(geometry))
    except (ValueError, TypeError, AttributeError, KeyError):
        logger.warning("report_map_snapshot_invalid_geometry")
        return None
    if not projected.is_valid and projected.geom_type in {"Polygon", "MultiPolygon"}:
        projected = shapely.make_valid(projected)
    return _clip(projected, clip)


def _geojson_geometry(node: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if not isinstance(node, Mapping):
        return None
    if node.get("type") == "Feature":
        return _geojson_geometry(node.get("geometry"))
    if node.get("type") == "FeatureCollection":
        geometries = [
            geometry
            for feature in node.get("features") or []
            if (geometry := _geojson_geometry(feature)) is not None
        ]
        return {"type": "GeometryCollection", "geometries": geometries} if geometries else None
    return node if node.get("type") else None


def _label_point(geometry: Mapping[str, Any]) -> list[float] | None:
    try:
        point = shape(geometry).representative_point()
    except (ValueError, TypeError, AttributeError):
        return None
    return round_coordinates([point.x, point.y])


def _map_data_provenance(response: Any, map_id: str) -> tuple[list[str], list[int]]:
    """Daty pobrania i wydania danych, z których powstały warstwy danej mapy."""
    sources: list[Any] = []
    parcel = getattr(response, "parcel", None)
    if parcel is not None:
        sources.append(parcel.source)
    if map_id == "mpzp":
        sources.extend(zone.source for zone in getattr(response, "mpzp_zones", None) or [])
    elif map_id == "mpzp_acts":
        state = getattr(response, "mpzp_status", None)
        if state is not None:
            sources.extend(channel.source for channel in state.channels)
    elif map_id == "pog":
        pog = getattr(response, "pog", None)
        if pog is not None:
            sources.append(pog.source)
            if pog.act is not None:
                sources.append(pog.act)
    elif map_id == "environment":
        sources.extend(section.source for section in getattr(response, "risk_sections", None) or [])
        cover = getattr(response, "land_cover", None)
        if cover is not None:
            sources.append(cover.source)
            for name in ("road_context", "neighbour_buildings"):
                section = getattr(response, name, None)
                if section:
                    sources.append(section.source)
    elif map_id == "parcel":
        sources.extend(item.source for item in getattr(response, "infrastructure", None) or [])
    dates = sorted(
        {
            _iso(source.fetched_at) or ""
            for source in sources
            if source is not None and getattr(source, "fetched_at", None) is not None
        }
    )
    releases = sorted(
        {
            int(source.data_release_id)
            for source in sources
            if source is not None and getattr(source, "data_release_id", None)
        }
    )
    if map_id == "mpzp":
        releases = sorted(
            {*releases, *(int(zone.data_release_id) for zone in getattr(response, "mpzp_zones", None) or []
                           if zone.data_release_id)}
        )
    return dates, releases


def _data_release_ids(response: Any) -> list[int]:
    ids: set[int] = set()
    for zone in getattr(response, "mpzp_zones", None) or []:
        if zone.data_release_id:
            ids.add(int(zone.data_release_id))
    pog = getattr(response, "pog", None)
    if pog is not None:
        if pog.act is not None and pog.act.data_release_id:
            ids.add(int(pog.act.data_release_id))
        if pog.source is not None and pog.source.data_release_id:
            ids.add(int(pog.source.data_release_id))
    for source in getattr(response, "sources", None) or []:
        if source.data_release_id:
            ids.add(int(source.data_release_id))
    return sorted(ids)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
