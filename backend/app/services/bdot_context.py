"""Mapowanie wyników przypadków użycia drogi i zabudowy (BDOT10k) na sekcje kontraktu API (Task 23.2–23.3).

Sekcje mają statusy rozłączne; provenance niesie każdy wynik, także nieudany. Czas ``fetched_at`` to chwila importu
wydania do bazy (nie chwila zapytania), więc świeżość w macierzy jakości liczy wiek danych lokalnej migawki, a data
stanu danych (``data_as_of``) jest osobnym polem wydania.
"""

from __future__ import annotations

from typing import Any, Final

from shapely import from_wkt

from app.core.data_sources import CatalogError, get_catalog
from app.modules.analysis.application.buildings import BuildingContextOutcome
from app.modules.analysis.application.roads import RoadContextOutcome
from app.modules.analysis.domain.bdot import BDOT10K_SOURCE_ID, BdotReleaseRef
from app.modules.analysis.domain.buildings import BuildingMetrics
from app.modules.analysis.domain.roads import RoadCandidate
from app.schemas.bdot_context import (
    BdotReleaseInfo,
    BuildingBufferResult,
    NeighbourBuildingItem,
    NeighbourBuildingsResult,
    ParcelBuildingsResult,
    RoadContextResult,
    RoadNearestResult,
)
from app.schemas.source import SourceMetadata, WarningMessage
from app.services.geojson import analysis_layer_geometry_to_geojson

SOURCE_NAME: Final[str] = "BDOT10k"
REASON_NOT_RECORDED: Final[str] = "SECTION_NOT_RECORDED"
REASON_FEATURE_DISABLED: Final[str] = "FEATURE_DISABLED"
LEGACY_ROAD_WARNING: Final[str] = (
    "Zapis sprzed sekcji drogowej (Task 23.2) — brak informacji o drodze; nie oznacza to braku drogi."
)
LEGACY_BUILDINGS_WARNING: Final[str] = (
    "Zapis sprzed sekcji zabudowy sąsiedniej (Task 23.3) — brak informacji o budynkach; nie oznacza to braku budynków."
)
_AVAILABLE_CONFIDENCE: Final[float] = 0.9
_PARTIAL_CONFIDENCE: Final[float] = 0.6


def release_info(release: BdotReleaseRef | None) -> BdotReleaseInfo | None:
    if release is None:
        return None
    return BdotReleaseInfo(
        data_release_id=release.data_release_id,
        version_label=release.version_label,
        teryt_powiat=release.teryt_powiat,
        data_as_of=release.data_as_of,
        package_sha256=release.package_sha256,
        imported_at=release.published_at,
    )


def _source_url(teryt_powiat: str | None) -> str | None:
    """Adres paczki powiatu z katalogu źródeł (dowód pochodzenia); bez powiatu — adres bazowy usługi katalogu."""
    try:
        entry = get_catalog().get(BDOT10K_SOURCE_ID)
    except CatalogError:
        return None
    if teryt_powiat and entry.file_url:
        return entry.file_url.replace("{woj}", teryt_powiat[:2]).replace("{powiat}", teryt_powiat)
    return entry.capabilities_url


def bdot_source_metadata(
    release: BdotReleaseRef | None,
    *,
    available: bool,
    partial: bool,
    manual_review: bool,
) -> SourceMetadata:
    """Provenance sekcji: wydanie (jeśli ustalono), adres paczki, czas importu i pewność."""
    return SourceMetadata(
        source_id=BDOT10K_SOURCE_ID,
        source_name=SOURCE_NAME,
        source_version=release.version_label if release else None,
        artifact_sha256=release.package_sha256 if release else None,
        data_release_id=release.data_release_id if release else None,
        source_url=_source_url(release.teryt_powiat if release else None),
        # Czas pobrania to chwila importu wydania; bez wydania (brak pokrycia, błąd) nic nie zostało odczytane, więc czasu
        # nie ma — świeżość jest wtedy „nieustalona”, a nie „aktualna” względem próby zapytania.
        fetched_at=release.published_at if release is not None else None,
        response_status=None,
        confidence=(_PARTIAL_CONFIDENCE if partial else _AVAILABLE_CONFIDENCE) if available else 0.0,
        manual_review_required=manual_review,
    )


# --- drogi ---------------------------------------------------------------------------------------------


def _line_feature(wkt: str | None, layer: str, properties: dict[str, Any]) -> dict[str, Any] | None:
    if not wkt:
        return None
    return analysis_layer_geometry_to_geojson(from_wkt(wkt), layer, properties)


