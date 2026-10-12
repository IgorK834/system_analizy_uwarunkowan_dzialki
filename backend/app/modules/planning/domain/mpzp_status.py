"""Rozłączny stan ustalenia MPZP dla działki z wyników kanałów KIMPZP i Rejestru Urbanistycznego (AU-103).

Tabela decyzyjna (kolejność wierszy ma znaczenie):

1. są strefy z przecięcia wektorowego                        → ``available`` (``MPZP_ZONES_INTERSECTED``)
2. analiza czeka na ręczny symbol strefy                      → ``available`` (``MPZP_MANUAL_ZONE_REQUIRED``)
3. jest akt, który może obowiązywać:
   a. którykolwiek ma pierwszeństwo ``unresolved``            → ``ambiguous`` (``MPZP_ACTS_UNRESOLVED``)
   b. w przeciwnym razie                                      → ``available`` (``MPZP_ACT_FOUND``)
4. brak aktu, który może obowiązywać:
   a. żaden kanał nie został odpytany                         → ``unknown`` (``MPZP_NO_CHANNEL_QUERIED``)
   b. którykolwiek kanał ``unavailable``                      → ``unavailable`` — błąd źródła nigdy nie jest
                                                                „brakiem planu”, także gdy drugi kanał mówi
                                                                ``no_match``
   c. którykolwiek kanał ``unknown``                          → ``unknown`` (``MPZP_NOT_DETERMINED``)
   d. są tylko akty bez mocy (projekt, utracił moc)           → ``no_match`` (``MPZP_ONLY_NON_BINDING_ACTS``)
   e. KIMPZP ``no_coverage``                                  → ``no_coverage`` (``KIMPZP_NO_SERVICE_FOR_AREA``)
   f. w przeciwnym razie                                      → ``no_match`` (``MPZP_NO_ACT_AT_PARCEL``)

Stan ``no_match`` występuje więc tylko wtedy, gdy każdy odpytany kanał odpowiedział poprawnie.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Sequence

from app.modules.planning.domain.mpzp_acts import ChannelOutcome, MpzpActRecord
from app.shared.mpzp_status_text import MpzpStatusValue

MPZP_STATUS_SCHEMA_VERSION: Final[str] = "1.0"

MPZP_ZONES_INTERSECTED: Final[str] = "MPZP_ZONES_INTERSECTED"
MPZP_MANUAL_ZONE_REQUIRED: Final[str] = "MPZP_MANUAL_ZONE_REQUIRED"
MPZP_MANUAL_ZONE_SELECTED: Final[str] = "MPZP_MANUAL_ZONE_SELECTED"
MPZP_ACT_FOUND: Final[str] = "MPZP_ACT_FOUND"
MPZP_ACTS_UNRESOLVED: Final[str] = "MPZP_ACTS_UNRESOLVED"
MPZP_NO_CHANNEL_QUERIED: Final[str] = "MPZP_NO_CHANNEL_QUERIED"
MPZP_SOURCE_UNAVAILABLE: Final[str] = "MPZP_SOURCE_UNAVAILABLE"
MPZP_NOT_DETERMINED: Final[str] = "MPZP_NOT_DETERMINED"
MPZP_ONLY_NON_BINDING_ACTS: Final[str] = "MPZP_ONLY_NON_BINDING_ACTS"
MPZP_NO_ACT_AT_PARCEL: Final[str] = "MPZP_NO_ACT_AT_PARCEL"
KIMPZP_NO_SERVICE_FOR_AREA: Final[str] = "KIMPZP_NO_SERVICE_FOR_AREA"


@dataclass(frozen=True)
class StatusDecision:
    """Stan z głównym kodem powodu i pełną listą kodów (kanałów oraz aktów)."""

    status: MpzpStatusValue
    reason_code: str
    reason_codes: tuple[str, ...]


def _unique(codes: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(code for code in codes if code))


def derive_mpzp_status(
    *,
    channels: Sequence[ChannelOutcome],
    acts: Sequence[MpzpActRecord],
    has_zones: bool,
    manual_zone_required: bool,
) -> StatusDecision:
    channel_codes = [code for channel in channels for code in channel.reason_codes]
    if has_zones:
        return StatusDecision("available", MPZP_ZONES_INTERSECTED, _unique([MPZP_ZONES_INTERSECTED, *channel_codes]))
    if manual_zone_required:
        return StatusDecision(
            "available", MPZP_MANUAL_ZONE_REQUIRED, _unique([MPZP_MANUAL_ZONE_REQUIRED, *channel_codes])
        )
    candidates = [act for act in acts if act.in_force_candidate]
    if candidates:
        if any(act.precedence == "unresolved" for act in candidates):
            return StatusDecision(
                "ambiguous", MPZP_ACTS_UNRESOLVED, _unique([MPZP_ACTS_UNRESOLVED, *channel_codes])
            )
        return StatusDecision("available", MPZP_ACT_FOUND, _unique([MPZP_ACT_FOUND, *channel_codes]))
    if not channels:
        return StatusDecision("unknown", MPZP_NO_CHANNEL_QUERIED, (MPZP_NO_CHANNEL_QUERIED,))
    statuses = {channel.status for channel in channels}
    if "unavailable" in statuses:
        return StatusDecision(
            "unavailable", MPZP_SOURCE_UNAVAILABLE, _unique([MPZP_SOURCE_UNAVAILABLE, *channel_codes])
        )
    if "unknown" in statuses:
        return StatusDecision("unknown", MPZP_NOT_DETERMINED, _unique([MPZP_NOT_DETERMINED, *channel_codes]))
    if acts:
        return StatusDecision(
            "no_match", MPZP_ONLY_NON_BINDING_ACTS, _unique([MPZP_ONLY_NON_BINDING_ACTS, *channel_codes])
        )
    if any(channel.channel == "kimpzp" and channel.status == "no_coverage" for channel in channels):
        return StatusDecision(
            "no_coverage", KIMPZP_NO_SERVICE_FOR_AREA, _unique([KIMPZP_NO_SERVICE_FOR_AREA, *channel_codes])
        )
    return StatusDecision("no_match", MPZP_NO_ACT_AT_PARCEL, _unique([MPZP_NO_ACT_AT_PARCEL, *channel_codes]))
