"""Porty (protokoły) modułu ``location``."""

from __future__ import annotations

from typing import Protocol

from app.modules.location.domain.models import AddressQuery, RawAddressCandidate


class AddressSearchProviderError(Exception):
    """Błąd dostawcy wyszukiwania adresów (niedostępność, zły format)."""


class AddressSearchProvider(Protocol):
    """Port dostawcy surowych kandydatów adresowych.

    Implementacja (adapter) należy do warstwy infrastructure i musi respektować
    guard katalogu źródeł oraz nie ujawniać publicznie kontraktu dostawcy.
    """

    async def search(self, query: AddressQuery) -> list[RawAddressCandidate]: ...
