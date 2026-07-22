"""Konfiguracja raportu PDF: klauzula informacyjna, kolory mapy i progi pewności.

Ten moduł jest jedynym źródłem prawdy dla stałych raportu. Klauzula
informacyjna, paleta warstw miniatury mapy oraz progi opisowe pewności są tu
scentralizowane, aby generator raportu (``report.py``), generator miniatury
(``report_map.py``) oraz testy korzystały z tych samych wartości i nie
rozjeżdżały się między sobą ani z frontendem.
"""

from __future__ import annotations

from dataclasses import dataclass

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
