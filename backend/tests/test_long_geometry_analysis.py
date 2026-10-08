"""AU-001: działki o dużej liczbie wierzchołków przechodzą analizę, zapis i raport PDF.

Test na żywych usługach (2026-10-05) wykazał, że 8 z 30 działek korpusu referencyjnego kończy się HTTP 500:
adres zapytania NMT ``GetMinMaxByPolygon`` zawiera cały wielokąt (do kilku tysięcy znaków), a
``source_records.source_url`` miało ``VARCHAR(1000)`` → ``StringDataRightTruncation`` w ``save_analysis``.

Geometrie są zamrożonymi, rzeczywistymi wielokątami z korpusu (``fixtures/parcels/long_geometry``), a
odpowiedzi NMT — rzeczywistymi odpowiedziami usługi na dokładnie ten WKT. ``run_analysis`` używa realnego
adaptera NMT, orkiestratora i PostGIS; reszta źródeł jest zamrożona jak w ``test_terrain_e2e``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import shapely
from shapely.geometry import shape
from sqlalchemy import select

from app.core.access_control import make_analysis_token
from app.core.settings import settings
from app.db.session import SessionLocal
from app.models.analysis import Analysis
from app.models.source_record import SourceRecord
from tests import test_terrain_e2e as base

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parent / "fixtures"
LONG_GEOMETRY = FIXTURES / "parcels" / "long_geometry"
CORPUS = FIXTURES / "reference_corpus"
_SOURCE_URL_LIMIT = 1000  # dawny limit ``source_records.source_url``

# (case_id, identyfikator ULDK, wierzchołki, (Hmin, Hmax) albo None, gdy NMT odmawia pomiaru)
FAILING_PARCELS = [
    ("real-001-146510-8-0502-1-3", "146510_8.0502.1/3", 76, (109.1, 113.7)),
    ("real-005-126105-9-0001-580-4", "126105_9.0001.580/4", 134, (209.4, 212.0)),
    ("real-007-126105-9-0001-540-15", "126105_9.0001.540/15", 164, (203.6, 210.8)),
    ("real-013-026201-1-0009-1319-4", "026201_1.0009.1319/4", 35, (121.0, 121.7)),
    ("real-014-026201-1-0010-410-1", "026201_1.0010.410/1", 28, (118.8, 120.8)),
    ("real-030-026201-1-0009-578-2", "026201_1.0009.578/2", 35, (118.5, 121.0)),
    ("real-017-281603-4-0001-496-5", "281603_4.0001.496/5", 87, (117.2, 118.3)),
    # Rzeczywista odpowiedź NMT: poligon > 100 000 m² jest odrzucany przez usługę ze statusem HTTP 200.
    ("real-025-281603-4-0001-431-66", "281603_4.0001.431/66", 98, None),
]
THE_TWO_FROM_THE_ACCEPTANCE_CRITERIA = {"real-001-146510-8-0502-1-3", "real-005-126105-9-0001-580-4"}


@pytest.fixture(autouse=True)
def cleanup_rows():
    base._cleanup()
    yield
    base._cleanup()


def _wkt(case: str) -> str:
    return (LONG_GEOMETRY / f"{case}.wkt").read_text(encoding="utf-8").strip()


def _nmt(case: str) -> str:
    return (LONG_GEOMETRY / f"{case}.nmt.txt").read_text(encoding="utf-8")


def _old_style_url(wkt: str) -> str:
    """Adres, który do 2026-10-05 trafiał do ``source_url`` (pełny wielokąt w zapytaniu)."""
    params = {"request": "GetMinMaxByPolygon", "polygon": wkt}
    return str(httpx.URL(settings.nmt_base_url, params=params))


def test_frozen_geometries_equal_the_reference_corpus_and_reproduce_the_failing_condition() -> None:
    manifest = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
    ids = {case["case_id"]: case["parcel_identifier"] for case in manifest["cases"]}
    for case, identifier, vertices, _ in FAILING_PARCELS:
        assert ids[case] == identifier
        corpus = json.loads(
            (CORPUS / "artifacts" / "parcels" / f"{case}.geojson").read_text(encoding="utf-8")
        )
        frozen = shapely.from_wkt(_wkt(case))
        assert frozen.equals_exact(shape(corpus["geometry"]), 1e-6), case
        assert shapely.get_num_coordinates(frozen) == vertices
        # Warunek awarii sprzed poprawki: adres z wielokątem nie mieści się w VARCHAR(1000).
        assert len(_old_style_url(_wkt(case))) > _SOURCE_URL_LIMIT, case


def test_exactly_the_eight_audit_parcels_of_the_30_corpus_parcels_exceeded_the_old_limit() -> None:
    """Audyt 2026-10-05: Warszawa 1/4, Kraków 2/4, Legnica 3/5, Pisz 2/5 — razem 8 z 30."""
    manifest = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
    over = {}
    for case in manifest["cases"]:
        corpus = json.loads(
            (CORPUS / "artifacts" / "parcels" / f"{case['case_id']}.geojson").read_text(encoding="utf-8")
        )
        if len(_old_style_url(shape(corpus["geometry"]).wkt)) > _SOURCE_URL_LIMIT:
            over[case["case_id"]] = case["municipality"]

    assert len(manifest["cases"]) == 30
    assert set(over) == {case for case, *_ in FAILING_PARCELS}
    assert sorted(over.values()) == sorted(
        ["Warszawa"] + ["Kraków"] * 2 + ["Legnica"] * 3 + ["Pisz"] * 2
    )


@pytest.mark.parametrize(
    ("case", "identifier", "vertices", "heights"),
    FAILING_PARCELS,
    ids=[identifier for _, identifier, _, _ in FAILING_PARCELS],
)
def test_long_geometry_parcel_survives_analysis_save_reread_cache_and_pdf(
    case: str, identifier: str, vertices: int, heights: tuple[float, float] | None
) -> None:
    stored_identifier = f"{base._PREFIX}{identifier}"
    wkt = _wkt(case)

    response, nmt_calls = base._post_analyze(stored_identifier, wkt, _nmt(case))

    # API: bez HTTP 500, z pełnym wynikiem; NMT dostał pełny wielokąt dokładnie raz.
    assert response.status_code == 200, response.text
    assert nmt_calls == 1
    body = response.json()
    analysis_id = body["analysis_id"]
    terrain = body["terrain"]
    if heights is not None:
        assert terrain["status"] == "available"
        assert (terrain["min_height_m"], terrain["max_height_m"]) == heights
    else:
        # Odmowa usługi nie jest „brakiem deniwelacji”: sekcja jest niedostępna z kodem przyczyny.
        assert terrain["status"] == "unavailable"
        assert terrain["reason_code"] == "SERVICE_REPORTED_ERROR"
        assert terrain["min_height_m"] is None and terrain["height_difference_m"] is None

    # Adres źródła niesie skrót wielokąta i liczbę wierzchołków, nie sam wielokąt.
    source_url = terrain["source"]["source_url"]
    assert len(source_url) < 300
    query = parse_qs(urlsplit(source_url).query)
    assert query["vertex_count"] == [str(vertices)]
    assert re.fullmatch(r"[0-9a-f]{64}", query["polygon_sha256"][0])
    assert "polygon" not in query

    # DB: zapis przeszedł, a rekord źródła ma ten sam adres.
    with SessionLocal() as db:
        saved = db.get(Analysis, analysis_id)
        assert saved is not None and saved.terrain == terrain
        urls = {
            record.source_name: record.source_url
            for record in db.scalars(select(SourceRecord).where(SourceRecord.analysis_id == analysis_id))
        }
    assert urls["NMT"] == source_url

    # Odczyt historyczny i cache.
    assert base._reread(analysis_id)["terrain"] == terrain
    cached, cached_nmt_calls = base._post_analyze(stored_identifier, wkt, _nmt(case))
    assert cached.status_code == 200 and cached_nmt_calls == 0
    assert cached.json()["analysis_id"] == analysis_id

    # Raport PDF z zapisanego snapshotu (AC: „do zapisu i odczytu raportu PDF”).
    report = base.client.get(f"/report/{analysis_id}?access_token={make_analysis_token(analysis_id)}")
    assert report.status_code == 200
    assert report.headers["content-type"].startswith("application/pdf") and report.content[:4] == b"%PDF"
    pdf_text = base._pdf_text(report.content)
    assert "6. Teren (NMT)" in pdf_text
    if heights is not None:
        # Raport pomija zbędne zero po przecinku (``212 m``, ale ``113,7 m``).
        low, high = (f"{value:g}".replace(".", ",") for value in heights)
        assert re.search(rf"Najniższa wysokość \(Hmin\) {re.escape(low)}(?:,0)? m", pdf_text)
        assert re.search(rf"Najwyższa wysokość \(Hmax\) {re.escape(high)}(?:,0)? m", pdf_text)


@pytest.mark.parametrize("case", sorted(THE_TWO_FROM_THE_ACCEPTANCE_CRITERIA))
def test_the_real_uldk_response_is_the_geometry_the_analysis_uses(case: str) -> None:
    """Zamrożona odpowiedź ULDK ma ten sam wielokąt, co WKT wysyłany do NMT."""
    from app.services.uldk import _parse_uldk_response

    raw = (LONG_GEOMETRY / f"{case}.uldk.txt").read_text(encoding="utf-8")
    parsed = _parse_uldk_response(raw, case)

    assert shapely.from_wkt(parsed.geometry_wkt).equals_exact(shapely.from_wkt(_wkt(case)), 1e-6)
    assert parsed.parcel_identifier == next(i for c, i, _, _ in FAILING_PARCELS if c == case)
