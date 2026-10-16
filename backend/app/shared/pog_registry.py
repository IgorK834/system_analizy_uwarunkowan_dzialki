"""Wspólny kontrakt krajowego rejestru wersji aktów POG (ADR-026): predykat stanu i odczyt bieżącego numeru stanu.

Moduł jest wyłącznie biblioteką standardową (fragmenty SQL jako tekst) — dostępny dla ``imports`` (zapis) i ``planning`` (kafle,
inspektor, analiza) bez zależności jednego modułu od infrastruktury drugiego (ADR-001).

Zbiór wersji aktywnych w stanie ``N`` to wpisy ``pog_registry_entries`` spełniające
``state_from_id <= N AND (state_to_id IS NULL OR state_to_id > N)``. Numery stanów są nadawane i zatwierdzane pod
jedną blokadą doradczą (``REGISTRY_LOCK``), więc rosną zgodnie z kolejnością zatwierdzania — stan o numerze ``N`` nie
zmienia się po tym, jak ktokolwiek go odczytał.
"""

from __future__ import annotations

from typing import Final

# Blokada doradcza serializująca zmiany rejestru (nadawanie numeru stanu + zamykanie/otwieranie wpisów).
REGISTRY_LOCK: Final[str] = "pog-registry"

_ACTIVE_AT_STATE_TEMPLATE: Final[str] = (
    "{a}.state_from_id <= :state_id AND ({a}.state_to_id IS NULL OR {a}.state_to_id > :state_id)"
)

# Predykat „wpis ``e`` jest aktywny w stanie ``:state_id``”.
ACTIVE_AT_STATE_SQL: Final[str] = _ACTIVE_AT_STATE_TEMPLATE.format(a="e")


def active_versions_join(*, version_column: str = "pav.id", entry_alias: str = "e") -> str:
    """Fragment ``JOIN`` ograniczający wersje do aktywnych w stanie ``:state_id``."""
    predicate = _ACTIVE_AT_STATE_TEMPLATE.format(a=entry_alias)
    return (
        f"JOIN pog_registry_entries {entry_alias} "
        f"ON {entry_alias}.planning_act_version_id = {version_column} AND {predicate}"
    )
