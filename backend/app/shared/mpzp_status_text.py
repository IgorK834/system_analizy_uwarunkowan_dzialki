"""Stany ustalenia MPZP i ich komunikaty wspólne dla API, raportu PDF i pakietu audytowego (AU-103).

Sześć stanów jest rozłącznych i każdy ma własny kod oraz komunikat — interfejs, PDF i paczka audytowa
nie mogą ich zlewać w jedno „nie sprawdzono albo nie znaleziono”:

``available``    ustalono akt (akty) MPZP przecinający działkę;
``ambiguous``    kilka obowiązujących aktów bez rozstrzygnięcia (nic nie jest wybierane po cichu);
``no_match``     źródła odpowiedziały i nie wskazały aktu — obserwacja z dnia analizy, nie dowód braku planu;
``no_coverage``  dla obszaru nie ma usługi planistycznej — brak danych, nie brak planu;
``unavailable``  błąd albo przekroczony czas źródła — nie ustalono, czy plan obowiązuje;
``unknown``      odpowiedź nierozpoznana albo rozpoznanie się nie wykonało.

Moduł nie zależy od frameworków (może być importowany z każdej warstwy).
"""

from __future__ import annotations

from typing import Final, Literal

MpzpStatusValue = Literal[
    "available", "ambiguous", "no_match", "no_coverage", "unavailable", "unknown"
]

MPZP_STATUS_VALUES: Final[tuple[str, ...]] = (
    "available",
    "ambiguous",
    "no_match",
    "no_coverage",
    "unavailable",
    "unknown",
)

MPZP_STATUS_LABELS: Final[dict[str, str]] = {
    "available": "Ustalono akt MPZP",
    "ambiguous": "Wiele planów — brak rozstrzygnięcia",
    "no_match": "Nie znaleziono planu",
    "no_coverage": "Brak pokrycia danymi",
    "unavailable": "Nie sprawdzono — błąd źródła",
    "unknown": "Nie ustalono",
}

MPZP_STATUS_MESSAGES: Final[dict[str, str]] = {
    "available": (
        "Dla działki ustalono co najmniej jeden akt MPZP przecinający jej obszar (źródło, udział powierzchni i "
        "dokument są w tabeli aktów). Wynik jest informacyjny i nie zastępuje wypisu z planu miejscowego."
    ),
    "ambiguous": (
        "Działkę obejmuje więcej niż jeden akt MPZP, a dane nie rozstrzygają, który z nich decyduje o "
        "przeznaczeniu. System nie wybiera aktu: wszystkie uchwały są wymienione i wymagają ręcznej weryfikacji "
        "w treści dokumentów."
    ),
    "no_match": (
        "Usługi odpowiedziały, ale nie wskazały aktu MPZP przecinającego działkę. To obserwacja z dnia analizy, "
        "a nie dowód braku planu ani braku ograniczeń — sprawdź MPZP w gminie."
    ),
    "no_coverage": (
        "Dla obszaru działki nie ma usługi planistycznej (KIMPZP: „brak serwisu dla wskazanego obszaru”), a "
        "Rejestr Urbanistyczny nie wskazał aktu. To brak danych, a nie brak planu — sprawdź MPZP w gminie."
    ),
    "unavailable": (
        "Usługa MPZP nie odpowiedziała poprawnie (błąd albo przekroczony czas), więc nie ustalono, czy dla "
        "działki obowiązuje plan. Błąd źródła nie oznacza braku ograniczeń — ponów analizę później albo sprawdź "
        "MPZP w gminie."
    ),
    "unknown": (
        "Odpowiedź źródeł nie pozwala ustalić, czy dla działki obowiązuje plan (nierozpoznany format albo "
        "rozpoznanie się nie wykonało). Wymagana ręczna weryfikacja MPZP w gminie."
    ),
}

# Zapis sprzed wprowadzenia stanów (snapshot bez ``mpzp_status``) — nie jest żadnym z sześciu stanów.
MPZP_STATUS_MISSING_MESSAGE: Final[str] = (
    "Ten zapis analizy powstał przed wprowadzeniem stanów MPZP — nie ustalono, czy plan obowiązuje. "
    "Odśwież analizę, aby uzyskać stan."
)

# Etykiety kanałów w tabeli i interfejsie.
MPZP_CHANNEL_LABELS: Final[dict[str, str]] = {
    "kimpzp": "KIMPZP (GUGiK)",
    "mpzp_ru": "Rejestr Urbanistyczny (MRiT)",
}

MPZP_CHANNEL_STATUS_LABELS: Final[dict[str, str]] = {
    "available": "wskazał akt",
    "no_match": "nie wskazał aktu",
    "no_coverage": "brak usługi dla obszaru",
    "unavailable": "błąd lub brak odpowiedzi",
    "unknown": "odpowiedź nierozpoznana",
}
