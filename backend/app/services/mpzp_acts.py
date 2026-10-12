"""Akty MPZP wielokanałowo: Rejestr Urbanistyczny + KIMPZP + przecięcie z granicą aktu (AU-101, AU-103).

Kanał ``mpzp_ru`` pobiera akty z granicą (WFS ``app-mpzp``) dla ``bbox`` działki, przecina je z działką w EPSG:2180
i odrzuca akty, które tylko dotykają ``bbox``. Kanał ``kimpzp`` (discovery) dostarcza akty bez geometrii. Obie
listy są scalane po numerze uchwały, a ich pierwszeństwo oceniane jest jawną regułą
``mpzp-act-precedence/1.0`` (``modules/planning/domain/mpzp_acts``). Wynik niesie też rozłączny stan ustalenia
MPZP (``mpzp_status``, AU-103).

Żaden wyjątek źródła nie wychodzi z tego modułu: błąd kanału RU to stan ``unavailable`` tego kanału, a nie pusta
lista aktów, więc nigdy nie staje się „brakiem planu” ani „brakiem ograniczeń”.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Final

import shapely
from shapely.geometry.base import BaseGeometry

from app.core.data_sources import CatalogError, ensure_source_runnable
from app.core.settings import settings
from app.modules.planning.application.ports import RuMpzpActsSource
from app.modules.planning.composition import ru_mpzp_acts_source
from app.modules.planning.domain.mpzp_acts import (
    KIMPZP_SOURCE_ID,
    MPZP_CHANNEL_MISMATCH,
    MPZP_ACT_OLDER_VERSION_DROPPED,
    MPZP_ACT_DOCUMENT_UNRESOLVED,
    PRECEDENCE_RULE_VERSION,
    RU_SOURCE_ID,
    ChannelOutcome,
    ChannelStatus,
    IntersectionOutcome,
    MpzpActRecord,
    RejectedAct,
    RuActRecord,
    apply_precedence,
    channel_agreement,
    intersect_act_with_parcel,
    kimpzp_candidates,
    latest_versions,
    merge_channels,
    ru_candidates,
    ru_legal_status,
)
from app.modules.planning.domain.mpzp_status import StatusDecision, derive_mpzp_status
from app.modules.planning.infrastructure.ru_mpzp_acts import (
    RU_GEOMETRY_UNUSABLE,
    RU_PARTIAL_RESPONSE,
)
from app.schemas.analyze import (
    MpzpActAmendment,
    MpzpActResult,
    MpzpChannelStatus,
    MpzpRejectedAct,
    MpzpStatus,
)
from app.schemas.source import SourceMetadata, WarningMessage
from app.services.geojson import analysis_layer_geometry_to_geojson
from app.shared.mpzp_status_text import (
    MPZP_CHANNEL_LABELS,
    MPZP_CHANNEL_STATUS_LABELS,
    MPZP_STATUS_LABELS,
    MPZP_STATUS_MESSAGES,
)

logger = logging.getLogger(__name__)

MPZP_ACT_BOUNDARY_OMITTED: Final[str] = "MPZP_ACT_BOUNDARY_OMITTED"
MPZP_RU_UNAVAILABLE: Final[str] = "MPZP_RU_UNAVAILABLE"
MPZP_RU_INTERNAL_ERROR: Final[str] = "MPZP_RU_INTERNAL_ERROR"
MPZP_ACTS_RESOLUTION_ERROR: Final[str] = "MPZP_ACTS_RESOLUTION_ERROR"
MPZP_RU_NO_ACT_AT_PARCEL: Final[str] = "MPZP_RU_NO_ACT_AT_PARCEL"

# Kontekst mapy: granica aktu przycięta do okna wokół działki (zasięg całej gminy nie trafia do odpowiedzi).
_BOUNDARY_MIN_MARGIN_M: Final[float] = 100.0
_BOUNDARY_SIMPLIFY_M: Final[float] = 0.5
_BOUNDARY_MAX_VERTICES: Final[int] = 4000
_COORD_DECIMALS: Final[int] = 6


@dataclass(frozen=True)
class RuChannelResult:
    """Wynik kanału ``mpzp_ru`` dla działki."""

    queried: bool
    status: ChannelStatus = "unknown"
    reason_codes: tuple[str, ...] = ()
    records: tuple[MpzpActRecord, ...] = ()
    rejected: tuple[RejectedAct, ...] = ()
    source: SourceMetadata | None = None
    parcel: BaseGeometry | None = None


@dataclass(frozen=True)
class MpzpActsResolution:
    """Wynik scalenia kanałów: akty z pierwszeństwem, odrzucone, kanały, ostrzeżenia."""

    records: tuple[MpzpActRecord, ...]
    rejected: tuple[RejectedAct, ...]
    channels: tuple[ChannelOutcome, ...]
    sources: dict[str, SourceMetadata]
    agreement: str
    warnings: tuple[WarningMessage, ...]
    parcel: BaseGeometry | None = None
    precedence_rule_version: str = PRECEDENCE_RULE_VERSION

    @property
    def selected(self) -> MpzpActRecord | None:
        """Jedyny akt, który obowiązuje na całej działce bez konfliktu; w innych przypadkach ``None``."""
        candidates = [item for item in self.records if item.in_force_candidate]
        if len(candidates) == 1 and candidates[0].precedence == "applies":
            return candidates[0]
        return None


# --- kanał RU ----------------------------------------------------------------------------------------------


def _source_metadata(
    source_id: str, name: str, url: str | None, provenance: Any, *, status: ChannelStatus, review: bool
) -> SourceMetadata:
    digest = getattr(provenance, "content_hash", None)
    complete = getattr(provenance, "complete", None)
    return SourceMetadata(
        source_id=source_id,
        source_name=name,
        source_url=url,
        fetched_at=getattr(provenance, "fetched_at", None) or datetime.now(timezone.utc),
        artifact_sha256=digest if status in {"available", "no_match"} and complete and digest and len(digest) == 64 else None,
        response_status=200 if status in {"available", "no_match", "unknown"} else None,
        confidence={"available": 0.8, "no_match": 0.6}.get(status, 0.3),
        manual_review_required=review or status != "available",
    )


def _ru_wfs_url() -> str | None:
    try:
        entry = ensure_source_runnable(RU_SOURCE_ID)
    except CatalogError:
        return None
    resource = next((item for item in entry.resources if item.role == "boundaries"), None)
    return resource.url if resource else None


def _fetch_ru_channel_sync(
    parcel: BaseGeometry, source: RuMpzpActsSource, as_of: date
) -> RuChannelResult:
    url = _ru_wfs_url()
    response = source.fetch_acts(tuple(parcel.bounds))  # type: ignore[arg-type]
    if response.error_code is not None:
        return RuChannelResult(
            queried=True,
            status="unavailable",
            reason_codes=(MPZP_RU_UNAVAILABLE, response.error_code),
            source=_source_metadata(RU_SOURCE_ID, "Rejestr Urbanistyczny (MPZP)", url, response.source,
                                    status="unavailable", review=True),
            parcel=parcel,
        )
    acts, dropped = latest_versions(response.acts)
    reasons: list[str] = list(response.issues)
    if dropped:
        reasons.append(MPZP_ACT_OLDER_VERSION_DROPPED)
    accepted: dict[str, Any] = {}
    outcomes: dict[str, IntersectionOutcome] = {}
    rejected: list[RejectedAct] = []
    accepted_acts: list[RuActRecord] = []
    unusable = 0
    for act in acts:
        outcome = intersect_act_with_parcel(parcel, act.geometry)
        outcomes[act.gml_id] = outcome
        status, _ = ru_legal_status(act, as_of)
        if outcome.accepted is not None:
            accepted[act.gml_id] = outcome.accepted
            accepted_acts.append(act)
            continue
        if outcome.rejection == "geometry_unusable":
            unusable += 1
        rejected.append(
            RejectedAct(
                act_identifier=act.identifier,
                title=act.title,
                legal_status=status,
                reason=outcome.rejection or "geometry_unusable",
                distance_m=outcome.distance_m,
                area_sqm=outcome.area_sqm,
                raw_status=act.status_title or act.status_code,
            )
        )
    records: list[MpzpActRecord] = []
    if accepted_acts:
        document_paths = [path for act in accepted_acts for _, path in act.document_refs]
        drawing_paths = [path for act in accepted_acts for path in act.drawing_refs]
        related = source.fetch_related(document_paths, drawing_paths)
        if related.error_codes or related.unresolved_documents:
            reasons.append(MPZP_ACT_DOCUMENT_UNRESOLVED)
            reasons.extend(related.error_codes)
        records = ru_candidates(accepted_acts, related.documents, related.drawings, accepted, as_of)
        # Odrzucony akt dostaje numer uchwały z dokumentu tylko wtedy, gdy dokument był pobrany — tu nie był.
    if records:
        status_value: ChannelStatus = "available"
    elif unusable or RU_PARTIAL_RESPONSE in reasons or not response.complete:
        status_value = "unknown"
        if unusable:
            reasons.append(RU_GEOMETRY_UNUSABLE)
    else:
        status_value = "no_match"
        reasons.append(MPZP_RU_NO_ACT_AT_PARCEL)
    reasons = [code for code in dict.fromkeys(reasons)]
    review = any(item.manual_review_required for item in records)
    return RuChannelResult(
        queried=True,
        status=status_value,
        reason_codes=tuple(reasons),
        records=tuple(records),
        rejected=tuple(rejected),
        source=_source_metadata(RU_SOURCE_ID, "Rejestr Urbanistyczny (MPZP)", url, response.source,
                                status=status_value, review=review),
        parcel=parcel,
    )


async def fetch_ru_channel(
    parcel: BaseGeometry,
    *,
    source: RuMpzpActsSource | None = None,
    as_of: date | None = None,
) -> RuChannelResult:
    """Pobiera i przecina akty RU; nigdy nie podnosi wyjątku (błąd = kanał ``unavailable``)."""
    active = source
    if active is None:
        if not settings.ru_mpzp_acts_enabled:
            return RuChannelResult(queried=False)
        try:
            active = ru_mpzp_acts_source()
        except Exception:  # noqa: BLE001 - konfiguracja kanału nie może zatrzymać analizy
            logger.exception("ru_mpzp_acts_source_unavailable")
            active = None
        if active is None:
            return RuChannelResult(queried=False)
    day = as_of or datetime.now(timezone.utc).date()
    try:
        return await asyncio.to_thread(_fetch_ru_channel_sync, parcel, active, day)
    except Exception as exc:  # noqa: BLE001 - błąd wewnętrzny kanału to nie „brak aktów”
        logger.exception("ru_mpzp_acts_internal_error", extra={"error": type(exc).__name__})
        return RuChannelResult(
            queried=True,
            status="unavailable",
            reason_codes=(MPZP_RU_UNAVAILABLE, MPZP_RU_INTERNAL_ERROR),
            source=_source_metadata(RU_SOURCE_ID, "Rejestr Urbanistyczny (MPZP)", _ru_wfs_url(), None,
                                    status="unavailable", review=True),
            parcel=parcel,
        )


# --- scalenie kanałów --------------------------------------------------------------------------------------


def _kimpzp_channel(discovery: Any) -> tuple[ChannelOutcome, SourceMetadata | None]:
    if discovery is None:
        return ChannelOutcome(KIMPZP_SOURCE_ID, "unknown", ("MPZP_DISCOVERY_ERROR",), 0), None
    codes = tuple(dict.fromkeys([*discovery.reason_codes, *(issue.code for issue in discovery.issues)]))
    return (
        ChannelOutcome(KIMPZP_SOURCE_ID, discovery.status, codes, len(discovery.acts)),
        discovery.source_metadata,
    )


def _mismatch_warning(ru_labels: tuple[str, ...], kimpzp_labels: tuple[str, ...]) -> WarningMessage:
    return WarningMessage(
        code=MPZP_CHANNEL_MISMATCH,
        message=(
            "Kanały wskazują różne akty MPZP dla działki. Rejestr Urbanistyczny: "
            + (", ".join(ru_labels) or "brak")
            + "; KIMPZP: "
            + (", ".join(kimpzp_labels) or "brak")
            + ". System nie rozstrzyga rozbieżności — sprawdź w uchwałach, który akt obowiązuje na działce."
        ),
        severity="warning",
        source_name="mpzp",
    )


def resolve_mpzp_acts(
    discovery: Any,
    ru: RuChannelResult,
    *,
    as_of: date | None = None,
    parcel: BaseGeometry | None = None,
) -> MpzpActsResolution:
    """Scala akty KIMPZP i RU, ocenia pierwszeństwo i zgodność kanałów (czysta funkcja, bez sieci)."""
    day = as_of or datetime.now(timezone.utc).date()
    kimpzp_outcome, kimpzp_source = _kimpzp_channel(discovery)
    kimpzp_records = kimpzp_candidates(discovery.acts, day) if discovery is not None else []
    ru_outcome = ChannelOutcome(
        RU_SOURCE_ID, ru.status, ru.reason_codes, len([r for r in ru.records if r.in_force_candidate])
    )
    merged = merge_channels(list(ru.records), kimpzp_records)
    records = apply_precedence(merged)
    agreement, ru_labels, kimpzp_labels = channel_agreement(
        list(ru.records), kimpzp_records, ru.status, kimpzp_outcome.status
    )
    warnings: list[WarningMessage] = []
    if agreement == "mismatch":
        warnings.append(_mismatch_warning(ru_labels, kimpzp_labels))
    channels = [kimpzp_outcome]
    sources: dict[str, SourceMetadata] = {}
    if kimpzp_source is not None:
        sources[KIMPZP_SOURCE_ID] = kimpzp_source
    if ru.queried:
        channels.append(ru_outcome)
        if ru.source is not None:
            sources[RU_SOURCE_ID] = ru.source
    return MpzpActsResolution(
        records=tuple(records),
        rejected=ru.rejected,
        channels=tuple(channels),
        sources=sources,
        agreement=agreement,
        warnings=tuple(warnings),
        parcel=parcel if parcel is not None else ru.parcel,
    )


def unresolved_resolution(parcel: BaseGeometry | None = None) -> MpzpActsResolution:
    """Wynik awaryjny, gdy scalenie się nie powiodło: nic nie jest twierdzone o planie (stan ``unknown``)."""
    return MpzpActsResolution(
        records=(),
        rejected=(),
        channels=(ChannelOutcome(KIMPZP_SOURCE_ID, "unknown", (MPZP_ACTS_RESOLUTION_ERROR,), 0),),
        sources={},
        agreement="none",
        warnings=(),
        parcel=parcel,
    )


# --- kontrakt API ------------------------------------------------------------------------------------------


def _round(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, _COORD_DECIMALS)
    if isinstance(value, (list, tuple)):
        return [_round(item) for item in value]
    return value


def _vertex_count(geometry: BaseGeometry) -> int:
    return int(shapely.get_num_coordinates(geometry))


def _geojson(geometry: BaseGeometry | None, layer: str, properties: dict[str, Any]) -> dict[str, Any] | None:
    if geometry is None or geometry.is_empty:
        return None
    feature = analysis_layer_geometry_to_geojson(geometry, layer, properties)
    if feature is None:
        return None
    geom = feature["geometry"]
    feature["geometry"] = {"type": geom["type"], "coordinates": _round(geom["coordinates"])}
    return feature


def _boundary_window(parcel: BaseGeometry) -> BaseGeometry:
    min_x, min_y, max_x, max_y = parcel.bounds
    margin = max(_BOUNDARY_MIN_MARGIN_M, 0.5 * max(max_x - min_x, max_y - min_y))
    return shapely.box(min_x - margin, min_y - margin, max_x + margin, max_y + margin)


def _boundary_geometry(record: MpzpActRecord, parcel: BaseGeometry | None) -> tuple[BaseGeometry | None, bool]:
    """Granica aktu w oknie wokół działki, uproszczona; ``(None, True)`` gdy nie da się jej ograniczyć."""
    if record.ru is None or record.ru.geometry is None or parcel is None:
        return None, False
    geometry = record.ru.geometry
    if not geometry.is_valid:
        geometry = shapely.make_valid(geometry)
    clipped = shapely.intersection(geometry, _boundary_window(parcel))
    if clipped.is_empty:
        return None, False
    tolerance = _BOUNDARY_SIMPLIFY_M
    simple = clipped
    for _ in range(6):
        simple = shapely.simplify(clipped, tolerance, preserve_topology=True)
        if _vertex_count(simple) <= _BOUNDARY_MAX_VERTICES:
            return simple, False
        tolerance *= 2
    return None, True


def acts_to_contract(resolution: MpzpActsResolution) -> list[MpzpActResult]:
    """Akty w kształcie ``AnalyzeResponse.mpzp_acts[]`` (geometrie w EPSG:4326)."""
    results: list[MpzpActResult] = []
    for record in resolution.records:
        boundary, omitted = _boundary_geometry(record, resolution.parcel)
        label = record.resolution_number or record.act_identifier or "akt"
        properties = {
            "resolution_number": record.resolution_number,
            "act_identifier": record.act_identifier,
            "legal_status": record.legal_status,
            "precedence": record.precedence,
            "sources": list(record.sources),
        }
        codes = list(record.reason_codes)
        if omitted:
            codes.append(MPZP_ACT_BOUNDARY_OMITTED)
        results.append(
            MpzpActResult(
                act_identifier=record.act_identifier,
                title=record.title,
                resolution_number=record.resolution_number,
                resolution_number_basis=record.resolution_number_basis,
                resolution_date=record.resolution_date,
                effective_from=record.effective_from,
                effective_to=record.effective_to,
                legal_status=record.legal_status,
                raw_status=record.raw_status,
                document_url=record.document_url,
                document_kind=record.document_kind,
                legend_url=record.legend_url,
                drawing_url=record.drawing_url,
                journal=record.journal,
                amendments=[
                    MpzpActAmendment(
                        resolution_number=item.resolution_number,
                        effective_from=item.effective_from,
                        document_url=item.document_url,
                        name=item.name,
                        source=item.source,
                    )
                    for item in record.amendments
                ],
                intersection_area_sqm=record.intersection.area_sqm if record.intersection else None,
                intersection_pct=record.intersection.pct if record.intersection else None,
                intersection_geojson=_geojson(
                    record.intersection.geometry if record.intersection else None,
                    "mpzp_act_intersection",
                    {**properties, "label": label},
                ),
                boundary_geojson=_geojson(boundary, "mpzp_act_boundary", {**properties, "label": label}),
                sources=list(record.sources),
                precedence=record.precedence,
                manual_review_required=record.manual_review_required,
                reason_codes=list(dict.fromkeys(codes)),
            )
        )
    return results


def _deciding_source(
    resolution: MpzpActsResolution, decision: StatusDecision
) -> SourceMetadata | None:
    """Źródło kanału, który rozstrzygnął stan — trafia do macierzy jakości zamiast „brak źródła”."""
    order: tuple[str, ...]
    if decision.status in {"available", "ambiguous"}:
        wanted = "available"
        order = (RU_SOURCE_ID, KIMPZP_SOURCE_ID)
    elif decision.status == "unavailable":
        wanted, order = "unavailable", (KIMPZP_SOURCE_ID, RU_SOURCE_ID)
    elif decision.status == "unknown":
        wanted, order = "unknown", (KIMPZP_SOURCE_ID, RU_SOURCE_ID)
    elif decision.status == "no_coverage":
        wanted, order = "no_coverage", (KIMPZP_SOURCE_ID,)
    else:
        wanted, order = "no_match", (KIMPZP_SOURCE_ID, RU_SOURCE_ID)
    by_channel = {item.channel: item for item in resolution.channels}
    for channel in order:
        outcome = by_channel.get(channel)  # type: ignore[call-overload]
        if outcome is not None and outcome.status == wanted and channel in resolution.sources:
            return resolution.sources[channel]
    for channel in order:
        if channel in resolution.sources:
            return resolution.sources[channel]
    return next(iter(resolution.sources.values()), None)


def build_mpzp_status(
    resolution: MpzpActsResolution, *, has_zones: bool, manual_zone_required: bool
) -> MpzpStatus:
    """Rozłączny stan ustalenia MPZP z kanałów, aktów i stref (AU-103)."""
    decision = derive_mpzp_status(
        channels=resolution.channels,
        acts=resolution.records,
        has_zones=has_zones,
        manual_zone_required=manual_zone_required,
    )
    return MpzpStatus(
        status=decision.status,
        reason_code=decision.reason_code,
        reason_codes=list(decision.reason_codes),
        label=MPZP_STATUS_LABELS[decision.status],
        message=MPZP_STATUS_MESSAGES[decision.status],
        channels=[
            MpzpChannelStatus(
                channel=item.channel,
                label=MPZP_CHANNEL_LABELS[item.channel],
                status=item.status,
                status_label=MPZP_CHANNEL_STATUS_LABELS[item.status],
                reason_codes=list(item.reason_codes),
                acts_found=item.acts_found,
                source=resolution.sources.get(item.channel),
            )
            for item in resolution.channels
        ],
        channel_agreement=resolution.agreement,  # type: ignore[arg-type]
        precedence_rule_version=resolution.precedence_rule_version,
        rejected_acts=[
            MpzpRejectedAct(
                act_identifier=item.act_identifier,
                title=item.title,
                resolution_number=item.resolution_number,
                legal_status=item.legal_status,
                raw_status=item.raw_status,
                reason=item.reason,
                distance_m=item.distance_m,
                area_sqm=item.area_sqm,
                channel="mpzp_ru",
            )
            for item in resolution.rejected
        ],
        source=_deciding_source(resolution, decision),
    )