def _nearest_result(candidate: RoadCandidate) -> RoadNearestResult:
    properties = {
        "road_id": candidate.road_id,
        "distance_m": round(candidate.distance_m, 2),
        "category": candidate.category,
        "road_class": candidate.road_class,
    }
    return RoadNearestResult(
        road_id=candidate.road_id,
        distance_m=round(candidate.distance_m, 2),
        relation=candidate.relation,
        category=candidate.category,
        road_class=candidate.road_class,
        road_number=candidate.road_number,
        street_name=candidate.street_name,
        surface_material=candidate.surface_material,
        surface_width_m=candidate.surface_width_m,
        position=candidate.position,
        feature_version=candidate.feature_version,
        road_geojson=_line_feature(candidate.road_wkt, "road_context", properties),
        link_geojson=_line_feature(candidate.link_wkt, "road_context_link", properties),
    )


def build_road_context(outcome: RoadContextOutcome) -> RoadContextResult:
    """Sekcja ``road_context`` z wyniku przypadku użycia."""
    covered = outcome.search_area_within_coverage
    partial = outcome.status in {"available", "no_match"} and covered is False
    return RoadContextResult(
        status=outcome.status,
        reason_code=outcome.reason_code,
        nearest=_nearest_result(outcome.nearest) if outcome.nearest is not None else None,
        search_radius_m=outcome.search_radius_m,
        searched_radii_m=list(outcome.searched_radii_m),
        max_search_radius_m=outcome.max_search_radius_m,
        candidate_limit=outcome.candidate_limit,
        search_area_within_coverage=covered,
        intersecting_road_count=outcome.intersecting_road_count,
        touching_road_count=outcome.touching_road_count,
        release=release_info(outcome.release),
        source=bdot_source_metadata(
            outcome.release,
            available=outcome.status in {"available", "no_match"},
            partial=partial,
            manual_review=outcome.status == "unavailable" or partial,
        ),
        warnings=list(outcome.warnings),
    )


def disabled_road_context() -> RoadContextResult:
    """Sekcja wyłączona konfiguracją (``ROAD_CONTEXT_ENABLED=false``): jawny brak, nie „brak drogi”."""
    return RoadContextResult(
        status="unavailable",
        reason_code=REASON_FEATURE_DISABLED,
        source=bdot_source_metadata(None, available=False, partial=False, manual_review=True),
        warnings=["Kontekst drogowy jest wyłączony w konfiguracji — drogi nie sprawdzono."],
    )


def failed_road_context(error_name: str) -> RoadContextResult:
    """Awaria mapowania sekcji: jawny ``unavailable``, nigdy „brak drogi” (typ błędu w kodzie, bez treści)."""
    return RoadContextResult(
        status="unavailable",
        reason_code="UNEXPECTED_ERROR",
        source=bdot_source_metadata(None, available=False, partial=False, manual_review=True),
        warnings=[f"Nieoczekiwany błąd kontekstu drogowego ({error_name}) — odległości nie policzono."],
    )


def road_context_from_snapshot(raw: dict[str, Any] | None) -> RoadContextResult:
    """Odczyt historyczny: brak zapisu to ``unknown``, nigdy „brak drogi”."""
    if raw is None:
        return RoadContextResult(status="unknown", reason_code=REASON_NOT_RECORDED, warnings=[LEGACY_ROAD_WARNING])
    return RoadContextResult.model_validate(raw)


# --- zabudowa ------------------------------------------------------------------------------------------


def _buffer_results(metrics: BuildingMetrics) -> list[BuildingBufferResult]:
    return [
        BuildingBufferResult(
            radius_m=item.radius_m,
            ring_area_sqm=item.ring_area_sqm,
            count=item.count,
            straddling_parcel_count=item.straddling_parcel_count,
            footprint_area_in_ring_sqm=item.footprint_area_in_ring_sqm,
            union_area_in_ring_sqm=item.union_area_in_ring_sqm,
            built_share_pct=min(item.built_share_pct, 100.0),
            min_distance_m=item.min_distance_m,
            footprint_min_sqm=item.footprint_min_sqm,
            footprint_median_sqm=item.footprint_median_sqm,
            footprint_max_sqm=item.footprint_max_sqm,
        )
        for item in metrics.buffers
    ]


def _building_items(metrics: BuildingMetrics) -> list[NeighbourBuildingItem]:
    items: list[NeighbourBuildingItem] = []
    for listed in metrics.listed:
        building = listed.footprint
        properties = {
            "building_id": building.building_id,
            "relation": listed.relation,
            "distance_m": listed.distance_m,
            "floors": building.floors,
            "function": building.general_function,
        }
        items.append(
            NeighbourBuildingItem(
                building_id=building.building_id,
                distance_m=listed.distance_m,
                relation=listed.relation,
                overlap_with_parcel_sqm=listed.overlap_with_parcel_sqm,
                footprint_sqm=round(building.area_sqm, 2),
                general_function=building.general_function,
                detailed_function=building.detailed_function,
                predominant_function=building.predominant_function,
                floors=building.floors,
                geometry_source=building.geometry_source,
                feature_version=building.feature_version.isoformat() if building.feature_version else None,
                geometry_geojson=analysis_layer_geometry_to_geojson(building.geometry, "neighbour_buildings", properties),
            )
        )
    return items


