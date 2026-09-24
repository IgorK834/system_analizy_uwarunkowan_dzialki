"""BK-106: scenariusz końcowy PostGIS → orkiestrator → API → HTML raportu.

Każdy test przechodzi przez rzeczywiste warstwy: wersjonowany import POG w
PostGIS, zapytania repozytorium, wspólną tabelę decyzyjną statusu, kontrakt
``PogResult`` i prezentację raportu. Transakcja jest wycofywana po teście.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from shapely import from_wkt
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.modules.imports.application.pog_import import run_pog_import
from app.modules.imports.domain.pog import PogActRecord
from app.modules.imports.infrastructure.artifacts import LocalArtifactStore
from app.modules.imports.infrastructure.repository import (
    SqlAlchemyImportRepository,
    active_pog_release,
    find_pog_intersections,
    last_confirmed_pog_status,
)
from app.schemas.analyze import AnalyzeResponse
from app.services.analysis_orchestrator import _analyze_pog_best_effort
from app.services.pog import PogDiscoveryResult, PogLayerSection
from app.services.report import _build_report_context, _render_report_html
from app.schemas.source import SourceMetadata
from app.shared.geometry import GeometryPayload
from app.shared.planning_status import inspire_status_uri
from tests.test_imports_pog import StaticPogReader, _source, feature, geom, release

pytestmark = pytest.mark.integration

PARCEL_WKT = "POLYGON((5 5,15 5,15 15,5 15,5 5))"
TERYT = "1261011"


@pytest.fixture
def session() -> Iterator[Session]:
    db = SessionLocal()
    # Otwarta transakcja sprawia, że publikacja używa SAVEPOINT i całość jest
    # wycofywana po teście — bez wpływu na aktywne wydania innych testów.
    db.execute(text("SELECT 1"))
    try:
        yield db
    finally:
        db.rollback()
        db.close()


def _publish(
    session: Session,
    tmp_path: Path,
    *,
    legal_status: str,
    with_features: bool = True,
    boundary: str | None = "POLYGON((0 0,100 0,100 100,0 100,0 0))",
    teryt: str = TERYT,
) -> tuple[str, int]:
    source_id = f"pog_status_{uuid4().hex[:10]}"
    repository = SqlAlchemyImportRepository(
        session, _source(source_id), LocalArtifactStore(tmp_path)
    )
    record = PogActRecord(
        act_identifier=f"pog-status-{uuid4().hex[:8]}",
        resolution_number="I/1/2026",
        resolution_date=None,
        teryt=teryt,
        name="POG testowy",
        legal_status=legal_status,
        raw_legal_status=inspire_status_uri(legal_status),  # type: ignore[arg-type]
        boundary=geom(boundary) if boundary else None,
        features=(
            (
                feature("planning_zone", "POLYGON((0 0,100 0,100 100,0 100,0 0))", SYMBOL="SJ-01"),
                feature("ouz", "POLYGON((0 0,40 0,40 40,0 40,0 0))"),
            )
            if with_features
            else ()
        ),
    )
    outcome = run_pog_import(
        StaticPogReader(record, content=uuid4().bytes),
        source_id,
        repository,
        release=release(source_id, label=f"{legal_status}-{uuid4().hex[:4]}"),
    )
    assert outcome.data_release_id is not None
    return source_id, outcome.data_release_id


async def _analyze(session: Session, source_id: str | None, *, discovery=None):
    pinned = active_pog_release(session, source_id=source_id) if source_id else None
    discover = (
        AsyncMock(side_effect=TimeoutError("RU timeout"))
        if discovery is None
        else AsyncMock(return_value=discovery)
    )
    with (
        patch("app.services.analysis_orchestrator.active_pog_release", return_value=pinned),
        patch("app.services.analysis_orchestrator.discover_pog", new=discover),
    ):
        return await _analyze_pog_best_effort(from_wkt(PARCEL_WKT), TERYT, session)


def _report_pog_html(pog) -> str:
    response = AnalyzeResponse(
        status="partial",
        analyzed_at=datetime(2026, 9, 24, tzinfo=timezone.utc),
        mpzp_zones=[],
        pog=pog,
        infrastructure=[],
        risks=[],
        warnings=[],
        sources=[],
    )
    html = _render_report_html(_build_report_context(response, None, None))
    start = html.index("Plan Ogólny Gminy (POG)")
    return html[start : html.index("</section>", start)]


@pytest.mark.asyncio
async def test_project_release_is_available_but_never_in_force(
    session: Session, tmp_path: Path
) -> None:
    source_id, release_id = _publish(session, tmp_path, legal_status="project")

    pog, _ouz, warnings, sources = await _analyze(session, source_id)

    assert (pog.legal_status, pog.coverage_status, pog.data_availability) == (
        "project", "available", "current",
    )
    assert [zone.symbol for zone in pog.zones] == ["SJ-01"]
    assert pog.manual_review_required is True
    assert sources[0].data_release_id == release_id
    # Projekt nie jest zwracany przez zapytanie aktów wiążących.
    assert find_pog_intersections(
        session, GeometryPayload(PARCEL_WKT), data_release_id=release_id
    ) == []
    section = _report_pog_html(pog).lower()
    assert "projekt aktu" in section
    assert "obowiązuj" not in section


@pytest.mark.asyncio
async def test_binding_act_without_spatial_data_is_not_missing_plan(
    session: Session, tmp_path: Path
) -> None:
    source_id, _release_id = _publish(
        session, tmp_path, legal_status="binding", with_features=False, boundary=None
    )

    pog, _ouz, warnings, _sources = await _analyze(session, source_id)

    assert (pog.legal_status, pog.coverage_status) == ("binding", "act_without_spatial_data")
    assert pog.zones == []
    assert pog.legal_status_evidence is not None
    assert pog.legal_status_evidence.raw_value == inspire_status_uri("binding")
    assert any(w.code == "POG_ACT_WITHOUT_SPATIAL_DATA" for w in warnings)
    section = _report_pog_html(pog)
    assert "Brak geometrii lub pusta odpowiedź usługi nie oznacza braku planu." in section
    assert "brak planu" not in section.lower().replace("braku planu", "")


@pytest.mark.asyncio
async def test_superseded_release_is_reported_as_superseded(
    session: Session, tmp_path: Path
) -> None:
    source_id, _release_id = _publish(session, tmp_path, legal_status="superseded")

    pog, _ouz, _warnings, _sources = await _analyze(session, source_id)

    assert pog.legal_status == "superseded"
    assert pog.coverage_status == "available"
    assert "nieaktualny" in _report_pog_html(pog)


@pytest.mark.asyncio
async def test_empty_ru_response_is_unknown_not_no_act(session: Session) -> None:
    source = SourceMetadata(
        source_name="REJESTR_URBANISTYCZNY_WMS",
        source_url="https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe/wms-pog/wms",
        fetched_at=datetime.now(timezone.utc),
        confidence=0.3,
        manual_review_required=True,
    )
    empty = PogDiscoveryResult(
        legal_status="unknown",
        uchwala_nr=None,
        uchwala_date=None,
        links=[],
        planning_act=PogLayerSection("planning_act", "empty"),
        downtown_area=PogLayerSection("downtown_area", "empty"),
        ouz=PogLayerSection("ouz", "empty"),
        planning_zones=PogLayerSection("planning_zones", "empty"),
        is_discovery_only=True,
        source_metadata=source,
        source_responded=True,
    )

    pog, _ouz, warnings, _sources = await _analyze(session, None, discovery=empty)

    assert (pog.legal_status, pog.coverage_status, pog.data_availability) == (
        "unknown", "unknown", "current",
    )
    assert any(w.code == "POG_EMPTY_RESPONSE_NOT_ABSENCE" for w in warnings)
    section = _report_pog_html(pog)
    assert "obowiązuj" not in section.lower()
    assert "nie oznacza braku planu" in section


@pytest.mark.asyncio
async def test_timeout_keeps_last_confirmed_status_with_date_as_stale(
    session: Session, tmp_path: Path
) -> None:
    source_id, release_id = _publish(session, tmp_path, legal_status="binding")
    fetched_at = session.execute(
        text(
            "SELECT DISTINCT sa.fetched_at FROM source_artifacts sa "
            "JOIN planning_act_versions pav ON pav.source_artifact_id = sa.id "
            "WHERE pav.data_release_id = :id"
        ),
        {"id": release_id},
    ).scalar_one()

    with patch(
        "app.services.analysis_orchestrator.last_confirmed_pog_status",
        new=partial(last_confirmed_pog_status, source_id=source_id),
    ):
        # Lokalne wydanie nie jest przypięte (np. przełączane), a RU nie odpowiada.
        pog, _ouz, warnings, _sources = await _analyze(session, None)

    assert (pog.legal_status, pog.data_availability) == ("binding", "stale")
    assert pog.coverage_status == "unknown"
    assert pog.status_confirmed_at == fetched_at
    assert pog.legal_status_evidence is not None
    assert f"data_release:{release_id}" in (pog.legal_status_evidence.reference or "")
    assert any(w.code == "POG_STATUS_STALE" for w in warnings)
    assert "ostatnia potwierdzona wartość" in _report_pog_html(pog)


@pytest.mark.asyncio
async def test_timeout_without_confirmed_value_is_unknown_unavailable(
    session: Session,
) -> None:
    with patch(
        "app.services.analysis_orchestrator.last_confirmed_pog_status",
        return_value=None,
    ):
        pog, _ouz, warnings, _sources = await _analyze(session, None)

    assert (pog.legal_status, pog.coverage_status, pog.data_availability) == (
        "unknown", "unknown", "unavailable",
    )
    assert pog.status_confirmed_at is None
    assert any(w.code == "POG_SOURCE_UNAVAILABLE" for w in warnings)
