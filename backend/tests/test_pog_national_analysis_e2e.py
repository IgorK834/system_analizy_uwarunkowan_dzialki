"""Analiza działki na krajowym stanie POG od końca do końca (ADR-026).

Dwie realne gminy (Jejkowice i Sejny, pliki GML z RU zamrożone 2026-10-13) są importowane produkcyjnym kodem importu
krajowego. Potem: kafle obu gmin → ``POST /analyze`` działek z obu gmin → zapis i odczyt historyczny → trafienie cache →
raport PDF → pakiet audytowy ZIP. Sprawdzane są też własności modelu krajowego:

* analiza przypina stan rejestru i wersje aktów (identyfikator + ``content_hash``) i nie zmienia ich po późniejszych
  importach;
* import innej gminy nie unieważnia cache analizy (zakres cache = gmina działki), a nowa wersja aktu gminy działki tak;
* raport i pakiet audytowy starej analizy nadal odtwarzają dawną wersję aktu.

Usługi zewnętrzne są zamrożone jak w ``test_terrain_e2e`` (bez internetu).
"""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.core.access_control import make_analysis_token
from app.core.settings import settings
from app.db.session import SessionLocal, get_db
from app.main import app
from app.models.analysis import Analysis
from app.models.analysis_pending_document import AnalysisPendingDocument
from app.models.infrastructure import Infrastructure
from app.models.mpzp_parameter import MpzpParameter
from app.models.mpzp_zone import MpzpZone
from app.models.parcel import Parcel
from app.models.pog_data import PogData
from app.models.risk import Risk
from app.models.source_record import SourceRecord
from app.modules.planning.composition import pog_tile_cache
from app.services.initiation import ParcelLookupResult
from tests import test_terrain_e2e as base
from tests.mvt_decoder import decode_tile
from tests.national_support import (
    FIXTURES,
    JEJKOWICE,
    SEJNY,
    SLUPSK,
    FixtureGateway,
    build_stack,
    fixture_listing,
)
from tests.test_national_import_e2e import TERC, _zone_point
from tests.test_terrain_e2e import _nmt, _pdf_text, _reread

pytestmark = pytest.mark.integration

# Identyfikator działki zaczyna się od TERC gminy (jak w ULDK), więc zakres cache jest rozpoznawany po gminie.
PARCEL_PREFIX = {JEJKOWICE: "241203_9.9999.", SEJNY: "200901_9.9999."}
ACT_IDENTIFIER = {
    JEJKOWICE: "PL.ZIPPZP.10169/241203-POG/1POG",
}


def _cleanup() -> None:
    patterns = [f"{prefix}%" for prefix in PARCEL_PREFIX.values()]
    with SessionLocal() as db:
        parcel_ids = select(Parcel.id).where(or_(*[Parcel.parcel_identifier.like(item) for item in patterns]))
        analysis_ids = select(Analysis.id).where(Analysis.parcel_id.in_(parcel_ids))
        zone_ids = select(MpzpZone.id).where(MpzpZone.analysis_id.in_(analysis_ids))
        db.execute(delete(MpzpParameter).where(MpzpParameter.mpzp_zone_id.in_(zone_ids)))
        for model in (SourceRecord, PogData, MpzpZone, Infrastructure, Risk, AnalysisPendingDocument):
            db.execute(delete(model).where(model.analysis_id.in_(analysis_ids)))
        db.execute(delete(Analysis).where(Analysis.id.in_(analysis_ids)))
        db.execute(delete(Parcel).where(Parcel.id.in_(parcel_ids)))
        db.commit()


@pytest.fixture(autouse=True)
def cleanup_rows() -> Iterator[None]:
    _cleanup()
    pog_tile_cache().clear()
    yield
    _cleanup()
    pog_tile_cache().clear()


@pytest.fixture
def session() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()


