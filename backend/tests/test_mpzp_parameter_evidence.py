"""BK-203 (i scenariusz końcowy BK-202): strefa z wektora → cytowalny parametr uchwały.

Scenariusz końcowy uruchamia ``run_analysis`` na PostGIS: działka o kształcie
L (1000 m², centroid w strefie mniejszej) leży w dwóch wydzieleniach tej samej
uchwały. Symbole z geometrii trafiają do realnego parsera tekstowego PDF
(pdfplumber), parametry są przypisane do właściwych stref z evidence, a
zapisany snapshot, odczyt z bazy, cache i HTML raportu zachowują wartości
surowe, stronę, fragment, jednostkę redakcyjną i hash dokumentu.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from shapely import from_wkt
from sqlalchemy import delete, select, text

from app.db.session import SessionLocal
from app.models.analysis import Analysis
from app.models.mpzp_parameter import MpzpParameter
from app.models.mpzp_zone import MpzpZone
from app.models.parcel import Parcel
from app.models.pog_data import PogData
from app.models.source_record import SourceRecord
from app.schemas.analyze import MpzpZoneResult, ParcelIdAnalyzeRequest
from app.schemas.source import SourceMetadata
from app.services.analysis_orchestrator import (
    _analyze_mpzp_vector_zones,
    _result_status,
    run_analysis,
)
from app.services.initiation import ParcelLookupResult
from app.services.mpzp import MpzpDiscoveryResult
from app.services.mpzp_parser import MPZP_PARSER_VERSION, parse_mpzp_document
from app.services.mpzp_zones import apply_parser_zone
from app.services.persistence import add_mpzp_zone_snapshot, build_analyze_response_from_analysis
from app.services.report import _build_report_context, _render_report_html
from tests.mpzp_fixtures import (
    TranscribingOcr,
    act,
    document_blob,
    import_acts,
    origin,
    rect,
    scanned_pdf,
    text_pdf,
    zone,
)
from tests.test_analysis_orchestrator import (
    _empty_context,
    _found_discovery,
    _unknown_pog_discovery,
)

_PREFIX = "BK203_TEST_"
NOW = datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc)


def _cleanup() -> None:
    with SessionLocal() as db:
        parcel_ids = select(Parcel.id).where(Parcel.parcel_identifier.like(f"{_PREFIX}%"))
        analysis_ids = select(Analysis.id).where(Analysis.parcel_id.in_(parcel_ids))
        zone_ids = select(MpzpZone.id).where(MpzpZone.analysis_id.in_(analysis_ids))
        db.execute(delete(MpzpParameter).where(MpzpParameter.mpzp_zone_id.in_(zone_ids)))
        for model in (SourceRecord, PogData, MpzpZone):
            db.execute(delete(model).where(model.analysis_id.in_(analysis_ids)))
        from app.models.infrastructure import Infrastructure
        from app.models.risk import Risk

        for model in (Infrastructure, Risk):
            db.execute(delete(model).where(model.analysis_id.in_(analysis_ids)))
        db.execute(delete(Analysis).where(Analysis.id.in_(analysis_ids)))
        db.execute(delete(Parcel).where(Parcel.id.in_(parcel_ids)))
        db.commit()


@pytest.fixture(autouse=True)
def cleanup_rows():
    _cleanup()
    yield
    _cleanup()


def _source() -> SourceMetadata:
    return SourceMetadata(
        source_name="MPZP_WEKTOR:test", confidence=0.95, manual_review_required=False
    )


# --- Parser: przypisanie do stref, konflikty, OCR, brak scalania symboli -------


@pytest.mark.asyncio
async def test_two_zones_of_same_resolution_get_their_own_heights_with_evidence() -> None:
    pdf = text_pdf()
    result = await parse_mpzp_document(document_blob(pdf), ["1MN", "2MN"])
    zones = {item.zone_symbol: item for item in result.zones}
    heights = {
        symbol: [p for p in zones[symbol].parameters if p.name == "max_building_height_m"]
        for symbol in ("1MN", "2MN")
    }
    assert [p.normalized_value for p in heights["1MN"]] == [9.0]
    assert [p.normalized_value for p in heights["2MN"]] == [12.0]
    first = heights["1MN"][0]
    assert first.raw_value == "9 m" and first.unit == "m"
    assert first.page_number == 2 and first.segment_id
    assert heights["2MN"][0].segment_id != first.segment_id
    assert first.document_sha256 == hashlib.sha256(pdf).hexdigest()
    assert (first.parser_version, first.extraction_method) == (MPZP_PARSER_VERSION, "pdf_text")
    assert "9 m" in (first.source_text or "")


async def _mapped_height_zone(pages: tuple[str, ...]):
    result = await parse_mpzp_document(document_blob(text_pdf(pages)), ["1MN"])
    base = MpzpZoneResult(
        zone_symbol="1MN", intersection_area_sqm=1000, intersection_pct=100,
        is_dominant=True, source=_source(), assignment_method="vector_intersection",
    )
    return apply_parser_zone(base, result.zones[0])


@pytest.mark.asyncio
async def test_conflicting_values_are_kept_and_not_resolved() -> None:
    # Ta sama przesłanka (zabudowa w strefie, bez warunku) i dwie różne wartości = prawdziwy konflikt.
    mapped, _skipped, conflicts = await _mapped_height_zone((
        "§ 5. Dla terenu oznaczonego symbolem 1MN ustala się:\n"
        "1) maksymalna wysokość zabudowy: 9 m;\n"
        "2) maksymalna wysokość zabudowy: 6 m.",
    ))
    heights = [p for p in mapped.parameters if p.name == "max_building_height_m"]
    assert sorted(p.normalized_value for p in heights) == [6.0, 9.0]
    assert len({p.conflict_group_id for p in heights}) == 1 and heights[0].conflict_group_id
    assert {p.value_kind for p in heights} == {"conflict"}
    assert all(p.manual_review_required for p in heights)
    assert conflicts == ["max_building_height_m"]
    assert mapped.max_building_height_m is None  # brak automatycznego wyboru
    assert mapped.manual_review_required is True
    assert _result_status(
        context=_empty_context(), mpzp_zones=[mapped], pog=None, sources=[]
    ) == "partial"


@pytest.mark.asyncio
async def test_conditional_values_are_not_a_conflict() -> None:
    # PV3-08: wartość z warunkiem (inny typ budynku) obok bezwarunkowej to nie sprzeczność.
    mapped, _skipped, conflicts = await _mapped_height_zone((
        "§ 5. Dla terenu oznaczonego symbolem 1MN ustala się:\n"
        "1) maksymalna wysokość zabudowy: 9 m;\n"
        "2) dla budynków gospodarczych maksymalna wysokość zabudowy: 6 m.",
    ))
    heights = {p.normalized_value: p for p in mapped.parameters if p.name == "max_building_height_m"}
    assert set(heights) == {6.0, 9.0}
    assert heights[9.0].value_kind == "unconditional" and heights[9.0].conditions == []
    assert heights[6.0].value_kind == "conditional"
    assert [(c.kind, c.label) for c in heights[6.0].conditions] == [("building_type", "budynków gospodarczych")]
    assert all(p.conflict_group_id is None for p in heights.values())
    assert conflicts == []
    assert mapped.max_building_height_m == 9.0  # jedyna wartość bezwarunkowa


@pytest.mark.asyncio
async def test_ocr_gives_same_value_with_lower_confidence() -> None:
    text_result = await parse_mpzp_document(document_blob(text_pdf()), ["1MN", "2MN"])
    ocr = TranscribingOcr()
    ocr_result = await parse_mpzp_document(document_blob(scanned_pdf()), ["1MN", "2MN"], ocr)
    assert ocr.calls == 1

    def values(result):
        return {
            (zone.zone_symbol, p.name): p for zone in result.zones for p in zone.parameters
        }

    text_values, ocr_values = values(text_result), values(ocr_result)
    assert set(text_values) == set(ocr_values)
    for key, text_parameter in text_values.items():
        ocr_parameter = ocr_values[key]
        assert ocr_parameter.normalized_value == text_parameter.normalized_value
        assert ocr_parameter.extraction_method == "ocr"
        # PV3-09: obniżenie wynika z cech dowodu (metoda ekstrakcji), nie ze stałego mnożnika.
        assert ocr_parameter.confidence < text_parameter.confidence
        assert ocr_parameter.confidence_features["extraction_method"] == "ocr"
        assert text_parameter.confidence_features["extraction_method"] == "pdf_text"


@pytest.mark.asyncio
async def test_similar_symbols_are_not_merged_by_substring() -> None:
    pages = (
        "§ 5. Dla terenu oznaczonego symbolem MN.1 ustala się:\n"
        "1) maksymalna wysokość zabudowy: 11 m.\n"
        "§ 6. Dla terenu oznaczonego symbolem MN ustala się:\n"
        "1) maksymalna wysokość zabudowy: 8 m.\n"
        "§ 7. Dla terenu oznaczonego symbolem MW/U ustala się:\n"
        "1) maksymalna wysokość zabudowy: 20 m.",
    )
    result = await parse_mpzp_document(document_blob(text_pdf(pages)), ["MN", "MN.1", "U"])
    heights = {
        zone.zone_symbol: sorted(p.normalized_value for p in zone.parameters if p.name == "max_building_height_m")
        for zone in result.zones
    }
    assert heights == {"MN": [8.0], "MN.1": [11.0], "U": []}


def test_parser_symbol_must_match_zone_exactly() -> None:
    from app.schemas.mpzp import MpzpZoneResult as ParserZone

    base = MpzpZoneResult(
        zone_symbol="MN", intersection_area_sqm=1, intersection_pct=100, is_dominant=True, source=_source()
    )
    with pytest.raises(ValueError, match="nie jest symbolem strefy"):
        apply_parser_zone(base, ParserZone(zone_symbol="MN.1"))


# --- Scenariusz końcowy: PostGIS → run_analysis → DB → cache → raport ----------


def _lookup(identifier: str, wkt: str) -> ParcelLookupResult:
    return ParcelLookupResult(
        parcel_identifier=identifier,
        wkt=wkt,
        teryt="126101",
        source_metadata=SourceMetadata(
            source_name="ULDK", source_url="https://uldk.example.test", fetched_at=NOW,
            confidence=0.95, manual_review_required=False,
        ),
    )


def _discovery_without_document() -> MpzpDiscoveryResult:
    return MpzpDiscoveryResult(
        plan_id=None,
        candidate_zone_symbols=[],
        uchwala_url=None,
        brak_wektorow=False,
        status="no_match",
        is_discovery_only=True,
        source_metadata=SourceMetadata(source_name="KIMPZP", confidence=0.3, manual_review_required=True),
    )


async def _run(identifier: str, wkt: str, fetch: AsyncMock, discovery: MpzpDiscoveryResult, *, force: bool = False):
    with (
        patch("app.services.analysis_orchestrator.resolve_parcel", new=AsyncMock(return_value=_lookup(identifier, wkt))),
        patch("app.services.analysis_orchestrator.analyze_context", new=AsyncMock(return_value=_empty_context())),
        patch("app.services.analysis_orchestrator.discover_mpzp", new=AsyncMock(return_value=discovery)),
        patch("app.services.analysis_orchestrator.discover_pog", new=AsyncMock(return_value=_unknown_pog_discovery())),
        patch("app.services.analysis_orchestrator.fetch_mpzp_document", new=fetch),
        patch(
            "app.services.analysis_orchestrator.check_kiut_coverage_for_geometry",
            new=AsyncMock(side_effect=RuntimeError("offline")),
        ),
        SessionLocal() as db,
    ):
        return await run_analysis(ParcelIdAnalyzeRequest(method="parcel_id", parcel_identifier=identifier), db, force_refresh=force)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_end_to_end_vector_zones_to_cited_parameters(tmp_path: Path) -> None:
    x, y = origin()
    identifier = f"plan-{uuid4().hex[:8]}"
    document_url = f"https://bip.example.gov.pl/{identifier}.pdf"
    source_id = f"mpzp_{uuid4().hex[:8]}"
    zones = (
        zone("1MN", rect(x, y, x + 60, y + 100), "single_family_housing"),
        zone("2MN", rect(x + 60, y, x + 200, y + 100), "single_family_housing"),
    )
    with SessionLocal() as db:
        first = import_acts(db, tmp_path, source_id, act(identifier, rect(x, y, x + 200, y + 100), zones, document_url=document_url))
        db.commit()
    parcel = from_wkt(rect(x + 40, y, x + 60, y + 30)).union(from_wkt(rect(x + 60, y, x + 100, y + 10)))
    assert parcel.area == pytest.approx(1000.0) and parcel.centroid.x - x > 60
    pdf = text_pdf()
    fetch = AsyncMock(return_value=document_blob(pdf, document_url))
    parcel_identifier = f"{_PREFIX}{uuid4().hex[:6]}"

    response = await _run(parcel_identifier, parcel.wkt, fetch, _discovery_without_document())

    fetch.assert_awaited_once_with(document_url)
    assert response.manual_zone_required is False
    by_symbol = {z.zone_symbol: z for z in response.mpzp_zones}
    assert [(z.zone_symbol, round(z.intersection_pct, 6)) for z in response.mpzp_zones] == [("1MN", 60.0), ("2MN", 40.0)]
    assert by_symbol["1MN"].max_building_height_m == 9.0
    assert by_symbol["2MN"].max_building_height_m == 12.0
    assert by_symbol["2MN"].min_biologically_active_pct == 30.0
    height = next(p for p in by_symbol["2MN"].parameters if p.name == "max_building_height_m")
    assert height.raw_value == "12 m" and height.page_number == 2
    assert height.document_sha256 == hashlib.sha256(pdf).hexdigest()
    assert height.legal_unit_id is not None and height.document_version_id is not None
    assert height.conflict_group_id is None
    assert {z.assignment_method for z in response.mpzp_zones} == {"vector_intersection"}
    assert {z.data_release_id for z in response.mpzp_zones} == {first.data_release_id}
    assert any(s.source_name == f"MPZP_WEKTOR:{source_id}" for s in response.sources)

    # Odczyt z bazy: pełny snapshot, wiersze evidence i wartości surowe.
    with SessionLocal() as db:
        saved = db.get(Analysis, response.analysis_id)
        rows = db.scalars(
            select(MpzpParameter).join(MpzpZone).where(
                MpzpZone.analysis_id == response.analysis_id,
                MpzpParameter.parameter_name == "max_building_height_m",
            )
        ).all()
        assert sorted(row.raw_value for row in rows) == ["12 m", "9 m"]
        assert all(row.document_sha256 == height.document_sha256 and row.legal_unit_id for row in rows)
        assert all(row.parser_version == MPZP_PARSER_VERSION for row in rows)
        zone_rows = db.scalars(select(MpzpZone).where(MpzpZone.analysis_id == response.analysis_id)).all()
        assert {row.zone_identifier for row in zone_rows} == {z.zone_id for z in response.mpzp_zones}
        rebuilt = build_analyze_response_from_analysis(saved, db)
    assert [z.model_dump() for z in rebuilt.mpzp_zones] == [z.model_dump() for z in response.mpzp_zones]

    # Raport wskazuje stronę, fragment i hash dokumentu dla każdej wartości.
    report = _render_report_html(_build_report_context(rebuilt))
    assert "str. 2" in report and height.document_sha256 in report
    assert "maksymalna wysokość zabudowy: 12 m" in report
    assert "przecięcie z wektorem wydzieleń" in report
    assert f"jednostka #{height.legal_unit_id}" in report

    # Przełączenie wydania nie zmienia starego snapshotu, a cache jest unieważniany.
    moved = (
        zone("1MN", rect(x, y, x + 80, y + 100), "single_family_housing"),
        zone("2MN", rect(x + 80, y, x + 200, y + 100), "single_family_housing"),
    )
    with SessionLocal() as db:
        second = import_acts(db, tmp_path, source_id, act(identifier, rect(x, y, x + 200, y + 100), moved, document_url=document_url), label="v2")
        db.commit()
    assert second.data_release_id != first.data_release_id
    with SessionLocal() as db:
        old = build_analyze_response_from_analysis(db.get(Analysis, response.analysis_id), db)
    assert [round(z.intersection_pct) for z in old.mpzp_zones] == [60, 40]
    assert {z.data_release_id for z in old.mpzp_zones} == {first.data_release_id}

    fresh = await _run(parcel_identifier, parcel.wkt, AsyncMock(return_value=document_blob(pdf, document_url)), _discovery_without_document())
    assert fresh.analysis_id != response.analysis_id  # nowy zestaw wydań → brak trafienia cache
    assert [(z.zone_symbol, round(z.intersection_pct)) for z in fresh.mpzp_zones] == [("1MN", 80), ("2MN", 20)]
    assert {z.data_release_id for z in fresh.mpzp_zones} == {second.data_release_id}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_without_vector_fallback_works_with_lowered_confidence() -> None:
    x, y = origin()
    parcel_identifier = f"{_PREFIX}{uuid4().hex[:6]}"
    pdf = text_pdf(
        ("§ 3. Dla terenu oznaczonego symbolem MN ustala się:\n1) maksymalna wysokość zabudowy: 9 m.",)
    )
    response = await _run(
        parcel_identifier,
        rect(x, y, x + 20, y + 20),
        AsyncMock(return_value=document_blob(pdf)),
        _found_discovery(),
    )
    (only,) = response.mpzp_zones
    assert only.assignment_method == "document_candidate"
    assert only.zone_id is None and only.source.confidence <= 0.5
    assert only.manual_review_required is True
    assert only.max_building_height_m == 9.0
    assert any(w.code == "MPZP_VECTOR_UNAVAILABLE" for w in response.warnings)
    assert response.status == "partial"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_vector_act_without_document_keeps_parameters_null(tmp_path: Path) -> None:
    x, y = origin()
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
        import_acts(
            db, tmp_path, f"mpzp_{uuid4().hex[:8]}",
            act(f"plan-{uuid4().hex[:8]}", rect(x, y, x + 100, y + 100), (zone("1MN", rect(x, y, x + 100, y + 100)),)),
        )
        from app.services.analysis_orchestrator import _assess_mpzp_vectors_safely

        vector, _ = _assess_mpzp_vectors_safely(db, from_wkt(rect(x + 10, y + 10, x + 20, y + 20)), datetime.now(timezone.utc))
        assert vector is not None
        zones, warnings, sources, status = await _analyze_mpzp_vector_zones(
            vector, _discovery_without_document(), "P", db
        )
        db.rollback()
    (only,) = zones
    assert only.max_building_height_m is None and only.parameters == []
    assert any(w.code == "MPZP_ACT_DOCUMENT_MISSING" for w in warnings)
    assert status is None and sources


def test_snapshot_rows_keep_raw_value_and_evidence() -> None:
    from app.schemas.analyze import MpzpParameterEvidence

    parameter = MpzpParameterEvidence(
        name="max_building_height_m", normalized_value=9.0, raw_value="9,0 m", unit="m",
        evidence_text="maksymalna wysokość zabudowy: 9,0 m", page_number=4, segment_id="seg-0004",
        legal_unit_id=None, document_sha256="e" * 64, document_version_id=None,
        parser_version=MPZP_PARSER_VERSION, extraction_method="ocr", confidence=0.6,
        conflict_group_id="v:1MN:max_building_height_m", manual_review_required=True,
    )
    zone_result = MpzpZoneResult(
        zone_symbol="1MN", intersection_area_sqm=100, intersection_pct=100, is_dominant=True,
        source=_source(), parameters=[parameter], assignment_method="vector_intersection",
        zone_id="plan:1MN:x",
    )
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
        parcel = Parcel(parcel_identifier=f"{_PREFIX}{uuid4().hex[:6]}", geometry="SRID=2180;MULTIPOLYGON(((0 0,1 0,1 1,0 1,0 0)))")
        db.add(parcel)
        db.flush()
        analysis = Analysis(parcel_id=parcel.id, status="partial")
        db.add(analysis)
        db.flush()
        record = add_mpzp_zone_snapshot(db, analysis.id, zone_result)
        db.flush()
        (row,) = db.scalars(select(MpzpParameter).where(MpzpParameter.mpzp_zone_id == record.id)).all()
        assert (row.raw_value, row.segment_id, row.extraction_method) == ("9,0 m", "seg-0004", "ocr")
        assert row.conflict_group_id == parameter.conflict_group_id
        assert record.result_snapshot["parameters"][0]["raw_value"] == "9,0 m"
        db.rollback()
