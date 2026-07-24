"""Porty (protokoły) modułu ``analysis``.

Definiuje kontrakt repozytorium snapshotów analizy. Importuje wyłącznie
``app.shared`` (i ewentualnie własne typy domenowe) — zgodnie z regułami
zależności warstwy aplikacyjnej.
"""

from __future__ import annotations

from typing import Protocol

from app.shared.provenance import Provenance


class AnalysisSnapshotWriter(Protocol):
    """Port zapisu niezmiennego snapshotu wyniku analizy."""

    def persist(self, snapshot_json: str, provenance: Provenance) -> int: ...
