"""BK-107: odtwarzalny łańcuch cecha → akt/wersja → metadane CSW → dokument + SHA.

Scenariusz końcowy używa realnej próbki Sopotu (226401) z zamrożonych
fixtur RU: WFS APP 3.0 (akt, dokument, strefa, OUZ, OZS) i CSW GetRecords
ISO 19139. CSW jest pobierane wspólnym klientem OGC (BK-102) na transporcie
mock — bez internetu. Dane przechodzą import → PostGIS → analizę → snapshot
w bazie → API → HTML raportu, a zapisany artefakt pozwala odtworzyć każdy SHA.
"""

from __future__ import annotations

import hashlib
import html
import io
import zipfile
from collections.abc import Iterator
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4
from xml.etree import ElementTree

import httpx
import pytest
from shapely import from_wkt
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.versioned import PogActMetadataRecord, PogFormalDocument, SourceArtifact
from app.modules.imports.application.common import ImportRelease
from app.modules.imports.application.pog_import import run_pog_import
from app.modules.imports.infrastructure.artifacts import LocalArtifactStore
from app.modules.imports.infrastructure.ogc_client import OgcClient, OgcClientConfig
from app.modules.imports.infrastructure.pog.reader import (
    WfsPogReader,
    canonical_record_sha256,
    parse_ru_app_feature_collection,
)
from app.modules.imports.infrastructure.repository import (
    SqlAlchemyImportRepository,
    active_pog_release,
)
from app.modules.imports.infrastructure.wfs import WfsResource
from app.schemas.analyze import AnalyzeResponse
from app.services.analysis_orchestrator import _analyze_pog_best_effort
from app.services.persistence import build_analyze_response_from_analysis, save_analysis
from app.services.report import _build_report_context, _render_report_html
from tests.test_imports_pog import _source

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parent / "fixtures" / "ru"
WFS_URL = "https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe/app-pog/wfs"
CSW_URL = "https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe/csw"
APP = "https://www.gov.pl/zagospodarowanieprzestrzenne/app"
ACT_ID = "PL.ZIPPZP.10011/226401-POG/1POG"
ACT_VERSION = "20260819T010000"
ZONE_ID = "PL.ZIPPZP.10011/226401-POG/1POG-100SU"
POG_RECORD_ID = "9223a9d0-e8b7-453b-b7bc-000e5e4d4d87"
LAYERS = (
    ("planning_act", "AktPlanowaniaPrzestrzennego", "act"),
    ("formal_document", "DokumentFormalny", "document"),
    ("planning_zone", "StrefaPlanistyczna", "zone"),
    ("ouz", "ObszarUzupelnieniaZabudowy", "ouz"),
    ("downtown_area", "ObszarZabudowySrodmiejskiej", "ozs"),
)


class FixtureWfsFetcher:
    """Zwraca artefakt ZIP złożony z zamrożonych odpowiedzi WFS RU."""

    def fetch(self, resources: tuple[WfsResource, ...]) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for index, (resource, (_role, _type, name)) in enumerate(zip(resources, LAYERS, strict=True)):
                info = zipfile.ZipInfo(f"{index:02d}-{resource.role}.gml")
                info.date_time = (1980, 1, 1, 0, 0, 0)
                archive.writestr(info, (FIXTURES / f"wfs_pog_getfeature_{name}.xml").read_bytes())
        return buffer.getvalue()


def _csw_client(requests: list[httpx.Request], *, fail: bool = False) -> OgcClient:
    payload = (FIXTURES / "csw_getrecords_iso_226401.xml").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if fail:
            return httpx.Response(503, text="Service Unavailable")
        return httpx.Response(200, content=payload, headers={"content-type": "application/xml"})

    return OgcClient(
        source_id="pog_app",
        config=OgcClientConfig(
            allowed_hosts=frozenset({"rejestr-urbanistyczny.gov.pl"}), retries=0, backoff_seconds=0
        ),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        resolver=lambda _host: ("151.101.1.1",),
        sleep=lambda _seconds: None,
    )


def _reader(client: OgcClient) -> WfsPogReader:
    resources = tuple(
        (
            role,
            WfsResource(
                role=role,
                url=WFS_URL,
                type_name=f"app-pog:{type_name}",
                source_crs="EPSG:2180",
                field_mapping={},
                source_id="pog_app",
            ),
        )
        for role, type_name, _name in LAYERS
    )
    from app.modules.imports.infrastructure.pog.reader import PogActMetadata

    return WfsPogReader(
        resources,
        metadata=PogActMetadata(act_identifier=ACT_ID, teryt="226401", legal_status="unknown"),
        fetcher=FixtureWfsFetcher(),  # type: ignore[arg-type]
        csw_url=CSW_URL,
        csw_client=client,
    )


