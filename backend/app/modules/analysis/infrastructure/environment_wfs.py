"""Adaptery WFS ISOK i GDOŚ dla portu sekcji środowiskowych.

Transport pozostaje czasowo w kompatybilnych serwisach ``app.services``;
adapter jest granicą modułu i pilnuje, aby do obliczeń trafiła geometria w
kanonicznym EPSG:2180. Serwisy transportowe zapewniają timeouty, retry, limity
bajtów i allowlistę hostów.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

import httpx
from shapely import from_wkt
from shapely.geometry.base import BaseGeometry

from app.services.gdos import (
    GDOS_SOURCE_ID,
    NatureProtectionSection,
    fetch_nature_protection_section,
)
from app.services.isok import (
    ISOK_SOURCE_ID,
    FloodRiskSection,
    fetch_flood_risk_section,
)
from app.shared.geometry import GeometryPayload

SectionT = TypeVar("SectionT")
SectionT_co = TypeVar("SectionT_co", covariant=True)


class SectionFetcher(Protocol[SectionT_co]):
    async def __call__(
        self,
        parcel_geometry: BaseGeometry,
        client: httpx.AsyncClient | None = None,
    ) -> SectionT_co: ...


@dataclass(frozen=True)
class WfsEnvironmentalSectionAdapter(Generic[SectionT]):
    """Wspólna implementacja portu dla utwardzonych adapterów WFS."""

    source_id: str
    client: httpx.AsyncClient
    fetcher: SectionFetcher[SectionT]

    async def fetch(self, parcel: GeometryPayload) -> SectionT:
        if not parcel.is_canonical():
            raise ValueError(
                f"Adapter {self.source_id} wymaga EPSG:2180, otrzymał {parcel.crs}."
            )
        geometry = from_wkt(parcel.wkt)
        if geometry.is_empty:
            raise ValueError("Geometria działki nie może być pusta.")
        return await self.fetcher(geometry, client=self.client)


class IsokWfsAdapter(WfsEnvironmentalSectionAdapter[FloodRiskSection]):
    """Adapter portu dla stref zagrożenia powodziowego ISOK."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        fetcher: SectionFetcher[FloodRiskSection] = fetch_flood_risk_section,
    ) -> None:
        super().__init__(source_id=ISOK_SOURCE_ID, client=client, fetcher=fetcher)


class GdosWfsAdapter(WfsEnvironmentalSectionAdapter[NatureProtectionSection]):
    """Adapter portu dla form ochrony i obiektów środowiskowych GDOŚ."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        fetcher: SectionFetcher[NatureProtectionSection] = fetch_nature_protection_section,
    ) -> None:
        super().__init__(source_id=GDOS_SOURCE_ID, client=client, fetcher=fetcher)
