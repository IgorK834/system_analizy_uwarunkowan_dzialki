"""Port granic i prezentacja pokrycia; status aktu jest niezależny od lokalnego importu (AU-108)."""
from __future__ import annotations

from typing import Any, Protocol


class CommuneBoundaries(Protocol):
    def features(self) -> list[dict[str, Any]]: ...


def coverage_features(
    boundaries: CommuneBoundaries, communes: list[dict[str, Any]], *, catalog_fresh: bool,
) -> dict[str, Any]:
    by_terc = {item["terc"][:6]: item for item in communes}
    features = []
    for feature in boundaries.features():
        props = feature["properties"]
        terc = props["teryt"][:6]
        commune = by_terc.get(terc, {})
        local = commune.get("imported_acts", [])
        ru = commune.get("ru_acts", [])
        legal = {act["legal_status"] for act in local}
        state = (
            "binding" if "binding" in legal else "project" if legal & {"project", "in_progress"} else
            "unknown" if local else "no_local_data" if ru or not catalog_fresh else "no_act_in_ru"
        )
        features.append({
            "type": "Feature", "geometry": feature["geometry"],
            "properties": {"terc": terc, "name": props["nazwa"], "state": state,
                           "local_data": bool(local), "status": "available" if local else "no_coverage",
                           "legal_status": next((s for s in ("binding", "project", "in_progress") if s in legal), "unknown"),
                           "release_ids": [act["release_id"] for act in local]},
        })
    return {"type": "FeatureCollection", "features": features}
