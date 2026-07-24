"""Porty (protokoły) modułu ``parcels``.

Definiuje kontrakt repozytorium działek jako ``Protocol``. Implementacje
(adaptery) należą do warstwy ``infrastructure``. Importuje wyłącznie typy
domenowe i ``app.shared`` — zgodnie z regułami zależności.
"""

from __future__ import annotations

from typing import Protocol

from app.modules.parcels.domain.models import ParcelGeometry, ParcelIdentifier


class ParcelRepository(Protocol):
    """Port zapisu i odczytu geometrii działek."""

    def get_by_identifier(
        self, identifier: ParcelIdentifier
    ) -> ParcelGeometry | None: ...

    def save(self, parcel: ParcelGeometry) -> None: ...
