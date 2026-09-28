"""Stałe teksty oceny relacji MPZP–POG (BK-205), bez zależności od frameworków.

Wydzielone z ``app.core.planning_compatibility``, aby kontrakt Pydantic
(``app.schemas.analyze``) mógł ich używać bez cyklu importów.
"""

from __future__ import annotations

from typing import Final

COMPATIBILITY_INFORMATIONAL_NOTICE: Final[str] = (
    "Ocena relacji MPZP–POG jest analizą informacyjną opartą na jawnej tabeli "
    "reguł systemu. Nie jest opinią prawną i nie przesądza o prawnej możliwości "
    "zabudowy działki — o niej rozstrzygają ustalenia obowiązujących aktów i "
    "właściwy organ."
)
# Etykiety świadomie nie używają słów „zgodne/dopuszczalne”: ocena opisuje
# wynik tabeli reguł, nie prawną możliwość zabudowy.
COMPATIBILITY_STATUS_LABELS_PL: Final[dict[str, str]] = {
    "compatible": "brak wskazanej rozbieżności w tabeli reguł",
    "incompatible": "potencjalna rozbieżność funkcji — wymaga weryfikacji",
    "uncertain": "nierozstrzygnięte — wymaga analizy ustaleń obu aktów",
    "not_applicable": "nie dotyczy — brak aktu ustanawiającego obowiązek do porównania",
    "unknown": "nieustalone — brak danych lub reguły",
}
LEGACY_AGGREGATION_NOTE: Final[str] = (
    "Brak agregacji: zapis historyczny nie zawiera par stref."
)
