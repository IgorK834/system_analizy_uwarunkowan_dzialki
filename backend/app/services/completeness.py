"""Kompletność analizy i status nagłówkowy (AU-104, ADR-022).

Przed AU-104 status ``complete`` wymagał przypisania MPZP z lokalnego wektora (``vector_intersection``), który
istnieje tylko po ręcznym imporcie — w konfiguracji domyślnej status nagłówkowy był zawsze ``partial`` (0 z 22
udanych analiz korpusu) i nie niósł informacji. Nowa definicja jest jawna, wersjonowana (``RULE_VERSION``) i liczona
z tego, co analiza faktycznie ustaliła, sekcja po sekcji:

* **sekcja sprawdzona** (``checked``) — źródło zwróciło wynik zgodny z kontraktem (``available`` w macierzy jakości
  sekcji) albo, dla MPZP, wszystkie odpytane kanały odpowiedziały poprawnie i nie wskazały obowiązującego planu
  (``no_match``). Pusty wynik niedziałającego źródła, brak pokrycia, błąd i wynik częściowy NIE są sprawdzeniem;
* **MPZP** może być ustalone przez akt i dokument (``assignment_method = document_candidate``), nie tylko przez wektor
  lokalny — wektor jest wyższą jakością (udział strefy w działce), nie warunkiem. Symbol podany ręcznie
  (``manual_user_input``) i snapshot sprzed wersjonowania (``legacy``) nie są ustaleniem systemu;
* **poza zakresem** (``out_of_scope``) — sekcje bez kontraktu źródła, uzbrojenie terenu, dla którego jest
  tylko podgląd pokrycia KIUT (bez analizy odległości), oraz relacja MPZP–POG, gdy brak strefy MPZP do porównania
  (``no_match``) albo strefa MPZP nie ma geometrii (``document_candidate``) — para stref jest wtedy nierozstrzygalna z założenia.
  Nie wchodzą do ``total``, ale zawsze są wymienione w komunikacie — nie są po cichu pomijane;
* ``complete`` ⇔ wszystkie sekcje w zakresie są sprawdzone. Status prawny POG, ręczna weryfikacja i świeżość to
  osobne fakty: nie obniżają kompletności, lecz trafiają do ``notes`` i do komunikatu.

Funkcja nie zgaduje: brak danych nie jest brakiem ograniczenia, a ``null`` nie jest zerem.
"""

from __future__ import annotations

from typing import Final

from app.modules.reporting.domain.sections import QUALITY_SECTIONS
from app.schemas.analyze import AnalyzeResponse
from app.schemas.source import (
    Completeness,
    CompletenessSection,
    SectionQuality,
    SectionQualityMatrix,
)
from app.services.mpzp_parser_hybrid import WARNING_LLM_CANDIDATES_REJECTED, WARNING_LLM_UNAVAILABLE
from app.services.section_quality import build_section_quality

# 1.1 (Task 23.2–23.3): transport (droga) i zabudowa sąsiednia mają kontrakt źródła (BDOT10k), więc wchodzą do zakresu.
RULE_VERSION: Final[str] = "completeness/1.2"

SHORT_LABELS: Final[dict[str, str]] = {
    "parcel": "geometria działki",
    "mpzp": "MPZP",
    "pog": "strefy POG",
    "pog_overlays": "OUZ, OZS, OSDIS",
    "flood": "zagrożenie powodziowe",
    "nature": "ochrona przyrody",
    "terrain": "teren (NMT)",
    "utilities": "uzbrojenie terenu",
    "transport": "droga i dostęp",
    "buildings": "zabudowa sąsiednia",
    "land_cover": "pokrycie terenu i lasy",
    "mpzp_pog_relation": "relacja MPZP–POG",
}

# Przypisania, które system ustalił sam (z wektora albo z aktu i dokumentu).
_SYSTEM_ASSIGNMENTS: Final[frozenset[str]] = frozenset({"vector_intersection", "document_candidate"})

# Ostrzeżenia, po których MPZP z wektora nie jest ustalone dla całej działki albo wymaga rozstrzygnięcia nakładania.
_VECTOR_COVERAGE_WARNINGS: Final[frozenset[str]] = frozenset({"MPZP_PARTIAL_COVERAGE", "MPZP_ZONES_OVERLAP"})
# Tryb z modelem językowym bez modelu albo z odrzuceniami bramek: wynik deterministyczny, nie pełnowartościowy.
_LLM_DEGRADATION_WARNINGS: Final[frozenset[str]] = frozenset(
    {WARNING_LLM_UNAVAILABLE, WARNING_LLM_CANDIDATES_REJECTED}
)

