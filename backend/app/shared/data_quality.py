"""Wspólne pojęcia jakości danych: status sekcji, świeżość i redystrybucja (BK-504/505).

Status kompletności sekcji, świeżość danych i zgoda na redystrybucję to trzy
niezależne fakty (ADR-011):

- **status** opisuje wynik sprawdzenia według kontraktu źródła (dostępne,
  częściowe, brak pokrycia, niedostępne, błąd, nieustalone…). Brak pokrycia
  i błąd źródła są różnymi statusami — żaden z nich nie jest „brakiem
  ograniczenia”;
- **świeżość** (``fresh`` / ``stale`` / ``unknown``) wynika z jawnej reguły
  wieku przypisanej do *źródła* i z jawnego punktu odniesienia czasu. Brak reguły
  albo nieprawidłowy czas daje ``unknown`` — nigdy wymyślony TTL. ``fetched_at``
  to chwila pobrania danych, nie wejścia aktu w życie;
- **redystrybucja** decyduje, czy pochodna albo surowa treść źródła może trafić
  do pakietu audytowego.

Moduł używa wyłącznie biblioteki standardowej, dzięki czemu mogą go importować
warstwy ``domain`` wszystkich modułów oraz katalog źródeł (ADR-001).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final, Literal

# --- Status sekcji ---------------------------------------------------------------

SectionQualityStatus = Literal[
    "available",
    "partial",
    "no_coverage",
    "unavailable",
    "error",
    "unknown",
    "out_of_scope",
    "awaiting_input",
]

QUALITY_STATUSES: Final[tuple[SectionQualityStatus, ...]] = (
    "available",
    "partial",
    "no_coverage",
    "unavailable",
    "error",
    "unknown",
    "out_of_scope",
    "awaiting_input",
)

QUALITY_STATUS_LABELS_PL: Final[dict[str, str]] = {
    "available": "sprawdzono",
    "partial": "częściowo",
    "no_coverage": "brak pokrycia źródła",
    "unavailable": "źródło niedostępne",
    "error": "błąd sprawdzenia",
    "unknown": "nieustalone",
    "out_of_scope": "poza zakresem",
    "awaiting_input": "oczekuje na dane użytkownika",
}

QUALITY_STATUS_DESCRIPTIONS_PL: Final[dict[str, str]] = {
    "available": (
        "źródło zwróciło wynik zgodny z kontraktem; pusty wynik oznacza sprawdzony "
        "brak obiektów, a nie brak ograniczeń poza zakresem źródła"
    ),
    "partial": (
        "wynik jest niepełny albo obniżonej pewności (np. strefa bez wektora, "
        "podgląd zamiast geometrii, sprzeczne parametry)"
    ),
    "no_coverage": (
        "źródło potwierdziło brak danych dla tego obszaru (np. poza zasięgiem NMT "
        "albo powiat nie publikuje GESUT) — to nie jest błąd źródła i nie dowodzi "
        "braku ograniczeń"
    ),
    "unavailable": (
        "próba pobrania nie powiodła się (limit czasu, błąd HTTP, odpowiedź "
        "niezgodna z kontraktem) — wynik nie jest znany"
    ),
    "error": "nieoczekiwany błąd przetwarzania sekcji — wynik nie jest znany",
    "unknown": (
        "brak zapisanego wyniku albo niemożliwe rozstrzygnięcie; brak danych nie "
        "oznacza braku ograniczenia ani braku planu"
    ),
    "out_of_scope": (
        "sekcja nie jest analizowana, bo brakuje potwierdzonego kontraktu źródła"
    ),
    "awaiting_input": "analiza wstrzymana do czasu podania danych przez użytkownika",
}

# --- Świeżość ------------------------------------------------------------------------

FreshnessState = Literal["fresh", "stale", "unknown"]
FreshnessBasis = Literal["source_declared_interval", "project_decision"]

FRESHNESS_STATES: Final[tuple[FreshnessState, ...]] = ("fresh", "stale", "unknown")
FRESHNESS_LABELS_PL: Final[dict[str, str]] = {
    "fresh": "aktualne wg reguły",
    "stale": "starsze niż reguła",
    "unknown": "świeżość nieustalona",
}
FRESHNESS_DESCRIPTIONS_PL: Final[dict[str, str]] = {
    "fresh": (
        "wiek danych w punkcie odniesienia mieści się w regule wieku przypisanej do "
        "źródła"
    ),
    "stale": (
        "wiek danych w punkcie odniesienia przekracza regułę wieku źródła — dane "
        "mogą nie odzwierciedlać bieżącego stanu źródła"
    ),
    "unknown": (
        "brak reguły wieku dla źródła, brak czasu pobrania albo czas nieprawidłowy "
        "— system nie zgaduje terminu ważności"
    ),
}
FRESHNESS_BASIS_LABELS_PL: Final[dict[str, str]] = {
    "source_declared_interval": "częstotliwość deklarowana przez źródło",
    "project_decision": "decyzja projektowa (nie deklaracja właściciela danych)",
}

FRESHNESS_NO_POLICY: Final[str] = "FRESHNESS_NO_POLICY"
FRESHNESS_NO_SOURCE: Final[str] = "FRESHNESS_NO_SOURCE"
FRESHNESS_NO_FETCH_TIME: Final[str] = "FRESHNESS_NO_FETCH_TIME"
FRESHNESS_INVALID_TIME: Final[str] = "FRESHNESS_INVALID_TIME"
FRESHNESS_FUTURE_TIME: Final[str] = "FRESHNESS_FUTURE_TIME"
FRESHNESS_OLDER_THAN_POLICY: Final[str] = "FRESHNESS_OLDER_THAN_POLICY"

# Zegary hostów i źródeł mogą się minimalnie rozjeżdżać. Pobranie „z przyszłości”
# w granicach tolerancji liczymy jako wiek 0; dalsze — jako czas nieprawidłowy.
CLOCK_SKEW_TOLERANCE: Final[timedelta] = timedelta(minutes=5)
_SECONDS_PER_DAY: Final[int] = 86_400
MAX_POLICY_AGE_DAYS: Final[int] = 3650


@dataclass(frozen=True)
class FreshnessRule:
    """Jawna reguła wieku przypisana do źródła w katalogu (nigdy globalna)."""

    max_age_days: int
    basis: FreshnessBasis
    rationale: str

    def __post_init__(self) -> None:
        if not 1 <= self.max_age_days <= MAX_POLICY_AGE_DAYS:
            raise ValueError(
                f"max_age_days musi mieścić się w zakresie 1–{MAX_POLICY_AGE_DAYS}."
            )
        if not self.rationale.strip():
            raise ValueError("Reguła świeżości wymaga uzasadnienia (rationale).")


@dataclass(frozen=True)
class FreshnessVerdict:
    """Wynik oceny świeżości w konkretnym punkcie odniesienia."""

    state: FreshnessState
    reason_code: str | None
    age_seconds: int | None
    max_age_days: int | None
    basis: FreshnessBasis | None


def unknown_freshness(
    reason_code: str, rule: FreshnessRule | None = None
) -> FreshnessVerdict:
    """Werdykt ``unknown`` z jawnym powodem (bez wieku)."""
    return FreshnessVerdict(
        state="unknown",
        reason_code=reason_code,
        age_seconds=None,
        max_age_days=rule.max_age_days if rule else None,
        basis=rule.basis if rule else None,
    )


def evaluate_freshness(
    fetched_at: datetime | None,
    reference_at: datetime,
    rule: FreshnessRule | None,
) -> FreshnessVerdict:
    """Ocenia świeżość ``fetched_at`` względem ``reference_at`` i reguły źródła.

    Zasady (od najsilniejszej):

    1. brak ``fetched_at`` → ``unknown`` (``FRESHNESS_NO_FETCH_TIME``);
    2. czas bez strefy → ``unknown`` (``FRESHNESS_INVALID_TIME``): nie wiadomo, do
       którego momentu się odnosi;
    3. pobranie późniejsze niż punkt odniesienia + tolerancja → ``unknown``
       (``FRESHNESS_FUTURE_TIME``); w tolerancji wiek wynosi 0;
    4. brak reguły → ``unknown`` (``FRESHNESS_NO_POLICY``), ale zmierzony wiek jest
       zwracany informacyjnie;
    5. wiek > ``max_age_days`` → ``stale``, w przeciwnym razie ``fresh``
       (wiek równy limitowi jest jeszcze świeży).

    ``reference_at`` musi mieć strefę czasową — jego brak to błąd wywołującego.
    """
    if reference_at.tzinfo is None or reference_at.utcoffset() is None:
        raise ValueError("reference_at musi być czasem ze strefą (timezone-aware).")
    if fetched_at is None:
        return unknown_freshness(FRESHNESS_NO_FETCH_TIME, rule)
    if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
        return unknown_freshness(FRESHNESS_INVALID_TIME, rule)

    delta = reference_at - fetched_at
    if delta < -CLOCK_SKEW_TOLERANCE:
        return unknown_freshness(FRESHNESS_FUTURE_TIME, rule)
    age_seconds = max(0, int(delta.total_seconds()))

    if rule is None:
        return FreshnessVerdict("unknown", FRESHNESS_NO_POLICY, age_seconds, None, None)
    if age_seconds > rule.max_age_days * _SECONDS_PER_DAY:
        return FreshnessVerdict(
            "stale", FRESHNESS_OLDER_THAN_POLICY, age_seconds, rule.max_age_days, rule.basis
        )
    return FreshnessVerdict("fresh", None, age_seconds, rule.max_age_days, rule.basis)


def format_age_pl(age_seconds: int | None) -> str:
    """Czytelny wiek: ``brak danych``, ``mniej niż 1 dzień`` albo ``N dni``."""
    if age_seconds is None:
        return "brak danych"
    days = age_seconds // _SECONDS_PER_DAY
    if days == 0:
        return "mniej niż 1 dzień"
    if days == 1:
        return "1 dzień"
    return f"{days} dni"


# --- Powody (kody) ---------------------------------------------------------------------

# Kody przyczyn statusu sekcji i kody przyczyn źródeł, które mogą się w nich
# pojawić. Nieznany kod jest pokazywany dosłownie (bez zgadywania znaczenia).
QUALITY_REASON_LABELS_PL: Final[dict[str, str]] = {
    # Świeżość
    FRESHNESS_OLDER_THAN_POLICY: "dane starsze niż reguła wieku źródła",
    FRESHNESS_FUTURE_TIME: "czas pobrania późniejszy niż punkt odniesienia (nieprawidłowy)",
    FRESHNESS_INVALID_TIME: "czas pobrania bez strefy czasowej (nieprawidłowy)",
    FRESHNESS_NO_POLICY: "brak reguły wieku dla źródła — terminu ważności nie zgadujemy",
    FRESHNESS_NO_FETCH_TIME: "brak czasu pobrania danych",
    FRESHNESS_NO_SOURCE: "brak źródła danych dla sekcji",
    # Działka
    "PARCEL_GEOMETRY_MISSING": "nie ustalono geometrii działki",
    "GEOMETRY_REPAIRED": "geometrię działki naprawiono przed obliczeniami",
    "SOURCE_RECORD_MISSING": "brak zapisanego rekordu źródła geometrii",
    # MPZP
    "MPZP_MANUAL_ZONE_REQUIRED": "gmina nie udostępnia wektora stref — wymagany symbol strefy",
    "MPZP_NOT_DETERMINED": "nie znaleziono albo nie sprawdzono planu (nie dowodzi braku planu)",
    "MPZP_MANUAL_ZONE": "symbol strefy podany ręcznie",
    "MPZP_DOCUMENT_CANDIDATE": "strefa przypisana bez wektora wydzieleń (obniżona pewność)",
    "MPZP_LEGACY_SNAPSHOT": "snapshot sprzed wersjonowania stref MPZP",
    "MPZP_PARAMETER_CONFLICT": "sprzeczne wartości parametrów w uchwale",
    "MPZP_SHARE_UNDETERMINED": "udział strefy w działce nieustalony",
    "MPZP_REVIEW_REQUIRED": "parametry lub przypisanie wymagają weryfikacji",
    # POG
    "POG_RESULT_MISSING": "snapshot nie zawiera wyniku POG (nie dowodzi braku planu)",
    "POG_SOURCE_UNAVAILABLE": "źródło POG niedostępne",
    "POG_COVERAGE_UNKNOWN": "pokrycie danymi POG nieustalone",
    "POG_COVERAGE_PARTIAL": "dane POG pokrywają działkę częściowo",
    "POG_ACT_WITHOUT_SPATIAL_DATA": "akt istnieje, ale nie opublikowano danych przestrzennych",
    "POG_DATA_STALE": "źródło niedostępne — użyto ostatniego potwierdzonego stanu",
    "POG_NO_ACT_CONFIRMED": "urzędowo potwierdzono brak aktu POG",
    "POG_REVIEW_REQUIRED": "wynik POG wymaga ręcznej weryfikacji",
    # Ryzyka, teren
    "RISK_SECTION_NOT_RECORDED": "zapisany wynik nie zawiera statusu sprawdzenia sekcji",
    "TERRAIN_RELIEF_NOT_AVAILABLE": "spadek, ekspozycja i profil nie zostały policzone",
    # KIUT
    "KIUT_COVERAGE_NOT_CHECKED": "nie sprawdzono pokrycia KIUT",
    "KIUT_COUNTY_NOT_PUBLISHED": "powiat nie publikuje GESUT w KIUT",
    "KIUT_COVERAGE_UNKNOWN": "nie rozstrzygnięto pokrycia KIUT",
    "KIUT_PREVIEW_ONLY": "podgląd nie jest geometrią sieci — brak odległości i liczby sieci",
    # Transport, relacja
    "NO_SOURCE_CONTRACT": "brak potwierdzonego kontraktu źródła danych (BK-305)",
    "COMPATIBILITY_NOT_ASSESSED": "nie wykonano oceny relacji MPZP–POG",
    "COMPATIBILITY_REVIEW_REQUIRED": "relacja MPZP–POG wymaga weryfikacji",
    # Ścieżki decyzji oceny relacji MPZP–POG (services/pog_scenarios.py)
    "POG_SOURCE_MISSING": "brak wyniku POG — relacji nie ustalono",
    "POG_STATUS_UNKNOWN": "nie potwierdzono statusu prawnego POG w źródle urzędowym",
    "MPZP_ZONES_MISSING": "POG obowiązuje, ale nie ustalono strefy MPZP (brak danych ≠ brak ograniczeń)",
    "POG_ZONES_MISSING": "POG obowiązuje, ale nie ustalono stref POG na działce",
    "NO_SPATIAL_PAIRS": "brak par stref MPZP–POG ustalonych przestrzennie",
    "POG_PROJECT_NOT_BINDING": "POG ma status projektu — ocena relacji nie dotyczy",
    "POG_PROCEDURE_IN_PROGRESS": "akt POG w procedurze — ocena relacji nie dotyczy",
    "POG_SUPERSEDED": "akt POG nieaktualny — ocena relacji nie jest wykonywana",
    "DERIVED_SECTION": "sekcja wyliczona z innych sekcji — bez własnego źródła",
    # Rejestr źródeł
    "SOURCE_ID_UNRESOLVED": "źródło bez identyfikatora z katalogu",
    "SOURCE_NOT_IN_CATALOG": "źródło nie występuje w katalogu źródeł",
    "NO_SOURCE_RECORD": "brak metadanych źródła",
    "SOURCE_REVIEW_REQUIRED": "źródło oznaczyło wynik jako wymagający weryfikacji",
    "LEGACY_QUALITY_RECONSTRUCTED": "ocena odtworzona ze snapshotu sprzed BK-504",
    # Kody źródeł (adaptery)
    "SERVICE_TIMEOUT": "przekroczono limit czasu usługi",
    "SERVICE_HTTP_ERROR": "usługa zwróciła błąd HTTP",
    "SERVICE_REPORTED_ERROR": "usługa zgłosiła błąd",
    "INVALID_RESPONSE": "odpowiedź usługi niezgodna z kontraktem",
    "NO_COVERAGE_SENTINEL": "usługa potwierdziła brak pokrycia obszaru",
    "UNEXPECTED_ERROR": "nieoczekiwany błąd przetwarzania",
    "LEGACY_SNAPSHOT": "zapis sprzed wprowadzenia sekcji",
    "MISSING_MEASUREMENT": "usługa nie zwróciła pomiaru",
    "VECTOR_SOURCE_NOT_CONFIRMED": "kontrakt wektorowy źródła niepotwierdzony",
    "SOURCE_NOT_RUNNABLE": "źródło zablokowane przez katalog",
    "TRANSPORT_ERROR": "błąd transportu",
    "HTTP_ERROR": "błąd HTTP",
    "OUTSIDE_COVERAGE": "obszar poza zasięgiem danych",
}


def reason_label_pl(code: str) -> str:
    """Etykieta kodu przyczyny; nieznany kod jest zwracany dosłownie."""
    return QUALITY_REASON_LABELS_PL.get(code, f"kod źródła: {code}")


# --- Hash kanoniczny ---------------------------------------------------------------


def canonical_json(payload: Any) -> str:
    """Kanoniczny JSON (posortowane klucze, bez spacji, UTF-8, bez NaN)."""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def canonical_sha256(payload: Any) -> str:
    """SHA-256 kanonicznego JSON — hash merytoryczny, niezależny od kolejności kluczy."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


# --- Redystrybucja -----------------------------------------------------------------------

Redistribution = Literal["allowed", "derived_only", "forbidden", "unconfirmed"]
REDISTRIBUTION_VALUES: Final[tuple[Redistribution, ...]] = (
    "allowed",
    "derived_only",
    "forbidden",
    "unconfirmed",
)
REDISTRIBUTION_LABELS_PL: Final[dict[str, str]] = {
    "allowed": "dozwolona (dane surowe i pochodne)",
    "derived_only": "tylko dane pochodne (bez surowych zbiorów)",
    "forbidden": "zabroniona",
    "unconfirmed": "niepotwierdzona — traktowana jak zakaz",
}


def allows_derived(policy: str | None) -> bool:
    """Czy warstwy pochodne źródła mogą trafić do pakietu."""
    return policy in {"allowed", "derived_only"}


def allows_raw(policy: str | None) -> bool:
    """Czy surowe dane źródła mogą trafić do pakietu (tylko ``allowed``)."""
    return policy == "allowed"
