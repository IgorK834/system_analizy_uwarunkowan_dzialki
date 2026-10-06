"""Dane do ręcznego odbioru UI BK-404–406 (osobna baza demonstracyjna).

Wydanie #1: realny akt APP Sopotu (fixture RU, strefa 1POG-100SU + OUZ/OZS/OSDIS
przesunięte do środka strefy, jak w teście BK-401), syntetyczny PROJEKT z
strefą SJ nakładającą się na część strefy SU oraz syntetyczny, kompletny akt
1 km² (60% SW / 40% SU) w innej „gminie” — do pokazania stanu available.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from shapely import affinity, from_wkt
from shapely.geometry import box

from app.db.session import SessionLocal
from app.modules.imports.application.common import ImportRelease
from app.modules.imports.application.pog_import import run_pog_import
from app.modules.imports.domain.pog import (
    PogActRecord,
    PogFeatureRecord,
    PogNumericValue,
    PogObjectId,
    PogPlanningParameters,
)
from app.modules.imports.infrastructure.artifacts import LocalArtifactStore
from app.modules.imports.infrastructure.repository import SqlAlchemyImportRepository
from app.shared.geometry import GeometryPayload
from app.shared.planning_status import inspire_status_uri
from tests.test_map_tiles import POG_SOURCE_ID, _StaticReader, _pog_source_entry, _sopot_act

sopot = _sopot_act()
zone = next(f for f in sopot.features if f.feature_type == "planning_zone")
shape = from_wkt(zone.geometry.wkt)
minx, miny, maxx, maxy = shape.bounds
width, height = maxx - minx, maxy - miny

project_ns = "PL.ZIPPZP.99999/226401-POG"
project_zone = box(minx + width * 0.55, miny + height * 0.1, maxx + 60, miny + height * 0.7)
project = PogActRecord(
    act_identifier=f"{project_ns}/2POG",
    resolution_number=None,
    resolution_date=None,
    teryt="226401",
    name="Projekt zmiany planu ogólnego Sopotu (syntetyczny, odbiór BK-404–406)",
    legal_status="project",
    raw_legal_status=inspire_status_uri("project"),
    boundary=GeometryPayload(project_zone.buffer(2).wkt),
    object_id=PogObjectId(project_ns, "2POG", "20260901T000000"),
    features=(
        PogFeatureRecord(
            feature_type="planning_zone",
            geometry=GeometryPayload(project_zone.wkt),
            object_id=PogObjectId(project_ns, "2POG-1SJ", "20260901T000000"),
            symbol="SJ",
            label="strefa wielofunkcyjna z zabudową mieszkaniową jednorodzinną",
            parameters=PogPlanningParameters(
                max_overground_floor_area_ratio=PogNumericValue(0.6, "1"),
                max_building_height=None,
                max_building_coverage=PogNumericValue(0.0, "%"),
                min_biologically_active=PogNumericValue(50.0, "%"),
            ),
        ),
    ),
)

# Kompletny akt 1 km² ~3 km na zachód od Sopotu (inna „gmina”, TERYT syntetyczny).
x0, y0 = minx - 3500, miny - 500
synthetic_ns = "PL.ZIPPZP.99998/999901-POG"
synthetic = PogActRecord(
    act_identifier=f"{synthetic_ns}/1POG",
    resolution_number="I/1/2026",
    resolution_date=None,
    teryt="999901",
    name="Syntetyczny plan ogólny 1 km² (60/40)",
    legal_status="binding",
    raw_legal_status=inspire_status_uri("binding"),
    boundary=GeometryPayload(box(x0, y0, x0 + 1000, y0 + 1000).wkt),
    object_id=PogObjectId(synthetic_ns, "1POG", "20260901T000000"),
    version_started_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    features=(
        PogFeatureRecord(
            feature_type="planning_zone",
            geometry=GeometryPayload(box(x0, y0, x0 + 600, y0 + 1000).wkt),
            object_id=PogObjectId(synthetic_ns, "1POG-1SW", "20260901T000000"),
            symbol="SW",
            parameters=PogPlanningParameters(
                max_overground_floor_area_ratio=PogNumericValue(1.2, "1"),
                max_building_height=PogNumericValue(16.0, "m"),
                max_building_coverage=PogNumericValue(40.0, "%"),
                min_biologically_active=PogNumericValue(25.0, "%"),
            ),
        ),
        PogFeatureRecord(
            feature_type="planning_zone",
            geometry=GeometryPayload(box(x0 + 600, y0, x0 + 1000, y0 + 1000).wkt),
            object_id=PogObjectId(synthetic_ns, "1POG-2SU", "20260901T000000"),
            symbol="SU",
        ),
    ),
)

session = SessionLocal()
repository = SqlAlchemyImportRepository(
    session, _pog_source_entry(), LocalArtifactStore(Path("/tmp/bk404-artifacts"))
)
outcome = run_pog_import(
    _StaticReader(sopot, project, synthetic, content=b"bk404-demo-release-1"),
    POG_SOURCE_ID,
    repository,
    release=ImportRelease(
        POG_SOURCE_ID,
        "demo",
        datetime(2026, 9, 28, tzinfo=timezone.utc),
        publication_allowed=True,
        dry_run=False,
        teryt_scope=("226401", "999901"),
    ),
)
session.commit()
session.close()
print(json.dumps({
    "status": outcome.status,
    "release": outcome.data_release_id,
    "stats": outcome.stats,
    "warnings": list(outcome.warnings),
}, ensure_ascii=False, indent=1, default=str))
