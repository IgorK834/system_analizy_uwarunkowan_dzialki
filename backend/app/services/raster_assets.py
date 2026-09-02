"""Warstwa serwująca rastry planistyczne — twardy warunek akceptacji.

Ten moduł jest jedynym punktem, przez który wynikowy COG rastra jest udostępniany
do warstwy mapy. Wymusza regułę: raster o ``review_status`` innym niż
``verified`` NIGDY nie jest serwowany ani używany jako źródło precyzyjnych
przecięć.

DECYZJA (gap): nie rozszerzamy ``app/services/wms_tiles.py``. ``WmsTileProxy`` jest
proxy do zewnętrznego WMS (KIMPZP) z cache kafelków upstreamu — inny model niż
serwowanie lokalnego, zaakceptowanego pliku COG. Dedykowany, wąski guard jest
czytelniejszy i nie miesza dwóch odpowiedzialności. Renderowanie kafelków XYZ z
COG (gdy zajdzie potrzeba) może później ponownie użyć wzorca cache z WmsTileProxy.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.versioned import RasterAsset, SourceArtifact
from app.modules.imports.application.raster_import import is_raster_servable


class RasterNotServableError(RuntimeError):
    """Raster nie może zostać udostępniony (brak lub odmowa ręcznej akceptacji)."""


def get_servable_cog_uri(session: Session, raster_asset_id: int) -> str:
    """Zwraca URI zaakceptowanego COG albo podnosi ``RasterNotServableError``.

    Twardy warunek: dostęp do pliku COG jest możliwy WYŁĄCZNIE dla rastra o
    statusie ``verified``. Dzięki temu raster bez ręcznej akceptacji nie może
    trafić do warstwy mapy nawet, jeśli istnieje w bazie.
    """
    asset = session.get(RasterAsset, raster_asset_id)
    if asset is None:
        raise RasterNotServableError(f"Raster {raster_asset_id} nie istnieje.")
    if not is_raster_servable(asset.review_status):
        raise RasterNotServableError(
            f"Raster {raster_asset_id} ma status {asset.review_status!r} — "
            "wymagana jest ręczna akceptacja (verified) przed publikacją do mapy."
        )
    artifact = session.get(SourceArtifact, asset.cog_artifact_id)
    if artifact is None:
        raise RasterNotServableError(
            f"Brak artefaktu COG dla rastra {raster_asset_id}."
        )
    return artifact.uri


def list_servable_rasters(session: Session) -> list[dict[str, object]]:
    """Zwraca listę zaakceptowanych rastrów gotowych do serwowania na mapie."""
    assets = (
        session.query(
            RasterAsset.id,
            RasterAsset.planning_act_version_id,
            RasterAsset.cog_artifact_id,
            RasterAsset.rmse_m,
            RasterAsset.review_status,
        )
        .filter(RasterAsset.review_status == "verified")
        .all()
    )
    return [
        {
            "id": row.id,
            "planning_act_version_id": row.planning_act_version_id,
            "cog_artifact_id": row.cog_artifact_id,
            "rmse_m": row.rmse_m,
            "review_status": row.review_status,
        }
        for row in assets
    ]
