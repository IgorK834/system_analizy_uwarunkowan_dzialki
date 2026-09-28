"""Reguły technicznych buforów sieci uzbrojenia terenu (BK-306).

Promienie buforów są jawnie trzymane w konfiguracji (network_rules.json), a nie
jako magiczne liczby w kodzie serwisów domenowych — zgodnie z sekcją 7.6 i 22
context.md. Brak reguły dla danego typu sieci (np. 'unknown') oznacza brak
zdefiniowanej strefy ochronnej, a nie domyślny/zgadywany bufor.

Każda reguła musi mieć **dowód albo flagę symulacyjną**:

* reguła może pomniejszać „powierzchnię zabudowalną” (``affects_buildable_area``)
  wyłącznie wtedy, gdy ma podstawę prawną, datę ręcznej weryfikacji, status
  ``verified`` i nie jest oznaczona jako symulacyjna;
* reguła bez wiarygodnej podstawy ma ``simulation_only=true`` i
  ``affects_buildable_area=false`` — jej bufor jest co najwyżej prezentowany
  jako przybliżenie i nigdy nie zmienia wyniku powierzchni.

Loader odrzuca konfigurację naruszającą te zasady, zamiast po cichu ją
stosować.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any, Final, Literal

VerificationStatus = Literal["verified", "unverified"]
_ALLOWED_UNITS: Final[frozenset[str]] = frozenset({"m"})
_ALLOWED_VERIFICATION: Final[frozenset[str]] = frozenset({"verified", "unverified"})


class NetworkRuleConfigError(ValueError):
    """Reguła bufora nie spełnia kontraktu dowodu albo flagi symulacyjnej."""


@dataclass(frozen=True)
class NetworkRule:
    """Pojedyncza reguła technicznego bufora dla danego typu sieci uzbrojenia."""

    network_type: str
    default_buffer_m: float
    source: str
    confidence: float
    note: str
    apply_even_outside_parcel: bool
    unit: str = "m"
    legal_basis: str | None = None
    basis_verified_at: date | None = None
    verification_status: VerificationStatus = "unverified"
    simulation_only: bool = True
    affects_buildable_area: bool = False

    def __post_init__(self) -> None:
        name = self.network_type or "<brak typu>"
        if not self.network_type.strip():
            raise NetworkRuleConfigError("Reguła bufora musi mieć network_type.")
        if self.unit not in _ALLOWED_UNITS:
            raise NetworkRuleConfigError(
                f"Reguła {name!r}: nieobsługiwana jednostka {self.unit!r}."
            )
        if not self.default_buffer_m > 0:
            raise NetworkRuleConfigError(f"Reguła {name!r}: bufor musi być dodatni.")
        if not 0.0 <= self.confidence <= 1.0:
            raise NetworkRuleConfigError(
                f"Reguła {name!r}: confidence musi mieścić się w [0, 1]."
            )
        if not self.source.strip() or not self.note.strip():
            raise NetworkRuleConfigError(
                f"Reguła {name!r}: źródło i uwaga nie mogą być puste."
            )
        if self.verification_status not in _ALLOWED_VERIFICATION:
            raise NetworkRuleConfigError(
                f"Reguła {name!r}: nieznany status weryfikacji "
                f"{self.verification_status!r}."
            )
        if self.simulation_only and self.affects_buildable_area:
            raise NetworkRuleConfigError(
                f"Reguła {name!r}: reguła symulacyjna nie może pomniejszać "
                "powierzchni zabudowalnej."
            )
        if not self.simulation_only and not self.has_verified_basis:
            raise NetworkRuleConfigError(
                f"Reguła {name!r}: bez zweryfikowanej podstawy (legal_basis, "
                "basis_verified_at, verification_status=verified) reguła musi "
                "mieć simulation_only=true."
            )

    @property
    def has_verified_basis(self) -> bool:
        """Czy reguła ma jawny, ręcznie zweryfikowany dowód."""
        return (
            self.verification_status == "verified"
            and bool(self.legal_basis and self.legal_basis.strip())
            and self.basis_verified_at is not None
        )


def parse_network_rules(raw_rules: list[dict[str, Any]]) -> dict[str, NetworkRule]:
    """Waliduje surowe wpisy konfiguracji i zwraca reguły według typu sieci."""
    rules: dict[str, NetworkRule] = {}
    for raw in raw_rules:
        values = dict(raw)
        verified_at = values.get("basis_verified_at")
        if isinstance(verified_at, str):
            values["basis_verified_at"] = date.fromisoformat(verified_at)
        try:
            rule = NetworkRule(**values)
        except TypeError as exc:
            raise NetworkRuleConfigError(f"Niepoprawny wpis reguły: {exc}") from exc
        if rule.network_type in rules:
            raise NetworkRuleConfigError(
                f"Zduplikowana reguła dla typu sieci {rule.network_type!r}."
            )
        rules[rule.network_type] = rule
    return rules


@lru_cache(maxsize=1)
def load_network_rules() -> dict[str, NetworkRule]:
    """
    Wczytuje reguły technicznych buforów sieci uzbrojenia z network_rules.json.

    Wynik jest cache'owany (lru_cache), bo plik konfiguracyjny nie zmienia się
    w trakcie działania procesu. Brak reguły dla danego network_type oznacza
    brak zdefiniowanej strefy — takie sieci są pomijane w obliczeniach, nie
    domyślnie buforowane magiczną wartością.
    """
    config_path = Path(__file__).parent / "network_rules.json"
    with config_path.open("r", encoding="utf-8") as handle:
        raw_rules = json.load(handle)
    return parse_network_rules(raw_rules)
