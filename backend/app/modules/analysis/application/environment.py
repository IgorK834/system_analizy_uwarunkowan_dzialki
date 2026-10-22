"""Porty sekcji środowiskowych analizowanych z geometrii działki."""

from __future__ import annotations

from typing import Protocol, TypeVar

from app.shared.geometry import GeometryPayload

SectionT_co = TypeVar("SectionT_co", covariant=True)


class EnvironmentalSectionPort(Protocol[SectionT_co]):
    """Źródło jednej sekcji środowiskowej działające w kanonicznym CRS.

    Warstwa aplikacyjna przekazuje geometrię jako lekki ``GeometryPayload``;
    konkretny klient HTTP i biblioteka GIS pozostają po stronie adaptera.
    """

    source_id: str

    async def fetch(self, parcel: GeometryPayload) -> SectionT_co: ...
