"""Wspólny odczyt wektorów przez pyogrio bez zależności od GeoPandas."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pyproj import CRS
from pyproj.exceptions import CRSError
from shapely import from_wkb

from app.shared.geometry import GeometryPayload


class VectorReadError(RuntimeError):
    """OGR nie rozpoznał warstwy, geometrii albo CRS."""


@dataclass(frozen=True)
class VectorFeature:
    attributes: dict[str, Any]
    geometry: GeometryPayload


def read_vector_features(
    path: str | Path,
    *,
    layer: str | int | None = None,
    declared_crs: str | None = None,
) -> tuple[VectorFeature, ...]:
    """Czyta warstwę do WKB/atrybutów przez niskopoziomowe API pyogrio."""
    try:
        from pyogrio import read_info
        from pyogrio.raw import read
    except ImportError as exc:  # pragma: no cover - błąd obrazu/deploymentu
        raise VectorReadError(
            "Brak pyogrio. Zbuduj ponownie obraz backendu (zależności: requirements.lock)."
        ) from exc

    try:
        info = read_info(path, layer=layer)
        metadata, _fids, geometries, field_data = read(
            path,
            layer=layer,
            force_2d=True,
        )
    except Exception as exc:
        raise VectorReadError(f"Nie udało się odczytać warstwy OGR: {exc}") from exc

    detected = info.get("crs") or metadata.get("crs")
    crs = _normalize_crs(detected)
    if not crs:
        raise VectorReadError(
            "Warstwa nie deklaruje rozpoznawalnego CRS; importer nie zgaduje układu."
        )
    if declared_crs and crs != declared_crs:
        raise VectorReadError(
            f"CRS warstwy {crs} jest inny niż kontrakt {declared_crs}."
        )

    fields = [str(item) for item in metadata.get("fields", info.get("fields", ()))]
    features: list[VectorFeature] = []
    for row_index, geometry_wkb in enumerate(geometries):
        if geometry_wkb is None:
            continue
        attributes = {
            field: _scalar(field_data[column_index][row_index])
            for column_index, field in enumerate(fields)
        }
        features.append(
            VectorFeature(
                attributes=attributes,
                geometry=GeometryPayload(from_wkb(geometry_wkb).wkt, crs=crs),
            )
        )
    return tuple(features)


def _normalize_crs(value: Any) -> str | None:
    if not value:
        return None
    try:
        epsg = CRS.from_user_input(value).to_epsg()
    except (CRSError, TypeError, ValueError):
        return None
    return f"EPSG:{epsg}" if epsg is not None else None


def _scalar(value: Any) -> Any:
    if value is None:
        return None
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return item()
        except (TypeError, ValueError):
            pass
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        try:
            return isoformat()
        except (TypeError, ValueError):
            pass
    return value
