"""Mapowanie pól ``AnalyzeResponse`` na elementy raportu PDF (BK-501).

Każde pole kontraktu API ma tu jednoznaczny odpowiednik (sekcja + element
raportu + rodzaj ustalenia) albo jawne uzasadnienie pominięcia. Test
``tests/test_report_field_mapping.py`` przechodzi po drzewie modelu Pydantic i
wymaga, aby każda ścieżka liścia pasowała do dokładnie jednego pierwszego
wzorca, a każdy wzorzec był użyty — nowe pole API bez decyzji o raporcie
zatrzymuje CI.

Wzorce: ``[]`` oznacza element listy, ``*`` dowolny ciąg znaków (także kropki).
Pierwszy pasujący wzorzec wygrywa, więc wpisy szczegółowe poprzedzają ogólne.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from typing import Final, Literal

MappingKind = Literal["source_fact", "computed", "approximation", "manual", "metadata"]

MAPPING_KIND_LABELS: Final[dict[str, str]] = {
    "source_fact": "fakt źródłowy",
    "computed": "wynik obliczenia",
    "approximation": "przybliżenie",
    "manual": "dane ręczne",
    "metadata": "metadane",
}


@dataclass(frozen=True)
class FieldMapping:
    pattern: str
    section: str
    element: str
    kind: MappingKind
    omitted_reason: str | None = None

    @property
    def omitted(self) -> bool:
        return self.omitted_reason is not None


def _m(pattern: str, section: str, element: str, kind: MappingKind) -> FieldMapping:
    return FieldMapping(pattern, section, element, kind)


def _omit(pattern: str, section: str, reason: str, kind: MappingKind = "metadata") -> FieldMapping:
    return FieldMapping(pattern, section, "pominięte", kind, omitted_reason=reason)


_SOURCES_TABLE = "Tabela 9.1 — rejestr źródeł (nazwa, ID, wersja, URL, data, HTTP, SHA-256, wydanie)"

FIELD_MAPPINGS: Final[tuple[FieldMapping, ...]] = (
    # --- 1. Identyfikacja i geometria ---------------------------------------
    _m("analysis_id", "parcel", "Tabela 1.1 — identyfikator analizy", "metadata"),
    _m("status", "parcel", "Tabela 1.1 — status analizy; macierz 8.1", "metadata"),
    _m("analyzed_at", "parcel", "Tabela 1.1 — data analizy (stan danych)", "metadata"),
    _m("parcel.parcel_identifier", "parcel", "Tabela 1.1 — identyfikator działki", "source_fact"),
    _m("parcel.geometry_geojson", "parcel",
       "Mapa 1 — obrys działki zamrożony w EPSG:2180 (snapshot mapy)", "source_fact"),
    _m("parcel.metrics.*", "parcel",
       "Tabela 1.2 — pole [m², ha], obwód [m], poprawność i naprawa geometrii", "computed"),
    _m("parcel.buildable_area_geojson", "parcel",
       "Mapa 1 — obszar po technicznym odsunięciu od granic", "approximation"),
    _m("parcel.source.*", "sources", f"Tabela 1.1 — źródło geometrii; {_SOURCES_TABLE}", "source_fact"),
    _m("buildable_area_sqm", "parcel", "Tabela 1.2 — szacowany obszar zabudowy [m²]", "approximation"),
    # --- 3. MPZP -------------------------------------------------------------
    _m("mpzp_zones[].intersection_geojson", "mpzp",
       "Mapa 3 — przecięcia stref MPZP (zamrożone w EPSG:2180)", "computed"),
    _m("mpzp_zones[].intersection_area_sqm", "mpzp", "Tabela 3.1 — pole przecięcia [m²]", "computed"),
    _m("mpzp_zones[].intersection_pct", "mpzp", "Tabela 3.1 — udział w działce [%]", "computed"),
    _m("mpzp_zones[].touches_boundary", "mpzp", "Tabela 3.1 — oznaczenie „tylko styk granicy”", "computed"),
    _m("mpzp_zones[].is_dominant", "mpzp",
       "Tabela 3.1 — oznaczenie „największy udział” (pole pomocnicze)", "computed"),
    _m("mpzp_zones[].assignment_method", "mpzp", "Tabela 3.1 — sposób przypisania strefy", "metadata"),
    _m("mpzp_zones[].manual_review_required", "quality",
       "Tabela 3.1 — plakietka weryfikacji; macierz 8.1", "metadata"),
    _m("mpzp_zones[].manual_selection.*", "mpzp",
       "Tabela 3.4 — decyzja użytkownika i przypięty dokument (tryb ręczny)", "manual"),
    _m("mpzp_zones[].parameters[].*", "mpzp",
       "Tabela 3.3 — evidence parametrów (odsyłacze [E#]: strona, segment, SHA-256)", "source_fact"),
    _m("mpzp_zones[].source.*", "sources", f"Tabela 3.1 — kolumna „źródło”; {_SOURCES_TABLE}", "source_fact"),
    _m("mpzp_zones[].*", "mpzp",
       "Tabela 3.1 (symbol, ID, akt, wersja, wydanie) i Tabela 3.2 (przeznaczenie, parametry z jednostkami)",
       "source_fact"),
    _m("manual_zone_required", "mpzp", "Tabela 3.4 — analiza wstrzymana do podania symbolu; macierz 8.1",
       "metadata"),
    _omit("manual_zone_context.document.preview_path", "mpzp",
          "ścieżka endpointu podglądu dokumentu dla UI — w PDF nieaktywna; dokument opisuje SHA-256"),
    _omit("manual_zone_context.raster_preview_source_key", "mpzp",
          "klucz źródła podglądu rastrowego UI — WMS nie jest renderowany w PDF (BK-503)"),
    _omit("manual_zone_context.symbol_max_length", "mpzp", "reguła walidacji formularza UI"),
    _omit("manual_zone_context.symbol_allowed_pattern", "mpzp", "reguła walidacji formularza UI"),
    _m("manual_zone_context.*", "mpzp",
       "Tabela 3.4 — plan, kandydaci, status i SHA-256 przypiętego dokumentu, komunikat", "manual"),
    # --- 4. POG + OUZ/OZS/OSDIS ---------------------------------------------
    _m("pog.schema_version", "sources", "Tabela 9.3 — wersje kontraktów snapshotu", "metadata"),
    _omit("pog.status", "pog",
          "historyczny alias statusu (ADR-002) — raport pokazuje kanoniczne legal_status"),
    _omit("pog.raw_attributes", "pog",
          "surowe atrybuty rekordu źródłowego bez normalizacji — nie są ustaleniem; "
          "dostępne w API, a w pakiecie audytowym (BK-505) tylko gdy katalog źródeł "
          "zezwala na redystrybucję surowych danych"),
    _m("pog.legal_status_evidence.*", "pog", "Tabela 4.1 — podstawa statusu prawnego", "source_fact"),
    _m("pog.coverage_evidence.*", "pog", "Tabela 4.1 — podstawa zakresu danych", "source_fact"),
    _m("pog.act.metadata.metadata_url_verified", "pog",
       "Tabela 4.7 — decyduje o klikalności odnośnika (tylko zweryfikowany HTTPS)", "metadata"),
    _omit("pog.act.metadata.references[]", "pog",
          "lista technicznych odnośników rekordu CSW — pakiet audytowy (BK-505); "
          "raport pokazuje identyfikator i SHA-256 rekordu"),
    _m("pog.act.metadata.*", "pog", "Tabela 4.7 — rekord metadanych CSW (ID, daty, SHA-256)", "source_fact"),
    _m("pog.act.formal_documents[].link_verified", "pog",
       "Tabela 4.8 — decyduje o klikalności odnośnika", "metadata"),
    _m("pog.act.formal_documents[].*", "pog", "Tabela 4.8 — dokumenty formalne aktu", "source_fact"),
    _m("pog.act.*_verified", "pog", "Tabela 4.7 — decyduje o klikalności odnośnika", "metadata"),
    _m("pog.act.*", "pog", "Tabela 4.7 — provenance aktu (ID, wersja, publikacja, wydanie, SHA-256)",
       "source_fact"),
    _m("pog.zones[].geometry_geojson", "pog", "Mapa 4 — strefy w wybranym trybie tematycznym", "computed"),
    _m("pog.zones[].area_sqm", "pog", "Tabela 4.2 — pole przecięcia [m²]", "computed"),
    _m("pog.zones[].area_pct", "pog", "Tabela 4.2 — udział w działce [%]", "computed"),
    _m("pog.zones[].source.*", "sources", f"Tabela 4.2 — kolumna „źródło”; {_SOURCES_TABLE}", "source_fact"),
    _m("pog.zones[].gml_url_verified", "pog", "Tabela 4.2 — decyduje o klikalności odnośnika GML", "metadata"),
    _m("pog.zones[].primary_profile[].*", "pog", "Tabela 4.3 — profil podstawowy (kod, etykieta, słownik)",
       "source_fact"),
    _m("pog.zones[].additional_profiles[].*", "pog", "Tabela 4.3 — profile dodatkowe", "source_fact"),
    _m("pog.zones[].max_*", "pog", "Tabela 4.3 — parametry z jednostkami (bez uśredniania stref)",
       "source_fact"),
    _m("pog.zones[].min_*", "pog", "Tabela 4.3 — parametry z jednostkami (bez uśredniania stref)",
       "source_fact"),
    _m("pog.zones[].*", "pog", "Tabela 4.2 — strefa (ID, symbol, rodzaj, etykieta, wersja obiektu, GML)",
       "source_fact"),
    _m("pog.dominant_zone_id", "pog", "Tabela 4.2 — oznaczenie „największy udział” (pole pomocnicze)",
       "computed"),
    _m("pog.ouz[].geometry_geojson", "pog", "Mapa 4 — OUZ (wzór i obrys przerywany)", "computed"),
    _m("pog.ouz[].area_*", "pog", "Tabela 4.4 — OUZ: pole [m²] i udział [%]", "computed"),
    _m("pog.ouz[].touches_boundary", "pog", "Tabela 4.4 — OUZ: styk granicy", "computed"),
    _m("pog.ouz[].source.*", "sources", _SOURCES_TABLE, "source_fact"),
    _m("pog.ouz[].*", "pog", "Tabela 4.4 — OUZ: ID, symbol, etykieta, wersja obiektu", "source_fact"),
    _m("pog.downtown_areas[].geometry_geojson", "pog", "Mapa 4 — OZS (wzór i obrys kropkowany)", "computed"),
    _m("pog.downtown_areas[].area_*", "pog", "Tabela 4.5 — OZS: pole [m²] i udział [%]", "computed"),
    _m("pog.downtown_areas[].touches_boundary", "pog", "Tabela 4.5 — OZS: styk granicy", "computed"),
    _m("pog.downtown_areas[].source.*", "sources", _SOURCES_TABLE, "source_fact"),
    _m("pog.downtown_areas[].*", "pog", "Tabela 4.5 — OZS: ID, symbol, etykieta, wersja obiektu",
       "source_fact"),
    _m("pog.social_infrastructure_standard_areas[].geometry_geojson", "pog",
       "Mapa 4 — OSDIS (wzór i obrys kreska-kropka)", "computed"),
    _m("pog.social_infrastructure_standard_areas[].area_*", "pog",
       "Tabela 4.6 — OSDIS: pole [m²] i udział [%]", "computed"),
    _m("pog.social_infrastructure_standard_areas[].touches_boundary", "pog",
       "Tabela 4.6 — OSDIS: styk granicy", "computed"),
    _m("pog.social_infrastructure_standard_areas[].source.*", "sources", _SOURCES_TABLE, "source_fact"),
    _m("pog.social_infrastructure_standard_areas[].*", "pog",
       "Tabela 4.6 — OSDIS: ID, symbol, etykieta, wersja obiektu", "source_fact"),
    _m("pog.in_ouz", "pog", "Tabela 4.4 — decyzja „działka w OUZ” wg jawnych progów", "computed"),
    _m("pog.ouz_intersection_*", "pog", "Tabela 4.4 — łączne pole [m²] i udział [%] OUZ", "computed"),
    _m("pog.touches_ouz_boundary", "pog", "Tabela 4.4 — styk z granicą OUZ", "computed"),
    _m("pog.in_downtown_area", "pog", "Tabela 4.5 — decyzja „działka w OZS”", "computed"),
    _m("pog.area_ratio", "pog", "Tabela 4.1 — udział strefy dominującej (pole zgodności v1)", "computed"),
    _m("pog.planning_zone", "pog", "Tabela 4.1 — strefa dominująca (pole zgodności v1)", "source_fact"),
    _m("pog.zone_type", "pog", "Tabela 4.1 — strefa dominująca (pole zgodności v1)", "source_fact"),
    _m("pog.manual_review_required", "quality", "Tabela 4.1 — plakietka weryfikacji; macierz 8.1",
       "metadata"),
    _m("pog.compatibility_assessment.schema_version", "sources", "Tabela 9.3 — wersje kontraktów",
       "metadata"),
    _m("pog.compatibility_assessment.*", "pog",
       "Tabela 4.9 — relacja MPZP–POG: wynik, reguła, stan prawny, pary stref, źródła (analiza informacyjna)",
       "computed"),
    _m("pog.presentation_style.*", "pog", "Mapa 4 — legenda i wersja stylu POG (zamrożone)", "metadata"),
    _m("pog.source.*", "sources", f"Tabela 4.1 — pewność; {_SOURCES_TABLE}", "source_fact"),
    _m("pog.*", "pog", "Tabela 4.1 — status prawny, zakres danych, aktualność, uchwała", "source_fact"),
    # --- 5. Środowisko -------------------------------------------------------
    _m("risks[].geometry_geojson", "environment", "Mapa 5 — obiekty ISOK/GDOŚ (zamrożone, przycięte do kadru)",
       "source_fact"),
    _m("risks[].intersection_*", "environment", "Tabele 5.1/5.2 — pole [m²] i udział [%] przecięcia",
       "computed"),
    _m("risks[].touches_boundary", "environment", "Tabele 5.1/5.2 — styk granicy", "computed"),
    _m("risks[].description", "environment",
       "Tabele 5.1/5.2 — opis prezentacyjny (pochodny z pól strukturalnych)", "metadata"),
    _m("risks[].source.*", "sources", _SOURCES_TABLE, "source_fact"),
    _m("risks[].*", "environment",
       "Tabele 5.1/5.2 — klasa, okres powtarzalności, poziom, forma ochrony, nazwa, ID, ostrzeżenia",
       "source_fact"),
    _m("risk_sections[].schema_version", "sources", "Tabela 9.3 — wersje kontraktów", "metadata"),
    _m("risk_sections[].source.*", "sources", f"Tabela 5.x — źródło sekcji; {_SOURCES_TABLE}",
       "source_fact"),
    _m("risk_sections[].union_*", "environment", "Tabela 5.x — łączne pokrycie (suma mnogościowa)",
       "computed"),
    _m("risk_sections[].*", "environment",
       "Tabela 5.x — status sekcji, relacja, liczność obiektów, powód, ostrzeżenia; macierz 8.1",
       "computed"),
    # --- 6. Teren ------------------------------------------------------------
    _m("terrain.schema_version", "sources", "Tabela 9.3 — wersje kontraktów", "metadata"),
    _m("terrain.relief.schema_version", "sources", "Tabela 9.3 — wersje kontraktów", "metadata"),
    _omit("terrain.relief.profile.line_geojson", "terrain",
          "linia profilu opisana współrzędnymi początku i końca w EPSG:2180 (Tabela 6.4)"),
    _m("terrain.source.*", "sources", f"Tabela 6.1 — źródło; {_SOURCES_TABLE}", "source_fact"),
    _m("terrain.relief.source.*", "sources", f"Tabela 6.5 — źródło rastra; {_SOURCES_TABLE}", "source_fact"),
    _m("terrain.relief.raster.*", "terrain", "Tabela 6.5 — okno rastra WCS, NoData, środowisko", "metadata"),
    _m("terrain.relief.profile.*", "terrain", "Tabela 6.4 i wykres — profil wysokościowy", "computed"),
    _m("terrain.relief.*", "terrain", "Tabele 6.2–6.3 — spadek, klasy nachylenia, ekspozycja", "computed"),
    _m("terrain.*", "terrain", "Tabela 6.1 — Hmin, Hmax, deniwelacja, siatka, status", "computed"),
    # --- 7. Infrastruktura i transport --------------------------------------
    _m("utilities_preview.source.*", "sources", f"Tabela 7.1 — źródło; {_SOURCES_TABLE}", "source_fact"),
    _m("utilities_preview.*", "infrastructure",
       "Tabela 7.1 — pokrycie powiatu danymi GESUT w KIUT (bez geometrii sieci)", "source_fact"),
    _m("infrastructure[].network_geometry_geojson", "infrastructure",
       "Mapa 1 — przebieg sieci (warstwa przybliżenia, bez wniosków obliczeniowych)", "approximation"),
    _m("infrastructure[].protection_zone_geojson", "infrastructure",
       "Mapa 1 — techniczna strefa bufora (przybliżenie)", "approximation"),
    _m("infrastructure[].source.*", "sources", _SOURCES_TABLE, "source_fact"),
    _m("infrastructure[].*", "infrastructure",
       "Tabela 7.2 — bufor [m], pole [m²], podstawa i pewność reguły, wpływ na obszar zabudowy",
       "approximation"),
    # --- 8. Jakość, 9. Źródła, bezpieczeństwo -------------------------------
    # --- 8. Macierz jakości (BK-504) ------------------------------------------
    _m("section_quality.legend", "quality",
       "Tabela 8.2 — legenda statusów, świeżości i kodów powodów", "metadata"),
    _m("section_quality.sections[].*", "quality",
       "Tabela 8.1 — macierz sekcji: status, źródło, pobranie, wydanie, weryfikacja, "
       "świeżość, powody; tabela 8.3 — wiek na dzień eksportu", "metadata"),
    _m("section_quality.*", "quality",
       "Tabele 8.1 i 9.3 — wersja polityki, punkt odniesienia, pochodzenie i suma "
       "kontrolna macierzy", "metadata"),
    _m("warnings[].*", "quality", "Tabela 8.4 — ostrzeżenia analizy (kod, poziom, źródło, treść)",
       "metadata"),
    _m("sources[].*", "sources", _SOURCES_TABLE, "source_fact"),
    _omit("access_token", "sources",
          "sekret dostępu do API — nigdy nie jest umieszczany w dokumencie PDF"),
)


def _compile(pattern: str) -> re.Pattern[str]:
    return re.compile("^" + re.escape(pattern).replace(r"\*", ".*") + "$")


@lru_cache(maxsize=1)
def _compiled() -> tuple[tuple[re.Pattern[str], FieldMapping], ...]:
    return tuple((_compile(item.pattern), item) for item in FIELD_MAPPINGS)


def mapping_for(path: str) -> FieldMapping | None:
    """Pierwszy wzorzec pasujący do ścieżki liścia (``a.b[].c``)."""
    for regex, item in _compiled():
        if regex.match(path):
            return item
    return None


def unmapped(paths: Iterable[str]) -> list[str]:
    return sorted(path for path in paths if mapping_for(path) is None)


def unused_patterns(paths: Iterable[str]) -> list[str]:
    used = {mapping.pattern for path in paths if (mapping := mapping_for(path)) is not None}
    return [item.pattern for item in FIELD_MAPPINGS if item.pattern not in used]
