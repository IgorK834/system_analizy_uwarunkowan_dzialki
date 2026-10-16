"""Przypadek użycia: kontekst istniejącej zabudowy w otoczeniu działki (BK-304, Task 23.3).

Warstwa aplikacyjna zna wyłącznie port indeksu budynków — lokalne wydanie BDOT10k w PostGIS dostarcza infrastruktura.
Indeks zwraca kandydatów (obrysy w promieniu największego bufora), a liczności, pola i odległości liczy czysta domena
(``compute_building_metrics``) w EPSG:2180. Wynik zawsze niesie provenance i metadane obliczeń, także gdy danych brak:

* ``available`` — obliczono statystyki; zero budynków w pokrytym obszarze to sprawdzony wynik „0”, a nie brak danych.
  Gdy bufor wykracza poza zasięg wydania (granica powiatu), wynik niesie ``search_area_within_coverage=False`` i
  ostrzeżenie — liczby dotyczą wyłącznie pokrytej części;
* ``no_match`` — nic nie znaleziono, a bufor wykracza poza zasięg wydania: nie wiadomo, czy budynków nie ma;
* ``no_coverage`` — działka leży poza zasięgiem aktywnych wydań albo nic nie zaimportowano;
* ``unavailable`` — timeout, błąd bazy albo zbyt wiele kandydatów, by policzyć poprawnie.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from shapely import from_wkt

from app.modules.analysis.application.bdot_ports import (
    BdotIndexError,
    BdotReleaseLocator,
)
from app.modules.analysis.domain.bdot import (
    BDOT10K_SOURCE_ID,
    REASON_NO_ACTIVE_RELEASE,
    REASON_OUTSIDE_COVERAGE,
    REASON_SEARCH_AREA_PARTIALLY_OUTSIDE_COVERAGE,
    REASON_TOO_MANY_CANDIDATES,
    BdotReleaseRef,
)
from app.modules.analysis.domain.buildings import (
    BuildingContextStatus,
    BuildingFootprint,
    BuildingMetrics,
    BuildingSearchPlan,
    compute_building_metrics,
)
from app.shared.provenance import Provenance

BUILDING_SOURCE_ID = BDOT10K_SOURCE_ID
_OPERATION = "PostGIS:ST_DWithin(bdot_buildings)+Shapely:ring-statistics"


class BuildingIndex(BdotReleaseLocator, Protocol):
    """Port indeksu przestrzennego obrysów budynków (EPSG:2180)."""

    def candidates(
        self, release: BdotReleaseRef, parcel_wkt: str, radius_m: float, limit: int
    ) -> list[BuildingFootprint]:
        """Obrysy istniejących budynków w odległości najwyżej ``radius_m`` od działki, od najbliższego; do ``limit``."""
        ...


@dataclass(frozen=True)
class BuildingContextOutcome:
    """Wynik sekcji zabudowy sąsiedniej z metadanymi obliczeń i provenance."""

    status: BuildingContextStatus
    provenance: Provenance
    plan: BuildingSearchPlan
    reason_code: str | None = None
    metrics: BuildingMetrics | None = None
    search_area_within_coverage: bool | None = None
    release: BdotReleaseRef | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)


def find_building_context(
    parcel_wkt: str,
    index: BuildingIndex,
    plan: BuildingSearchPlan,
) -> BuildingContextOutcome:
    """Zabudowa na działce i w pierścieniach sąsiedztwa; błąd źródła nigdy nie staje się „brakiem budynków”."""
    started = datetime.now(timezone.utc)
    release: BdotReleaseRef | None = None
    try:
        release = index.release_for(parcel_wkt)
        if release is None:
            if not index.has_active_releases():
                return _outcome(
                    "no_coverage",
                    plan,
                    started,
                    release,
                    reason_code=REASON_NO_ACTIVE_RELEASE,
                    warnings=(
                        "Brak aktywnego wydania danych BDOT10k — zabudowy nie sprawdzono. "
                        "Nie oznacza to braku budynków.",
                    ),
                )
            return _outcome(
                "no_coverage",
                plan,
                started,
                release,
                reason_code=REASON_OUTSIDE_COVERAGE,
                warnings=(
                    "Działka leży poza zasięgiem zaimportowanych powiatów BDOT10k — zabudowy nie sprawdzono. "
                    "Nie oznacza to braku budynków.",
                ),
            )

        candidates = index.candidates(release, parcel_wkt, plan.max_radius_m, plan.candidate_limit + 1)
        if len(candidates) > plan.candidate_limit:
            return _outcome(
                "unavailable",
                plan,
                started,
                release,
                reason_code=REASON_TOO_MANY_CANDIDATES,
                error_code=REASON_TOO_MANY_CANDIDATES,
                warnings=(
                    f"W promieniu {plan.max_radius_m:g} m jest więcej niż {plan.candidate_limit} budynków — "
                    "statystyk nie policzono (wynik byłby zaniżony).",
                ),
            )
        covered = index.coverage(release, parcel_wkt, plan.max_radius_m).search_area_covered
        metrics = compute_building_metrics(from_wkt(parcel_wkt), candidates, plan)
        warnings: list[str] = []
        if not covered:
            warnings.append(
                f"Bufor {plan.max_radius_m:g} m wykracza poza zasięg zaimportowanego powiatu — liczby dotyczą "
                "wyłącznie pokrytej części otoczenia i mogą być zaniżone."
            )
            if not candidates:
                return _outcome(
                    "no_match",
                    plan,
                    started,
                    release,
                    reason_code=REASON_SEARCH_AREA_PARTIALLY_OUTSIDE_COVERAGE,
                    metrics=metrics,
                    search_area_within_coverage=False,
                    warnings=tuple(warnings),
                )
        if metrics.listed_truncated:
            warnings.append(
                f"Tabela pokazuje {len(metrics.listed)} najbliższych budynków; pozostałe są w statystykach."
            )
        return _outcome(
            "available",
            plan,
            started,
            release,
            metrics=metrics,
            search_area_within_coverage=covered,
            warnings=tuple(warnings),
        )
    except BdotIndexError as exc:
        return _outcome(
            "unavailable",
            plan,
            started,
            release,
            reason_code=exc.reason_code,
            error_code=exc.reason_code,
            warnings=(
                f"Zapytanie o zabudowę sąsiednią nie powiodło się ({exc.reason_code}) — statystyk nie policzono.",
            ),
        )


def _outcome(
    status: BuildingContextStatus,
    plan: BuildingSearchPlan,
    started: datetime,
    release: BdotReleaseRef | None,
    *,
    reason_code: str | None = None,
    metrics: BuildingMetrics | None = None,
    search_area_within_coverage: bool | None = None,
    error_code: str | None = None,
    warnings: tuple[str, ...] = (),
) -> BuildingContextOutcome:
    return BuildingContextOutcome(
        status=status,
        reason_code=reason_code,
        provenance=Provenance(
            source_id=BUILDING_SOURCE_ID,
            fetched_at=started,
            data_release_id=release.data_release_id if release else None,
            operation=_OPERATION,
            complete=status in {"available", "no_match"},
            error_code=error_code,
        ),
        plan=plan,
        metrics=metrics,
        search_area_within_coverage=search_area_within_coverage,
        release=release,
        warnings=warnings,
    )
