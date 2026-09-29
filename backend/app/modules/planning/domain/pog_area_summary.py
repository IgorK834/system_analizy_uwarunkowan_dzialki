"""Kontrakt odczytu agregatów powierzchniowych stref POG (BK-405).

Agregaty są liczone przy publikacji wydania (moduł ``imports``) i czytane tu
jako gotowe liczby — HTTP nie wykonuje żadnych operacji przestrzennych.
Zakres wskazuje dokładnie jeden z parametrów: ``act_id`` (akt w wydaniu) albo
``teryt`` (gmina, z edycją ``binding``/``project`` — nakładające się akty
gminy nie są dublowane).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

POG_SUMMARY_SCHEMA_VERSION: Final[str] = "pog-area-summary/1"
SUMMARY_EDITIONS: Final[tuple[str, ...]] = ("binding", "project")
DEFAULT_SUMMARY_EDITION: Final[str] = "binding"
_TERYT: Final[re.Pattern[str]] = re.compile(r"^\d{6,7}$")
MAX_ACT_ID_LENGTH: Final[int] = 200


class InvalidPogSummaryQueryError(ValueError):
    """Niepoprawny zakres zapytania o agregat (HTTP 422)."""


@dataclass(frozen=True)
class PogAreaSummaryQuery:
    release_id: int
    act_id: str | None = None
    teryt: str | None = None
    edition: str | None = None

    def normalized(self) -> PogAreaSummaryQuery:
        act_id = (self.act_id or "").strip() or None
        teryt = (self.teryt or "").strip() or None
        edition = (self.edition or "").strip() or None
        if self.release_id <= 0:
            raise InvalidPogSummaryQueryError("Identyfikator wydania musi być dodatni.")
        if (act_id is None) == (teryt is None):
            raise InvalidPogSummaryQueryError(
                "Podaj dokładnie jeden zakres: act_id (akt) albo teryt (gmina)."
            )
        if act_id is not None:
            if edition is not None:
                raise InvalidPogSummaryQueryError(
                    "Parametr edition dotyczy wyłącznie agregatu gminy (teryt)."
                )
            if len(act_id) > MAX_ACT_ID_LENGTH:
                raise InvalidPogSummaryQueryError("Identyfikator aktu jest za długi.")
            return PogAreaSummaryQuery(self.release_id, act_id=act_id)
        assert teryt is not None
        if not _TERYT.match(teryt):
            raise InvalidPogSummaryQueryError("TERYT gminy musi mieć 6 lub 7 cyfr.")
        edition = edition or DEFAULT_SUMMARY_EDITION
        if edition not in SUMMARY_EDITIONS:
            raise InvalidPogSummaryQueryError(
                f"Nieznana edycja {edition!r}; dozwolone: {', '.join(SUMMARY_EDITIONS)}."
            )
        # Kod 7-cyfrowy (z rodzajem gminy) jest sprowadzany do 6 cyfr TERYT gminy.
        return PogAreaSummaryQuery(self.release_id, teryt=teryt[:6], edition=edition)

    @property
    def scope(self) -> str:
        return "act" if self.act_id is not None else "municipality"


@dataclass(frozen=True)
class PogAreaSummaryZoneView:
    zone_code: str
    area_sqm: float
    area_sqkm: float
    share_pct: float | None
    zone_count: int


@dataclass(frozen=True)
class PogAreaSummaryView:
    release_id: int
    release_label: str
    release_is_active: bool
    artifact_sha256: str | None
    scope: str
    act_id: str | None
    act_version: str | None
    teryt: str | None
    edition: str | None
    legal_status: str | None
    act_ids: tuple[str, ...]
    act_count: int
    denominator_area_sqm: float | None
    denominator_source: str | None
    zones_area_sqm: float
    missing_area_sqm: float | None
    overlap_area_sqm: float
    outside_area_sqm: float
    deduplicated_area_sqm: float | None
    share_sum_pct: float | None
    share_tolerance_pct: float
    area_tolerance_sqm: float
    zone_count: int
    is_complete: bool
    incomplete_reasons: tuple[str, ...]
    zones: tuple[PogAreaSummaryZoneView, ...]
    method_version: str
    computed_at: object
    schema: str = POG_SUMMARY_SCHEMA_VERSION
