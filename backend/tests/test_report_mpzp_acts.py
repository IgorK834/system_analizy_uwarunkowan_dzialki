"""Raport PDF: stan ustalenia MPZP, tabela aktów, kanały i Mapa 3a (AU-101, AU-103).

Akty i stany pochodzą z rzeczywistych, zamrożonych odpowiedzi KIMPZP i RU (jak w ``test_mpzp_acts_service``); raport
jest renderowany z ``AnalyzeResponse`` i zamrożonego snapshotu map, bez bazy i sieci.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import pytest
from bs4 import BeautifulSoup

from app.schemas.analyze import AnalyzeResponse
from app.services import mpzp_acts as service
from app.services.report import _build_report_context, _html_to_pdf, _render_report_html
from app.services.report_map import render_report_maps
from app.services.report_map_snapshot import build_report_map_snapshot
from app.shared.mpzp_status_text import MPZP_STATUS_LABELS, MPZP_STATUS_MESSAGES, MPZP_STATUS_MISSING_MESSAGE
from tests.report_map_reference import FROZEN_AT, load_fixture
from tests.ru_mpzp_fixtures import BIELSKO, DYGOWO_EMPTY, GORA_KALWARIA, WARSZAWA_EMPTY, parcel
from tests.test_mpzp_acts_service import AS_OF, _flow, _status
from tests.test_report_v2 import _sections, _text

NBSP = chr(0xA0)


def _response(slug: str, html: str, *, manual: bool = False) -> tuple[AnalyzeResponse, Any]:
    _, _, resolution = asyncio.run(_flow(slug, html))
    base, _ = load_fixture("multizone")
    geometry = parcel(slug)
    response = base.model_copy(
        update={
            "mpzp_zones": [],
            "mpzp_acts": service.acts_to_contract(resolution),
            "mpzp_status": _status(resolution, manual=manual),
            "mpzp_discovery": None,
            "section_quality": None,
            "manual_zone_required": manual,
        }
    )
    return response, geometry


def _html(response: AnalyzeResponse, maps=None) -> str:  # noqa: ANN001
    return _render_report_html(_build_report_context(response, maps))


def _callout(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    node = soup.select_one("[data-mpzp-status]")
    assert node is not None
    return node["data-mpzp-status"], re.sub(r"\s+", " ", node.get_text(" ", strip=True).replace(NBSP, " "))


CASES = {
    "ambiguous": (GORA_KALWARIA, "gora_kalwaria_141801_4.0701.23_8.html"),
    "available": (BIELSKO, "bielsko_biala_246101_1.0056.155_3.html"),
    "no_coverage": (DYGOWO_EMPTY, "dygowo_321606_2.0029.362.html"),
    "unavailable": (WARSZAWA_EMPTY, "warszawa_146510_8.0502.1_3.html"),
    "no_match": (WARSZAWA_EMPTY, "ruciane_nida_281604_5.0011.107.html"),
}


@pytest.mark.parametrize("state", list(CASES))
def test_each_state_has_its_own_box_label_and_message_in_the_pdf(state: str) -> None:
    response, _ = _response(*CASES[state])
    html = _html(response)
    shown, text = _callout(html)
    assert shown == state
    assert MPZP_STATUS_LABELS[state] in text and MPZP_STATUS_MESSAGES[state] in text
    assert response.mpzp_status is not None and response.mpzp_status.reason_code in text
    others = [label for key, label in MPZP_STATUS_LABELS.items() if key != state]
    assert not any(label in text for label in others), (state, text)
    pdf = _html_to_pdf(html)
    section = _sections(pdf)[3]
    assert MPZP_STATUS_LABELS[state] in section
    assert "Nie sprawdzono albo nie znaleziono" not in section and "Nie znaleziono ani nie sprawdzono" not in section


def test_unknown_state_and_snapshot_without_state_are_distinct_boxes() -> None:
    base, _ = load_fixture("multizone")
    unknown = base.model_copy(
        update={
            "mpzp_zones": [],
            "mpzp_acts": [],
            "mpzp_status": service.build_mpzp_status(
                service.resolve_mpzp_acts(None, service.RuChannelResult(queried=False), as_of=AS_OF),
                has_zones=False,
                manual_zone_required=False,
            ),
            "section_quality": None,
        }
    )
    state, text = _callout(_html(unknown))
    assert state == "unknown" and MPZP_STATUS_MESSAGES["unknown"] in text
    legacy = unknown.model_copy(update={"mpzp_status": None})
    state, text = _callout(_html(legacy))
    assert state == "missing" and MPZP_STATUS_MISSING_MESSAGE in text


def test_acts_table_lists_both_audit_acts_without_choosing_and_the_rejected_one_with_distance() -> None:
    response, _ = _response(*CASES["ambiguous"])
    html = _html(response)
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select('tr[data-row="mpzp-act"]')
    assert [row.select_one("strong").get_text() for row in rows] == ["IV/30/2024", "576/XLVII/2010"]
    assert {row["data-precedence"] for row in rows} == {"unresolved"}
    first = re.sub(r"\s+", " ", rows[0].get_text(" ", strip=True).replace(NBSP, " "))
    assert "nie ustalono (akt bez granicy w źródle)" in first
    assert "http://mpzp.gorakalwaria.pl/portal/mpzp/uch/IV_30_2024.pdf" in first
    assert "wymaga weryfikacji" in first and "MPZP_ACT_OVERLAP_UNRESOLVED" in first
    rejected = soup.select('tr[data-row="mpzp-rejected-act"]')
    assert len(rejected) == 1
    text = re.sub(r"\s+", " ", rejected[0].get_text(" ", strip=True).replace(NBSP, " "))
    assert "cz. II" in text and "granica aktu leży poza działką" in text and "5,99 m" in text
    channels = soup.select_one('table[data-table="mpzp-channels"]')
    assert channels is not None
    assert [node["data-channel-status"] for node in channels.select("[data-channel-status]")] == ["available", "no_match"]
    note = soup.select_one('[data-note="mpzp-acts"]')
    assert note is not None and "a nie strefa przeznaczenia" in note.get_text()
    assert soup.select_one("[data-note='mpzp-rejected']") is not None


def test_ru_act_row_has_share_document_and_amendment_and_map_3a_is_rendered() -> None:
    response, geometry = _response(*CASES["available"])
    snapshot = build_report_map_snapshot(response, geometry, basemap_dir="", frozen_at=FROZEN_AT)
    ids = [item["id"] for item in snapshot["maps"]]
    assert ids == ["parcel", "mpzp", "mpzp_acts", "pog", "environment"]
    acts_map = next(item for item in snapshot["maps"] if item["id"] == "mpzp_acts")
    assert acts_map["status"] == "rendered"
    assert [layer["id"] for layer in acts_map["layers"]] == ["mpzp_act_boundary:0", "mpzp_act:0", "parcel"]
    assert acts_map["layers"][0]["style"]["line_dash"] == [6, 4]
    assert acts_map["legend"][0]["label"] == "Granica aktu LIII/749/1998"
    maps = render_report_maps(snapshot, basemap_dir="")
    html = _html(response, maps)
    soup = BeautifulSoup(html, "html.parser")
    (row,) = soup.select('tr[data-row="mpzp-act"]')
    text = re.sub(r"\s+", " ", row.get_text(" ", strip=True).replace(NBSP, " "))
    assert "11,84%" in text and "167,44 m²" in text
    assert "LIII/749/1998" in text and "obowiązuje od: 01.04.1998" in text
    assert "https://brm.bielsko-biala.pl/wp-content/uploads/2021/01/006_uchwala.pdf" in text
    assert "zmiana XLIV/1038/2022" in text and "źródła: Rejestr Urbanistyczny" in text
    assert "Mapa 3a. Granice aktów MPZP" in html
    pdf = _html_to_pdf(html)
    assert "Mapa 3a. Granice aktów MPZP" in _text(pdf)
    assert "Tabela 3.6" in _sections(pdf)[3] and "Tabela 3.7" in _sections(pdf)[3]


def test_map_3a_is_absent_without_acts_and_explains_acts_without_boundary() -> None:
    response, geometry = _response(*CASES["no_coverage"])
    snapshot = build_report_map_snapshot(response, geometry, basemap_dir="", frozen_at=FROZEN_AT)
    assert [item["id"] for item in snapshot["maps"]] == ["parcel", "mpzp", "pog", "environment"]
    kimpzp_only, geometry = _response(*CASES["ambiguous"])
    snapshot = build_report_map_snapshot(kimpzp_only, geometry, basemap_dir="", frozen_at=FROZEN_AT)
    acts_map = next(item for item in snapshot["maps"] if item["id"] == "mpzp_acts")
    assert acts_map["status"] == "empty"
    assert "nie mają granicy w źródle" in acts_map["empty_reason"]


def test_draft_act_is_shown_without_effective_date_and_without_a_plan_number() -> None:
    response, _ = _response(*CASES["available"])
    act = response.mpzp_acts[0].model_copy(
        update={"resolution_number": None, "legal_status": "not_yet_binding", "raw_status": "w opracowaniu",
                "document_kind": "commencement", "precedence": "not_in_force", "amendments": []}
    )
    html = _html(response.model_copy(update={"mpzp_acts": [act]}))
    (row,) = BeautifulSoup(html, "html.parser").select('tr[data-row="mpzp-act"]')
    text = re.sub(r"\s+", " ", row.get_text(" ", strip=True).replace(NBSP, " "))
    assert "projekt lub jeszcze nieobowiązujący (w opracowaniu)" in text
    assert "obowiązuje od" not in text
    assert "uchwała o przystąpieniu (nie tekst planu)" in text
    assert "nie obowiązuje" in text


def test_limitations_and_findings_name_the_state_instead_of_the_combined_sentence() -> None:
    response, _ = _response(*CASES["unavailable"])
    context = _build_report_context(response, None)
    joined = " ".join(context["limitations"])
    assert "Stan ustalenia MPZP: Nie sprawdzono — błąd źródła" in joined
    assert "nie sprawdzono planu albo działka nie przecina" not in joined
