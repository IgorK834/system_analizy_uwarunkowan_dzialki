"""Przypadek użycia: najbliższa droga i sąsiedztwo działki (BK-305).

Warstwa aplikacyjna zna wyłącznie port indeksu dróg — lokalne wydanie BDOT10k
w PostGIS dostarcza infrastruktura. Wyszukiwanie odbywa się rosnącym buforem
(np. 25 → 50 → 100 → 250 → 500 m); w każdym kroku indeks zwraca kandydatów w
kolejności KNN. Wynik zawsze niesie metadane wyszukiwania i provenance, także
gdy drogi nie znaleziono albo zapytanie się nie powiodło:

* ``available`` — znaleziono obiekt w promieniu ``search_radius_m``;
* ``no_match`` — pusty ograniczony zasięg (``SEARCH_RADIUS_EXHAUSTED``); to NIE jest
  dowód, że w ogóle nie ma drogi;
* ``no_coverage`` — działka leży poza zasięgiem aktywnych wydań (powiaty) albo
  żadnego wydania nie zaimportowano — dane nie zostały zaimportowane dla obszaru;
* ``unavailable`` — timeout, błąd bazy albo niepotwierdzone źródło.

Wydanie wybiera port po położeniu działki: zasięgiem wydania jest granica powiatu paczki.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from app.modules.analysis.application.bdot_ports import (
    BdotIndexError,
    BdotIndexTimeout,
    BdotReleaseLocator,
)
from app.modules.analysis.domain.bdot import (
    BDOT10K_SOURCE_ID,
    REASON_NO_ACTIVE_RELEASE,
    REASON_OUTSIDE_COVERAGE,
    REASON_SEARCH_AREA_PARTIALLY_OUTSIDE_COVERAGE,
    BdotReleaseRef,
    CoverageCheck,
)
from app.modules.analysis.domain.roads import (
    REASON_SEARCH_RADIUS_EXHAUSTED,
    RoadCandidate,
    RoadContextStatus,
    RoadSearchPlan,
    select_nearest,
)
from app.shared.provenance import Provenance

ROAD_SOURCE_ID = BDOT10K_SOURCE_ID
_OPERATION = "PostGIS:ST_DWithin+ST_Distance(bdot_roads)"

# Dawne nazwy typów (z czasów pojedynczego wydania) — wydanie jest teraz wyznaczane po położeniu działki.
RoadRelease = BdotReleaseRef
RoadIndexError = BdotIndexError
RoadIndexTimeout = BdotIndexTimeout
__all__ = ["BdotIndexError", "BdotIndexTimeout", "CoverageCheck", "RoadRelease", "RoadIndexError", "RoadIndexTimeout"]


class RoadNetworkIndex(BdotReleaseLocator, Protocol):
    """Port indeksu przestrzennego osi jezdni (EPSG:2180)."""

    def nearest(
        self, release: BdotReleaseRef, parcel_wkt: str, radius_m: float, limit: int
    ) -> list[RoadCandidate]: ...

    def adjacency_counts(self, release: BdotReleaseRef, parcel_wkt: str) -> tuple[int, int]: ...


@dataclass(frozen=True)
class RoadContextOutcome:
    """Wynik sekcji drogowej z metadanymi wyszukiwania i provenance."""

    status: RoadContextStatus
    provenance: Provenance
    max_search_radius_m: float
    reason_code: str | None = None
    nearest: RoadCandidate | None = None
    search_radius_m: float | None = None
    searched_radii_m: tuple[float, ...] = ()
    candidate_limit: int | None = None
    search_area_within_coverage: bool | None = None
    intersecting_road_count: int | None = None
    touching_road_count: int | None = None
    release: BdotReleaseRef | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)


def find_road_context(
    parcel_wkt: str,
    index: RoadNetworkIndex,
    plan: RoadSearchPlan,
) -> RoadContextOutcome:
    """Wyszukuje najbliższą oś jezdni rosnącym buforem z maksymalnym promieniem."""
    started = datetime.now(timezone.utc)
    searched: list[float] = []
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
                        "Brak aktywnego wydania danych BDOT10k — kontekstu drogowego nie sprawdzono. "
                        "Nie oznacza to braku drogi.",
                    ),
                )
            return _outcome(
                "no_coverage",
                plan,
                started,
                release,
                reason_code=REASON_OUTSIDE_COVERAGE,
                warnings=(
                    "Działka leży poza zasięgiem zaimportowanych powiatów BDOT10k — kontekstu drogowego "
                    "nie sprawdzono. Nie oznacza to braku drogi.",
                ),
            )

        for radius in plan.radii_m:
            searched.append(radius)
            candidates = index.nearest(release, parcel_wkt, radius, plan.candidate_limit)
            best = select_nearest(candidates)
            if best is None:
                continue
            # Bliższa droga poza zasięgiem wydania mogłaby leżeć w promieniu znalezionej odległości — flaga mówi,
            # czy ten obszar był pokryty (a także, czy sama działka leży w całości w powiecie wydania).
            covered = index.coverage(release, parcel_wkt, best.distance_m).search_area_covered
            intersecting, touching = (
                index.adjacency_counts(release, parcel_wkt) if best.distance_m <= 0.0 else (0, 0)
            )
            warnings = () if covered else (
                "Obszar do znalezionej drogi wykracza poza zasięg zaimportowanego powiatu — bliższa droga "
                "poza zasięgiem nie została sprawdzona.",
            )
            return _outcome(
                "available",
                plan,
                started,
                release,
                nearest=best,
                search_radius_m=radius,
                searched=tuple(searched),
                search_area_within_coverage=covered,
                intersecting=intersecting,
                touching=touching,
                warnings=warnings,
            )

        covered = index.coverage(release, parcel_wkt, plan.max_radius_m).search_area_covered
        return _outcome(
            "no_match",
            plan,
            started,
            release,
            reason_code=(
                REASON_SEARCH_RADIUS_EXHAUSTED if covered else REASON_SEARCH_AREA_PARTIALLY_OUTSIDE_COVERAGE
            ),
            search_radius_m=plan.max_radius_m,
            searched=tuple(searched),
            search_area_within_coverage=covered,
            warnings=(
                f"Nie znaleziono osi jezdni BDOT10k w promieniu {plan.max_radius_m:g} m. "
                "Brak kandydata w ograniczonym zasięgu nie dowodzi braku drogi.",
            ),
        )
    except BdotIndexError as exc:
        return _outcome(
            "unavailable",
            plan,
            started,
            release,
            reason_code=exc.reason_code,
            searched=tuple(searched),
            error_code=exc.reason_code,
            warnings=(
                f"Zapytanie o kontekst drogowy nie powiodło się ({exc.reason_code}) — odległości nie policzono.",
            ),
        )


def _outcome(
    status: RoadContextStatus,
    plan: RoadSearchPlan,
    started: datetime,
    release: BdotReleaseRef | None,
    *,
    reason_code: str | None = None,
    nearest: RoadCandidate | None = None,
    search_radius_m: float | None = None,
    searched: tuple[float, ...] = (),
    search_area_within_coverage: bool | None = None,
    intersecting: int | None = None,
    touching: int | None = None,
    error_code: str | None = None,
    warnings: tuple[str, ...] = (),
) -> RoadContextOutcome:
    return RoadContextOutcome(
        status=status,
        reason_code=reason_code,
        provenance=Provenance(
            source_id=ROAD_SOURCE_ID,
            fetched_at=started,
            data_release_id=release.data_release_id if release else None,
            operation=_OPERATION,
            complete=status in {"available", "no_match"},
            error_code=error_code,
        ),
        max_search_radius_m=plan.max_radius_m,
        nearest=nearest,
        search_radius_m=search_radius_m,
        searched_radii_m=searched,
        candidate_limit=plan.candidate_limit,
        search_area_within_coverage=search_area_within_coverage,
        intersecting_road_count=intersecting,
        touching_road_count=touching,
        release=release,
        warnings=warnings,
    )
