"""Współdzielony, publiczny typ pochodzenia danych (provenance).

Każdy wynik pochodzący z danych zewnętrznych lub zaimportowanych musi nieść ślad
pochodzenia: identyfikator źródła (spójny z katalogiem), identyfikator wydania
danych oraz moment pobrania. Typ używa wyłącznie biblioteki standardowej.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Provenance:
    """Minimalny, niezmienny ślad pochodzenia danych domenowych."""

    source_id: str
    fetched_at: datetime | None = None
    data_release_id: int | None = None
    source_artifact_id: int | None = None
    content_hash: str | None = None

    def with_release(self, data_release_id: int) -> "Provenance":
        """Zwraca kopię provenance powiązaną z konkretnym wydaniem danych."""
        return Provenance(
            source_id=self.source_id,
            fetched_at=self.fetched_at,
            data_release_id=data_release_id,
            source_artifact_id=self.source_artifact_id,
            content_hash=self.content_hash,
        )
