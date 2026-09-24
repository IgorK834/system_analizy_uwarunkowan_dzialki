"""BK-107: mapowanie provenance na kontrakt i bezpieczna prezentacja w raporcie."""

from __future__ import annotations

from datetime import date, datetime, timezone

from app.schemas.analyze import AnalyzeResponse, PogResult, PogStatusEvidence, PogZoneResult
from app.schemas.source import FormalDocumentSource
from app.services.pog_provenance import (
    UNVERIFIED_LINK_WARNING,
    act_result_from_provenance,
    document_source,
    feature_gml_url,
    object_id_from_identifier,
)
from app.services.report import _build_report_context, _render_report_html

WFS = "https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe/app-pog/wfs"


def _doc(**overrides):
    base = {
        "document_identifier": "PL.ZIPPZP.10011/226401-POG/1",
        "document_version": None,
        "title": "Uchwała",
        "link": "https://bip.sopot.pl/uchwala.pdf",
        "link_verified": True,
        "record_sha256": "c" * 64,
        "resolution_status": "resolved",
    }
    base.update(overrides)
    return base


def test_document_statuses_and_warnings() -> None:
    assert document_source(_doc()).status == "current"
    assert document_source(_doc()).warning is None
    superseded = document_source(_doc(repeal_date=date(2026, 8, 19)))
    assert superseded.status == "superseded" and "uchylony" in (superseded.warning or "")
    unresolved = document_source(_doc(resolution_status="unresolved", resolution_note="Inna wersja."))
    assert unresolved.status == "unresolved" and unresolved.warning.endswith("Inna wersja.")
    unavailable = document_source(_doc(resolution_status="unavailable", title=None, link=None))
    assert unavailable.status == "unavailable" and "niedostępny" in (unavailable.warning or "")
    insecure = document_source(_doc(link="http://bip.sopot.pl/a.pdf", link_verified=True))
    assert insecure.link_verified is False
    assert insecure.warning == UNVERIFIED_LINK_WARNING


def test_act_result_without_metadata_or_ru_identifier() -> None:
    act = act_result_from_provenance(
        {
            "act_identifier": "pog-lokalny",
            "object_version_id": None,
            "source_reference": WFS,
            "documents": [],
            "metadata": [],
        }
    )
    assert act.gml_url is None and act.card_url is None and act.metadata is None
    assert object_id_from_identifier("pog-lokalny", None) is None
    assert feature_gml_url(WFS, "unknown_type", "A/B/C", None) is None
    assert feature_gml_url(WFS, "planning_zone", "A/B/C", "v1").startswith(WFS)


def _response(documents: list[FormalDocumentSource]) -> AnalyzeResponse:
    act = act_result_from_provenance(
        {
            "act_identifier": "PL.ZIPPZP.10011/226401-POG/1POG",
            "object_version_id": "20260819T010000",
            "publication_id": "https://www.gov.pl/zagospodarowanieprzestrzenne/app/x",
            "version_started_at": datetime(2026, 8, 19, 1, tzinfo=timezone.utc),
            "legal_valid_from": date(2026, 8, 19),
            "source_reference": WFS,
            "data_release_id": 5,
            "release_label": "ru",
            "artifact_sha256": "a" * 64,
            "artifact_fetched_at": datetime(2026, 9, 24, tzinfo=timezone.utc),
            "documents": [],
            "metadata": [],
        }
    ).model_copy(update={"formal_documents": documents})
    pog = PogResult(
        legal_status="binding",
        coverage_status="available",
        data_availability="current",
        status_confirmed_at=datetime(2026, 9, 24, tzinfo=timezone.utc),
        legal_status_evidence=PogStatusEvidence(source_name="RU", official=True, raw_value="legalForce"),
        act=act,
        zones=[
            PogZoneResult(
                id="z1", symbol="SU", type="SU", area_sqm=10, area_pct=100,
                gml_url=f"{WFS}?typeNames=app-pog%3AStrefaPlanistyczna&x=1", feature_version="v1",
            ),
            PogZoneResult(
                id="z2", symbol="SN", type="SN", area_sqm=0, area_pct=0,
                gml_url="javascript:alert(1)",
            ),
        ],
        touches_ouz_boundary=False,
    )
    return AnalyzeResponse(
        status="partial", analyzed_at=datetime.now(timezone.utc), mpzp_zones=[], pog=pog,
        infrastructure=[], risks=[], warnings=[], sources=[],
    )


def test_report_links_only_verified_https_and_escapes_titles() -> None:
    documents = [
        document_source(_doc(title='<script>alert("x")</script>')),
        document_source(_doc(document_identifier="NS/2", title="Stara", link="http://bip.sopot.pl/s.pdf")),
        document_source(_doc(document_identifier="NS/3", resolution_status="unavailable", title=None, link=None)),
    ]
    html = _render_report_html(_build_report_context(_response(documents), None, None))

    assert "<script>" not in html
    assert "&lt;script&gt;alert(&#34;x&#34;)&lt;/script&gt;" in html
    assert 'href="https://bip.sopot.pl/uchwala.pdf"' in html
    assert 'href="http://bip.sopot.pl/s.pdf"' not in html
    assert "http://bip.sopot.pl/s.pdf" in html  # widoczny tekst, bez linku
    assert "javascript:" not in html
    assert "GML</a>" in html and ">v1</small>" in html
    assert "Dokument niedostępny" in html
    assert "metadane CSW niedostępne" in html
    assert "#5 (ru)" in html and "a" * 64 in html
    assert "20260819T010000" in html and "początek wersji 19.08.2026 01:00" in html
