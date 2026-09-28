"""Mapowanie pomiarów NMT na sekcję ``terrain`` wyniku analizy (BK-301/BK-302).

Sekcja ma cztery rozłączne zachowania, których nie wolno pomylić:

* ``available`` — zmierzone Hmin/Hmax; deniwelacja 0 m to realny płaski teren;
* ``no_coverage`` — usługa potwierdziła brak danych (sentinel ``Hmin=2500``);
* ``unavailable`` — timeout, błąd HTTP lub błąd zgłoszony w treści odpowiedzi;
* ``unknown`` — snapshot zapisany przed BK-301, bez informacji o NMT.

Poza ``unknown`` wynik zawsze niesie provenance zapytania, także pusty.
"""

from __future__ import annotations

from typing import Any, Final

from shapely.geometry import LineString

from app.core.settings import settings
from app.modules.analysis.application.terrain import ReliefOutcome
from app.modules.analysis.domain.terrain import (
    ALGORITHM_VERSION,
    SLOPE_CLASSES_VERSION,
)
from app.schemas.analyze import (
    TerrainAspectResult,
    TerrainProfileResult,
    TerrainProfileSample,
    TerrainRasterMetadata,
    TerrainReliefResult,
    TerrainResult,
    TerrainSlopeClass,
    TerrainSlopeStatistics,
)
from app.schemas.source import SourceMetadata, WarningMessage
from app.services.context import ContextSectionResult
from app.services.geojson import analysis_layer_geometry_to_geojson
from app.services.nmt import (
    NMT_SOURCE_ID,
    NMT_SOURCE_NAME,
    REASON_NO_COVERAGE,
    TerrainExtremes,
    TerrainNoCoverage,
)

REASON_UNEXPECTED_ERROR: Final[str] = "UNEXPECTED_ERROR"
REASON_LEGACY_SNAPSHOT: Final[str] = "LEGACY_SNAPSHOT"
REASON_MISSING_MEASUREMENT: Final[str] = "MISSING_MEASUREMENT"
RELIEF_SOURCE_NAME: Final[str] = "NMT_WCS"
# Rozbieżność Hmin/Hmax między usługą GetMinMaxByPolygon (siatka 4 m) a
# rastrem WCS (1 m), powyżej której wynik wymaga weryfikacji.
SOURCE_CONSISTENCY_TOLERANCE_M: Final[float] = 1.0
_PARTIAL_RELIEF_CONFIDENCE: Final[float] = 0.6
_RELIEF_CONFIDENCE: Final[float] = 0.9

LEGACY_TERRAIN_WARNING: Final[str] = (
    "Snapshot zapisano przed udostępnieniem sekcji rzeźby terenu (BK-301) — "
    "brak informacji o NMT; nie oznacza to płaskiego terenu."
)


def build_terrain_result(
    section: ContextSectionResult,
    relief: ReliefOutcome | None,
) -> TerrainResult:
    """Buduje sekcję ``terrain`` z wyniku sekcji NMT i opcjonalnych pochodnych."""
    relief_result = relief_result_from_outcome(relief) if relief is not None else None
    measurement = section.data[0] if section.status == "available" and section.data else None

    if isinstance(measurement, TerrainExtremes):
        terrain = TerrainResult(
            status="available",
            min_height_m=measurement.min_height_m,
            max_height_m=measurement.max_height_m,
            height_difference_m=measurement.height_difference_m,
            grid_size_m=measurement.grid_size_m,
            sampled_points=measurement.sampled_points,
            source=measurement.source_metadata,
            warnings=list(measurement.warnings),
            relief=relief_result,
        )
        return _with_consistency_check(terrain)

    if isinstance(measurement, TerrainNoCoverage):
        return TerrainResult(
            status="no_coverage",
            reason_code=REASON_NO_COVERAGE,
            grid_size_m=measurement.grid_size_m,
            sampled_points=measurement.sampled_points,
            source=measurement.source_metadata,
            warnings=list(measurement.warnings),
            relief=relief_result,
        )

    if section.status == "available":
        reason = REASON_MISSING_MEASUREMENT
    elif section.status == "error":
        reason = REASON_UNEXPECTED_ERROR
    else:
        reason = section.reason_code or REASON_UNEXPECTED_ERROR
    return TerrainResult(
        status="unavailable",
        reason_code=reason,
        source=section.source_metadata or _attempt_source(),
        warnings=list(section.warnings),
        relief=relief_result,
    )


