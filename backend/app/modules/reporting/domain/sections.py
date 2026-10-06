"""Architektura informacji raportu v2 (BK-501, ADR-010).

Dziesięć sekcji w ustalonej kolejności oraz słownik rodzajów ustaleń. Raport
nie zawiera syntetycznego „scoringu inwestycyjnego”: każda wartość jest
oznaczona jako fakt źródłowy, wynik obliczenia, przybliżenie albo dane ręczne.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from app.shared.data_quality import QUALITY_STATUS_LABELS_PL

REPORT_LAYOUT_VERSION: Final[str] = "report-v2/2026.09.29-1"

FindingKind = Literal["source_fact", "computed", "approximation", "manual"]


@dataclass(frozen=True)
class ReportSection:
    id: str
    number: int
    title: str


REPORT_SECTIONS: Final[tuple[ReportSection, ...]] = (
    ReportSection("parcel", 1, "Identyfikacja i geometria działki"),
    ReportSection("summary", 2, "Podsumowanie wykrytych uwarunkowań"),
    ReportSection("mpzp", 3, "Miejscowy plan zagospodarowania przestrzennego (MPZP)"),
    ReportSection("pog", 4, "Plan ogólny gminy (POG) oraz OUZ, OZS i OSDIS"),
    ReportSection("environment", 5, "Środowisko: zagrożenie powodziowe i ochrona przyrody"),
    ReportSection("terrain", 6, "Teren (NMT)"),
    ReportSection("infrastructure", 7, "Infrastruktura i transport"),
    ReportSection("quality", 8, "Jakość i kompletność analizy"),
    ReportSection("sources", 9, "Źródła i provenance"),
    ReportSection("limitations", 10, "Ograniczenia interpretacyjne"),
)
SECTION_BY_ID: Final[dict[str, ReportSection]] = {
    section.id: section for section in REPORT_SECTIONS
}

FINDING_KIND_LABELS: Final[dict[str, str]] = {
    "source_fact": "fakt źródłowy",
    "computed": "wynik obliczenia",
    "approximation": "przybliżenie",
    "manual": "dane ręczne",
    "model_reading": "odczyt automatyczny",
}
FINDING_KIND_DESCRIPTIONS: Final[dict[str, str]] = {
    "source_fact": (
        "wartość przepisana z urzędowego źródła (atrybut rekordu, zapis uchwały, "
        "status aktu) bez przekształceń innych niż normalizacja jednostki"
    ),
    "computed": (
        "wynik deterministycznego obliczenia systemu na geometrii w EPSG:2180 "
        "(pole i udział przecięcia, deniwelacja, spadek)"
    ),
    "approximation": (
        "szacunek techniczny o jawnych założeniach (odsunięcie od granic, bufor "
        "sieci) — nie jest ustaleniem prawnym ani pomiarem"
    ),
    "manual": (
        "wartość wprowadzona lub wskazana przez użytkownika — wymaga weryfikacji "
        "w materiale źródłowym"
    ),
    "model_reading": (
        "wartość zaproponowana przez model językowy i potwierdzona programowo wyłącznie co do tego, że "
        "cytat i liczba występują w tekście uchwały — kandydat do ręcznej weryfikacji, nie interpretacja prawna"
    ),
}



@dataclass(frozen=True)
class QualitySection:
    """Sekcja analizy oceniana w macierzy jakości (BK-504) i jej sekcja w raporcie."""

    key: str
    report_section: str
    label: str


# Kolejność wierszy macierzy jest stała: podsumowanie (sekcja 2), macierz jakości
# (sekcja 8), API i UI pokazują te same sekcje w tej samej kolejności.
QUALITY_SECTIONS: Final[tuple[QualitySection, ...]] = (
    QualitySection("parcel", "parcel", "Działka i geometria"),
    QualitySection("mpzp", "mpzp", "MPZP"),
    QualitySection("pog", "pog", "POG — strefy planistyczne"),
    QualitySection("pog_overlays", "pog", "POG — OUZ, OZS, OSDIS"),
    QualitySection("flood", "environment", "Zagrożenie powodziowe (ISOK)"),
    QualitySection("nature", "environment", "Formy ochrony przyrody (GDOŚ)"),
    QualitySection("terrain", "terrain", "Teren (NMT)"),
    QualitySection("utilities", "infrastructure", "Uzbrojenie terenu (KIUT)"),
    QualitySection("transport", "infrastructure", "Transport i dostęp do drogi"),
    QualitySection("mpzp_pog_relation", "pog", "Relacja MPZP–POG"),
)
QUALITY_SECTION_BY_KEY: Final[dict[str, QualitySection]] = {
    section.key: section for section in QUALITY_SECTIONS
}

# Jedyny dopuszczalny zapis braku wartości w raporcie (null ≠ 0).
NOT_SPECIFIED: Final[str] = "nie określono"

# Statusy kompletności sekcji — jedna definicja dla macierzy jakości, podsumowania,
# API i UI (BK-504): ``app.shared.data_quality``. Brak pokrycia i błąd źródła są
# odrębnymi statusami, a ręczna weryfikacja jest osobną flagą, nie statusem.
SECTION_STATUS_LABELS: Final[dict[str, str]] = dict(QUALITY_STATUS_LABELS_PL)
