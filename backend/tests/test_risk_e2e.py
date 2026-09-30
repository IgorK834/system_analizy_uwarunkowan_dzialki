"""Scenariusz końcowy BK-303 na rzeczywistym połączeniu warstw.

``POST /analyze`` → realne adaptery ISOK i GDOŚ (odpowiedzi WFS zamrożone przez
respx) → sekcja kontekstu → orkiestrator → PostGIS → odczyt historyczny →
trafienie cache → raport PDF → ewaluator. Ewaluator czyta wyłącznie pola
strukturalne — ``description`` celowo zastąpiony śmieciowym tekstem nie
zmienia wyniku. Jeżeli ustawiono ``EVIDENCE_OUT``, zapisywane są artefakty.
"""

from __future__ import annotations

import copy
import io
import json
import os
import re
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pdfplumber
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.access_control import make_analysis_token
from app.db.session import SessionLocal
from app.main import app
from app.models.analysis import Analysis
from app.models.risk import Risk
from app.models.source_record import SourceRecord
from app.services.persistence import build_analyze_response_from_analysis
from app.services.nmt import TerrainNoCoverage
from scripts.evaluate_reference_corpus import calculate_metrics, sections_from_analyze_response
from tests.risk_fixtures import Zone, gdos_collection, isok_collection, mock_gdos, mock_isok
from tests.test_terrain_e2e import (
    CONTROL,
    _binding_pog_discovery,
    _cleanup,
    _kiut,
    _kiut_section,
    _lookup,
    _no_mpzp,
    _pog_vectors,
    _source,
)

pytestmark = pytest.mark.integration

_PREFIX = "BK301_302_E2E_risk_"  # sprzątane przez _cleanup scenariusza NMT
EVIDENCE_OUT = os.environ.get("EVIDENCE_OUT")
client = TestClient(app)
Q1 = "scenariusz Q 1% (raz na 100 lat)"
Q02 = "scenariusz Q 0,2% (raz na 500 lat)"

FLOOD = isok_collection(
    Zone("HA-Q1", (636990, 485990, 637040, 486110), Q1, "100.0"),
    Zone("HA-Q02", (636900, 485900, 637200, 486200), Q02, "500.0"),
    Zone("HA-EDGE", (637100, 486000, 637150, 486100), Q1, None),
)
NATURE = {
    "GDOS:ParkiKrajobrazowe": gdos_collection(
        "ParkiKrajobrazowe", Zone("PK-1", (636900, 485900, 637200, 486200), name="Park Testowy")
    ),
    "GDOS:SpecjalneObszaryOchrony": gdos_collection(
        "SpecjalneObszaryOchrony", Zone("N2K-1", (637000, 486000, 637060, 486100), name="Dolina Testowa")
    ),
}


@pytest.fixture(autouse=True)
def cleanup_rows():
    _cleanup()
    yield
    _cleanup()


def _post(identifier: str, flood, nature) -> dict:
    no_coverage = TerrainNoCoverage(4.0, 676, _source("NMT", "https://nmt.example.test"), [])
    patches = (
        patch("app.services.analysis_orchestrator.resolve_parcel", new=AsyncMock(return_value=_lookup(identifier, CONTROL.wkt))),
        patch("app.services.context.fetch_kiut_network_section", new=AsyncMock(return_value=_kiut_section())),
        patch("app.services.context.fetch_terrain_extremes", new=AsyncMock(return_value=no_coverage)),
        patch("app.services.analysis_orchestrator.discover_mpzp", new=AsyncMock(return_value=_no_mpzp())),
        patch("app.services.analysis_orchestrator.discover_pog", new=AsyncMock(return_value=_binding_pog_discovery())),
        patch("app.services.analysis_orchestrator.fetch_pog_vector_data", new=AsyncMock(return_value=_pog_vectors())),
        patch("app.services.analysis_orchestrator.check_kiut_coverage_for_geometry", new=AsyncMock(return_value=_kiut())),
    )
    for item in patches:
        item.start()
    try:
        with respx.mock(assert_all_called=False) as router:
            isok_route = mock_isok(router, flood)
            gdos_route = mock_gdos(router, nature)
            response = client.post("/analyze", json={"method": "parcel_id", "parcel_identifier": identifier})
            assert response.status_code == 200
            return {"body": response.json(), "calls": isok_route.call_count + gdos_route.call_count}
    finally:
        for item in reversed(patches):
            item.stop()


