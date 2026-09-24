"""Porty (protokoły) modułu ``analysis``.

Definiuje kontrakt repozytorium snapshotów analizy. Importuje wyłącznie
``app.shared`` (i ewentualnie własne typy domenowe) — zgodnie z regułami
zależności warstwy aplikacyjnej.
"""

from __future__ import annotations

from typing import Mapping, Protocol

from app.shared.provenance import Provenance


class AnalysisSnapshotWriter(Protocol):
    """Port zapisu niezmiennego snapshotu wyniku analizy."""

    def persist(self, snapshot_json: str, provenance: Provenance) -> int: ...


class AnalysisCaseRunner(Protocol):
    """Port pojedynczego uruchomienia analizy dla powtarzalnego eksperymentu.

    Adapter otrzymuje wyłącznie wejście i zamrożone obserwacje źródeł. Pole
    ``expected`` należy do warstwy porównującej i nie może być przekazywane do
    implementacji tego portu.
    """

    def analyze(self, case_input: Mapping[str, object]) -> Mapping[str, object]: ...
