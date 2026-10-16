"""Wspólne pojęcia kontekstu z lokalnego wydania BDOT10k: drogi (BK-305) i zabudowa sąsiednia (BK-304).

Wydanie to paczka jednego powiatu; zasięgiem wydania jest granica tego powiatu. Brak wydania dla obszaru działki to
``no_coverage`` — nigdy „brak drogi” ani „brak budynków”. Moduł jest czysty (bez SQL i HTTP).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Final

BDOT10K_SOURCE_ID: Final[str] = "bdot10k"
# Obiekty uwzględniane w kontekście to wyłącznie istniejące (nie „w budowie”, „nieczynny”, „zniszczony”).
IN_OPERATION: Final[str] = "eksploatowany"

# Kody przyczyn statusów innych niż ``available`` (wspólne dla dróg i zabudowy).
REASON_NO_ACTIVE_RELEASE: Final[str] = "NO_ACTIVE_RELEASE"
REASON_OUTSIDE_COVERAGE: Final[str] = "OUTSIDE_COVERAGE"
REASON_SEARCH_AREA_PARTIALLY_OUTSIDE_COVERAGE: Final[str] = "SEARCH_AREA_PARTIALLY_OUTSIDE_COVERAGE"
REASON_QUERY_TIMEOUT: Final[str] = "QUERY_TIMEOUT"
REASON_DATABASE_ERROR: Final[str] = "DATABASE_ERROR"
REASON_SOURCE_NOT_RUNNABLE: Final[str] = "SOURCE_NOT_RUNNABLE"
REASON_UNEXPECTED_ERROR: Final[str] = "UNEXPECTED_ERROR"
REASON_TOO_MANY_CANDIDATES: Final[str] = "TOO_MANY_CANDIDATES"


@dataclass(frozen=True)
class BdotReleaseRef:
    """Aktywne wydanie powiatu przypięte do jednego zapytania (a więc do snapshotu analizy)."""

    data_release_id: int
    version_label: str
    teryt_powiat: str
    published_at: datetime | None = None
    package_sha256: str | None = None
    data_as_of: date | None = None
    source_id: str = BDOT10K_SOURCE_ID


@dataclass(frozen=True)
class CoverageCheck:
    """Czy działka i obszar wyszukiwania leżą w zasięgu wydania (granica powiatu)."""

    parcel_covered: bool
    search_area_covered: bool
