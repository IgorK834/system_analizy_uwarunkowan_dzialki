"""Wspólne fixtures raportu v2 i porównania referencyjne map (BK-502/BK-503).

Fixtures wejściowe (``tests/fixtures/reports/*.json``) są zamrożonymi
odpowiedziami ``AnalyzeResponse`` z geometrią działki w ``EPSG:2180``.
Mapy referencyjne (``maps/*.png`` + ``maps/reference.json``) powstały w
przypiętym środowisku obrazu backendu; hash semantyczny musi się zgadzać
zawsze, a obraz — pikselowo w granicach tolerancji. Hash bajtowy PNG jest
porównywany tylko, gdy środowisko (Pillow + SHA-256 fontu) jest identyczne.
"""

from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops
from shapely import wkt

from app.schemas.analyze import AnalyzeResponse
from app.services.report_map import render_report_maps
from app.services.report_map_snapshot import build_report_map_snapshot
from app.services.section_quality import with_section_quality

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "reports"
MAPS_DIR = FIXTURES_DIR / "maps"
REFERENCE_FILE = MAPS_DIR / "reference.json"
FROZEN_AT = datetime(2026, 9, 20, 9, 31, tzinfo=timezone.utc)
# Maksymalny odsetek pikseli różniących się o więcej niż próg kanału — pokrywa
# drobne różnice rasteryzacji fontu między wersjami FreeType/Pillow.
MAX_DIFF_RATIO = 0.01
CHANNEL_TOLERANCE = 48

# (przypadek, fixture, tryb tematyczny POG, mapa)
REFERENCE_CASES: tuple[tuple[str, str, str, str], ...] = (
    ("parcel", "multizone", "zones", "parcel"),
    ("multizone_mpzp", "multizone", "zones", "mpzp"),
    ("multizone_pog", "multizone", "zones", "pog"),
    ("project_pog", "project", "zones", "pog"),
    ("risk_environment", "multizone", "zones", "environment"),
    ("null_height_pog", "multizone", "height", "pog"),
)


def load_fixture(name: str) -> tuple[AnalyzeResponse, Any]:
    payload = json.loads((FIXTURES_DIR / f"{name}.json").read_text("utf-8"))
    # Fixture odpowiada analizie zapisanej po BK-504: macierz jakości jest oceną
    # z chwili analizy (punkt odniesienia = analyzed_at fixture), nie z dnia testu.
    response = with_section_quality(AnalyzeResponse.model_validate(payload["response"]))
    return response, wkt.loads(payload["parcel_wkt_2180"])


def reference_snapshot(fixture: str, theme: str) -> dict[str, Any]:
    response, parcel = load_fixture(fixture)
    return build_report_map_snapshot(
        response, parcel, pog_theme=theme, basemap_dir="", frozen_at=FROZEN_AT
    )


def render_case(case: str) -> tuple[bytes, dict[str, Any], dict[str, Any]]:
    """(PNG, snapshot, środowisko) dla przypadku referencyjnego."""
    _, fixture, theme, map_id = next(item for item in REFERENCE_CASES if item[0] == case)
    snapshot = reference_snapshot(fixture, theme)
    maps = render_report_maps(snapshot, basemap_dir="")
    rendered = maps.by_id(map_id)
    assert rendered is not None and rendered.png_bytes is not None, case
    return rendered.png_bytes, snapshot, maps.environment


def diff_ratio(left: bytes, right: bytes) -> float:
    a = Image.open(io.BytesIO(left)).convert("RGB")
    b = Image.open(io.BytesIO(right)).convert("RGB")
    if a.size != b.size:
        return 1.0
    diff = ImageChops.difference(a, b).convert("L").point(lambda value: 255 if value > CHANNEL_TOLERANCE else 0)
    histogram = diff.histogram()
    return histogram[255] / (a.size[0] * a.size[1])


def write_reference_maps() -> None:
    MAPS_DIR.mkdir(parents=True, exist_ok=True)
    reference: dict[str, Any] = {}
    for case, *_ in REFERENCE_CASES:
        png, snapshot, environment = render_case(case)
        (MAPS_DIR / f"{case}.png").write_bytes(png)
        reference[case] = {
            "semantic_sha256": snapshot["semantic_sha256"],
            "png_sha256": hashlib.sha256(png).hexdigest(),
            "environment": environment,
        }
    REFERENCE_FILE.write_text(json.dumps(reference, indent=1, sort_keys=True) + "\n", "utf-8")
