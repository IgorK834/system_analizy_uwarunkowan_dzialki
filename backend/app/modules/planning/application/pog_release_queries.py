"""Przypadki użycia odczytu wydania POG: inspektor obiektu i agregaty stref.

BK-404: szczegóły obiektu, których kafel MVT nie mieści, przypięte do
``release_id``. BK-405: gotowe agregaty powierzchniowe aktu/gminy liczone przy
imporcie — odczyt nie wykonuje obliczeń przestrzennych.
"""

from __future__ import annotations

from typing import Protocol

from app.modules.planning.application.pog_tiles import PogReleaseNotFoundError
from app.modules.planning.domain.pog_area_summary import (
    PogAreaSummaryQuery,
    PogAreaSummaryView,
)
from app.modules.planning.domain.pog_inspector import (
    PogFeatureDetails,
    PogFeatureDetailsRow,
    PogReleaseHeader,
    feature_details,
    parse_feature_ref,
    PogFeatureRef,
)


class PogFeatureNotFoundError(LookupError):
    """Obiekt nie należy do wskazanego wydania (HTTP 404)."""


class PogFeatureAmbiguousError(LookupError):
    """Identyfikator wskazuje więcej niż jeden obiekt wydania (HTTP 409)."""


class PogAreaSummaryNotFoundError(LookupError):
    """Brak agregatu dla zakresu albo wydanie sprzed BK-405 (HTTP 404)."""


class PogReleaseQueryRepository(Protocol):
    def release_header(self, release_id: int) -> PogReleaseHeader | None:
        """Nagłówek wydania POG albo ``None``, gdy wydanie nie istnieje."""

    def feature_rows(self, release_id: int, ref: PogFeatureRef) -> list[PogFeatureDetailsRow]:
        """Co najwyżej dwa obiekty wydania pasujące do identyfikatora."""

    def area_summary(self, query: PogAreaSummaryQuery) -> PogAreaSummaryView | None:
        """Zapisany agregat zakresu (bez operacji przestrzennych)."""

    def release_has_area_summaries(self, release_id: int) -> bool:
        """Czy dla wydania policzono agregaty (wydania sprzed BK-405 — nie)."""


class PogReleaseQueryService:
    def __init__(self, repository: PogReleaseQueryRepository) -> None:
        self._repository = repository

    def _release(self, release_id: int) -> PogReleaseHeader:
        header = self._repository.release_header(release_id)
        if header is None:
            raise PogReleaseNotFoundError(
                f"Wydanie {release_id} nie istnieje albo nie zawiera aktów POG."
            )
        return header

    def feature_details(self, release_id: int, feature_id: str) -> PogFeatureDetails:
        ref = parse_feature_ref(feature_id)
        release = self._release(release_id)
        rows = self._repository.feature_rows(release_id, ref)
        if not rows:
            raise PogFeatureNotFoundError(
                f"Obiekt {feature_id!r} nie należy do wydania {release_id}."
            )
        if len(rows) > 1:
            raise PogFeatureAmbiguousError(
                f"Identyfikator {feature_id!r} wskazuje kilka obiektów wydania {release_id}; "
                "użyj zapisu planning_feature:<id> z kafla."
            )
        return feature_details(rows[0], release)

    def area_summary(self, query: PogAreaSummaryQuery) -> PogAreaSummaryView:
        normalized = query.normalized()
        self._release(normalized.release_id)
        summary = self._repository.area_summary(normalized)
        if summary is not None:
            return summary
        if not self._repository.release_has_area_summaries(normalized.release_id):
            raise PogAreaSummaryNotFoundError(
                f"Wydanie {normalized.release_id} nie ma policzonych agregatów stref "
                "(opublikowano je przed BK-405); ponowny import artefaktu je uzupełni."
            )
        target = (
            f"aktu {normalized.act_id!r}"
            if normalized.act_id is not None
            else f"gminy {normalized.teryt} w edycji {normalized.edition}"
        )
        raise PogAreaSummaryNotFoundError(
            f"Wydanie {normalized.release_id} nie zawiera agregatu {target}."
        )
