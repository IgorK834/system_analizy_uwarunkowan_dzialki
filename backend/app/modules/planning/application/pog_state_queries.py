"""Przypadki użycia odczytu stanu krajowego POG: inspektor obiektu i agregaty stref.

BK-404: szczegóły obiektu, których kafel MVT nie mieści, przypięte do numeru
stanu krajowego (ADR-026). BK-405: gotowe agregaty powierzchniowe aktu/gminy
liczone przy imporcie — odczyt nie wykonuje obliczeń przestrzennych.
"""

from __future__ import annotations

from typing import Protocol

from app.modules.planning.application.pog_tiles import PogStateNotFoundError
from app.modules.planning.domain.pog_area_summary import (
    PogAreaSummaryQuery,
    PogAreaSummaryView,
)
from app.modules.planning.domain.pog_inspector import (
    PogFeatureDetails,
    PogFeatureDetailsRow,
    PogFeatureRef,
    PogStateHeader,
    feature_details,
    parse_feature_ref,
)


class PogFeatureNotFoundError(LookupError):
    """Obiekt nie należy do wskazanego stanu krajowego (HTTP 404)."""


class PogFeatureAmbiguousError(LookupError):
    """Identyfikator wskazuje więcej niż jeden obiekt stanu (HTTP 409)."""


class PogAreaSummaryNotFoundError(LookupError):
    """Brak agregatu dla zakresu w tym stanie (HTTP 404)."""


class PogStateQueryRepository(Protocol):
    def state_header(self, state_id: int) -> PogStateHeader | None:
        """Nagłówek stanu krajowego albo ``None``, gdy stan nie istnieje."""

    def feature_rows(self, state_id: int, ref: PogFeatureRef) -> list[PogFeatureDetailsRow]:
        """Co najwyżej dwa obiekty aktywnych wersji aktów w stanie pasujące do identyfikatora."""

    def area_summary(self, query: PogAreaSummaryQuery) -> PogAreaSummaryView | None:
        """Zapisany agregat zakresu w stanie (bez operacji przestrzennych)."""

    def state_has_area_summaries(self, state_id: int) -> bool:
        """Czy dla aktywnych wersji stanu policzono agregaty (dane sprzed BK-405 — nie)."""


class PogStateQueryService:
    def __init__(self, repository: PogStateQueryRepository) -> None:
        self._repository = repository

    def _state(self, state_id: int) -> PogStateHeader:
        header = self._repository.state_header(state_id)
        if header is None:
            raise PogStateNotFoundError(f"Stan krajowy POG {state_id} nie istnieje.")
        return header

    def feature_details(self, state_id: int, feature_id: str) -> PogFeatureDetails:
        ref = parse_feature_ref(feature_id)
        state = self._state(state_id)
        rows = self._repository.feature_rows(state_id, ref)
        if not rows:
            raise PogFeatureNotFoundError(
                f"Obiekt {feature_id!r} nie należy do żadnej wersji aktu aktywnej w stanie {state_id}."
            )
        if len(rows) > 1:
            raise PogFeatureAmbiguousError(
                f"Identyfikator {feature_id!r} wskazuje kilka obiektów stanu {state_id}; "
                "użyj zapisu planning_feature:<id> z kafla."
            )
        return feature_details(rows[0], state)

    def area_summary(self, query: PogAreaSummaryQuery) -> PogAreaSummaryView:
        normalized = query.normalized()
        self._state(normalized.state_id)
        summary = self._repository.area_summary(normalized)
        if summary is not None:
            return summary
        if not self._repository.state_has_area_summaries(normalized.state_id):
            raise PogAreaSummaryNotFoundError(
                f"Stan {normalized.state_id} nie ma policzonych agregatów stref "
                "(dane sprzed BK-405); ponowny import artefaktu je uzupełni."
            )
        target = (
            f"aktu {normalized.act_id!r}"
            if normalized.act_id is not None
            else f"gminy {normalized.teryt} w edycji {normalized.edition}"
        )
        raise PogAreaSummaryNotFoundError(
            f"Stan {normalized.state_id} nie zawiera agregatu {target}."
        )