def _reread(analysis_id: int) -> dict:
    with SessionLocal() as db:
        return build_analyze_response_from_analysis(db.get(Analysis, analysis_id), db).model_dump(mode="json")


def _pdf_text(content: bytes) -> str:
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        raw = "\n".join(page.extract_text() or "" for page in pdf.pages)
    return re.sub(r"\s+", " ", raw.replace(" ", " "))


def _evidence(name: str, payload) -> None:
    if not EVIDENCE_OUT:
        return
    target = Path(EVIDENCE_OUT) / "bk-303" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, bytes):
        target.write_bytes(payload)
    else:
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _sections(body: dict) -> dict[str, dict]:
    return {item["section"]: item for item in body["risk_sections"]}


def test_structured_risks_survive_api_db_cache_pdf_and_feed_evaluator() -> None:
    identifier = f"{_PREFIX}{uuid4().hex[:6]}"
    first = _post(identifier, FLOOD, NATURE)
    body = first["body"]

    # API: status sekcji i pola strukturalne.
    sections = _sections(body)
    flood, nature = sections["flood"], sections["nature"]
    assert (flood["status"], flood["relation"], flood["feature_count"]) == ("available", "intersection", 3)
    assert (flood["intersecting_feature_count"], flood["boundary_feature_count"]) == (2, 1)
    assert flood["union_intersection_pct"] == 100.0
    assert (nature["status"], nature["feature_count"], nature["union_intersection_pct"]) == ("available", 2, 100.0)
    assert flood["source"]["artifact_sha256"] and nature["source"]["artifact_sha256"]
    risks = {item["feature_id"]: item for item in body["risks"]}
    assert risks["HA-Q1"]["return_period_years"] == 100
    assert risks["HA-Q1"]["intersection_pct"] == 40.0
    assert risks["HA-EDGE"]["touches_boundary"] is True
    assert risks["HA-EDGE"]["return_period_years"] is None
    assert (risks["N2K-1"]["protection_type"], risks["N2K-1"]["name"]) == ("natura2000", "Dolina Testowa")
    assert risks["PK-1"]["intersection_pct"] + risks["N2K-1"]["intersection_pct"] == 160.0
    analysis_id = body["analysis_id"]
    _evidence("01-api-response.json", body)

    # DB: typowane kolumny i snapshot sekcji.
    with SessionLocal() as db:
        rows = db.scalars(select(Risk).where(Risk.analysis_id == analysis_id).order_by(Risk.id)).all()
        saved_sections = db.get(Analysis, analysis_id).risk_sections
        records = db.scalars(select(SourceRecord).where(SourceRecord.analysis_id == analysis_id)).all()
    by_id = {row.feature_id: row for row in rows}
    assert by_id["HA-Q1"].return_period_years == 100 and by_id["HA-Q1"].section == "flood"
    assert by_id["HA-EDGE"].touches_boundary is True
    assert by_id["N2K-1"].intersection_pct == 60.0 and by_id["N2K-1"].name == "Dolina Testowa"
    assert saved_sections == body["risk_sections"]
    assert {"ISOK", "GDOS"} <= {record.source_name for record in records}

    # Odczyt historyczny i cache: identyczne sekcje i obiekty.
    reread = _reread(analysis_id)
    assert reread["risk_sections"] == body["risk_sections"]
    assert reread["risks"] == body["risks"]
    cached = _post(identifier, FLOOD, NATURE)
    assert cached["calls"] == 0
    assert cached["body"]["analysis_id"] == analysis_id
    assert cached["body"]["risks"] == body["risks"]
    assert cached["body"]["risk_sections"] == body["risk_sections"]

    # PDF z pól strukturalnych.
    report = client.get(f"/report/{analysis_id}?access_token={make_analysis_token(analysis_id)}")
    assert report.status_code == 200
    text = _pdf_text(report.content)
    for expected in (
        "Zagrożenie powodziowe (ISOK)",
        "Formy ochrony przyrody (GDOŚ)",
        "obiekty przecinają działkę",
        "100 lat",
        "500 lat",
        "styk granicy",
        "Dolina Testowa",
        "Łączne pokrycie działki",
        "10 000,00 m² (100,00%)",
        "suma mnogościowa, bez podwójnego liczenia",
        "HA-Q1",
    ):
        assert expected in text, expected
    _evidence("02-report.pdf", report.content)

    # Ewaluator: wyłącznie pola strukturalne, description bez znaczenia.
    tampered = copy.deepcopy(reread)
    for item in tampered["risks"]:
        item["description"] = "brak ryzyka 0% (tekst celowo mylący)"
    actual = sections_from_analyze_response(tampered)
    assert actual == sections_from_analyze_response(reread)
    assert actual["flood"]["values"]["relation"] == "intersection"
    expected_sections = {
        "flood": {
            "status": "available",
            "values": {
                "relation": "intersection",
                "features": [
                    {"class": Q1, "area": 4100.0, "share": 41.0},
                    {"class": Q02, "area": 10000.0, "share": 100.0},
                ],
            },
        },
        "nature": {"status": "available", "values": {"relation": "none", "features": []}},
    }
    metrics = calculate_metrics(
        [{"expected_sections": expected_sections, "actual_sections": actual, "status": "partial"}]
    )
    confusion = metrics["binary_conditions"]
    assert {key: confusion["flood_intersection"][key] for key in ("tp", "fp", "fn", "tn")} == {"tp": 1, "fp": 0, "fn": 0, "tn": 0}
    assert {key: confusion["nature_intersection"][key] for key in ("tp", "fp", "fn", "tn")} == {"tp": 0, "fp": 1, "fn": 0, "tn": 0}
    assert metrics["risk_area_mae"]["value"] == pytest.approx(50.0)
    assert metrics["risk_area_max_absolute_error"]["value"] == pytest.approx(100.0)
    assert metrics["risk_share_mae"]["value"] == pytest.approx(0.5)
    _evidence("03-evaluator-metrics.json", metrics)