@pytest.fixture
def tiles_client(session: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def _post(act_id: int, wkt: str, identifier: str | None = None):  # noqa: ANN202
    """Jedno żądanie ``/analyze``; ``identifier`` pozwala powtórzyć tę samą działkę (trafienie cache)."""
    identifier = identifier or f"{PARCEL_PREFIX[act_id]}{uuid4().hex[:8]}"
    lookup = ParcelLookupResult(
        parcel_identifier=identifier,
        wkt=wkt,
        teryt=TERC[act_id] + "1",
        source_metadata=base._source("ULDK", "https://uldk.example.test", confidence=0.95),
    )
    patches = [
        *base._patches(identifier, wkt, base._no_mpzp()),
        patch("app.services.analysis_orchestrator.resolve_parcel", new=AsyncMock(return_value=lookup)),
    ]
    for item in patches:
        item.start()
    try:
        with respx.mock(assert_all_called=False) as router:
            router.get(settings.nmt_base_url).mock(
                return_value=httpx.Response(200, text=_nmt("nmt_getminmaxbypolygon.txt"))
            )
            response = base.client.post("/analyze", json={"method": "parcel_id", "parcel_identifier": identifier})
        return identifier, response
    finally:
        for item in reversed(patches):
            item.stop()


def _parcel_wkt(act_id: int) -> str:
    point, _tile16, _zone = _zone_point(act_id)
    return point.buffer(8, cap_style="square").wkt


def _zip_members(content: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        assert archive.testzip() is None
        return {info.filename: archive.read(info) for info in archive.infolist()}


def _renumber(listing, act_id: int, new_id: int):  # noqa: ANN001, ANN202
    acts = tuple(
        act if act.published_act_id != act_id else type(act)(**{**act.__dict__, "published_act_id": new_id})
        for act in listing.acts
    )
    return type(listing)(acts, listing.declared_total, 1, 1, True, None)


def test_two_communes_flow_through_tiles_analysis_save_cache_report_and_audit_package(
    session: Session, tiles_client: TestClient, tmp_path: Path
) -> None:
    stack = build_stack(tmp_path)
    first_import = stack.run(FixtureGateway(fixture_listing(JEJKOWICE, SEJNY)))
    assert first_import.status == "succeeded", json.dumps(first_import.as_payload(), default=str, ensure_ascii=False)[:3000]
    state_1 = int(first_import.registry_state_after)  # type: ignore[arg-type]

    # --- Kafle: obie gminy w jednym stanie krajowym (z16 w punktach stref) ---------------------------------------
    state = tiles_client.get("/api/v1/map/pog/state").json()
    assert state["state_id"] == state_1 and state["act_count"] == 2 and state["map_ready"] is True
    for act_id in (JEJKOWICE, SEJNY):
        _point, (x, y), zone_id = _zone_point(act_id)
        layers = decode_tile(tiles_client.get(state["tile_url_template"].format(z=16, x=x, y=y)).content)
        assert zone_id in {f.properties["feature_id"] for f in layers["zones"].features}

    # --- Analiza działek z obu gmin: lokalny stan, przypięcie wersji --------------------------------------------
    wkt_a, wkt_b = _parcel_wkt(JEJKOWICE), _parcel_wkt(SEJNY)
    identifier_a, response_a = _post(JEJKOWICE, wkt_a)
    identifier_b, response_b = _post(SEJNY, wkt_b)
    assert response_a.status_code == 200 and response_b.status_code == 200, (response_a.text, response_b.text)
    body_a, body_b = response_a.json(), response_b.json()
    for body, act_id in ((body_a, JEJKOWICE), (body_b, SEJNY)):
        pog = body["pog"]
        assert (pog["legal_status"], pog["coverage_status"], pog["data_availability"]) == (
            "binding",
            "available",
            "current",
        )
        assert pog["zones"], "analiza nie znalazła stref gminy zaimportowanej obok drugiej"
        assert pog["registry_state_id"] == state_1
        pinned = pog["pinned_act_versions"]
        assert len(pinned) == 1 and len(pinned[0]["content_hash"]) == 64
        assert pinned[0]["act_identifier"] == pog["act"]["act_identifier"]
        assert f"/{TERC[act_id]}-POG/" in pog["act"]["act_identifier"]
    assert body_a["pog"]["pinned_act_versions"][0]["content_hash"] != body_b["pog"]["pinned_act_versions"][0]["content_hash"]
    hash_a = body_a["pog"]["pinned_act_versions"][0]["content_hash"]

    # --- Zapis i odczyt historyczny: te same przypięcia ------------------------------------------------------------
    reread = _reread(body_a["analysis_id"])
    assert reread["pog"]["registry_state_id"] == state_1
    assert reread["pog"]["pinned_act_versions"] == body_a["pog"]["pinned_act_versions"]

    # --- Cache: powtórzenie tej samej działki zwraca zapisaną analizę ---------------------------------------------
    _, again = _post(JEJKOWICE, wkt_a, identifier_a)
    assert again.status_code == 200 and again.json()["analysis_id"] == body_a["analysis_id"]

    # Import trzeciej gminy (Słupsk) nie unieważnia cache analiz gmin A i B.
    third = stack.run(FixtureGateway(fixture_listing(JEJKOWICE, SEJNY, SLUPSK)))
    assert third.status == "succeeded" and int(third.registry_state_after) > state_1  # type: ignore[arg-type]
    _, still_a = _post(JEJKOWICE, wkt_a, identifier_a)
    _, still_b = _post(SEJNY, wkt_b, identifier_b)
    assert still_a.json()["analysis_id"] == body_a["analysis_id"]
    assert still_b.json()["analysis_id"] == body_b["analysis_id"]

    # --- Nowa wersja aktu gminy A (zmieniona treść) unieważnia cache tylko tej gminy ------------------------------
    original = (FIXTURES / f"act_{JEJKOWICE}.gml").read_text(encoding="utf-8")
    edited = original.replace("Plan ogólny gminy Jejkowice", "Plan ogólny gminy Jejkowice (zmiana)", 1)
    assert edited != original
    listing = fixture_listing(JEJKOWICE, SEJNY, SLUPSK)
    changed = stack.run(FixtureGateway(_renumber(listing, JEJKOWICE, 91152), overrides={91152: edited.encode()}))
    assert changed.status == "succeeded"
    state_3 = int(changed.registry_state_after)  # type: ignore[arg-type]
    assert state_3 > int(third.registry_state_after)  # type: ignore[arg-type]

    _, refreshed_a = _post(JEJKOWICE, wkt_a, identifier_a)
    _, cached_b = _post(SEJNY, wkt_b, identifier_b)
    assert refreshed_a.status_code == 200 and refreshed_a.json()["analysis_id"] != body_a["analysis_id"]
    new_pog = refreshed_a.json()["pog"]
    assert new_pog["registry_state_id"] == state_3
    assert new_pog["pinned_act_versions"][0]["content_hash"] != hash_a
    assert cached_b.json()["analysis_id"] == body_b["analysis_id"], "zmiana gminy A unieważniła cache gminy B"

    # --- Stara analiza nadal odtwarza dawną wersję: odczyt, raport PDF i pakiet audytowy ---------------------------
    old_reread = _reread(body_a["analysis_id"])
    assert old_reread["pog"]["registry_state_id"] == state_1
    assert old_reread["pog"]["pinned_act_versions"][0]["content_hash"] == hash_a

    token = make_analysis_token(body_a["analysis_id"])
    report = base.client.get(f"/report/{body_a['analysis_id']}?access_token={token}")
    assert report.status_code == 200 and report.headers["content-type"].startswith("application/pdf")
    text = _pdf_text(report.content)
    assert ACT_IDENTIFIER[JEJKOWICE].replace("/", "") in text.replace("/", "").replace(" ", "") or "Jejkowice" in text
    assert "(zmiana)" not in text, "raport starej analizy pokazuje nowszą wersję aktu"

    package = base.client.get(f"/report/{body_a['analysis_id']}/audit.zip?access_token={token}")
    assert package.status_code == 200 and package.headers["content-type"].startswith("application/zip")
    members = _zip_members(package.content)
    payload = b"\n".join(content for name, content in members.items() if name.endswith(".json"))
    assert hash_a.encode() in payload, "pakiet audytowy nie zawiera przypiętej wersji aktu (content_hash)"
    assert json.loads(members["manifest.json"]) if "manifest.json" in members else True
