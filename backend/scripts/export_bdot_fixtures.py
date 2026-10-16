#!/usr/bin/env python3
"""Generuje ``frontend/test/bdotFixtures.ts`` z backendowych funkcji budujących sekcje drogi i zabudowy (Task 23.2–23.3).

Fixtures UI nie są pisane ręcznie: powstają z ``build_road_context`` / ``build_neighbour_buildings`` na geometrii zamrożonych
fixtures raportu (``tests/fixtures/reports/build_fixtures.py``), a stany niepełne — z tych samych modeli kontraktu (walidatory
Pydantic pilnują spójności statusu, ``nearest`` i statystyk). Uruchomienie z katalogu ``backend`` w obrazie testowym::

    python scripts/export_bdot_fixtures.py > ../frontend/test/bdotFixtures.ts
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
os.chdir(BACKEND_DIR)
sys.path.insert(0, str(BACKEND_DIR))

from shapely.geometry import box  # noqa: E402

from app.modules.analysis.application.buildings import BuildingContextOutcome  # noqa: E402
from app.modules.analysis.domain.buildings import (  # noqa: E402
    BuildingFootprint,
    BuildingSearchPlan,
    compute_building_metrics,
)
from app.schemas.bdot_context import NeighbourBuildingsResult, RoadContextResult  # noqa: E402
from app.services.bdot_context import build_neighbour_buildings  # noqa: E402
from app.shared.provenance import Provenance  # noqa: E402
from tests.fixtures.reports import build_fixtures as base  # noqa: E402


def _road_cases() -> dict[str, dict]:
    available = RoadContextResult.model_validate(base._road_context())
    nearest = available.nearest
    assert nearest is not None
    intersecting = available.model_copy(
        update={
            "nearest": nearest.model_copy(update={"distance_m": 0.0, "relation": "intersects", "link_geojson": None}),
            "intersecting_road_count": 2,
            "touching_road_count": 1,
        }
    )
    partial = available.model_copy(
        update={
            "search_area_within_coverage": False,
            "warnings": ["Obszar do znalezionej drogi wykracza poza zasięg zaimportowanego powiatu."],
        }
    )

    def without_road(status: str, reason: str, **extra: object) -> RoadContextResult:
        return available.model_copy(
            update={
                "status": status, "reason_code": reason, "nearest": None, "search_radius_m": None,
                "searched_radii_m": [], "intersecting_road_count": None, "touching_road_count": None,
                "release": None, **extra,
            }
        )

    no_match = without_road(
        "no_match", "SEARCH_RADIUS_EXHAUSTED", search_radius_m=500.0, searched_radii_m=[25.0, 50.0, 100.0, 250.0, 500.0],
        search_area_within_coverage=True, release=available.release,
    )
    return {
        "available": available,
        "intersects": intersecting,
        "partial_coverage": partial,
        "no_match": no_match,
        "no_coverage": without_road("no_coverage", "OUTSIDE_COVERAGE", search_area_within_coverage=None),
        "unavailable": without_road("unavailable", "QUERY_TIMEOUT", search_area_within_coverage=None),
    }


def _buildings_cases() -> dict[str, NeighbourBuildingsResult]:
    available = NeighbourBuildingsResult.model_validate(base._neighbour_buildings())
    plan = BuildingSearchPlan()
    crowd = [
        BuildingFootprint(
            building_id=f"CROWD-{index:03d}-0000-0000",
            geometry=box(566062.0 + 1.5 * (index % 10), 244000.0 + 2.0 * (index // 10), 566063.0 + 1.5 * (index % 10), 244001.5 + 2.0 * (index // 10)),
            area_sqm=1.5, general_function="budynki mieszkalne", floors=1 + index % 3, geometry_source="EGiB",
        )
        for index in range(30)
    ]
    crowded = build_neighbour_buildings(
        BuildingContextOutcome(
            status="available",
            provenance=Provenance(source_id="bdot10k", fetched_at=base.ANALYZED_AT, data_release_id=21),
            plan=plan, metrics=compute_building_metrics(base.PARCEL_2180, crowd, plan), search_area_within_coverage=True,
            release=base._bdot_release(),
        )
    )
    empty = build_neighbour_buildings(
        BuildingContextOutcome(
            status="available",
            provenance=Provenance(source_id="bdot10k", fetched_at=base.ANALYZED_AT, data_release_id=21),
            plan=plan, metrics=compute_building_metrics(base.PARCEL_2180, [], plan), search_area_within_coverage=True,
            release=base._bdot_release(),
        )
    )

    def without_stats(status: str, reason: str) -> NeighbourBuildingsResult:
        return available.model_copy(
            update={
                "status": status, "reason_code": reason, "parcel_area_sqm": None, "on_parcel": None, "buffers": [],
                "buildings": [], "buildings_truncated": False, "listed_radius_m": None, "candidates_considered": None,
                "search_area_within_coverage": None, "release": None,
            }
        )

    return {
        "available": available,
        "crowded": crowded,
        "empty": empty,
        "partial_coverage": available.model_copy(update={"search_area_within_coverage": False}),
        "no_coverage": without_stats("no_coverage", "OUTSIDE_COVERAGE"),
        "unavailable": without_stats("unavailable", "QUERY_TIMEOUT"),
    }


def main() -> None:
    roads = {key: value.model_dump(mode="json") for key, value in _road_cases().items()}
    buildings = {key: value.model_dump(mode="json") for key, value in _buildings_cases().items()}
    header = (
        "/**\n"
        " * Sekcje drogi i zabudowy sąsiedniej wygenerowane przez backend (`build_road_context`, `build_neighbour_buildings`)\n"
        " * na zamrożonej geometrii raportu — po jednym przypadku na istotną sytuację (Task 23.2–23.3). Nie edytuj ręcznie:\n"
        " * `python scripts/export_bdot_fixtures.py > ../frontend/test/bdotFixtures.ts`.\n"
        " */\n"
        'import type { NeighbourBuildingsResult, RoadContextResult } from "@/lib/types";\n\n'
    )
    sys.stdout.write(header)
    sys.stdout.write(
        "export const ROAD_CASES: Record<string, RoadContextResult> = "
        + json.dumps(roads, ensure_ascii=False, indent=2)
        + ";\n\n"
    )
    sys.stdout.write(
        "export const BUILDINGS_CASES: Record<string, NeighbourBuildingsResult> = "
        + json.dumps(buildings, ensure_ascii=False, indent=2)
        + ";\n"
    )


if __name__ == "__main__":
    main()
