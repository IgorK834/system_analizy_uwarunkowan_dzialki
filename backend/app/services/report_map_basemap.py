"""Podkład map raportu wyłącznie z zapisanego, dozwolonego artefaktu (BK-503).

Generowanie PDF nigdy nie pobiera WMS/OSM/KIUT — moduł nie importuje klienta
HTTP. Podkładem może być tylko lokalny artefakt PNG w ``EPSG:2180`` opisany
plikiem metadanych ``<nazwa>.json``::

    {"file": "orto.png", "sha256": "…", "crs": "EPSG:2180",
     "bbox": [min_x, min_y, max_x, max_y], "license": "…",
     "attribution": "…", "allowed_for_report": true}

Przy zamrażaniu mapy (zapis analizy) wybierany jest artefakt obejmujący cały
kadr; do snapshotu trafia jego SHA-256, zasięg i licencja. Przy renderowaniu
plik jest ładowany po SHA-256 — brak pliku, inna treść albo brak zgody daje
neutralne tło i jawną adnotację, nigdy błąd raportu.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from PIL import Image

logger = logging.getLogger(__name__)

BASEMAP_UNAVAILABLE_NOTE: str = (
    "Zapisany artefakt podkładu jest niedostępny albo jego SHA-256 nie zgadza "
    "się ze snapshotem — mapę narysowano na neutralnym tle."
)
_MAX_ARTIFACT_BYTES = 32 * 1024 * 1024


def _read_metadata(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        logger.warning("report_basemap_metadata_invalid path=%s", path.name)
        return None
    if not isinstance(data, dict):
        return None
    bbox = data.get("bbox")
    if (
        data.get("crs") != "EPSG:2180"
        or data.get("allowed_for_report") is not True
        or not isinstance(data.get("sha256"), str)
        or not isinstance(data.get("file"), str)
        or not isinstance(bbox, list)
        or len(bbox) != 4
        or not all(isinstance(item, (int, float)) for item in bbox)
    ):
        return None
    return data


def select_basemap_artifact(
    frame: Mapping[str, float], directory: str | None
) -> dict[str, Any]:
    """Referencja podkładu obejmującego kadr albo tryb neutralny.

    Wybór jest deterministyczny: spośród dozwolonych artefaktów, których
    zasięg zawiera cały kadr, wygrywa najmniejszy SHA-256.
    """
    if not directory:
        return {"mode": "neutral"}
    root = Path(directory)
    if not root.is_dir():
        return {"mode": "neutral"}
    candidates: list[dict[str, Any]] = []
    for meta_path in sorted(root.glob("*.json")):
        data = _read_metadata(meta_path)
        if data is None:
            continue
        min_x, min_y, max_x, max_y = (float(item) for item in data["bbox"])
        if (
            min_x <= frame["min_x"]
            and min_y <= frame["min_y"]
            and max_x >= frame["max_x"]
            and max_y >= frame["max_y"]
        ):
            candidates.append(data)
    if not candidates:
        return {"mode": "neutral"}
    chosen = min(candidates, key=lambda item: item["sha256"])
    return {
        "mode": "artifact",
        "sha256": chosen["sha256"],
        "bbox": [float(item) for item in chosen["bbox"]],
        "license": str(chosen.get("license") or ""),
        "attribution": str(chosen.get("attribution") or ""),
    }


def _find_artifact_bytes(sha256: str, directory: str) -> bytes | None:
    root = Path(directory)
    if not root.is_dir():
        return None
    for meta_path in sorted(root.glob("*.json")):
        data = _read_metadata(meta_path)
        if data is None or data["sha256"] != sha256:
            continue
        file_path = (root / data["file"]).resolve()
        if root.resolve() not in file_path.parents or not file_path.is_file():
            continue
        if file_path.stat().st_size > _MAX_ARTIFACT_BYTES:
            continue
        content = file_path.read_bytes()
        if hashlib.sha256(content).hexdigest() == sha256:
            return content
        logger.warning("report_basemap_sha_mismatch file=%s", file_path.name)
    return None


def load_basemap_image(
    reference: Mapping[str, Any] | None,
    frame: Mapping[str, float],
    directory: str | None,
) -> tuple[Image.Image | None, str | None]:
    """Wycina kadr z zapisanego podkładu; (obraz, adnotacja) — bez sieci."""
    if not reference or reference.get("mode") != "artifact":
        return None, None
    if not directory:
        return None, BASEMAP_UNAVAILABLE_NOTE
    content = _find_artifact_bytes(str(reference.get("sha256")), directory)
    if content is None:
        return None, BASEMAP_UNAVAILABLE_NOTE
    try:
        image = Image.open(io.BytesIO(content)).convert("RGBA")
    except OSError:
        return None, BASEMAP_UNAVAILABLE_NOTE
    min_x, min_y, max_x, max_y = (float(item) for item in reference["bbox"])
    span_x = max(max_x - min_x, 1e-9)
    span_y = max(max_y - min_y, 1e-9)
    left = (frame["min_x"] - min_x) / span_x * image.width
    right = (frame["max_x"] - min_x) / span_x * image.width
    top = (max_y - frame["max_y"]) / span_y * image.height
    bottom = (max_y - frame["min_y"]) / span_y * image.height
    crop = image.crop((round(left), round(top), round(right), round(bottom)))
    size = (int(frame["width_px"]), int(frame["height_px"]))
    return crop.resize(size, Image.Resampling.BILINEAR), None
