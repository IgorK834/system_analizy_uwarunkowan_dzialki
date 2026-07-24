"""Przypadek użycia: wyszukiwanie adresów.

Serwis orkiestruje dostawcę (port) i deterministyczny ranking domenowy. Nie zna
szczegółów infrastruktury ani kontraktu dostawcy.
"""

from __future__ import annotations

from app.modules.location.application.ports import AddressSearchProvider
from app.modules.location.domain.models import AddressQuery, RankedAddress
from app.modules.location.domain.ranking import rank_candidates


class AddressSearchService:
    """Wyszukiwarka adresów oparta o wstrzykiwany port dostawcy."""

    def __init__(self, provider: AddressSearchProvider) -> None:
        self._provider = provider

    async def search(self, query: AddressQuery) -> list[RankedAddress]:
        """Zwraca zrankowane wyniki adresowe dla zwalidowanego zapytania.

        Filtrowanie po typach jest stosowane po stronie serwisu na wynikach
        dostawcy (kontrakt zewnętrzny nie jest ujawniany), a ranking jest
        deterministyczny i ograniczony do ``query.limit``.
        """
        candidates = await self._provider.search(query)
        if query.type_filter:
            candidates = [
                candidate
                for candidate in candidates
                if candidate.result_type in query.type_filter
            ]
        return rank_candidates(
            candidates,
            query=query.q,
            limit=query.limit,
            bias=query.bias,
            bbox=query.bbox,
        )
