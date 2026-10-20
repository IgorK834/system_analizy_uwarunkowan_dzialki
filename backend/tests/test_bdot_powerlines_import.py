"""AU-201: three unmodified real powerline classes, PostGIS and independent GML measurement."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree
import zipfile

import pytest
from shapely.geometry import LineString
from sqlalchemy import text

from app.core.data_sources import get_catalog
from app.db.session import SessionLocal
from app.modules.imports.domain.bdot10k import Bdot10kValidationError, LayerStats
from app.modules.imports.application.powerlines import prepare_powerlines
from app.modules.imports.infrastructure.bdot10k.geometry import ShapelyGeometryTools
from app.modules.imports.infrastructure.bdot10k.reader import ZipBdot10kReader
from tests.bdot_support import import_package, purge_bdot

FIXTURES = Path(__file__).parent / "fixtures/source_contracts/bdot10k_base"
pytestmark = pytest.mark.integration


@pytest.mark.parametrize("county", ["1261", "0262", "2461"])
def test_real_class_is_reproducible_and_matches_independent_gml_lengths(county, tmp_path):
    package = FIXTURES / county / f"{county}_GML.zip"
    manifest = json.loads((package.parent / "manifest.json").read_text())
    assert hashlib.sha256(package.read_bytes()).hexdigest() == manifest["fixture_sha256"]
    with zipfile.ZipFile(package) as archive:
        raw = archive.read(next(name for name in archive.namelist() if name.endswith("__OT_SULN_L.xml")))
    assert hashlib.sha256(raw).hexdigest() == manifest["class_xml_sha256"]
    lengths = []
    for positions in ElementTree.fromstring(raw).iter("{http://www.opengis.net/gml/3.2}posList"):
        coords = [float(value) for value in positions.text.split()]
        lengths.append(LineString(list(zip(coords[::2], coords[1::2], strict=True))).length)
    with SessionLocal() as db:
        purge_bdot(db)
        try:
            first = import_package(db, county, tmp_path, package=package)
            second = import_package(db, county, tmp_path, package=package)
            assert first.data_release_id == second.data_release_id
            assert first.stats["powerlines_imported"] == second.stats["powerlines_imported"] == manifest["feature_count"]
            assert second.stats["created"] == 0
            row = db.execute(text("SELECT count(*), sum(ST_Length(geometry)), bool_and(ST_SRID(geometry)=2180 AND ST_IsValid(geometry)) FROM bdot_powerlines WHERE data_release_id=:id"), {"id": first.data_release_id}).one()
            assert row[0] == manifest["feature_count"] and row[2] is True
            assert row[1] == pytest.approx(sum(lengths), abs=0.001)
            stats = db.execute(text("SELECT class_stats FROM bdot_releases WHERE data_release_id=:id"), {"id": first.data_release_id}).scalar_one()
            assert stats["powerlines"]["complete"] is True
        finally:
            purge_bdot(db)


def test_absent_class_has_null_coverage_count_and_failures_never_publish(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import app
    package = FIXTURES / "1261/1261_GML.zip"
    mapping = get_catalog().get("bdot10k").field_mapping
    batch = ZipBdot10kReader(package, teryt_powiat="1261", mapping=mapping).read()
    first = batch.powerlines[0]
    for attributes, geometry in [({**first.attributes, "rodzaj": "nowa nieznana klasa"}, first.geometry_wkb),
                                 ({**first.attributes, "lokalnyId": None}, first.geometry_wkb),
                                 (first.attributes, None), (first.attributes, b"broken WKB")]:
        bad = replace(first, attributes=attributes, geometry_wkb=geometry)
        with pytest.raises(Bdot10kValidationError):
            prepare_powerlines((bad,), mapping, ShapelyGeometryTools(), LayerStats())
    with SessionLocal() as db:
        purge_bdot(db)
        try:
            import_package(db, "1261", tmp_path)
            body = TestClient(app).get("/api/v1/coverage/bdot10k").json()
            assert body["counties"][0]["powerline_count"] is None
            assert body["counties"][0]["landcover_count"] is None
        finally:
            purge_bdot(db)


def test_empty_confirmed_class_is_distinct_from_missing():
    assert prepare_powerlines((), {}, ShapelyGeometryTools(), LayerStats()) == ()
