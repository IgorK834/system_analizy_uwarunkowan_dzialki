"""Wspólne porty i błędy indeksów BDOT10k (drogi, zabudowa) — warstwa aplikacji modułu ``analysis``."""

from __future__ import annotations

from typing import Protocol

from app.modules.analysis.domain.bdot import (
    REASON_DATABASE_ERROR,
    REASON_QUERY_TIMEOUT,
    BdotReleaseRef,
    CoverageCheck,
)


class BdotIndexError(RuntimeError):
    """Kontrolowana niedostępność indeksu (baza, konfiguracja) — wynik sekcji to ``unavailable``."""

    reason_code: str = REASON_DATABASE_ERROR

    def __init__(self, message: str, reason_code: str | None = None) -> None:
        super().__init__(message)
        if reason_code is not None:
            self.reason_code = reason_code


class BdotIndexTimeout(BdotIndexError):
    """Zapytanie przestrzenne przekroczyło limit czasu."""

    reason_code = REASON_QUERY_TIMEOUT


class BdotReleaseLocator(Protocol):
    """Wybór wydania i kontrola pokrycia; wspólne dla wszystkich klas obiektów BDOT10k."""

    def release_for(self, parcel_wkt: str) -> BdotReleaseRef | None:
        """Aktywne wydanie, którego zasięg (granica powiatu) zawiera punkt reprezentatywny działki."""
        ...

    def has_active_releases(self) -> bool:
        """Czy w ogóle istnieje aktywne wydanie (odróżnia „brak importu” od „poza zasięgiem”)."""
        ...

    def coverage(self, release: BdotReleaseRef, parcel_wkt: str, radius_m: float) -> CoverageCheck:
        """Czy działka i jej otoczenie o promieniu ``radius_m`` leżą w całości w zasięgu wydania."""
        ...
