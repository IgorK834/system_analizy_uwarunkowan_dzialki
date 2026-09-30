"""Konfiguracja raportu PDF: klauzula, konfiguracja map, style warstw i progi pewności.

Ten moduł jest jedynym źródłem prawdy dla stałych raportu. Klauzula
informacyjna, paleta warstw miniatury mapy oraz progi opisowe pewności są tu
scentralizowane, aby generator raportu (``report.py``), generator miniatury
(``report_map.py``) oraz testy korzystały z tych samych wartości i nie
rozjeżdżały się między sobą ani z frontendem.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.pog_presentation import (
    PogPresentationError,
    load_pog_presentation,
    style_snapshot,
)

# Klauzula informacyjna umieszczana na końcu każdego raportu. Treść jest
# wymagana dosłownie przez kontrakt zadania i nie może różnić się między
# generatorem a testami — dlatego istnieje wyłącznie w tym jednym miejscu.
REPORT_DISCLAIMER: str = (
    "Raport ma charakter informacyjny i nie stanowi oficjalnego dokumentu "
    "urzędowego ani porady prawnej."
)

# Krótki tytuł dokumentu prezentowany na stronie tytułowej raportu.
REPORT_TITLE: str = "Raport analizy uwarunkowań przestrzennych działki"

# Nazwa systemu wyświetlana w stopce i na stronie tytułowej.
REPORT_SYSTEM_NAME: str = "System analizy uwarunkowań przestrzennych działki"


# --- Progi opisowego poziomu pewności (confidence) ---
# Confidence jest liczbą 0-1. Opis słowny musi być spójny z etykietami
# procentowymi pokazywanymi w panelu wyników frontendu.
CONFIDENCE_HIGH_THRESHOLD: float = 0.8
CONFIDENCE_MEDIUM_THRESHOLD: float = 0.5

CONFIDENCE_LABEL_HIGH: str = "wysoka"
CONFIDENCE_LABEL_MEDIUM: str = "średnia"
CONFIDENCE_LABEL_LOW: str = "niska"
CONFIDENCE_LABEL_REVIEW: str = "wymaga weryfikacji"


def describe_confidence(
    confidence: float | None,
    manual_review_required: bool = False,
) -> str:
    """Zwraca opisowy poziom pewności spójny z panelem wyników frontendu.

    Ręczna weryfikacja ma pierwszeństwo nad progami liczbowymi — jeżeli źródło
    wymaga weryfikacji, nie prezentujemy fałszywej pewności, zgodnie z zasadą
    "nie ukrywaj niepewności" z context.md.
    """
    if manual_review_required:
        return CONFIDENCE_LABEL_REVIEW
    if confidence is None:
        return CONFIDENCE_LABEL_REVIEW
    if confidence >= CONFIDENCE_HIGH_THRESHOLD:
        return CONFIDENCE_LABEL_HIGH
    if confidence >= CONFIDENCE_MEDIUM_THRESHOLD:
        return CONFIDENCE_LABEL_MEDIUM
    return CONFIDENCE_LABEL_LOW


# --- Mapy raportu (BK-503, ADR-010) ---
# Wersja konfiguracji renderowania. Każda zmiana wymiarów, stylów warstw,
# fontu lub reguł kadru MUSI podbić wersję: konfiguracja jest zamrażana w
# snapshocie analizy i stary raport rysuje się zapisaną wersją.
REPORT_MAP_CONFIG_VERSION: str = "report-map/2026.09.29-1"
MAP_IMAGE_WIDTH: int = 900
MAP_IMAGE_HEIGHT: int = 600
# Neutralne tło mapy, gdy snapshot nie wskazuje zapisanego artefaktu podkładu.
MAP_BACKGROUND_RGB: tuple[int, int, int] = (245, 247, 249)
# Margines kadru: 10% rozpiętości obrysu działki z każdej strony.
MAP_BBOX_EXPANSION_RATIO: float = 0.10
# Minimalna rozpiętość kadru w metrach (działka punktowa/bardzo mała).
MAP_MIN_SPAN_M: float = 10.0
# Maksymalna długość podziałki jako ułamek szerokości mapy.
MAP_SCALE_BAR_MAX_FRACTION: float = 0.25
# Font napisów na mapie (podziałka, strzałka północy, symbole stref). Rodzina
# i rozmiar są częścią znaczenia obrazu; plik fontu i jego SHA-256 opisują
# środowisko renderowania (bajtowy hash PNG).
REPORT_MAP_FONT: dict[str, object] = {
    "family": "DejaVu Sans",
    "file": "DejaVuSans.ttf",
    "bold_file": "DejaVuSans-Bold.ttf",
    "size_px": 15,
}
REPORT_MAP_FONT_DIRS: tuple[str, ...] = (
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/dejavu",
    "/usr/local/share/fonts",
    "/Library/Fonts",
)


@dataclass(frozen=True)
class MapLayerStyle:
    """Styl pojedynczej warstwy miniatury mapy.

    Kolory są semantycznie spójne z konfiguracją warstw frontendu
    (``frontend/lib/layerStyles.ts``), aby raport i mapa w UI używały tej samej
    palety domenowej. ``fill_rgb=None`` oznacza warstwę wyłącznie liniową.
    """

    layer: str
    label: str
    line_rgb: tuple[int, int, int]
    line_width: int
    fill_rgb: tuple[int, int, int] | None = None
    fill_alpha: int = 0
    # Wzór wypełnienia (BK-403): informacja nie może zależeć tylko od barwy.
    pattern: str | None = None
    line_dash: tuple[float, ...] | None = None


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    """Zamienia zapis heksadecymalny koloru na krotkę RGB 0-255."""
    cleaned = value.lstrip("#")
    return (
        int(cleaned[0:2], 16),
        int(cleaned[2:4], 16),
        int(cleaned[4:6], 16),
    )



# Paleta odwzorowuje kolory z frontend/lib/layerStyles.ts. Wartości alpha są
# przeliczone z fill-opacity frontendu (0-1) na kanał alfa 0-255.
PARCEL_LAYER_STYLE = MapLayerStyle(
    layer="parcel",
    label="Obrys działki",
    line_rgb=hex_to_rgb("#0d5137"),
    line_width=4,
    fill_rgb=hex_to_rgb("#176c4b"),
    fill_alpha=int(0.12 * 255),
)
BUILDABLE_AREA_LAYER_STYLE = MapLayerStyle(
    layer="buildable_area",
    label="Obszar zabudowy (przybliżenie techniczne)",
    line_rgb=hex_to_rgb("#8a4813"),
    line_width=3,
    fill_rgb=hex_to_rgb("#c96a1f"),
    fill_alpha=int(0.18 * 255),
)
NETWORK_LAYER_STYLE = MapLayerStyle(
    layer="networks",
    label="Sieci uzbrojenia terenu",
    line_rgb=hex_to_rgb("#1769aa"),
    line_width=3,
    fill_rgb=None,
    fill_alpha=0,
)
PROTECTION_ZONE_LAYER_STYLE = MapLayerStyle(
    layer="protection_zones",
    label="Strefy ochronne sieci",
    line_rgb=hex_to_rgb("#8f2525"),
    line_width=2,
    fill_rgb=hex_to_rgb("#c43d3d"),
    fill_alpha=int(0.22 * 255),
)


# --- Plan ogólny gminy (BK-403) ------------------------------------------------
# Paleta, etykiety i wzory POG NIE są definiowane tutaj: pochodzą ze wspólnego
# artefaktu ``shared/pog-presentation.json`` (ten sam plik zasila mapę i legendę
# frontendu). Raport używa stylu zapisanego w snapshocie analizy, a dla analiz
# sprzed BK-403 — bieżącej wersji z jawną adnotacją.
POG_ZONE_FILL_ALPHA: int = int(0.55 * 255)


@dataclass(frozen=True)
class ReportPogStyle:
    style: dict[str, Any]
    from_snapshot: bool

    @property
    def version(self) -> str:
        return str(self.style.get("style_version", ""))

    @property
    def sha256(self) -> str:
        return str(self.style.get("style_sha256", ""))


def report_pog_style(pog: Any) -> ReportPogStyle | None:
    """Styl POG dla raportu: zapisany w snapshocie albo bieżący (adnotacja)."""
    frozen = getattr(pog, "presentation_style", None) if pog is not None else None
    if frozen is not None:
        data = frozen.model_dump() if hasattr(frozen, "model_dump") else dict(frozen)
        return ReportPogStyle(style=data, from_snapshot=True)
    try:
        return ReportPogStyle(style=style_snapshot(load_pog_presentation()), from_snapshot=False)
    except PogPresentationError:
        return None


def pog_zone_layer_style(code: str | None, style: ReportPogStyle) -> MapLayerStyle:
    zones: dict[str, Any] = style.style.get("zones", {})
    entry = zones.get(code or "") if code else None
    if entry is None:
        unknown = style.style["unknown_zone"]
        return MapLayerStyle(
            layer=f"pog_zone:{code or 'unknown'}",
            label=str(unknown["label"]),
            line_rgb=hex_to_rgb(unknown["outline"]),
            line_width=2,
            fill_rgb=hex_to_rgb(unknown["fill"]),
            fill_alpha=POG_ZONE_FILL_ALPHA,
            pattern=str(unknown["pattern"]),
        )
    return MapLayerStyle(
        layer=f"pog_zone:{code}",
        label=f"{code} — {entry['label']}",
        line_rgb=hex_to_rgb(entry["outline"]),
        line_width=2,
        fill_rgb=hex_to_rgb(entry["fill"]),
        fill_alpha=POG_ZONE_FILL_ALPHA,
    )


def pog_overlay_layer_style(overlay_id: str, style: ReportPogStyle) -> MapLayerStyle:
    overlay = style.style["overlays"][overlay_id]
    dash = overlay.get("line_dasharray")
    return MapLayerStyle(
        layer=f"pog_overlay:{overlay_id}",
        label=f"{overlay['short_label']} — {overlay['label']}",
        line_rgb=hex_to_rgb(overlay["outline"]),
        line_width=max(2, int(round(float(overlay["line_width"])))),
        fill_rgb=None,
        pattern=overlay.get("pattern"),
        line_dash=tuple(float(item) for item in dash) if dash else None,
    )


# --- Warstwy map raportu v2 (BK-503) ------------------------------------------
# Zagrożenie powodziowe i formy ochrony przyrody mają odrębne style i wzory,
# aby rozróżnienie nie zależało wyłącznie od barwy.
FLOOD_LAYER_STYLE = MapLayerStyle(
    layer="risk:flood",
    label="Obszar zagrożenia powodziowego (ISOK)",
    line_rgb=hex_to_rgb("#1d4f91"),
    line_width=2,
    fill_rgb=hex_to_rgb("#3b82c4"),
    fill_alpha=int(0.30 * 255),
    pattern="horizontal-lines",
)
NATURE_LAYER_STYLE = MapLayerStyle(
    layer="risk:nature",
    label="Forma ochrony przyrody (GDOŚ)",
    line_rgb=hex_to_rgb("#2f6b1f"),
    line_width=2,
    fill_rgb=hex_to_rgb("#5aa33b"),
    fill_alpha=int(0.25 * 255),
    pattern="dots",
    line_dash=(3.0, 2.0),
)
# MPZP nie ma urzędowej palety: kolory są przydzielane deterministycznie według
# kolejności stref w wyniku, a symbol strefy jest podpisany na mapie, więc
# odczyt nie zależy od barwy.
MPZP_ZONE_PALETTE: tuple[tuple[str, str], ...] = (
    ("#e7b86a", "#8a5a14"),
    ("#d77a61", "#7a3322"),
    ("#8fb8de", "#2c5d8a"),
    ("#a8d08d", "#4b7a2a"),
    ("#c7a0d9", "#61377a"),
    ("#f2d16b", "#8a7314"),
    ("#9fd3c7", "#2b6e61"),
    ("#e6a1b6", "#853a52"),
)
MPZP_ZONE_FILL_ALPHA: int = int(0.45 * 255)

REPORT_MAP_LAYER_STYLES: dict[str, MapLayerStyle] = {
    "parcel": PARCEL_LAYER_STYLE,
    "buildable_area": BUILDABLE_AREA_LAYER_STYLE,
    "network": NETWORK_LAYER_STYLE,
    "protection_zone": PROTECTION_ZONE_LAYER_STYLE,
    "flood": FLOOD_LAYER_STYLE,
    "nature": NATURE_LAYER_STYLE,
}


def _rgb_to_hex(rgb: tuple[int, int, int] | None) -> str | None:
    return None if rgb is None else "#%02x%02x%02x" % rgb


def layer_style_to_dict(style: MapLayerStyle) -> dict[str, Any]:
    """Styl warstwy jako JSON — zamrażany w snapshocie mapy."""
    return {
        "layer": style.layer,
        "label": style.label,
        "outline": _rgb_to_hex(style.line_rgb),
        "line_width": style.line_width,
        "fill": _rgb_to_hex(style.fill_rgb),
        "fill_alpha": style.fill_alpha,
        "pattern": style.pattern,
        "line_dash": list(style.line_dash) if style.line_dash else None,
    }


def report_map_pog_config(pog: Any, theme_id: str) -> dict[str, Any] | None:
    """Styl POG mapy raportu: zapisany w analizie + temat i style statusu aktu.

    Paleta stref i wzory nakładek pochodzą ze stylu zapisanego w wyniku POG
    (BK-403); progi tematu i style statusu prawnego — z tej samej wersji
    ``shared/pog-presentation.json`` w chwili zamrożenia mapy. Całość jest
    zapisywana w snapshocie, więc późniejsza zmiana configu nie zmienia mapy.
    """
    style = report_pog_style(pog)
    if style is None:
        return None
    try:
        presentation = load_pog_presentation()
    except PogPresentationError:
        return None
    theme = presentation.presentation.theme(theme_id)
    return {
        "style_version": style.version,
        "style_sha256": style.sha256,
        "style_from_analysis": style.from_snapshot,
        "themes_style_version": presentation.style_version,
        "zones": style.style["zones"],
        "unknown_zone": style.style["unknown_zone"],
        "null_style": style.style["null_style"],
        "overlays": style.style["overlays"],
        "fill_alpha": POG_ZONE_FILL_ALPHA,
        "theme": theme.model_dump(mode="json"),
        "legal_statuses": {
            item.status: item.model_dump(mode="json")
            for item in presentation.presentation.legal_statuses
        },
    }


def report_map_render_config(
    pog: Any,
    theme_id: str,
    basemap: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pełna konfiguracja renderowania mapy — zamrażana w snapshocie analizy."""
    return {
        "config_version": REPORT_MAP_CONFIG_VERSION,
        "crs": "EPSG:2180",
        "width_px": MAP_IMAGE_WIDTH,
        "height_px": MAP_IMAGE_HEIGHT,
        "background": _rgb_to_hex(MAP_BACKGROUND_RGB),
        "margin_ratio": MAP_BBOX_EXPANSION_RATIO,
        "min_span_m": MAP_MIN_SPAN_M,
        "scale_bar_max_fraction": MAP_SCALE_BAR_MAX_FRACTION,
        "font": {
            "family": REPORT_MAP_FONT["family"],
            "file": REPORT_MAP_FONT["file"],
            "bold_file": REPORT_MAP_FONT["bold_file"],
            "size_px": REPORT_MAP_FONT["size_px"],
        },
        "layer_styles": {
            key: layer_style_to_dict(style) for key, style in REPORT_MAP_LAYER_STYLES.items()
        },
        "mpzp_palette": [list(item) for item in MPZP_ZONE_PALETTE],
        "mpzp_fill_alpha": MPZP_ZONE_FILL_ALPHA,
        "pog_theme": theme_id,
        "pog": report_map_pog_config(pog, theme_id) if pog is not None else None,
        "basemap": basemap or {"mode": "neutral"},
    }
