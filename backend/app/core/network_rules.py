"""Reguły stref ochronnych sieci uzbrojenia terenu.

Promienie buforów są jawnie trzymane w konfiguracji (network_rules.json), a nie
jako magiczne liczby w kodzie serwisów domenowych — zgodnie z sekcją 7.6 i 22
context.md. Brak reguły dla danego typu sieci (np. 'unknown') oznacza brak
zdefiniowanej strefy ochronnej, a nie domyślny/zgadywany bufor.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class NetworkRule:
    """Pojedyncza reguła strefy ochronnej dla danego typu sieci uzbrojenia."""

    network_type: str
    default_buffer_m: float
    source: str
    confidence: float
    note: str
    apply_even_outside_parcel: bool


@lru_cache(maxsize=1)
def load_network_rules() -> dict[str, NetworkRule]:
    """
    Wczytuje reguły stref ochronnych sieci uzbrojenia z network_rules.json.

    Wynik jest cache'owany (lru_cache), bo plik konfiguracyjny nie zmienia się
    w trakcie działania procesu. Brak reguły dla danego network_type oznacza
    brak zdefiniowanej strefy ochronnej — takie sieci są pomijane w
    obliczeniach, nie domyślnie buforowane magiczną wartością.
    """
    config_path = Path(__file__).parent / "network_rules.json"
    with config_path.open("r", encoding="utf-8") as handle:
        raw_rules = json.load(handle)
    return {rule["network_type"]: NetworkRule(**rule) for rule in raw_rules}
