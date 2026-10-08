"""Przycinanie tekstu pochodzącego z zewnątrz do długości kolumny bazy (AU-001).

Adresy zapytań, nazwy źródeł i numery uchwał pochodzą ze źródeł, nad którymi
aplikacja nie ma kontroli. Zapis wartości dłuższej niż ``String(n)`` kończy się w
PostgreSQL błędem ``StringDataRightTruncation`` i przewraca całą analizę, więc
tekst z zewnątrz jest przycinany przed zapisem — z widocznym znacznikiem ``…``,
żeby przycięcia nie dało się pomylić z oryginalną wartością.
"""

from __future__ import annotations

from typing import Final

ELLIPSIS: Final[str] = "…"


def clip_text(value: str | None, max_len: int) -> str | None:
    """Zwraca ``value`` o długości co najwyżej ``max_len`` znaków.

    Wartość krótsza lub równa limitowi (oraz ``None``) wraca bez zmian. Dłuższa jest
    cięta tak, by wynik miał dokładnie ``max_len`` znaków i kończył się ``…``.
    Długość liczona jest w znakach (punktach kodowych), tak jak robi to
    ``VARCHAR(n)`` w PostgreSQL.
    """
    if max_len < 1:
        raise ValueError("max_len musi być dodatnie.")
    if value is None or len(value) <= max_len:
        return value
    return value[: max_len - 1] + ELLIPSIS
