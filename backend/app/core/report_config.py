"""Konfiguracja raportu PDF: klauzula informacyjna, kolory mapy i progi pewności.

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


# --- Wymiary miniatury mapy ---
# Stałe, testowalne wymiary PNG. Proporcja zbliżona do 3:2 mieści się czytelnie
# w układzie A4 raportu.
MAP_IMAGE_WIDTH: int = 900
MAP_IMAGE_HEIGHT: int = 600
# Wewnętrzny margines miniatury w pikselach (letterbox wokół geometrii).
MAP_IMAGE_PADDING_PX: int = 24
# Kolor tła FALLBACK miniatury (jasny, neutralny). Docelowy wygląd to opcjonalny
# podkład WMS GetMap; ten kolor jest używany tylko gdy basemap jest wyłączony
# albo pobranie podkładu się nie powiodło (ścieżka MVP offline).
MAP_BACKGROUND_RGB: tuple[int, int, int] = (245, 247, 249)
# Współczynnik rozszerzenia BBOX o 10% z każdej strony, zgodnie z kontraktem.
MAP_BBOX_EXPANSION_RATIO: float = 0.10


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


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
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
    line_rgb=_hex_to_rgb("#0d5137"),
    line_width=4,
    fill_rgb=_hex_to_rgb("#176c4b"),
    fill_alpha=int(0.12 * 255),
)
BUILDABLE_AREA_LAYER_STYLE = MapLayerStyle(
    layer="buildable_area",
    label="Obszar zabudowy (przybliżenie techniczne)",
    line_rgb=_hex_to_rgb("#8a4813"),
    line_width=3,
    fill_rgb=_hex_to_rgb("#c96a1f"),
    fill_alpha=int(0.18 * 255),
)
NETWORK_LAYER_STYLE = MapLayerStyle(
    layer="networks",
    label="Sieci uzbrojenia terenu",
    line_rgb=_hex_to_rgb("#1769aa"),
    line_width=3,
    fill_rgb=None,
    fill_alpha=0,
)
PROTECTION_ZONE_LAYER_STYLE = MapLayerStyle(
    layer="protection_zones",
    label="Strefy ochronne sieci",
    line_rgb=_hex_to_rgb("#8f2525"),
    line_width=2,
    fill_rgb=_hex_to_rgb("#c43d3d"),
    fill_alpha=int(0.22 * 255),
)
RISK_LAYER_STYLE = MapLayerStyle(
    layer="risks",
    label="Ryzyka i formy ochrony",
    line_rgb=_hex_to_rgb("#54277d"),
    line_width=3,
    fill_rgb=_hex_to_rgb("#7b3fb3"),
    fill_alpha=int(0.20 * 255),
)


# --- Plan ogólny gminy (BK-403) ------------------------------------------------
# Paleta, etykiety i wzory POG NIE są definiowane tutaj: pochodzą ze wspólnego
# artefaktu ``shared/pog-presentation.json`` (ten sam plik zasila mapę i legendę
# frontendu). Raport używa stylu zapisanego w snapshocie analizy, a dla analiz
# sprzed BK-403 — bieżącej wersji z jawną adnotacją.
POG_ZONE_FILL_ALPHA: int = int(0.55 * 255)
POG_OVERLAY_ORDER: tuple[str, ...] = ("ouz", "downtown", "social_infrastructure_standard")


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
            line_rgb=_hex_to_rgb(unknown["outline"]),
            line_width=2,
            fill_rgb=_hex_to_rgb(unknown["fill"]),
            fill_alpha=POG_ZONE_FILL_ALPHA,
            pattern=str(unknown["pattern"]),
        )
    return MapLayerStyle(
        layer=f"pog_zone:{code}",
        label=f"{code} — {entry['label']}",
        line_rgb=_hex_to_rgb(entry["outline"]),
        line_width=2,
        fill_rgb=_hex_to_rgb(entry["fill"]),
        fill_alpha=POG_ZONE_FILL_ALPHA,
    )


def pog_overlay_layer_style(overlay_id: str, style: ReportPogStyle) -> MapLayerStyle:
    overlay = style.style["overlays"][overlay_id]
    dash = overlay.get("line_dasharray")
    return MapLayerStyle(
        layer=f"pog_overlay:{overlay_id}",
        label=f"{overlay['short_label']} — {overlay['label']}",
        line_rgb=_hex_to_rgb(overlay["outline"]),
        line_width=max(2, int(round(float(overlay["line_width"])))),
        fill_rgb=None,
        pattern=overlay.get("pattern"),
        line_dash=tuple(float(item) for item in dash) if dash else None,
    )
