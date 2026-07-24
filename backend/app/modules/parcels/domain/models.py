"""Typy domenowe działki.

Przykładowy, wolny od infrastruktury typ wartości. Importuje wyłącznie bibliotekę
standardową oraz publiczne typy z ``app.shared`` — co jest dozwolone regułami
zależności warstwy domenowej.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.shared.geometry import GeometryPayload

# Format identyfikatora ewidencyjnego (spójny z walidacją w adapterze ULDK).
_PARCEL_ID_RE = re.compile(r"^\d{6}_\d{1,2}\.\d{1,4}(\.[A-Z]+_\d+)?\.\d+(/\d+)*$")


@dataclass(frozen=True)
class ParcelIdentifier:
    """Identyfikator działki jako typ wartości z walidacją formatu."""

    value: str

    def __post_init__(self) -> None:
        if not _PARCEL_ID_RE.fullmatch(self.value):
            raise ValueError(
                f"Niepoprawny format identyfikatora działki: {self.value!r}."
            )


@dataclass(frozen=True)
class ParcelGeometry:
    """Geometria działki w kanonicznym układzie współrzędnych."""

    identifier: ParcelIdentifier
    geometry: GeometryPayload

    def is_canonical(self) -> bool:
        return self.geometry.is_canonical()