def relief_result_from_outcome(outcome: ReliefOutcome) -> TerrainReliefResult:
    """Mapuje wynik przypadku użycia BK-302 na kontrakt API."""
    derivatives = outcome.derivatives if outcome.status == "available" else None
    raster = outcome.raster
    return TerrainReliefResult(
        algorithm_version=ALGORITHM_VERSION,
        slope_classes_version=SLOPE_CLASSES_VERSION,
        status=outcome.status,  # type: ignore[arg-type]
        reason_code=outcome.reason_code,
        resolution_m=raster.resolution_m if raster is not None else None,
        parcel_pixel_count=derivatives.parcel_pixel_count if derivatives else None,
        valid_pixel_count=derivatives.valid_pixel_count if derivatives else None,
        nodata_pixel_count=derivatives.nodata_pixel_count if derivatives else None,
        valid_area_share_pct=derivatives.valid_area_share_pct if derivatives else None,
        min_height_m=derivatives.min_height_m if derivatives else None,
        max_height_m=derivatives.max_height_m if derivatives else None,
        mean_height_m=derivatives.mean_height_m if derivatives else None,
        slope=(
            TerrainSlopeStatistics(**derivatives.slope.__dict__)
            if derivatives and derivatives.slope
            else None
        ),
        slope_classes=[
            TerrainSlopeClass(**item.__dict__)
            for item in (derivatives.slope_classes if derivatives else ())
        ],
        aspect=(
            TerrainAspectResult(**derivatives.aspect.__dict__)
            if derivatives and derivatives.aspect
            else None
        ),
        profile=_profile_result(outcome) if derivatives else None,
        raster=(
            TerrainRasterMetadata(
                coverage_id=raster.coverage_id,
                resolution_m=raster.resolution_m,
                width_px=raster.width_px,
                height_px=raster.height_px,
                bbox=raster.bbox.as_tuple(),
                buffer_m=raster.buffer_m,
                size_bytes=raster.size_bytes,
                nodata_value=raster.nodata_value,
                nodata_policy=raster.nodata_policy,
                vertical_datum=raster.vertical_datum,
                gdal_version=raster.gdal_version,
            )
            if raster is not None
            else None
        ),
        source=_relief_source(outcome),
        warnings=list(outcome.warnings),
    )


def terrain_from_snapshot(raw: dict[str, Any] | None) -> TerrainResult:
    """Odczyt historyczny: brak zapisu to ``unknown``, nigdy fikcyjne 0 m."""
    if raw is None:
        return TerrainResult(
            status="unknown",
            reason_code=REASON_LEGACY_SNAPSHOT,
            warnings=[LEGACY_TERRAIN_WARNING],
        )
    return TerrainResult.model_validate(raw)


def terrain_warnings(terrain: TerrainResult) -> list[WarningMessage]:
    """Ostrzeżenia pochodnych rastra dla listy ostrzeżeń odpowiedzi.

    Ostrzeżenia sekcji NMT (Hmin/Hmax) trafiają do listy już przez mapowanie
    kontekstu, więc nie są tu powielane.
    """
    relief = terrain.relief
    if relief is None:
        return []
    severity = "warning" if relief.status == "available" else "info"
    return [
        WarningMessage(
            code="NMT_RELIEF_WARNING",
            message=message,
            severity=severity,  # type: ignore[arg-type]
            source_name="nmt_wcs",
        )
        for message in relief.warnings
    ]


