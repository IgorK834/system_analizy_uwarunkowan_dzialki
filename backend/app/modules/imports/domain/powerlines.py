"""BDOT10k OT_SULN_L: jawny kontrakt linii elektroenergetycznych, bez domniemanego napięcia."""
from dataclasses import dataclass
from datetime import datetime

POWERLINE_LAYER = "OT_SULN_L"
POWERLINE_KINDS = frozenset({
    "linia elektroenergetyczna średniego napięcia",
    "linia elektroenergetyczna wysokiego napięcia",
    "linia elektroenergetyczna najwyższego napięcia",
})


@dataclass(frozen=True)
class PowerlineRow:
    local_id: str
    geometry_wkb: bytes
    kind: str
    existence_category: str | None
    feature_version_at: datetime | None