def build_neighbour_buildings(outcome: BuildingContextOutcome) -> NeighbourBuildingsResult:
    """Sekcja ``neighbour_buildings`` z wyniku przypadku użycia."""
    metrics = outcome.metrics
    covered = outcome.search_area_within_coverage
    partial = outcome.status in {"available", "no_match"} and covered is False
    plan = outcome.plan
    result_kwargs: dict[str, Any] = {}
    if metrics is not None:
        result_kwargs = {
            "parcel_area_sqm": metrics.parcel_area_sqm if metrics.parcel_area_sqm > 0 else None,
            "on_parcel": ParcelBuildingsResult(
                count=metrics.on_parcel.count,
                union_area_in_parcel_sqm=metrics.on_parcel.union_area_in_parcel_sqm,
                coverage_pct=min(metrics.on_parcel.coverage_pct, 100.0),
            ),
            "buffers": _buffer_results(metrics),
            "buildings": _building_items(metrics),
            "buildings_truncated": metrics.listed_truncated,
            "listed_radius_m": metrics.listed_radius_m,
            "candidates_considered": metrics.candidates_considered,
        }
    return NeighbourBuildingsResult(
        status=outcome.status,
        reason_code=outcome.reason_code,
        buffers_m=list(plan.buffers_m),
        search_area_within_coverage=covered,
        release=release_info(outcome.release),
        source=bdot_source_metadata(
            outcome.release,
            available=outcome.status in {"available", "no_match"},
            partial=partial,
            manual_review=outcome.status == "unavailable" or partial,
        ),
        warnings=list(outcome.warnings),
        **result_kwargs,
    )


def disabled_neighbour_buildings(buffers_m: list[float]) -> NeighbourBuildingsResult:
    return NeighbourBuildingsResult(
        status="unavailable",
        reason_code=REASON_FEATURE_DISABLED,
        buffers_m=buffers_m,
        source=bdot_source_metadata(None, available=False, partial=False, manual_review=True),
        warnings=["Kontekst zabudowy sąsiedniej jest wyłączony w konfiguracji — budynków nie sprawdzono."],
    )


def failed_neighbour_buildings(error_name: str, buffers_m: list[float]) -> NeighbourBuildingsResult:
    return NeighbourBuildingsResult(
        status="unavailable",
        reason_code="UNEXPECTED_ERROR",
        buffers_m=buffers_m,
        source=bdot_source_metadata(None, available=False, partial=False, manual_review=True),
        warnings=[f"Nieoczekiwany błąd kontekstu zabudowy ({error_name}) — statystyk nie policzono."],
    )


def neighbour_buildings_from_snapshot(raw: dict[str, Any] | None) -> NeighbourBuildingsResult:
    if raw is None:
        return NeighbourBuildingsResult(
            status="unknown", reason_code=REASON_NOT_RECORDED, warnings=[LEGACY_BUILDINGS_WARNING]
        )
    return NeighbourBuildingsResult.model_validate(raw)


# --- ostrzeżenia i źródła ------------------------------------------------------------------------------


def bdot_warnings(
    road: RoadContextResult | None, buildings: NeighbourBuildingsResult | None
) -> list[WarningMessage]:
    """Ostrzeżenia sekcji dla listy ostrzeżeń odpowiedzi (jeden wpis na komunikat, z kodem sekcji)."""
    warnings: list[WarningMessage] = []
    for code, section in (("ROAD_CONTEXT", road), ("NEIGHBOUR_BUILDINGS", buildings)):
        if section is None:
            continue
        severity = "info" if section.status in {"available", "no_match"} and section.search_area_within_coverage is not False else "warning"
        for message in section.warnings:
            warnings.append(
                WarningMessage(
                    code=f"{code}_{section.reason_code or 'WARNING'}",
                    message=message,
                    severity=severity,  # type: ignore[arg-type]
                    source_name=BDOT10K_SOURCE_ID,
                )
            )
    return warnings


def bdot_sources(
    road: RoadContextResult | None, buildings: NeighbourBuildingsResult | None
) -> list[SourceMetadata]:
    return [item.source for item in (road, buildings) if item is not None and item.source is not None]
