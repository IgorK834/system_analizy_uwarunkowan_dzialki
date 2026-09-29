"""Root kompozycji modułu ``analysis`` — wiązanie adapterów rastra NMT (BK-302).

Plik świadomie leży poza warstwami domain/application/infrastructure/api. Łączy
przypadek użycia ``analyze_parcel_relief`` z adapterem WCS (klient OGC BK-102 i
dekoder GDAL udostępnione przez root kompozycji modułu ``imports``). Endpoint i
identyfikator pokrycia pochodzą z katalogu źródeł — adapter nie uruchomi się bez
potwierdzonego kontraktu ``nmt_wcs``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from shapely.geometry.base import BaseGeometry

from app.core.data_sources import (
    AccessType,
    CatalogError,
    DataSourceCatalog,
    ensure_source_runnable,
)
from app.core.settings import settings
from app.modules.analysis.application.terrain import (
    REASON_SERVICE_TIMEOUT,
    REASON_SOURCE_NOT_RUNNABLE,
    REASON_UNEXPECTED_ERROR,
    ReliefLimits,
    ReliefOutcome,
    analyze_parcel_relief,
)
from app.modules.analysis.infrastructure.terrain_raster import (
    WCS_SOURCE_ID,
    ShapelyParcelFootprint,
    WcsCoverageContract,
    WcsElevationRasterSource,
)
from app.modules.imports.composition import (
    GdalRasterProcessor,
    OgcClient,
    build_ogc_client,
    build_raster_decoder,
)
from app.shared.provenance import Provenance

logger = logging.getLogger(__name__)

_ELEVATION_ROLE = "elevation"
# Margines ponad limit klienta OGC i GDAL na koszt obliczeń w wątku.
_COMPUTE_MARGIN_SECONDS = 15.0


async def analyze_terrain_relief(parcel_geometry: BaseGeometry) -> ReliefOutcome:
    """Asynchroniczna fasada: blokujące IO i GDAL biegną w wątku z limitem czasu."""
    timeout = settings.terrain_raster_timeout_seconds * 2 + _COMPUTE_MARGIN_SECONDS
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(run_terrain_relief, parcel_geometry), timeout=timeout
        )
    except TimeoutError:
        logger.warning("Analiza rastra NMT przekroczyła limit %.0fs.", timeout)
        return ReliefOutcome(
            status="unavailable",
            reason_code=REASON_SERVICE_TIMEOUT,
            provenance=_attempt_provenance("timeout"),
            warnings=(
                "Analiza rastra NMT przekroczyła limit czasu — spadku i ekspozycji "
                "nie policzono.",
            ),
        )


def run_terrain_relief(
    parcel_geometry: BaseGeometry,
    *,
    catalog: DataSourceCatalog | None = None,
    client: OgcClient | None = None,
    decoder: GdalRasterProcessor | None = None,
) -> ReliefOutcome:
    """Synchroniczne uruchomienie BK-302; zależności wstrzykiwalne w testach."""
    try:
        entry = ensure_source_runnable(WCS_SOURCE_ID, catalog)
        resource = next(
            item
            for item in entry.resources
            if item.role == _ELEVATION_ROLE and item.access_type is AccessType.WCS
        )
        coverage_id = resource.layer or (entry.layers or [""])[0]
        if not coverage_id:
            raise CatalogError("Katalog nie deklaruje identyfikatora pokrycia WCS.")
    except (CatalogError, StopIteration) as exc:
        logger.warning("Źródło %s nie jest uruchamialne: %s", WCS_SOURCE_ID, exc)
        return ReliefOutcome(
            status="unavailable",
            reason_code=REASON_SOURCE_NOT_RUNNABLE,
            provenance=_attempt_provenance("catalog"),
            warnings=(
                "Kontrakt WCS NMT nie jest potwierdzony w katalogu źródeł — "
                "spadku i ekspozycji nie policzono.",
            ),
        )

    contract = WcsCoverageContract(
        service_url=resource.url,
        coverage_id=coverage_id,
        vertical_datum=_vertical_datum(coverage_id),
    )
    owned_client = client is None
    active_client = client or build_ogc_client(
        source_id=WCS_SOURCE_ID,
        urls=[resource.url],
        total_timeout_seconds=settings.terrain_raster_timeout_seconds,
        max_response_bytes=settings.terrain_raster_max_bytes,
    )
    try:
        source = WcsElevationRasterSource(
            contract,
            active_client,
            decoder or build_raster_decoder(settings.terrain_raster_timeout_seconds),
            max_bytes=settings.terrain_raster_max_bytes,
        )
        return analyze_parcel_relief(
            ShapelyParcelFootprint(parcel_geometry),
            source,
            ReliefLimits(max_pixels=settings.terrain_raster_max_pixels),
        )
    except Exception as exc:  # noqa: BLE001 - granica sekcji informacyjnej
        logger.exception("Nieoczekiwany błąd analizy rastra NMT")
        return ReliefOutcome(
            status="unavailable",
            reason_code=REASON_UNEXPECTED_ERROR,
            provenance=_attempt_provenance(type(exc).__name__),
            warnings=(
                "Wystąpił nieoczekiwany błąd analizy rastra NMT — spadku i "
                "ekspozycji nie policzono.",
            ),
        )
    finally:
        if owned_client:
            active_client.close()


def _vertical_datum(coverage_id: str) -> str | None:
    for datum in ("PL-KRON86-NH", "PL-EVRF2007-NH"):
        if datum in coverage_id:
            return datum
    return None


def _attempt_provenance(error_code: str) -> Provenance:
    return Provenance(
        source_id=WCS_SOURCE_ID,
        fetched_at=datetime.now(timezone.utc),
        operation="WCS:GetCoverage",
        complete=False,
        error_code=error_code,
    )
