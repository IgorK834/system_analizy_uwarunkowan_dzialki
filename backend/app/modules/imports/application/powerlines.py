"""Normalizacja i walidacja linii; naprawa geometrii przez istniejący port importu."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, TYPE_CHECKING

from app.modules.imports.domain.bdot10k import (
    Bdot10kValidationError, LayerStats, RawFeature, clean_text, keep_latest_per_id, parse_version,
)
from app.modules.imports.domain.powerlines import POWERLINE_KINDS, PowerlineRow

if TYPE_CHECKING:
    from app.modules.imports.application.bdot10k_import import RepairOutcome


class LineRepair(Protocol):
    def repair_line(self, wkb: bytes) -> RepairOutcome: ...


def prepare_powerlines(features: Sequence[RawFeature], mapping: Mapping[str, str], geometry: LineRepair,
                      stats: LayerStats) -> tuple[PowerlineRow, ...]:
    rows = []
    for feature in features:
        stats.read += 1
        attributes = feature.attributes
        local_id = clean_text(attributes.get(mapping["local_id"]))
        kind = clean_text(attributes.get("rodzaj"))
        if kind not in POWERLINE_KINDS:
            raise Bdot10kValidationError(f"Nieznany rodzaj OT_SULN_L: {kind!r}; kontrakt wymaga weryfikacji.")
        if not local_id or len(local_id) > 64 or not feature.geometry_wkb:
            raise Bdot10kValidationError("OT_SULN_L: brak identyfikatora lub geometrii; wydanie nie jest kompletne.")
        repaired = geometry.repair_line(feature.geometry_wkb)
        if repaired.wkb is None:
            raise Bdot10kValidationError(f"OT_SULN_L: odrzucona geometria ({repaired.reject_reason}).")
        version = parse_version(attributes.get(mapping["feature_version"]))
        if version and (stats.latest_version is None or version > stats.latest_version):
            stats.latest_version = version
        existence = clean_text(attributes.get(mapping["existence_category"]))
        stats.existence[existence or "(brak)"] += 1
        stats.repaired += int(repaired.repaired)
        rows.append(PowerlineRow(local_id, repaired.wkb, kind, existence, version))
    unique = tuple(keep_latest_per_id(rows, stats))
    stats.imported = len(unique)
    return unique
