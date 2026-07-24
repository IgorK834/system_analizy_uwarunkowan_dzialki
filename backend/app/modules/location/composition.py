"""Root kompozycji modułu ``location`` (wiązanie adapterów z aplikacją).

Plik świadomie leży poza warstwami domain/application/infrastructure/api — jest
miejscem, w którym wolno połączyć port aplikacyjny z konkretnym adapterem
infrastruktury. Dzięki temu warstwa ``api`` nie importuje infrastruktury wprost.
"""

from __future__ import annotations

from functools import lru_cache

from app.core.data_sources import CatalogError, get_catalog
from app.core.settings import settings
from app.modules.location.api.schemas import SourceInfo
from app.modules.location.application.service import AddressSearchService
from app.modules.location.infrastructure.local_index import (
    LocalFirstAddressSearchProvider,
    PostgresAddressSearchProvider,
)
from app.modules.location.infrastructure.uug_adapter import UugAddressSearchProvider

_ADDRESS_SOURCE_ID = "emuia_uug"


@lru_cache(maxsize=1)
def build_address_search_service() -> AddressSearchService:
    """Zwraca współdzieloną instancję serwisu wyszukiwania adresów."""
    fallback = (
        UugAddressSearchProvider()
        if settings.address_index_uug_fallback_enabled
        else None
    )
    return AddressSearchService(
        LocalFirstAddressSearchProvider(
            local=build_address_index_provider(),
            fallback=fallback,
        )
    )


@lru_cache(maxsize=1)
def build_address_index_provider() -> PostgresAddressSearchProvider:
    """Zwraca adapter indeksu używany także przez endpoint statusu."""
    return PostgresAddressSearchProvider()


def address_source_info(source_id: str = _ADDRESS_SOURCE_ID) -> SourceInfo:
    """Buduje informację o źródle na podstawie katalogu (jedyne źródło prawdy)."""
    try:
        entry = get_catalog().get(source_id)
        return SourceInfo(source_id=entry.source_id, attribution=entry.attribution)
    except CatalogError:
        # Fail-safe: nie ujawniamy szczegółów; adapter i tak wymusi guard katalogu.
        return SourceInfo(source_id=source_id, attribution="GUGiK / EMUiA")
