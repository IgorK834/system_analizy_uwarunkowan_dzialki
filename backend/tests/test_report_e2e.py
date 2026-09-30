"""Scenariusz końcowy BK-501–503: wydanie A → analiza → snapshot → PDF → wydanie B.

Łączy rzeczywiste warstwy: import aktu POG (realne rekordy APP Sopotu z
zamrożonych fixtur RU) do wydania PostGIS, analizę z przypiętego wydania,
``save_analysis`` z zamrożeniem mapy, ``GET /report`` przez router FastAPI przy
zablokowanych gniazdach sieciowych, publikację wydania B ze zmienioną
geometrią i parametrami strefy oraz ponowne wygenerowanie raportu A.

Kryterium BK-503: raport A po publikacji B ma te same granice, wartości,
legendę, skalę i hash semantyczny mapy; nowa analiza tej samej działki (B)
widzi zmianę. Gdy ustawiono ``REPORT_EVIDENCE_DIR``, PDF-y i obrazy stron
trafiają tam jako artefakty odbioru.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from fastapi.testclient import TestClient
from pyproj import Transformer
from shapely import affinity
from shapely.geometry import mapping
from shapely.ops import transform
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.access_control import make_analysis_token
from app.db.session import SessionLocal
from app.main import app
from app.models.analysis import Analysis
from app.models.parcel import Parcel
from app.models.pog_data import PogData
from app.models.source_record import SourceRecord
from app.modules.imports.domain.pog import PogNumericValue
from app.schemas.analyze import AnalyzeResponse, GeometryMetrics, ParcelGeometryResponse
from app.schemas.source import SourceMetadata
from app.services.analysis_orchestrator import _analyze_pog_local_release
from app.services.persistence import save_analysis
from app.services.report_map import render_report_maps
from app.shared.geometry import GeometryPayload
from tests.test_map_tiles import (  # noqa: F401 - fixtury pytest
    _project_act,
    _publish,
    _sopot_act,
    pog_session,
    sopot_release,
)

pytestmark = pytest.mark.integration

_PREFIX = "REPORT_E2E_"
_TO_WGS84 = Transformer.from_crs("EPSG:2180", "EPSG:4326", always_xy=True)
client = TestClient(app)


def _cleanup() -> None:
    with SessionLocal() as db:
        parcel_ids = select(Parcel.id).where(Parcel.parcel_identifier.like(f"{_PREFIX}%"))
        analysis_ids = select(Analysis.id).where(Analysis.parcel_id.in_(parcel_ids))
        db.execute(delete(SourceRecord).where(SourceRecord.analysis_id.in_(analysis_ids)))
        db.execute(delete(PogData).where(PogData.analysis_id.in_(analysis_ids)))
        db.execute(delete(Analysis).where(Analysis.id.in_(analysis_ids)))
        db.execute(delete(Parcel).where(Parcel.id.in_(parcel_ids)))
        db.commit()


@pytest.fixture(autouse=True)
def cleanup_rows():
    _cleanup()
    yield
    _cleanup()


def _response(parcel, pog, sources: list[SourceMetadata], identifier: str) -> AnalyzeResponse:
    geojson = {"type": "Feature", "geometry": mapping(transform(_TO_WGS84.transform, parcel)),
               "properties": {"layer": "parcel"}}
    return AnalyzeResponse(
        status="partial",
        analyzed_at=datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc),
        parcel=ParcelGeometryResponse(
            parcel_identifier=identifier,
            geometry_geojson=geojson,
            metrics=GeometryMetrics(area_sqm=parcel.area, area_ha=parcel.area / 10_000,
                                    perimeter_m=parcel.length, is_valid=True, geometry_repaired=False),
            source=SourceMetadata(source_name="ULDK", confidence=0.95, manual_review_required=False),
        ),
        mpzp_zones=[],
        pog=pog,
        infrastructure=[],
        risks=[],
        warnings=[],
        sources=[SourceMetadata(source_name="ULDK", confidence=0.95, manual_review_required=False), *sources],
    )


def _pdf_text(pdf: bytes) -> str:
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return " ".join(page.get_text() for page in doc).replace(chr(0xA0), " ")


def _squash(text: str) -> str:
    """Porównanie odporne na łamanie wierszy i dzielenie (U+2010) w PDF."""
    return re.sub("[\\s" + chr(0x2010) + "]", "", text)


def _report(analysis_id: int) -> bytes:
    response = client.get(f"/report/{analysis_id}?access_token={make_analysis_token(analysis_id)}")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    return response.content


def _evidence(name: str, content: bytes | str) -> None:
    target = os.environ.get("REPORT_EVIDENCE_DIR")
    if not target:
        return
    path = Path(target) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
        if name.endswith(".pdf"):
            with pymupdf.open(stream=content, filetype="pdf") as doc:
                for number, page in enumerate(doc, start=1):
                    page.get_pixmap(dpi=80).save(path.with_name(f"{path.stem}-p{number:02d}.png"))
    else:
        path.write_text(content, "utf-8")


def _map_signature(snapshot: dict[str, Any]) -> dict[str, Any]:
    maps = render_report_maps(snapshot, basemap_dir="")
    pog = maps.by_id("pog")
    assert pog is not None
    return {
        "semantic": maps.semantic_sha256,
        "png": [item.png_sha256 for item in maps.maps],
        "legend": pog.legend,
        "frame": maps.frame,
        "features": [
            (feature["id"], feature["geometry"])
            for layer in next(item for item in snapshot["maps"] if item["id"] == "pog")["layers"]
            for feature in layer["features"]
        ],
    }


def test_release_b_does_not_change_report_of_snapshot_a(
    pog_session: Session,  # noqa: F811
    sopot_release: dict[str, object],  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    zone_shape = sopot_release["zone_shape"]
    parcel = zone_shape.representative_point().buffer(12, cap_style="square")  # type: ignore[attr-defined]

    # --- Wydanie A: analiza, zapis snapshotu, raport przy zablokowanej sieci.
    analysed_a = _analyze_pog_local_release(pog_session, parcel, "2264011")
    assert analysed_a is not None
    pog_a, _, _, sources_a = analysed_a
    assert pog_a.zones and pog_a.act is not None
    identifier = f"{_PREFIX}SOPOT"
    with SessionLocal() as db:
        analysis_a = save_analysis(_response(parcel, pog_a, sources_a, identifier), identifier, parcel, db)
        analysis_id = analysis_a.id
        snapshot_a = json.loads(json.dumps(analysis_a.report_map_snapshot))

    attempts: list[object] = []

    def refuse(self: socket.socket, address: object) -> None:  # noqa: ARG001
        attempts.append(address)
        raise OSError("Sieć zablokowana w scenariuszu końcowym")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    report_a = _report(analysis_id)
    signature_a = _map_signature(snapshot_a)
    text_a = _pdf_text(report_a)
    assert snapshot_a["semantic_sha256"] in _squash(text_a)
    release_a = int(sopot_release["release_id"])  # type: ignore[call-overload]
    assert f"wydania danych: #{release_a}" in text_a
    zone_a = pog_a.zones[0]
    assert _squash(zone_a.id) in _squash(text_a)

    # --- Wydanie B: ta sama działka, przesunięta strefa i inne parametry.
    monkeypatch.undo()
    act = _sopot_act()
    features = []
    for feature in act.features:
        if feature.feature_type == "planning_zone":
            moved = affinity.translate(zone_shape, 30.0, 0.0)  # type: ignore[arg-type]
            parameters = replace(feature.parameters, max_building_height=PogNumericValue(33.0, "m"))
            feature = replace(feature.with_geometry(GeometryPayload(moved.wkt)), parameters=parameters)
        features.append(feature)
    release_b = _publish(
        pog_session, tmp_path, replace(act, features=tuple(features)),
        _project_act(zone_shape.wkt, height_m=None), label="B",  # type: ignore[attr-defined]
    )
    assert release_b != release_a
    analysed_b = _analyze_pog_local_release(pog_session, parcel, "2264011")
    assert analysed_b is not None
    pog_b = analysed_b[0]
    assert pog_b.zones[0].max_building_height_m == 33.0
    assert pog_b.zones[0].area_sqm != pog_a.zones[0].area_sqm
    with SessionLocal() as db:
        analysis_b = save_analysis(
            _response(parcel, pog_b, analysed_b[3], identifier), identifier, parcel, db
        )
        snapshot_b = analysis_b.report_map_snapshot
    assert snapshot_b["semantic_sha256"] != snapshot_a["semantic_sha256"]

    # --- Raport A ponownie: te same granice, wartości, legenda i skala.
    monkeypatch.setattr(socket.socket, "connect", refuse)
    report_a_again = _report(analysis_id)
    with SessionLocal() as db:
        stored_a = db.get(Analysis, analysis_id).report_map_snapshot  # type: ignore[union-attr]
    assert stored_a == snapshot_a
    assert _map_signature(stored_a) == signature_a
    text_again = _pdf_text(report_a_again)
    assert snapshot_a["semantic_sha256"] in _squash(text_again)
    assert "33 m" not in text_again
    assert f"wydania danych: #{release_b}" not in text_again
    assert attempts == []
    digest = lambda pdf: sorted(  # noqa: E731
        hashlib.sha256(pymupdf.Pixmap(doc, image[0]).samples).hexdigest()
        for doc in [pymupdf.open(stream=pdf, filetype="pdf")]
        for page in doc
        for image in page.get_images(full=True)
    )
    assert set(digest(report_a)) == set(digest(report_a_again))

    _evidence("e2e/report-a.pdf", report_a)
    _evidence("e2e/report-a-after-release-b.pdf", report_a_again)
    _evidence("e2e/summary.json", json.dumps({
        "analysis_a": analysis_id,
        "release_a": release_a,
        "release_b": release_b,
        "semantic_a": snapshot_a["semantic_sha256"],
        "semantic_b": snapshot_b["semantic_sha256"],
        "map_png_sha256_a": signature_a["png"],
        "zone_a": {"id": zone_a.id, "area_sqm": zone_a.area_sqm, "height_m": zone_a.max_building_height_m},
        "zone_b": {"id": pog_b.zones[0].id, "area_sqm": pog_b.zones[0].area_sqm,
                   "height_m": pog_b.zones[0].max_building_height_m},
        "network_attempts": attempts,
    }, indent=1, ensure_ascii=False))
    maps_png = render_report_maps(stored_a, basemap_dir="").by_id("pog")
    if maps_png is not None and maps_png.png_bytes:
        _evidence("e2e/map-pog-a.png", maps_png.png_bytes)
