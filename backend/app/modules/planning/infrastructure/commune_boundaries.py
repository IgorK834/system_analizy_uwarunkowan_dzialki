"""Zamrożony PRG — wyłącznie do wyświetlania pokrycia, bez WMS i bez obliczeń analizy."""
from functools import lru_cache
import json
from pathlib import Path
from typing import Any

_DATA = Path(__file__).resolve().parents[3] / "data" / "commune_boundaries.geojson"


@lru_cache(maxsize=1)
def _features() -> list[dict[str, Any]]:
    return json.loads(_DATA.read_text(encoding="utf-8"))["features"]


class PrgCommuneBoundaries:
    def features(self) -> list[dict[str, Any]]:
        return _features()
