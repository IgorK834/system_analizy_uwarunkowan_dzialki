"""Równoległa orkiestracja kontekstu działki z KIUT, ISOK, GDOŚ i NMT.

``analyze_context`` używa jednego współdzielonego ``httpx.AsyncClient`` dla
wszystkich sekcji, aby jedna analiza nie tworzyła kilku niezależnych pul
połączeń. Każda sekcja jest finalizowana osobno, dzięki czemu niedostępność lub
błąd jednego źródła nie usuwa poprawnych wyników pozostałych źródeł.

Sekcje nie są równorzędne pod względem wpływu na wynik analizy:

* KIUT, ISOK i GDOŚ opisują ograniczenia istotne prawnie albo bezpieczeństwowo,
  dlatego ich niedostępność obniża status całej analizy (``critical_sections``).
* NMT opisuje rzeźbę terenu — informację kosztową i projektową, nie zakaz.
  Brak danych wysokościowych nie może więc degradować statusu analizy, ale musi
  być widoczny jako ostrzeżenie sekcji.

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
from app.services.nmt import NmtServiceUnavailableError, fetch_terrain_extremes

logger = logging.getLogger(__name__)

SectionName = Literal["kiut", "isok", "gdos", "nmt"]

# Kontrolowane wyjątki niedostępności — mapowane na status ``unavailable``
# zamiast na nieoczekiwany błąd sekcji.
_EXPECTED_UNAVAILABLE_ERRORS: tuple[type[Exception], ...] = (
    IsokServiceUnavailableError,
    GdosServiceUnavailableError,
    NmtServiceUnavailableError,
)


@dataclass(frozen=True)
class ContextSectionResult:
    """Zunifikowany wynik jednej sekcji kontekstu działki.

    ``data`` zawiera surowe obiekty domenowe sekcji: ``NetworkFeature``,
    ``RiskFeature``, ``NatureProtectionFeature`` albo jeden pomiar NMT
    (``TerrainExtremes`` lub ``TerrainNoCoverage``). Lista jest typowana jako
    ``list[Any]`` z powodu heterogeniczności tych modeli. ``reason_code`` niesie
    kod przyczyny niedostępności, jeżeli źródło go podało.
    """

    section: SectionName
    status: Literal["available", "unavailable", "error"]
    data: list[Any] = field(default_factory=list)
    source_metadata: SourceMetadata | None = None
    warnings: list[str] = field(default_factory=list)
    reason_code: str | None = None


@dataclass(frozen=True)
class ContextResult:
    """Zagregowany wynik z jedną sekcją KIUT, ISOK, GDOŚ i NMT."""

    kiut: ContextSectionResult
    isok: ContextSectionResult
    gdos: ContextSectionResult
    nmt: ContextSectionResult

    def sections(self) -> tuple[ContextSectionResult, ...]:
        """Wszystkie sekcje kontekstu w stałej kolejności prezentacji.

        Metoda istnieje, aby dodanie kolejnej sekcji nie wymagało odnalezienia
        każdego miejsca, które wylicza sekcje ręcznie — inaczej nowe źródło po
        cichu nie trafiłoby do ostrzeżeń ani do rejestru źródeł.
        """
        return (self.kiut, self.isok, self.gdos, self.nmt)

    def critical_sections(self) -> tuple[ContextSectionResult, ...]:
        """Sekcje, których niedostępność obniża status całej analizy.

        NMT jest celowo pominięty: rzeźba terenu jest informacją projektową,
        a nie ograniczeniem prawnym, więc jej brak nie może oznaczać, że analiza
        ograniczeń jest niepełna.
        """
        return (self.kiut, self.isok, self.gdos)


async def analyze_context(parcel_geometry: BaseGeometry) -> ContextResult:
    """Pobiera równolegle kontekst KIUT, ISOK, GDOŚ i NMT dla działki.

    Operacje współdzielą jeden ``httpx.AsyncClient`` i są uruchamiane przez
    ``asyncio.gather(return_exceptions=True)``. Kontrolowana niedostępność
    ISOK/GDOŚ/NMT oraz każdy nieoczekiwany wyjątek są mapowane na wynik
    konkretnej sekcji i nigdy nie przerywają pozostałych operacji.
    """
    parcel_bounds = bbox_from_geometry(parcel_geometry)
    async with httpx.AsyncClient() as client:
        kiut_outcome, isok_outcome, gdos_outcome, nmt_outcome = await asyncio.gather(
            _run_kiut_section(parcel_bounds, client),
            _run_isok_section(parcel_geometry, client),
            _run_gdos_section(parcel_geometry, client),
            _run_nmt_section(parcel_geometry, client),
            return_exceptions=True,
        )

    return ContextResult(
        kiut=_finalize_section("kiut", kiut_outcome),
        isok=_finalize_section("isok", isok_outcome),
        gdos=_finalize_section("gdos", gdos_outcome),
        nmt=_finalize_section("nmt", nmt_outcome),
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


async def _run_nmt_section(
    parcel_geometry: BaseGeometry,
    client: httpx.AsyncClient,
) -> list[Any]:
    """Uruchamia sekcję NMT i opakowuje jej jedyny wynik w listę.

    ``fetch_terrain_extremes`` zwraca pomiar (``TerrainExtremes``) albo jawny
    brak pokrycia (``TerrainNoCoverage``) z provenance zapytania. Oba przypadki
    finalizują sekcję jako dostępną — brak pokrycia NMT nie jest awarią usługi,
    ale też nie jest pustą listą, którą dałoby się pomylić z płaskim terenem.
    """
    start = time.monotonic()
    try:
        return [await fetch_terrain_extremes(parcel_geometry, client=client)]
    finally:
        logger.info("Sekcja NMT zakończona po %.3fs.", time.monotonic() - start)


def _finalize_section(
    section: SectionName,
    outcome: list[Any] | BaseException,
) -> ContextSectionResult:
    """Mapuje wynik ``gather`` na dostępny, niedostępny albo błędny wynik.

    Kontrolowane wyjątki ISOK/GDOŚ/NMT oznaczają oczekiwaną niedostępność
    usługi. Inne wyjątki oznaczają nieoczekiwany błąd. Poprawna lista, także
    pusta, oznacza dostępność sekcji. Jeżeli kontrolowany wyjątek niesie
    provenance nieudanej próby (``source_metadata``) i kod przyczyny, są one
    zachowane — niepełny wynik nadal dokumentuje zapytanie.
    """
    if isinstance(outcome, _EXPECTED_UNAVAILABLE_ERRORS):
        logger.warning(
            "Sekcja %s niedostępna; error_type=%s",
            section,
            type(outcome).__name__,
        )
        return ContextSectionResult(
            section=section,
            status="unavailable",
            source_metadata=getattr(outcome, "source_metadata", None),
            reason_code=getattr(outcome, "reason_code", None),
            warnings=[
                f"Usługa {section.upper()} jest tymczasowo niedostępna — "
                "dane tej sekcji mogą być niepełne."
            ],
        )

    if isinstance(outcome, BaseException):
        logger.error(
            "Sekcja %s zakończona nieoczekiwanym błędem; error_type=%s",
            section,
            type(outcome).__name__,
        )
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
