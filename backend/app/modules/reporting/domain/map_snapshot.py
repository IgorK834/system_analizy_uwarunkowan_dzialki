"""Czysta logika deterministycznych map raportu (BK-503, ADR-010).

Moduł nie wykonuje IO: operuje na zamrożonej specyfikacji mapy zapisanej w
snapshocie analizy (``analyses.report_map_snapshot``). Specyfikacja zawiera
geometrie w ``EPSG:2180`` zaokrąglone do 1 cm, kadr, tryb tematyczny, kolejność
warstw, style i font — wszystko, czego renderer potrzebuje, aby narysować ten
sam obraz bez sięgania do bieżącego stanu źródeł ani bieżącej konfiguracji.

Hash semantyczny liczony jest z kanonicznego JSON danych i konfiguracji
renderowania. Nie zależy od wersji Pillow ani pliku fontu, dlatego identyczny
snapshot daje identyczny hash w każdym środowisku; bajtowy hash PNG jest
porównywalny wyłącznie w identycznym środowisku (patrz ``environment``).
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final

REPORT_MAP_SNAPSHOT_SCHEMA: Final[str] = "report-map-snapshot/1"
# Dokładność zamrożonych współrzędnych EPSG:2180 (metry). 1 cm jest poniżej
# rozdzielczości każdego źródła, więc zaokrąglenie nie zmienia znaczenia mapy,
# a usuwa szum zmiennoprzecinkowy z hasha semantycznego.
COORDINATE_DECIMALS: Final[int] = 2
# Pola snapshotu, które nie wchodzą do hasha semantycznego: sam hash i
# metadane zapisu (chwila zamrożenia nie zmienia znaczenia mapy).
_NON_SEMANTIC_KEYS: Final[frozenset[str]] = frozenset(
    {"semantic_sha256", "frozen_at", "frozen_by"}
)
# Szereg „ładnych” długości podziałki (1-2-5).
_SCALE_STEPS: Final[tuple[float, ...]] = (1.0, 2.0, 5.0)


class MapSnapshotError(ValueError):
    """Specyfikacja mapy narusza kontrakt ``report-map-snapshot/1``."""


def canonical_json(value: Any) -> str:
    """Kanoniczny JSON: posortowane klucze, bez spacji, UTF-8 bez escapowania."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def semantic_content(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Część snapshotu określająca znaczenie obrazu (dane + konfiguracja)."""
    return {
        key: value for key, value in snapshot.items() if key not in _NON_SEMANTIC_KEYS
    }


