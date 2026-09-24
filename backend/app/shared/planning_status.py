"""Kanoniczny status prawny aktu planistycznego i pokrycie danymi (BK-106).

Status prawny aktu, pokrycie danymi przestrzennymi i operacyjna dostępność
źródła to trzy niezależne fakty. Moduł jest jedynym miejscem, które:

- definiuje kanoniczne wartości wszystkich trzech wymiarów,
- mapuje aliasy i historyczne wartości (``adopted``, ``not_available``,
  ``outdated``, ``complete``) na kanoniczne,
- rozpoznaje urzędowe kody statusu Rejestru Urbanistycznego/INSPIRE,
- rozstrzyga status i pokrycie z obserwacji źródła (tabela decyzyjna).

Reguły nadrzędne (ADR-002):

- status prawny pochodzi wyłącznie z urzędowego kodu źródła; brak kodu daje
  ``unknown``, nigdy lokalne zgadywanie z dat lub obecności geometrii;
- pusta odpowiedź usługi nie jest dowodem braku aktu — ``no_act_confirmed``
  wymaga wskazania urzędowego potwierdzenia;
- awaria źródła daje ``unknown`` albo ostatnią potwierdzoną wartość z datą
  i dostępnością ``stale``; dostępność nigdy nie zastępuje statusu prawnego.

Moduł używa wyłącznie biblioteki standardowej, dzięki czemu mogą go importować
warstwy ``domain`` wszystkich modułów (ADR-001).
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal

LegalStatus = Literal["binding", "project", "in_progress", "superseded", "unknown"]
CoverageStatus = Literal[
    "available", "partial", "act_without_spatial_data", "no_act_confirmed", "unknown"
]
DataAvailability = Literal["current", "stale", "unavailable"]

LEGAL_STATUS_VALUES: Final[tuple[LegalStatus, ...]] = (
    "binding", "project", "in_progress", "superseded", "unknown",
)
COVERAGE_STATUS_VALUES: Final[tuple[CoverageStatus, ...]] = (
    "available", "partial", "act_without_spatial_data", "no_act_confirmed", "unknown",
)
DATA_AVAILABILITY_VALUES: Final[tuple[DataAvailability, ...]] = (
    "current", "stale", "unavailable",
)
BINDING: Final[LegalStatus] = "binding"
NON_BINDING_LEGAL_STATUSES: Final[frozenset[str]] = frozenset(
    {"project", "in_progress", "superseded", "unknown"}
)

# Aliasy kanoniczne: jedyna dopuszczalna nazwa alternatywna. ``outdated`` z
# backlogu i ``superseded`` z context.md oznaczają to samo — kanoniczne jest
# ``superseded``.
LEGAL_STATUS_ALIASES: Final[dict[str, LegalStatus]] = {"outdated": "superseded"}
# Wartości sprzed BK-106. ``adopted`` wymaga zachowanego potwierdzenia
# źródłowego (patrz :func:`upgrade_legacy_legal_status`).
LEGACY_LEGAL_STATUSES: Final[frozenset[str]] = frozenset({"adopted", "not_available"})
COVERAGE_STATUS_ALIASES: Final[dict[str, CoverageStatus]] = {"complete": "available"}

# Urzędowe kody statusu. INSPIRE ProcessStepGeneralValue jest słownikiem pola
# ``status`` w APP 3.0, a nazwy warstw WMS RU kodują te same kroki procesu.
_OFFICIAL_STATUS_CODES: Final[dict[str, LegalStatus]] = {
    # INSPIRE ProcessStepGeneralValue (URI kończy się kodem)
    "legalforce": "binding",
    "adoption": "project",
    "elaboration": "in_progress",
    "obsolete": "superseded",
    # Etykiety słownika w RU (xlink:title) i sufiksy warstw WMS RU
    "prawniewiazacylubrealizowany": "binding",
    "wtrakcieprzyjmowania": "project",
    "wopracowaniu": "in_progress",
    "nieaktualny": "superseded",
    # Jawne etykiety używane przez organy w atrybutach statusu
    "obowiazujacy": "binding",
    "obowiazuje": "binding",
    "projekt": "project",
    "projektplanu": "project",
    "wtrakciesporzadzania": "in_progress",
    "uchylony": "superseded",
    "zastapiony": "superseded",
    "nieobowiazujacy": "superseded",
}
_INSPIRE_PROCESS_STEP_PREFIX: Final[str] = (
    "http://inspire.ec.europa.eu/codelist/ProcessStepGeneralValue/"
)

LEGAL_STATUS_LABELS_PL: Final[dict[str, str]] = {
    "binding": "obowiązuje (potwierdzone urzędowym kodem statusu)",
    "project": "projekt aktu — niewiążący",
    "in_progress": "w trakcie sporządzania — niewiążący",
    "superseded": "nieaktualny (zastąpiony lub uchylony)",
    "unknown": "status prawny nieustalony",
}
COVERAGE_STATUS_LABELS_PL: Final[dict[str, str]] = {
    "available": "dane przestrzenne dostępne dla działki",
    "partial": "dane przestrzenne niepełne",
    "act_without_spatial_data": "akt bez danych przestrzennych dla działki",
    "no_act_confirmed": "urzędowo potwierdzony brak aktu",
    "unknown": "zakres danych nieustalony",
}
DATA_AVAILABILITY_LABELS_PL: Final[dict[str, str]] = {
    "current": "sprawdzone w źródle przy tej analizie",
    "stale": "ostatnia potwierdzona wartość — źródło było niedostępne",
    "unavailable": "źródło niedostępne",
}
NO_GEOMETRY_IS_NOT_NO_PLAN_PL: Final[str] = (
    "Brak geometrii lub pusta odpowiedź usługi nie oznacza braku planu."
)


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.strip().lower())
    return "".join(
        char for char in decomposed if not unicodedata.combining(char) and char.isalnum()
    )


def official_status_code(raw: str | None) -> LegalStatus | None:
    """Zwraca status dla rozpoznanego urzędowego kodu albo ``None``.

    Akceptowane są kody INSPIRE (także jako URI), etykiety słownika RU i
    sufiksy warstw WMS RU. Kanoniczne tokeny aplikacji (np. ``binding``) oraz
    historyczne ``adopted`` NIE są kodami urzędowymi.
    """
    if not raw or not raw.strip():
        return None
    candidates = [raw, raw.rstrip("/").rsplit("/", 1)[-1], raw.rsplit(".", 1)[-1]]
    for candidate in candidates:
        if status := _OFFICIAL_STATUS_CODES.get(_fold(candidate)):
            return status
    return None


def is_official_status_code(raw: str | None) -> bool:
    return official_status_code(raw) is not None


def normalize_official_legal_status(raw: str | None) -> LegalStatus:
    """Normalizuje urzędowy kod statusu; nierozpoznany kod daje ``unknown``.

    ``uchwalony`` celowo nie jest mapowany na ``binding``: uchwalenie nie
    oznacza wejścia w życie, a data uchwały nie jest dowodem obowiązywania.
    """
    return official_status_code(raw) or "unknown"


def canonical_legal_status(value: str | None) -> LegalStatus:
    """Kanoniczna wartość lub alias; historyczne wartości wymagają upgrade."""
    if value in LEGAL_STATUS_VALUES:
        return value  # type: ignore[return-value]
    if value in LEGAL_STATUS_ALIASES:
        return LEGAL_STATUS_ALIASES[value]
    return "unknown"


def upgrade_legacy_legal_status(value: str | None, *, confirmed: bool) -> LegalStatus:
    """Mapuje wartość sprzed BK-106 na kanoniczną.

    ``adopted`` staje się ``binding`` wyłącznie z zachowanym potwierdzeniem
    źródłowym (urzędowy kod lub przypięte wydanie z SHA). Bez niego wynik to
    ``unknown`` — brak dowodu nie może zostać zamieniony w obowiązywanie.
    ``not_available`` oznaczało brak danych, nie brak aktu, więc daje ``unknown``.
    """
    if value == "adopted":
        return "binding" if confirmed else "unknown"
    if value == "not_available":
        return "unknown"
    return canonical_legal_status(value)


def canonical_coverage_status(value: str | None) -> CoverageStatus:
    if value in COVERAGE_STATUS_VALUES:
        return value  # type: ignore[return-value]
    if value in COVERAGE_STATUS_ALIASES:
        return COVERAGE_STATUS_ALIASES[value]
    return "unknown"


def is_binding(value: str | None) -> bool:
    return value == BINDING


def inspire_status_uri(status: LegalStatus) -> str | None:
    """Odwrotne mapowanie do kodu INSPIRE (fixtures, dokumentacja, CLI)."""
    code = {
        "binding": "legalForce",
        "project": "adoption",
        "in_progress": "elaboration",
        "superseded": "obsolete",
    }.get(status)
    return f"{_INSPIRE_PROCESS_STEP_PREFIX}{code}" if code else None


# --- Tabela decyzyjna ---------------------------------------------------------


@dataclass(frozen=True)
class StatusEvidence:
    """Wskazanie źródła, które potwierdziło status lub pokrycie."""

    source_name: str
    official: bool
    reference: str | None = None
    source_id: str | None = None
    raw_value: str | None = None
    confirmed_at: datetime | None = None


@dataclass(frozen=True)
class ConfirmedPogStatus:
    """Ostatnia potwierdzona wartość, używana przy awarii źródła."""

    legal_status: LegalStatus
    coverage_status: CoverageStatus
    confirmed_at: datetime
    legal_evidence: StatusEvidence | None = None
    coverage_evidence: StatusEvidence | None = None


@dataclass(frozen=True)
class PogStatusObservation:
    """Fakty zaobserwowane w źródle dla jednej działki.

    ``source_responded`` oznacza poprawną, sparsowaną odpowiedź źródła
    (także pustą). ``response_complete`` jest dowodem pełnej paginacji, ale nie
    dowodem braku aktu. ``no_act_evidence`` musi wskazywać urzędowe
    potwierdzenie braku aktu (np. pismo organu lub urzędowy rejestr gminy).
    """

    source_responded: bool
    checked_at: datetime
    raw_legal_status: str | None = None
    legal_evidence: StatusEvidence | None = None
    act_found: bool = False
    act_has_spatial_data: bool = False
    spatial_features_on_parcel: int = 0
    zones_cover_parcel: bool = False
    response_complete: bool = False
    no_act_evidence: StatusEvidence | None = None
    previous: ConfirmedPogStatus | None = None


@dataclass(frozen=True)
class PogStatusDecision:
    legal_status: LegalStatus
    coverage_status: CoverageStatus
    data_availability: DataAvailability
    confirmed_at: datetime | None
    legal_evidence: StatusEvidence | None
    coverage_evidence: StatusEvidence | None
    reasons: tuple[str, ...] = ()


def resolve_pog_status(observation: PogStatusObservation) -> PogStatusDecision:
    """Rozstrzyga status prawny i pokrycie niezależnie od siebie."""
    if not observation.source_responded:
        previous = observation.previous
        if previous is not None and previous.legal_status != "unknown":
            return PogStatusDecision(
                legal_status=previous.legal_status,
                coverage_status=previous.coverage_status,
                data_availability="stale",
                confirmed_at=previous.confirmed_at,
                legal_evidence=previous.legal_evidence,
                coverage_evidence=previous.coverage_evidence,
                reasons=("POG_STATUS_STALE",),
            )
        return PogStatusDecision(
            legal_status="unknown",
            coverage_status="unknown",
            data_availability="unavailable",
            confirmed_at=None,
            legal_evidence=None,
            coverage_evidence=None,
            reasons=("POG_SOURCE_UNAVAILABLE",),
        )

    reasons: list[str] = []
    evidence = observation.legal_evidence
    raw = observation.raw_legal_status
    if evidence is not None and evidence.official and is_official_status_code(raw):
        legal_status = normalize_official_legal_status(raw)
    else:
        legal_status = "unknown"
        evidence = None
        if observation.act_found or observation.spatial_features_on_parcel:
            reasons.append("POG_STATUS_NOT_OFFICIAL")

    coverage_evidence: StatusEvidence | None = None
    no_act = observation.no_act_evidence
    if observation.spatial_features_on_parcel > 0:
        complete = observation.zones_cover_parcel and observation.response_complete
        coverage: CoverageStatus = "available" if complete else "partial"
        if not complete:
            reasons.append("POG_COVERAGE_PARTIAL")
    elif observation.act_found:
        if observation.act_has_spatial_data:
            coverage = "partial"
            reasons.append("POG_ACT_WITHOUT_PARCEL_FEATURES")
        else:
            coverage = "act_without_spatial_data"
            reasons.append("POG_ACT_WITHOUT_SPATIAL_DATA")
    elif no_act is not None and no_act.official and no_act.reference:
        coverage = "no_act_confirmed"
        coverage_evidence = no_act
        legal_status = "unknown"
        evidence = None
    else:
        # Pusta odpowiedź bez urzędowego potwierdzenia — także przy pełnej
        # paginacji — nie jest dowodem braku aktu.
        coverage = "unknown"
        reasons.append("POG_EMPTY_RESPONSE_NOT_ABSENCE")

    confirmed_at = (
        evidence.confirmed_at
        if evidence is not None and evidence.confirmed_at is not None
        else observation.checked_at
    )
    return PogStatusDecision(
        legal_status=legal_status,
        coverage_status=coverage,
        data_availability="current",
        confirmed_at=confirmed_at,
        legal_evidence=evidence,
        coverage_evidence=coverage_evidence,
        reasons=tuple(reasons),
    )


def pog_status_notes_pl(
    legal_status: str,
    coverage_status: str,
    data_availability: str,
    confirmed_at: datetime | None = None,
) -> list[str]:
    """Wspólne komunikaty prezentacji (PDF; frontend ma lustrzany moduł TS).

    Dla ``project``, ``in_progress`` i ``unknown`` żaden komunikat nie używa
    słowa „obowiązuje”. Brak geometrii nigdy nie jest opisany jako brak planu.
    """
    notes: list[str] = []
    if legal_status == "project":
        notes.append("Projekt aktu nie jest wiążący i nie może być traktowany jak prawo miejscowe.")
    elif legal_status == "in_progress":
        notes.append("Procedura sporządzania aktu trwa; ustalenia nie są wiążące.")
    elif legal_status == "superseded":
        notes.append("Akt jest nieaktualny — sprawdź akt, który go zastąpił.")
    elif legal_status == "unknown":
        notes.append("Nie potwierdzono statusu prawnego aktu w źródle urzędowym.")
    if coverage_status in {"act_without_spatial_data", "unknown", "partial"}:
        notes.append(NO_GEOMETRY_IS_NOT_NO_PLAN_PL)
    if coverage_status == "no_act_confirmed":
        notes.append("Brak aktu potwierdzono urzędowo — zobacz wskazane potwierdzenie.")
    if data_availability == "stale":
        when = confirmed_at.strftime("%d.%m.%Y") if confirmed_at else "nieznana data"
        notes.append(
            f"Źródło było niedostępne; pokazano ostatnią potwierdzoną wartość z dnia {when}."
        )
    elif data_availability == "unavailable":
        notes.append("Źródło było niedostępne; brak wyniku nie oznacza braku ograniczeń.")
    return notes