# Powody, dla których sekcja jest poza zakresem (stałe kody, jak w macierzy jakości).
REASON_PREVIEW_ONLY: Final[str] = "KIUT_PREVIEW_ONLY"
REASON_NO_SOURCE_CONTRACT: Final[str] = "NO_SOURCE_CONTRACT"
REASON_NO_MPZP_TO_COMPARE: Final[str] = "NO_MPZP_ZONE_TO_COMPARE"
REASON_MPZP_WITHOUT_GEOMETRY: Final[str] = "MPZP_WITHOUT_GEOMETRY"


def build_completeness(
    response: AnalyzeResponse,
    *,
    matrix: SectionQualityMatrix | None = None,
    origin: str = "stored",
) -> Completeness:
    """Kompletność odpowiedzi; ``matrix`` domyślnie z odpowiedzi (albo wystawiona na jej podstawie)."""
    quality = matrix or response.section_quality or build_section_quality(response)
    by_key = {item.section: item for item in quality.sections}
    mpzp_state, mpzp_basis, mpzp_codes = _mpzp(response, by_key["mpzp"])
    states: dict[str, tuple[str, str | None, list[str]]] = {"mpzp": (mpzp_state, mpzp_basis, mpzp_codes)}
    sections: list[CompletenessSection] = []
    for spec in QUALITY_SECTIONS:
        item = by_key[spec.key]
        if spec.key == "mpzp":
            state, basis, extra = states["mpzp"]
        elif spec.key == "utilities":
            state, basis, extra = "out_of_scope", None, [REASON_PREVIEW_ONLY]
        elif spec.key == "mpzp_pog_relation":
            state, basis, extra = _relation(item, mpzp_state, mpzp_basis, pog_state=states["pog"][0])
        else:
            state, basis, extra = ("checked" if item.status == "available" else "missing"), None, []
        states[spec.key] = (state, basis, extra)
        sections.append(
            CompletenessSection(
                section=spec.key,  # type: ignore[arg-type]
                label=spec.label,
                short_label=SHORT_LABELS[spec.key],
                report_section=spec.report_section,
                state=state,  # type: ignore[arg-type]
                quality_status=item.status,
                basis=basis,
                manual_review_required=item.manual_review_required,
                reason_codes=list(dict.fromkeys([*extra, *item.reason_codes])),
            )
        )
    in_scope = [item for item in sections if item.state != "out_of_scope"]
    checked = sum(1 for item in in_scope if item.state == "checked")
    complete = bool(in_scope) and checked == len(in_scope)
    notes = _notes(response, sections)
    return Completeness(
        rule_version=RULE_VERSION,
        checked=checked,
        total=len(in_scope),
        complete=complete,
        headline=_headline(sections, checked, len(in_scope), complete),
        notes=notes,
        sections=sections,
        origin=origin,  # type: ignore[arg-type]
    )


def with_completeness(response: AnalyzeResponse, *, update_status: bool = True) -> AnalyzeResponse:
    """Odpowiedź z kompletnością, komunikatem nagłówkowym i — domyślnie — statusem ``complete``/``partial``.

    Istniejąca kompletność nie jest nadpisywana. ``update_status=False`` zostawia ``status`` bez zmian (analiza
    wstrzymana do czasu podania symbolu strefy zachowuje własny status).
    """
    if response.completeness is not None:
        return response
    completeness = build_completeness(response)
    update: dict[str, object] = {"completeness": completeness, "headline": completeness.headline}
    if update_status:
        update["status"] = "complete" if completeness.complete else "partial"
    return response.model_copy(update=update)


def completeness_to_snapshot(completeness: Completeness) -> dict[str, object]:
    """Zapis JSONB; ``origin`` jest ustalany przy odczycie."""
    return completeness.model_dump(mode="json", exclude={"origin"})


def completeness_from_snapshot(
    raw: dict | None, response: AnalyzeResponse
) -> Completeness:
    """Odczyt zapisanej kompletności albo — dla zapisu sprzed AU-104 — jej odtworzenie z macierzy jakości.

    Odtworzona kompletność ma ``origin=reconstructed`` i nie zmienia zapisanego ``status``: stary status
    nagłówkowy pochodzi z poprzedniej definicji i pozostaje, jaki był.
    """
    if raw is not None:
        return Completeness.model_validate({**raw, "origin": "stored"})
    return build_completeness(response, origin="reconstructed")


# --- reguły sekcji ---------------------------------------------------------------------------------------