def test_unavailable_sources_are_visible_with_provenance_not_as_no_risk() -> None:
    identifier = f"{_PREFIX}down_{uuid4().hex[:6]}"
    body = _post(identifier, httpx.ReadTimeout("ISOK"), httpx.ConnectError("GDOŚ"))["body"]

    sections = _sections(body)
    assert body["risks"] == []
    assert body["status"] == "partial"
    assert (sections["flood"]["status"], sections["flood"]["reason_code"]) == ("unavailable", "SERVICE_TIMEOUT")
    assert (sections["nature"]["status"], sections["nature"]["reason_code"]) == ("unavailable", "SERVICE_HTTP_ERROR")
    for section in sections.values():
        assert section["relation"] == "unknown" and section["feature_count"] is None
        assert section["source"]["fetched_at"] is not None
        assert section["source"]["manual_review_required"] is True
    reread = _reread(body["analysis_id"])
    assert reread["risk_sections"] == body["risk_sections"]
    with SessionLocal() as db:
        statuses = {
            record.source_name: record.response_status
            for record in db.scalars(select(SourceRecord).where(SourceRecord.analysis_id == body["analysis_id"]))
        }
    assert statuses["ISOK"] == "unavailable" and statuses["GDOS"] == "unavailable"

    # Ewaluator nie liczy awarii jako „brak ryzyka”.
    actual = sections_from_analyze_response(reread)
    assert actual["flood"]["status"] == "unknown" and actual["nature"]["status"] == "unknown"
    metrics = calculate_metrics(
        [
            {
                "expected_sections": {
                    "flood": {"status": "available", "values": {"relation": "none"}},
                    "nature": {"status": "available", "values": {"relation": "none"}},
                },
                "actual_sections": actual,
                "status": "partial",
            }
        ]
    )
    for name in ("flood_intersection", "nature_intersection"):
        assert sum(metrics["binary_conditions"][name][key] for key in ("tp", "fp", "fn", "tn")) == 0

    report = client.get(f"/report/{body['analysis_id']}?access_token={make_analysis_token(body['analysis_id'])}")
    text = _pdf_text(report.content)
    assert "źródło niedostępne" in text
    assert "NIE oznacza braku ryzyka" in text
    assert "SERVICE_TIMEOUT" in text
    _evidence("04-unavailable-response.json", body)
    _evidence("05-unavailable-report.pdf", report.content)
