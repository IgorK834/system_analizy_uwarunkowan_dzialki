"""Przypadki użycia georeferencji rastra planistycznego i jego COG.

Pipeline: oryginał (PDF/GeoTIFF) → render strony/obrazu → transformacja GDAL do
EPSG:2180 z punktów kontrolnych → walidacja COG → zapis artefaktów i rekordu
``raster_assets`` ze statusem ``unreviewed``. Akceptacja (``verified``) jest
ZAWSZE ręczna i przechodzi przez ``accept_raster_asset`` z audytem ManualReview.

Reguła bezpieczeństwa domenowa: import sam z siebie nigdy nie oznacza rastra jako
zweryfikowanego, więc raster nie może automatycznie udawać wektorowej granicy
strefy ani trafić do warstwy mapy bez decyzji operatora.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol, Sequence

from app.modules.imports.application.common import (
    ImportRelease,
    ImportSourceNotRunnable,
    SourceBatch,
    artifact_sha256,
)
from app.modules.imports.domain.raster import (
    DEFAULT_MAX_RMSE_M,
    ControlPoint,
    GeoreferenceError,
    GeoreferenceResult,
    bounds_polygon,
    fit_georeference,
    quality_report,
)
from app.shared.crs import CANONICAL_CRS
from app.shared.geometry import GeometryPayload


class RasterReviewError(RuntimeError):
    """Nieprawidłowa próba zmiany statusu weryfikacji rastra."""


@dataclass(frozen=True)
class RasterSource(SourceBatch):
    """Oryginalny raster do georeferencji (PDF lub obraz/GeoTIFF)."""

    page_number: int = 0
    source_crs: str | None = None


@dataclass(frozen=True)
class RenderedRaster:
    """Wyekstrahowany obraz źródłowy gotowy do georeferencji."""

    image: bytes
    media_type: str
    width_px: int
    height_px: int


@dataclass(frozen=True)
class CogValidation:
    """Wynik strukturalnej walidacji Cloud Optimized GeoTIFF."""

    valid: bool
    messages: tuple[str, ...] = ()


@dataclass(frozen=True)
class RasterImportOutcome:
    status: str
    raster_asset_id: int | None = None
    rmse_m: float | None = None
    review_status: str | None = None
    qa_report: Mapping[str, object] | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class RasterReviewOutcome:
    raster_asset_id: int
    review_status: str
    manual_review_id: int


class RasterProcessor(Protocol):
    """Adapter operacji rastrowych (render PDF/obrazu, budowa i walidacja COG)."""

    def render(self, source: RasterSource) -> RenderedRaster: ...

    def build_cog(
        self,
        image: bytes,
        *,
        control_points: Sequence[ControlPoint],
        dst_crs: str,
        transform_method: str,
    ) -> bytes: ...

    def validate_cog(self, cog: bytes) -> CogValidation: ...


class RasterImportRepository(Protocol):
    def save_artifact(
        self, *, source_id: str, filename: str, content: bytes, media_type: str
    ) -> int: ...

    def persist_raster_asset(
        self,
        *,
        planning_act_version_id: int | None,
        source_artifact_id: int,
        cog_artifact_id: int,
        transform_method: str,
        control_points: Sequence[ControlPoint],
        georeference: GeoreferenceResult,
        bounds: GeometryPayload,
        width_px: int,
        height_px: int,
        nodata: float | None,
        qa_report: Mapping[str, object],
    ) -> int: ...


class RasterReviewRepository(Protocol):
    def review_status_of(self, raster_asset_id: int) -> str | None: ...

    def apply_review_decision(
        self,
        *,
        raster_asset_id: int,
        review_status: str,
        operator_id: str,
        reason: str,
    ) -> int: ...


def _control_points(raw: Sequence[Mapping[str, float]]) -> tuple[ControlPoint, ...]:
    points: list[ControlPoint] = []
    for item in raw:
        points.append(
            ControlPoint(
                pixel_col=float(item["pixel_col"]),
                pixel_row=float(item["pixel_row"]),
                map_x=float(item["map_x"]),
                map_y=float(item["map_y"]),
            )
        )
    return tuple(points)


def run_raster_import(
    source: RasterSource,
    control_points: Sequence[Mapping[str, float]],
    processor: RasterProcessor,
    db: RasterImportRepository,
    *,
    release: ImportRelease,
    planning_act_version_id: int | None = None,
    transform_method: str = "gcp_affine",
    nodata: float | None = None,
    max_rmse_m: float = DEFAULT_MAX_RMSE_M,
) -> RasterImportOutcome:
    """Georeferencjuje raster do COG i zapisuje rekord wymagający ręcznej akceptacji."""
    if not release.publication_allowed and not release.dry_run:
        raise ImportSourceNotRunnable(
            "DRY-RUN ONLY: źródło rastra nie jest production_ready."
        )

    points = _control_points(control_points)
    try:
        georeference = fit_georeference(points)
    except GeoreferenceError as exc:
        # Zbyt mało/współliniowe punkty kontrolne → kontrolowane odrzucenie.
        return RasterImportOutcome(
            status="georeference_rejected", warnings=(str(exc),)
        )

    rendered = processor.render(source)
    bounds = bounds_polygon(
        georeference.transform, rendered.width_px, rendered.height_px
    )
    report = quality_report(
        georeference,
        width_px=rendered.width_px,
        height_px=rendered.height_px,
        max_rmse_m=max_rmse_m,
    )

    cog = processor.build_cog(
        rendered.image,
        control_points=points,
        dst_crs=CANONICAL_CRS,
        transform_method=transform_method,
    )
    validation = processor.validate_cog(cog)
    if not validation.valid:
        return RasterImportOutcome(
            status="cog_invalid",
            rmse_m=georeference.rmse_m,
            qa_report=report,
            warnings=validation.messages,
        )

    warnings: list[str] = []
    if not georeference.is_within_tolerance(max_rmse_m):
        warnings.append(
            f"rmse_above_threshold:{georeference.rmse_m:.3f}>{max_rmse_m}"
        )

    if release.dry_run or not release.publication_allowed:
        return RasterImportOutcome(
            status="dry_run_only",
            rmse_m=georeference.rmse_m,
            qa_report=report,
            warnings=tuple(warnings),
        )

    source_artifact_id = db.save_artifact(
        source_id=release.source_id,
        filename=source.filename,
        content=source.content,
        media_type=source.media_type,
    )
    cog_artifact_id = db.save_artifact(
        source_id=release.source_id,
        filename=f"{artifact_sha256(cog)[:12]}.tif",
        content=cog,
        media_type="image/tiff; application=geotiff; profile=cloud-optimized",
    )
    raster_asset_id = db.persist_raster_asset(
        planning_act_version_id=planning_act_version_id,
        source_artifact_id=source_artifact_id,
        cog_artifact_id=cog_artifact_id,
        transform_method=transform_method,
        control_points=points,
        georeference=georeference,
        bounds=bounds,
        width_px=rendered.width_px,
        height_px=rendered.height_px,
        nodata=nodata,
        qa_report=report,
    )
    return RasterImportOutcome(
        status="succeeded",
        raster_asset_id=raster_asset_id,
        rmse_m=georeference.rmse_m,
        review_status="unreviewed",
        qa_report=report,
        warnings=tuple(warnings),
    )


def accept_raster_asset(
    db: RasterReviewRepository,
    *,
    raster_asset_id: int,
    operator_id: str,
    reason: str,
) -> RasterReviewOutcome:
    """Ręcznie akceptuje raster (``verified``) z audytem ManualReview.

    Akceptacja jest jedynym sposobem, aby raster mógł zostać podany do warstwy
    mapy — nigdy nie dzieje się to automatycznie w imporcie.
    """
    return _apply_decision(
        db,
        raster_asset_id=raster_asset_id,
        review_status="verified",
        operator_id=operator_id,
        reason=reason,
    )


def reject_raster_asset(
    db: RasterReviewRepository,
    *,
    raster_asset_id: int,
    operator_id: str,
    reason: str,
) -> RasterReviewOutcome:
    """Ręcznie odrzuca raster (``rejected``) z audytem ManualReview."""
    return _apply_decision(
        db,
        raster_asset_id=raster_asset_id,
        review_status="rejected",
        operator_id=operator_id,
        reason=reason,
    )


def _apply_decision(
    db: RasterReviewRepository,
    *,
    raster_asset_id: int,
    review_status: str,
    operator_id: str,
    reason: str,
) -> RasterReviewOutcome:
    if not operator_id or not operator_id.strip():
        raise RasterReviewError("Decyzja weryfikacyjna wymaga operator_id.")
    if not reason or not reason.strip():
        raise RasterReviewError("Decyzja weryfikacyjna wymaga uzasadnienia (reason).")
    if db.review_status_of(raster_asset_id) is None:
        raise RasterReviewError(f"Raster {raster_asset_id} nie istnieje.")
    manual_review_id = db.apply_review_decision(
        raster_asset_id=raster_asset_id,
        review_status=review_status,
        operator_id=operator_id,
        reason=reason,
    )
    return RasterReviewOutcome(
        raster_asset_id=raster_asset_id,
        review_status=review_status,
        manual_review_id=manual_review_id,
    )


def is_raster_servable(review_status: str | None) -> bool:
    """Twardy warunek: raster wolno serwować/analizować tylko po weryfikacji."""
    return review_status == "verified"
