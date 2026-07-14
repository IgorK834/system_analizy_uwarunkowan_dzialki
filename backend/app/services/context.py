"""Równoległa orkiestracja kontekstu działki z KIUT, ISOK i GDOŚ.

``analyze_context`` używa jednego współdzielonego ``httpx.AsyncClient`` dla
trzech sekcji, aby jedna analiza nie tworzyła trzech niezależnych pul połączeń.
Każda sekcja jest finalizowana osobno, dzięki czemu niedostępność lub błąd
jednego źródła nie usuwa poprawnych wyników pozostałych źródeł.

Znanym i zaakceptowanym ograniczeniem jest zachowanie KIUT: istniejąca funkcja
``fetch_kiut_networks`` przechwytuje timeouty i błędy HTTP oraz zwraca ``[]``.
Dlatego sekcja KIUT nie raportuje z tych powodów statusu ``unavailable`` ani
``error``; pusta lista jest finalizowana jako ``available``. Zachowanie to jest
celowe i pozostaje odmienne od bezpieczeństwo-krytycznych sekcji ISOK i GDOŚ.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
from shapely.geometry.base import BaseGeometry

from app.schemas.analyze import SourceMetadata
from app.services.gdos import (
    GdosServiceUnavailableError,
    fetch_nature_protection_areas,
)
from app.services.isok import IsokServiceUnavailableError, fetch_flood_risks
from app.services.kiut import bbox_from_geometry, fetch_kiut_networks

logger = logging.getLogger(__name__)

SectionName = Literal["kiut", "isok", "gdos"]


@dataclass(frozen=True)
class ContextSectionResult:
    """Zunifikowany wynik jednej sekcji kontekstu działki.

    ``data`` zawiera surowe obiekty domenowe sekcji: ``NetworkFeature``,
    ``RiskFeature`` albo ``NatureProtectionFeature``. Lista jest typowana jako
    ``list[Any]`` z powodu heterogeniczności tych trzech modeli.
    """

    section: SectionName
    status: Literal["available", "unavailable", "error"]
    data: list[Any] = field(default_factory=list)
    source_metadata: SourceMetadata | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ContextResult:
    """Zagregowany wynik z jedną sekcją KIUT, ISOK i GDOŚ."""

    kiut: ContextSectionResult
    isok: ContextSectionResult
    gdos: ContextSectionResult


async def analyze_context(parcel_geometry: BaseGeometry) -> ContextResult:
    """Pobiera równolegle kontekst KIUT, ISOK i GDOŚ dla działki.

    Trzy operacje współdzielą jeden ``httpx.AsyncClient`` i są uruchamiane
    przez ``asyncio.gather(return_exceptions=True)``. Kontrolowana niedostępność
    ISOK/GDOŚ oraz każdy nieoczekiwany wyjątek są mapowane na wynik konkretnej
    sekcji i nigdy nie przerywają pozostałych operacji.
    """
    parcel_bounds = bbox_from_geometry(parcel_geometry)
    async with httpx.AsyncClient() as client:
        kiut_outcome, isok_outcome, gdos_outcome = await asyncio.gather(
            _run_kiut_section(parcel_bounds, client),
            _run_isok_section(parcel_geometry, client),
            _run_gdos_section(parcel_geometry, client),
            return_exceptions=True,
        )

    return ContextResult(
        kiut=_finalize_section("kiut", kiut_outcome),
        isok=_finalize_section("isok", isok_outcome),
        gdos=_finalize_section("gdos", gdos_outcome),
    )


async def _run_kiut_section(
    parcel_bounds: tuple[float, float, float, float],
    client: httpx.AsyncClient,
) -> list[Any]:
    start = time.monotonic()
    try:
        return await fetch_kiut_networks(parcel_bounds, client=client)
    finally:
        logger.info("Sekcja KIUT zakończona po %.3fs.", time.monotonic() - start)


async def _run_isok_section(
    parcel_geometry: BaseGeometry,
    client: httpx.AsyncClient,
) -> list[Any]:
    start = time.monotonic()
    try:
        return await fetch_flood_risks(parcel_geometry, client=client)
    finally:
        logger.info("Sekcja ISOK zakończona po %.3fs.", time.monotonic() - start)


async def _run_gdos_section(
    parcel_geometry: BaseGeometry,
    client: httpx.AsyncClient,
) -> list[Any]:
    start = time.monotonic()
    try:
        return await fetch_nature_protection_areas(parcel_geometry, client=client)
    finally:
        logger.info("Sekcja GDOŚ zakończona po %.3fs.", time.monotonic() - start)


def _finalize_section(
    section: SectionName,
    outcome: list[Any] | BaseException,
) -> ContextSectionResult:
    """Mapuje wynik ``gather`` na dostępny, niedostępny albo błędny wynik.

    Kontrolowane wyjątki ISOK/GDOŚ oznaczają oczekiwaną niedostępność usługi.
    Inne wyjątki oznaczają nieoczekiwany błąd. Poprawna lista, także pusta,
    oznacza dostępność sekcji.
    """
    if isinstance(outcome, (IsokServiceUnavailableError, GdosServiceUnavailableError)):
        logger.warning("Sekcja %s niedostępna: %s", section, outcome)
        return ContextSectionResult(
            section=section,
            status="unavailable",
            warnings=[
                f"Usługa {section.upper()} jest tymczasowo niedostępna — "
                "dane tej sekcji mogą być niepełne."
            ],
        )

    if isinstance(outcome, BaseException):
        logger.error("Sekcja %s zakończona nieoczekiwanym błędem: %s", section, outcome)
        return ContextSectionResult(
            section=section,
            status="error",
            warnings=[
                "Wystąpił nieoczekiwany błąd podczas pobierania danych "
                f"sekcji {section}."
            ],
        )

    source_metadata = outcome[0].source_metadata if outcome else None
    return ContextSectionResult(
        section=section,
        status="available",
        data=outcome,
        source_metadata=source_metadata,
        warnings=_collect_feature_warnings(outcome),
    )


def _collect_feature_warnings(features: list[Any]) -> list[str]:
    """Zbiera pojedyncze ``warning`` i listowe ``warnings`` cech domenowych."""
    collected: list[str] = []
    for feature in features:
        single = getattr(feature, "warning", None)
        if single:
            collected.append(single)
        collected.extend(getattr(feature, "warnings", []) or [])
    return collected
