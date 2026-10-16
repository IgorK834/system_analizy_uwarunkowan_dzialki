"""Import drugiej gminy POG nie może ukrywać pierwszej (krajowy stan POG, ADR-026).

Model sprzed ADR-026 miał jedno aktywne wydanie na źródło ``pog_app``: ``_activate_release`` wygaszał wszystkie
inne, a ``publish_pog`` przenosił do nowego wydania tylko akty z importowanej paczki. Import gminy B odcinał więc
gminę A od analizy, kafli i inspektora obiektu. Testy odtwarzają to na PostGIS: gmina A (realny akt Sopotu z
zamrożonych odpowiedzi RU), potem gmina B (ten sam realny akt przesunięty o 40 km na wschód i opisany innym
TERYT — geometria i atrybuty są prawdziwe, zmieniono tylko położenie i identyfikatory).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pyproj import Transformer
from shapely import affinity, from_wkt
from sqlalchemy.orm import Session

from app.core.data_sources import DataSourceEntry
from app.db.session import SessionLocal, get_db
from app.main import app
from app.modules.imports.application.common import ImportRelease
from app.modules.imports.application.pog_import import PogSourceBatch, run_pog_import
from app.modules.imports.domain.pog import PogActRecord, PogObjectId
from app.modules.imports.infrastructure.artifacts import LocalArtifactStore
from app.modules.imports.infrastructure.pog.reader import (
    assemble_ru_pog_acts,
    merge_ru_pog_objects,
    parse_ru_app_feature_collection,
)
from app.modules.imports.infrastructure.repository import SqlAlchemyImportRepository
from app.modules.planning.composition import pog_tile_cache
from app.services.analysis_orchestrator import _analyze_pog_local_release
from app.shared.geometry import GeometryPayload
from tests.mvt_decoder import decode_tile

RU_FIXTURES = Path(__file__).parent / "fixtures" / "ru"
RU_TYPES = {
    "act": "AktPlanowaniaPrzestrzennego",
    "zone": "StrefaPlanistyczna",
    "ouz": "ObszarUzupelnieniaZabudowy",
    "ozs": "ObszarZabudowySrodmiejskiej",
    "osdis": "ObszarStandardowDostepnosciInfrastrukturySpolecznej",
}
SOURCE_ID = "pog_app"
TILE_ZOOM = 16
_TO_WGS84 = Transformer.from_crs("EPSG:2180", "EPSG:4326", always_xy=True)


def _source_entry() -> DataSourceEntry:
    return DataSourceEntry.model_validate(
        {
            "source_id": SOURCE_ID,
            "name": "POG APP (fixture RU)",
            "owner": "Test",
            "status": "production",
            "production_ready": True,
            "contract_confirmed": True,
            "access_type": "app_gml",
            "capabilities_url": None,
            "file_url": "file:///fixture.gml",
            "type_names": ["StrefaPlanistyczna"],
            "layers": None,
            "protocol_version": "fixture/1",
            "source_crs": "EPSG:2180",
            "target_crs": "EPSG:2180",
            "teryt_scope": ["226401"],
            "license": "Fixture RU.",
            "attribution": "Rejestr Urbanistyczny.",
            "expected_update_interval": "never",
            "sla": "test",
            "last_manual_verification": "2026-10-13",
        }
    )


def _real_sopot_act() -> PogActRecord:
    parts = []
    for name, feature_type in RU_TYPES.items():
        payload = (RU_FIXTURES / f"wfs_pog_getfeature_{name}.xml").read_bytes()
        if name == "osdis":
            payload = payload.replace(
                b"PL.ZIPPZP.10067/240203-POG/1POG", b"PL.ZIPPZP.10011/226401-POG/1POG"
            )
        parts.append(
            parse_ru_app_feature_collection(payload, expected_type=feature_type, source_reference="fixture")
        )
    act = assemble_ru_pog_acts(merge_ru_pog_objects(tuple(parts)))[0]
    zone = next(f for f in act.features if f.feature_type == "planning_zone")
    center = from_wkt(zone.geometry.wkt).centroid
    features = []
    for feature in act.features:
        if feature.feature_type == "planning_zone":
            features.append(feature)
            continue
        shape = from_wkt(feature.geometry.wkt)
        moved = affinity.translate(shape, center.x - shape.centroid.x, center.y - shape.centroid.y)
        features.append(feature.with_geometry(GeometryPayload(moved.wkt)))
    return replace(act, features=tuple(features))


def _shifted_commune(act: PogActRecord, *, namespace: str, teryt: str, dx: float) -> PogActRecord:
    """Ten sam realny akt w innym miejscu i z innymi identyfikatorami (druga gmina)."""
    old_namespace = act.object_id.namespace  # type: ignore[union-attr]

    def rename(object_id: PogObjectId | None) -> PogObjectId | None:
        if object_id is None:
            return None
        return PogObjectId(object_id.namespace.replace(old_namespace, namespace), object_id.local_id, object_id.version_id)

    def move(geometry: GeometryPayload) -> GeometryPayload:
        return GeometryPayload(affinity.translate(from_wkt(geometry.wkt), dx, 0).wkt)

    features = []
    for feature in act.features:
        reference = feature.act_reference
        if reference is not None:
            reference = replace(reference, href=reference.href.replace(old_namespace, namespace))
        features.append(
            replace(
                feature,
                geometry=move(feature.geometry),
                object_id=rename(feature.object_id),
                act_reference=reference,
            )
        )
    return replace(
        act,
        act_identifier=act.act_identifier.replace(old_namespace, namespace),
        teryt=teryt,
        name="Plan ogólny gminy B (realny akt przesunięty)",
        object_id=rename(act.object_id),
        boundary=move(act.boundary) if act.boundary is not None else None,
        features=tuple(features),
        documents=(),
        metadata=(),
        feature_references=(),
        document_references=(),
    )


class _StaticReader:
    def __init__(self, *acts: PogActRecord, content: bytes) -> None:
        self.acts = acts
        self.content = content

    def read(self) -> PogSourceBatch:
        return PogSourceBatch(self.content, "pog.gml", "application/gml+xml", self.acts)


def _publish(session: Session, tmp_path: Path, act: PogActRecord, *, label: str) -> int:
    repository = SqlAlchemyImportRepository(session, _source_entry(), LocalArtifactStore(tmp_path))
    outcome = run_pog_import(
        _StaticReader(act, content=f"pog-{label}-{uuid4().hex}".encode()),
        SOURCE_ID,
        repository,
        release=ImportRelease(
            SOURCE_ID,
            label,
            datetime(2026, 10, 13, tzinfo=timezone.utc),
            publication_allowed=True,
            dry_run=False,
            teryt_scope=("*",),
        ),
    )
    assert outcome.status == "succeeded", outcome
    return int(outcome.data_release_id)  # type: ignore[arg-type]


def _lonlat_tile(lon: float, lat: float, zoom: int) -> tuple[int, int]:
    import math

    n = 1 << zoom
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return x, y


@pytest.fixture
def session() -> Iterator[Session]:
    db = SessionLocal()
    pog_tile_cache().clear()
    try:
        yield db
    finally:
        db.rollback()
        db.close()
        pog_tile_cache().clear()


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def two_communes(session: Session, tmp_path: Path) -> dict[str, object]:
    """Gmina A (Sopot) zaimportowana jako pierwsza, gmina B jako druga."""
    act_a = _real_sopot_act()
    act_b = _shifted_commune(act_a, namespace="PL.ZIPPZP.10099/226499-POG", teryt="226499", dx=40_000.0)
    release_a = _publish(session, tmp_path, act_a, label="A")
    release_b = _publish(session, tmp_path, act_b, label="B")
    zone_a = next(f for f in act_a.features if f.feature_type == "planning_zone")
    zone_b = next(f for f in act_b.features if f.feature_type == "planning_zone")
    shape_a, shape_b = from_wkt(zone_a.geometry.wkt), from_wkt(zone_b.geometry.wkt)
    return {
        "release_a": release_a,
        "release_b": release_b,
        "zone_a": zone_a,
        "zone_b": zone_b,
        "shape_a": shape_a,
        "shape_b": shape_b,
        "tile_a": _lonlat_tile(*_TO_WGS84.transform(*shape_a.representative_point().coords[0]), TILE_ZOOM),
        "tile_b": _lonlat_tile(*_TO_WGS84.transform(*shape_b.representative_point().coords[0]), TILE_ZOOM),
    }


def _parcel_in(shape):  # type: ignore[no-untyped-def]
    return shape.representative_point().buffer(3, cap_style="square")


@pytest.mark.integration
def test_importing_commune_b_keeps_commune_a_in_parcel_analysis(
    session: Session, two_communes: dict[str, object]
) -> None:
    result_a = _analyze_pog_local_release(session, _parcel_in(two_communes["shape_a"]), "2264011")
    result_b = _analyze_pog_local_release(session, _parcel_in(two_communes["shape_b"]), "2264991")

    assert result_a is not None, "gmina A zniknęła z analizy po imporcie gminy B"
    assert result_b is not None
    assert result_a[0].zones and result_a[0].zones[0].id == two_communes["zone_a"].object_id.stable_id  # type: ignore[attr-defined]
    assert result_b[0].zones and result_b[0].zones[0].id == two_communes["zone_b"].object_id.stable_id  # type: ignore[attr-defined]


@pytest.mark.integration
def test_importing_commune_b_keeps_commune_a_on_the_map(
    client: TestClient, two_communes: dict[str, object]
) -> None:
    metadata = client.get("/api/v1/map/pog/state")
    assert metadata.status_code == 200
    template = metadata.json()["tile_url_template"]

    for key in ("tile_a", "tile_b"):
        x, y = two_communes[key]  # type: ignore[misc]
        url = template.replace("{z}", str(TILE_ZOOM)).replace("{x}", str(x)).replace("{y}", str(y))
        response = client.get(url)
        assert response.status_code == 200
        zones = decode_tile(response.content).get("zones")
        assert zones is not None and zones.features, f"kafel {key} jest pusty — gmina niewidoczna na mapie"


@pytest.mark.integration
def test_importing_commune_b_keeps_commune_a_in_feature_inspector(
    client: TestClient, two_communes: dict[str, object]
) -> None:
    metadata = client.get("/api/v1/map/pog/state").json()
    base = metadata["tile_url_template"].rsplit("/{z}/", 1)[0]
    feature_id = two_communes["zone_a"].object_id.stable_id  # type: ignore[attr-defined]

    response = client.get(f"{base}/features/{feature_id}")

    assert response.status_code == 200, "obiekt gminy A nie jest dostępny w inspektorze po imporcie gminy B"
    assert response.json()["act"]["teryt"] == "226401"
    assert response.json()["state"]["state_id"] == metadata["state_id"]