def _mpzp(response: AnalyzeResponse, quality: SectionQuality) -> tuple[str, str | None, list[str]]:
    zones = response.mpzp_zones
    state = response.mpzp_status
    if response.manual_zone_required:
        return "missing", None, []
    if zones:
        methods = {zone.assignment_method for zone in zones}
        if not methods <= _SYSTEM_ASSIGNMENTS:
            # Symbol podany ręcznie albo snapshot sprzed wersjonowania — to nie jest ustalenie systemu.
            return "missing", None, []
        if state is not None and state.status == "ambiguous":
            return "missing", None, [state.reason_code]
        if any(parameter.conflict_group_id for zone in zones for parameter in zone.parameters):
            # Dwie sprzeczne wartości w jednym akcie, z których parser nie wybiera: strefa jest, ale jej parametr
            # wymaga rozstrzygnięcia przez człowieka — to wynik niepełny, nie sprawdzenie.
            return "missing", None, ["MPZP_PARAMETER_CONFLICT"]
        warning_codes = {warning.code for warning in response.warnings}
        blocking = sorted(
            warning_codes & (_LLM_DEGRADATION_WARNINGS | (_VECTOR_COVERAGE_WARNINGS if "vector_intersection" in methods else set()))
        )
        if blocking:
            return "missing", None, blocking
        basis = "vector_intersection" if methods == {"vector_intersection"} else "document_candidate"
        return "checked", basis, []
    if state is not None and state.status == "no_match":
        # Wszystkie odpytane kanały odpowiedziały poprawnie i nie wskazały obowiązującego planu. To NIE jest dowód
        # braku planu w ogóle (RU jest niepełny), ale jest sprawdzeniem w zakresie możliwości źródeł.
        return "checked", "no_match", []
    return "missing", None, [state.reason_code] if state is not None else []


def _relation(
    quality: SectionQuality, mpzp_state: str, mpzp_basis: str | None, *, pog_state: str
) -> tuple[str, str | None, list[str]]:
    """Relacja MPZP–POG jest wynikiem pochodnym i przestrzennym (pary stref liczone na przecięciu geometrii w EPSG:2180).

    Bez strefy MPZP (``no_match``) nie ma czego porównywać, a strefa z aktu i dokumentu (``document_candidate``) nie ma
    geometrii, więc pary są nierozstrzygalne z założenia (``MPZP_POG_PAIRS_NOT_SPATIAL``) — w obu przypadkach relacja jest
    poza zakresem, a nie „brakiem”. Przy wektorze MPZP nierozstrzygnięta relacja pozostaje brakiem.
    """
    if quality.status == "available":
        return "checked", None, []
    if pog_state != "checked":
        return "missing", None, []  # drugi wejściowy element (POG) sam jest niesprawdzony — relacji nie ma z czego liczyć
    if mpzp_state == "checked" and mpzp_basis == "no_match":
        return "out_of_scope", None, [REASON_NO_MPZP_TO_COMPARE]
    if mpzp_state == "checked" and mpzp_basis == "document_candidate":
        return "out_of_scope", None, [REASON_MPZP_WITHOUT_GEOMETRY]
    return "missing", None, []


# --- komunikaty ------------------------------------------------------------------------------------------


def _join(labels: list[str]) -> str:
    return ", ".join(labels)


def _headline(sections: list[CompletenessSection], checked: int, total: int, complete: bool) -> str:
    missing = [item.short_label for item in sections if item.state == "missing"]
    out_of_scope = [item.short_label for item in sections if item.state == "out_of_scope"]
    review = [item.short_label for item in sections if item.state == "checked" and item.manual_review_required]
    if complete:
        text = f"Sprawdzono wszystkie {total} sekcji w zakresie analizy."
    elif total:
        text = f"{checked} z {total} sekcji sprawdzono; brak: {_join(missing)}."
    else:
        text = "Brak sekcji w zakresie analizy."
    if out_of_scope:
        text += f" Poza zakresem: {_join(out_of_scope)}."
    if review:
        text += f" Do weryfikacji ręcznej: {_join(review)}."
    return text


def _notes(response: AnalyzeResponse, sections: list[CompletenessSection]) -> list[str]:
    notes: list[str] = []
    by_key = {item.section: item for item in sections}
    mpzp = by_key["mpzp"]
    if mpzp.state == "checked" and mpzp.basis == "document_candidate":
        notes.append(
            "MPZP ustalono z aktu i dokumentu (assignment_method=document_candidate), bez lokalnego wektora stref — "
            "udział strefy w działce jest nieustalony."
        )
    if mpzp.state == "checked" and mpzp.basis == "no_match":
        notes.append(
            "MPZP: odpytane kanały nie wskazały obowiązującego planu; Rejestr Urbanistyczny jest niepełny, więc "
            "to nie jest dowód braku planu."
        )
    pog = response.pog
    if by_key["pog"].state == "checked" and pog is not None and pog.legal_status != "binding":
        notes.append(
            f"POG: status prawny „{pog.legal_status}” — plan ogólny nie jest (jeszcze) aktem wiążącym; "
            "kompletność danych nie przesądza o obowiązywaniu."
        )
    if by_key["utilities"].state == "out_of_scope":
        notes.append("Uzbrojenie terenu: dostępny jest tylko podgląd pokrycia KIUT, bez analizy odległości do sieci.")
    return notes