def semantic_hash(snapshot: Mapping[str, Any]) -> str:
    """SHA-256 kanonicznego JSON danych i konfiguracji renderowania."""
    payload = canonical_json(semantic_content(snapshot)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def verify_semantic_hash(snapshot: Mapping[str, Any]) -> bool:
    """Czy zapisany hash semantyczny odpowiada treści snapshotu."""
    stored = snapshot.get("semantic_sha256")
    return isinstance(stored, str) and stored == semantic_hash(snapshot)


def round_coordinates(value: Any, decimals: int = COORDINATE_DECIMALS) -> Any:
    """Zaokrągla zagnieżdżone listy współrzędnych GeoJSON (``-0.0`` → ``0.0``)."""
    if isinstance(value, (list, tuple)):
        return [round_coordinates(item, decimals) for item in value]
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        rounded = round(float(value), decimals)
        if not math.isfinite(rounded):
            raise MapSnapshotError("Współrzędna mapy musi być skończona.")
        return rounded + 0.0
    raise MapSnapshotError(f"Nieobsługiwana współrzędna mapy: {value!r}.")


def fit_frame(
    bounds: tuple[float, float, float, float],
    *,
    width_px: int,
    height_px: int,
    margin_ratio: float,
    min_span_m: float,
) -> dict[str, float]:
    """Kadr metryczny EPSG:2180 wypełniający płótno bez zniekształceń.

    BBOX działki jest poszerzany o ``margin_ratio`` z każdej strony, a potem
    krótszy wymiar jest dopełniany do proporcji płótna. Jeden piksel ma więc tę
    samą długość w terenie na obu osiach (``meters_per_pixel``).
    """
    if width_px <= 0 or height_px <= 0:
        raise MapSnapshotError("Wymiary płótna mapy muszą być dodatnie.")
    min_x, min_y, max_x, max_y = bounds
    if not all(math.isfinite(item) for item in bounds) or max_x < min_x or max_y < min_y:
        raise MapSnapshotError("Niepoprawny zasięg geometrii działki.")
    span_x = max(max_x - min_x, min_span_m)
    span_y = max(max_y - min_y, min_span_m)
    span_x += 2 * span_x * margin_ratio
    span_y += 2 * span_y * margin_ratio
    aspect = width_px / height_px
    if span_x / span_y >= aspect:
        span_y = span_x / aspect
    else:
        span_x = span_y * aspect
    center_x = (min_x + max_x) / 2.0
    center_y = (min_y + max_y) / 2.0
    meters_per_pixel = span_x / width_px
    return {
        "min_x": round(center_x - span_x / 2.0, COORDINATE_DECIMALS),
        "min_y": round(center_y - span_y / 2.0, COORDINATE_DECIMALS),
        "max_x": round(center_x + span_x / 2.0, COORDINATE_DECIMALS),
        "max_y": round(center_y + span_y / 2.0, COORDINATE_DECIMALS),
        "width_px": width_px,
        "height_px": height_px,
        "meters_per_pixel": round(meters_per_pixel, 6),
    }


def choose_scale_bar(meters_per_pixel: float, max_length_px: float) -> dict[str, float]:
    """Najdłuższa „ładna” podziałka (1-2-5 × 10ⁿ m) mieszcząca się w ``max_length_px``."""
    if meters_per_pixel <= 0 or max_length_px <= 0:
        raise MapSnapshotError("Skala mapy musi być dodatnia.")
    limit_m = meters_per_pixel * max_length_px
    exponent = math.floor(math.log10(limit_m))
    best = 10.0 ** exponent
    for power in (exponent - 1, exponent):
        for step in _SCALE_STEPS:
            candidate = step * 10.0 ** power
            if candidate <= limit_m + 1e-9:
                best = max(best, candidate)
    best = float(f"{best:.6g}")
    return {"length_m": best, "length_px": round(best / meters_per_pixel, 3)}


def to_pixel(frame: Mapping[str, float], x: float, y: float) -> tuple[float, float]:
    """Współrzędne EPSG:2180 → piksele kadru (oś Y w dół)."""
    mpp = frame["meters_per_pixel"]
    return ((x - frame["min_x"]) / mpp, (frame["max_y"] - y) / mpp)


def theme_class_for(value: float | None, classes: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """Klasa tematu POG dla wartości; ``None`` (brak wartości) nie jest klasą 0.

    Przedziały są lewostronnie domknięte ``[min, max)``, ostatni ma otwartą górę
    (``max=None``) — tak jak ``shared/pog-presentation.json`` i wyrażenia MapLibre.
    """
    if value is None or not math.isfinite(value):
        return None
    for item in classes:
        low = item.get("min")
        high = item.get("max")
        if (low is None or value >= low) and (high is None or value < high):
            return item
    return None


def geometry_is_empty(geometry: Mapping[str, Any] | None) -> bool:
    return not geometry or not geometry.get("coordinates")


def drawable_feature_count(layers: Iterable[Mapping[str, Any]]) -> int:
    return sum(
        1
        for layer in layers
        for feature in layer.get("features", [])
        if not geometry_is_empty(feature.get("geometry"))
    )


def validate_snapshot(snapshot: Mapping[str, Any]) -> None:
    """Minimalna walidacja kontraktu przed renderowaniem (bez IO)."""
    if snapshot.get("schema") != REPORT_MAP_SNAPSHOT_SCHEMA:
        raise MapSnapshotError(f"Nieobsługiwany schemat mapy: {snapshot.get('schema')!r}.")
    frame = snapshot.get("frame")
    if not isinstance(frame, Mapping) or frame.get("crs") != "EPSG:2180":
        raise MapSnapshotError("Kadr mapy musi być zapisany w EPSG:2180.")
    for key in ("min_x", "min_y", "max_x", "max_y", "meters_per_pixel", "width_px", "height_px"):
        if not isinstance(frame.get(key), (int, float)):
            raise MapSnapshotError(f"Kadr mapy nie ma pola {key}.")
    if not isinstance(snapshot.get("maps"), list):
        raise MapSnapshotError("Snapshot nie zawiera listy map.")