def terrain_sources(terrain: TerrainResult) -> list[SourceMetadata]:
    """Źródła sekcji NMT (w tym nieudanych prób) do rejestru źródeł wyniku."""
    sources = [terrain.source] if terrain.source is not None else []
    if terrain.relief is not None and terrain.relief.source is not None:
        sources.append(terrain.relief.source)
    return sources


def _with_consistency_check(terrain: TerrainResult) -> TerrainResult:
    relief = terrain.relief
    if (
        relief is None
        or relief.status != "available"
        or relief.min_height_m is None
        or relief.max_height_m is None
        or terrain.min_height_m is None
        or terrain.max_height_m is None
    ):
        return terrain
    min_gap = abs(relief.min_height_m - terrain.min_height_m)
    max_gap = abs(relief.max_height_m - terrain.max_height_m)
    if max(min_gap, max_gap) <= SOURCE_CONSISTENCY_TOLERANCE_M:
        return terrain
    message = (
        f"Wysokości z rastra WCS 1 m ({_pl(relief.min_height_m)}–"
        f"{_pl(relief.max_height_m)} m) różnią się od usługi GetMinMaxByPolygon "
        f"({_pl(terrain.min_height_m)}–{_pl(terrain.max_height_m)} m) o więcej niż "
        f"{SOURCE_CONSISTENCY_TOLERANCE_M:.0f} m. Źródła mają inną siatkę i mogą "
        "pochodzić z różnych aktualizacji NMT — wynik wymaga weryfikacji."
    )
    return terrain.model_copy(
        update={
            "relief": relief.model_copy(
                update={"warnings": [*relief.warnings, message]}
            )
        }
    )


def _pl(value: float) -> str:
    return f"{value:.2f}".replace(".", ",")


def _profile_result(outcome: ReliefOutcome) -> TerrainProfileResult | None:
    profile = outcome.profile
    if profile is None:
        return None
    return TerrainProfileResult(
        method=profile.method,
        start=profile.start,
        end=profile.end,
        length_m=profile.length_m,
        step_m=profile.step_m,
        samples=[TerrainProfileSample(**sample.__dict__) for sample in profile.samples],
        line_geojson=analysis_layer_geometry_to_geojson(
            LineString([profile.start, profile.end]),
            "terrain_profile",
            {"length_m": profile.length_m, "step_m": profile.step_m},
        ),
    )


def _relief_source(outcome: ReliefOutcome) -> SourceMetadata | None:
    provenance = outcome.provenance
    if provenance is None:
        return None
    measured = outcome.status == "available"
    derivatives = outcome.derivatives
    partial = (
        measured
        and derivatives is not None
        and (derivatives.nodata_pixel_count > 0 or derivatives.valid_area_share_pct < 100.0)
    )
    content_hash = provenance.content_hash
    return SourceMetadata(
        source_id=provenance.source_id,
        source_name=RELIEF_SOURCE_NAME,
        source_version=(
            f"{outcome.raster.coverage_id} (WCS 2.0.1)" if outcome.raster else None
        ),
        artifact_sha256=content_hash if content_hash and len(content_hash) == 64 else None,
        source_url=provenance.request_url,
        fetched_at=provenance.fetched_at,
        response_status=200 if outcome.raster is not None else None,
        confidence=(
            (_PARTIAL_RELIEF_CONFIDENCE if partial else _RELIEF_CONFIDENCE)
            if measured or outcome.status == "no_coverage"
            else 0.0
        ),
        manual_review_required=outcome.status == "unavailable" or partial,
    )


def _attempt_source() -> SourceMetadata:
    """Provenance próby, gdy wyjątek sekcji nie przyniósł własnych metadanych."""
    return SourceMetadata(
        source_id=NMT_SOURCE_ID,
        source_name=NMT_SOURCE_NAME,
        source_url=settings.nmt_base_url,
        fetched_at=None,
        confidence=0.0,
        manual_review_required=True,
    )