def _parcel_inside_zone():
    zone = parse_ru_app_feature_collection(
        (FIXTURES / "wfs_pog_getfeature_zone.xml").read_bytes()
    ).features[0]
    shape = from_wkt(zone.geometry.wkt)
    point = shape.representative_point()
    parcel = point.buffer(2.0, cap_style="square")
    assert shape.contains(parcel)
    return parcel


def _fixture_element(name: str, local: str) -> ElementTree.Element:
    root = ElementTree.fromstring((FIXTURES / name).read_bytes())
    return next(node for node in root.iter() if node.tag.endswith(local))


@pytest.fixture
def imported(tmp_path: Path) -> Iterator[tuple[Session, str, object, list[httpx.Request]]]:
    session = SessionLocal()
    session.execute(text("SELECT 1"))
    source_id = f"pog_chain_{uuid4().hex[:8]}"
    requests: list[httpx.Request] = []
    try:
        outcome = run_pog_import(
            _reader(_csw_client(requests)),
            source_id,
            SqlAlchemyImportRepository(session, _source(source_id), LocalArtifactStore(tmp_path)),
            release=ImportRelease(
                source_id, "ru-sopot", datetime(2026, 9, 24, tzinfo=timezone.utc),
                publication_allowed=True, dry_run=False, teryt_scope=("226401",),
            ),
        )
        yield session, source_id, outcome, requests
    finally:
        session.rollback()
        session.close()


@pytest.mark.asyncio
async def test_sopot_chain_feature_act_csw_document_and_sha(imported) -> None:
    session, source_id, outcome, requests = imported
    assert outcome.status == "succeeded"
    assert any(w.startswith("pog_document_unavailable:") for w in outcome.warnings)
    # CSW pobrano klientem BK-102 z jawną wersją i schematem ISO.
    assert len(requests) == 1
    assert requests[0].url.params["version"] == "2.0.2"
    assert requests[0].url.params["outputSchema"] == "http://www.isotc211.org/2005/gmd"
    assert "(226401)" in requests[0].url.params["constraint"]

    pinned = active_pog_release(session, source_id=source_id)
    with (
        patch("app.services.analysis_orchestrator.active_pog_release", return_value=pinned),
        patch("app.services.analysis_orchestrator.discover_pog", new=AsyncMock()) as discover,
    ):
        pog, _ouz, warnings, sources = await _analyze_pog_best_effort(
            _parcel_inside_zone(), "226401", session
        )
    discover.assert_not_awaited()
    assert (pog.legal_status, pog.coverage_status) == ("binding", "available")

    # cecha → akt/wersja
    zone = pog.zones[0]
    assert zone.id == ZONE_ID
    assert zone.feature_version == ACT_VERSION
    assert zone.gml_url_verified and zone.gml_url.startswith(WFS_URL + "?")
    assert "1POG-100SU" in zone.gml_url and "StrefaPlanistyczna" in zone.gml_url
    act = pog.act
    assert act is not None
    assert (act.act_identifier, act.act_version) == (ACT_ID, ACT_VERSION)
    assert act.publication_id == f"{APP}/AktPlanowaniaPrzestrzennego/{ACT_ID}/{ACT_VERSION}"
    assert act.version_started_at == datetime(2026, 8, 19, 1, 0, tzinfo=timezone.utc)
    assert act.valid_from == date(2026, 8, 19)
    assert act.gml_url_verified and ACT_VERSION in (act.gml_url or "")
    assert act.data_release_id == outcome.data_release_id == sources[0].data_release_id

    # akt/wersja → metadane CSW (po identyfikatorze, nie po tytule)
    metadata = act.metadata
    assert metadata is not None
    assert metadata.record_id == POG_RECORD_ID
    assert metadata.resource_identifier == f"{APP}/AktPlanowaniaPrzestrzennego/PL.ZIPPZP.10011/226401-POG/"
    assert metadata.publication_date == act.publication_date == date(2026, 8, 12)
    assert act.card_url_verified and POG_RECORD_ID in (act.card_url or "")
    iso = next(
        node for node in ElementTree.fromstring(
            (FIXTURES / "csw_getrecords_iso_226401.xml").read_bytes()
        ).iter("{http://www.isotc211.org/2005/gmd}MD_Metadata")
        if POG_RECORD_ID in ElementTree.tostring(node, encoding="unicode")
    )
    assert metadata.record_sha256 == canonical_record_sha256(iso)

    # metadane → dokument i jego SHA
    documents = {doc.document_identifier: doc for doc in act.formal_documents}
    joined = documents["PL.ZIPPZP.10011/226401-POG/1"]
    assert joined.status == "current" and joined.relation == "przystapienie"
    assert joined.record_sha256 == canonical_record_sha256(
        _fixture_element("wfs_pog_getfeature_document.xml", "DokumentFormalny")
    )
    assert joined.link_verified and joined.link == "https://bip.sopot.pl/m,287,plan-ogolny.html"
    missing = documents["PL.ZIPPZP.10011/226401-POG/XXIV.300.2026"]
    assert missing.status == "unavailable" and missing.warning
    assert any(w.code == "POG_DOCUMENT_UNAVAILABLE" for w in warnings)

    # Odtwarzalność: zapisany artefakt zawiera WFS i CSW o tych samych SHA.
    artifact_uri = session.scalar(
        select(SourceArtifact.uri).where(SourceArtifact.content_hash == act.artifact_sha256)
    )
    content = Path(artifact_uri).read_bytes()
    assert hashlib.sha256(content).hexdigest() == act.artifact_sha256
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        csw_bytes = archive.read("90-csw-226401.xml")
        document_gml = archive.read("01-formal_document.gml")
    assert hashlib.sha256(csw_bytes).hexdigest() == metadata.response_sha256
    reparsed = parse_ru_app_feature_collection(document_gml).documents[0]
    assert reparsed.record_sha256 == joined.record_sha256


@pytest.mark.asyncio
async def test_saved_report_keeps_exact_version_without_reading_current_catalog(imported) -> None:
    session, source_id, _outcome, _requests = imported
    parcel = _parcel_inside_zone()
    pinned = active_pog_release(session, source_id=source_id)
    with (
        patch("app.services.analysis_orchestrator.active_pog_release", return_value=pinned),
        patch("app.services.analysis_orchestrator.discover_pog", new=AsyncMock()),
    ):
        pog, _ouz, _warnings, sources = await _analyze_pog_best_effort(parcel, "226401", session)
    response = AnalyzeResponse(
        status="partial",
        analyzed_at=datetime.now(timezone.utc),
        mpzp_zones=[],
        pog=pog,
        infrastructure=[],
        risks=[],
        warnings=[],
        sources=sources,
    )
    with patch.object(session, "commit", session.flush):
        saved = save_analysis(response, f"BK107_{uuid4().hex[:8]}", parcel, session)

    # „Dzisiejszy katalog” się zmienia: nowe metadane i tytuł dokumentu w PostGIS.
    version_id = session.scalar(
        select(PogFormalDocument.planning_act_version_id).limit(1).where(
            PogFormalDocument.document_identifier == "PL.ZIPPZP.10011/226401-POG/1"
        ).order_by(PogFormalDocument.id.desc())
    )
    session.execute(
        PogFormalDocument.__table__.update()
        .where(PogFormalDocument.planning_act_version_id == version_id)
        .values(title="ZMIENIONY TYTUŁ", record_sha256="0" * 64)
    )
    session.execute(
        PogActMetadataRecord.__table__.update()
        .where(PogActMetadataRecord.planning_act_version_id == version_id)
        .values(publication_date=date(2030, 1, 1))
    )
    with (
        patch("app.services.analysis_orchestrator.discover_pog", side_effect=AssertionError),
        patch("app.services.pog_provenance.act_result_from_provenance", side_effect=AssertionError),
    ):
        rebuilt = build_analyze_response_from_analysis(saved, session)

    assert rebuilt.pog is not None and rebuilt.pog.act is not None
    act = rebuilt.pog.act
    assert act.act_version == ACT_VERSION
    assert act.publication_date == date(2026, 8, 12)
    assert act.artifact_sha256 == pog.act.artifact_sha256
    doc = next(d for d in act.formal_documents if d.document_identifier.endswith("/1"))
    assert doc.title and "ZMIENIONY" not in doc.title
    assert doc.record_sha256 == pog.act.formal_documents[0].record_sha256

    report = _render_report_html(_build_report_context(rebuilt, None, None))
    assert html.escape(act.card_url or "", quote=True) in report
    assert html.escape(act.gml_url or "", quote=True) in report
    assert 'href="https://bip.sopot.pl/m,287,plan-ogolny.html"' in report
    assert doc.record_sha256 in report
    assert ACT_VERSION in report and POG_RECORD_ID in report
    assert f"#{act.data_release_id}" in report
    assert "Dokument niedostępny" in report


def test_csw_outage_keeps_import_with_explicit_incomplete_provenance(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []
    batch = _reader(_csw_client(requests, fail=True)).read()
    (act,) = batch.acts
    assert act.metadata == ()
    assert any(w.startswith("csw_unavailable:") for w in batch.warnings)
    assert any(w.startswith("csw_metadata_unresolved:") for w in batch.warnings)
    with zipfile.ZipFile(io.BytesIO(batch.content)) as archive:
        assert not any(name.startswith("90-csw") for name in archive.namelist())
