"""Testy treści i układu raportu PDF v2 (BK-501, BK-502) na zamrożonych fixtures.

Raport jest renderowany z ``AnalyzeResponse`` i zamrożonego snapshotu mapy —
dokładnie tak, jak w ``generate_analysis_report_pdf`` — bez bazy danych.
Tekst sprawdza PyMuPDF (kolejność sekcji, wartości, polskie znaki), a układ:
granice stron (brak obcięć), rozłączność linii tekstu i obrazów (brak
nakładania) oraz render każdej strony do obrazu. Gdy ustawiono
``REPORT_EVIDENCE_DIR``, obrazy stron i PDF trafiają tam jako artefakty odbioru.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from bs4 import BeautifulSoup

from app.modules.reporting.domain.sections import REPORT_SECTIONS
from app.schemas.analyze import AnalyzeResponse, RiskSectionResult, TerrainResult
from app.schemas.source import SourceMetadata
from app.services.report import (
    AnalysisRecordMeta,
    _build_limitations,
    _build_report_context,
    _html_to_pdf,
    _offline_url_fetcher,
    _render_report_html,
)
from app.services.report_map import render_report_maps
from tests.report_map_reference import load_fixture, reference_snapshot

_MARGIN_PT = 14 / 25.4 * 72  # lewy/prawy margines @page
_TOP_PT = 16 / 25.4 * 72
_BOTTOM_PT = 18 / 25.4 * 72
NBSP = chr(0xA0)
_POLISH = set("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ")


@lru_cache(maxsize=8)
def _render(fixture: str) -> tuple[str, bytes]:
    response, _ = load_fixture(fixture)
    maps = render_report_maps(reference_snapshot(fixture, "zones"), basemap_dir="")
    record = AnalysisRecordMeta(result_contract_version="analysis-v9", data_release_ids=(7, 12),
                                cache_signature="sig-123")
    html = _render_report_html(_build_report_context(response, maps, record))
    return html, _html_to_pdf(html)


def _doc(pdf: bytes) -> pymupdf.Document:
    return pymupdf.open(stream=pdf, filetype="pdf")


def _text(pdf: bytes) -> str:
    """Tekst stron bez żywej paginy i stopki (obszar treści @page)."""
    with _doc(pdf) as doc:
        raw = "\n".join(
            page.get_text(
                clip=pymupdf.Rect(0, _TOP_PT - 2, page.rect.width, page.rect.height - _BOTTOM_PT + 2)
            )
            for page in doc
        )
    return re.sub(r"\s+", " ", raw.replace(NBSP, " "))


def _squash(text: str) -> str:
    """Porównanie odporne na łamanie wierszy i dzielenie wyrazów (U+2010)."""
    return re.sub("[\\s\\-" + chr(0xAD) + NBSP + chr(0x2010) + "]", "", text)


def _rows(html: str, kind: str) -> list[list[str]]:
    """Komórki wierszy tabeli oznaczonych ``data-row`` (dokładna liczba wierszy)."""
    soup = BeautifulSoup(html, "html.parser")
    return [
        [
            re.sub(r"\s+", " ", cell.get_text(" ", strip=True).replace(NBSP, " "))
            for cell in row.find_all("td")
        ]
        for row in soup.select(f'tr[data-row="{kind}"]')
    ]


def _sections(pdf: bytes) -> dict[int, str]:
    text = _text(pdf)
    # Nagłówki w treści — za stroną tytułową (spis treści i rodzaje ustaleń).
    cursor = text.find("Rodzaje ustaleń")
    positions = []
    for section in REPORT_SECTIONS:
        heading = f"{section.number}. {section.title}"
        index = text.find(heading, cursor)
        assert index > cursor, heading
        positions.append((section.number, index))
        cursor = index
    positions.append((11, text.find("Załącznik A. Mapowanie pól API", cursor)))
    return {
        number: text[start:positions[i + 1][1]]
        for i, (number, start) in enumerate(positions[:-1])
    }


def _save_evidence(name: str, pdf: bytes) -> None:
    target = os.environ.get("REPORT_EVIDENCE_DIR")
    if not target:
        return
    out = Path(target)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{name}.pdf").write_bytes(pdf)
    with _doc(pdf) as doc:
        for number, page in enumerate(doc, start=1):
            page.get_pixmap(dpi=80).save(out / f"{name}-p{number:02d}.png")


# --- Struktura: 10 sekcji --------------------------------------------------------


def test_report_has_ten_sections_in_order_and_appendix() -> None:
    _, pdf = _render("multizone")
    text = _text(pdf)
    sections = _sections(pdf)
    assert list(sections) == list(range(1, 11))
    assert all(len(body) > 80 for body in sections.values())
    assert "Załącznik A. Mapowanie pól API na elementy raportu" in text
    assert "Spis treści" in text
    for label in ("fakt źródłowy", "wynik obliczenia", "przybliżenie", "dane ręczne"):
        assert label in text
    assert "scoring" in text.lower() and "Raport nie zawiera syntetycznej oceny (scoringu)" in text
    _save_evidence("multizone", pdf)


def _sparse_response() -> AnalyzeResponse:
    response, _ = load_fixture("multizone")
    return response.model_copy(
        update={
            "mpzp_zones": [],
            "pog": None,
            "risks": [],
            "risk_sections": [
                RiskSectionResult(section="flood", status="unavailable", reason_code="ISOK_TIMEOUT",
                                  relation="unknown"),
                RiskSectionResult(section="nature", status="unknown", reason_code="LEGACY_SNAPSHOT",
                                  relation="unknown"),
            ],
            "terrain": TerrainResult(
                status="unavailable",
                reason_code="NMT_TIMEOUT",
                source=SourceMetadata(source_name="NMT GUGiK", confidence=0.0, manual_review_required=True),
            ),
            "utilities_preview": None,
            "infrastructure": [],
            "warnings": [],
            "sources": [],
            # Macierz z fixture opisuje pełną analizę — ta rzadka jest oceniana od nowa.
            "section_quality": None,
        }
    )


def test_every_empty_section_states_an_explicit_reason() -> None:
    response = _sparse_response()
    html = _render_report_html(_build_report_context(response, None))
    pdf = _html_to_pdf(html)
    sections = _sections(pdf)
    reasons = {
        3: "nie potwierdza to braku planu",
        4: "Brak wyniku nie oznacza braku planu",
        5: "NIE oznacza braku ryzyka ani ograniczeń",
        6: "nie oznacza płaskiego terenu",
        7: "Brak danych nie oznacza braku sieci",
        8: "nieustalone",
        9: "brak zarejestrowanych źródeł",
    }
    for number, phrase in reasons.items():
        assert phrase in sections[number], (number, sections[number][:400])
    assert "Mapa niedostępna" in sections[1] or "Nie udało się wygenerować map" in sections[1]
    assert "Nie analizowano" in sections[7]
    assert "Nie udało się wygenerować map raportu" in sections[10]
    _save_evidence("sparse", pdf)


# --- BK-502: tabele MPZP i POG ------------------------------------------------------


def test_three_pog_zones_and_two_mpzp_zones_have_exact_rows() -> None:
    html, pdf = _render("multizone")
    zones = _rows(html, "pog-zone")
    assert len(zones) == 3
    assert [row[0].split(" ")[0] for row in zones] == ["1SW", "2SJ", "3SU"]
    assert [row[0].split(" ")[1] for row in zones] == [
        f"PL.ZIPOG.1261.POG/strefa/{symbol}" for symbol in ("1SW", "2SJ", "3SU")
    ]
    # Pole i udział z dokładnością 0,01 — każda strefa osobnym wierszem.
    assert [(row[2], row[3]) for row in zones] == [("800,00 m²", "33,33%")] * 3
    assert [row[1].split(" — ")[0] for row in zones] == ["SW", "SJ", "SU"]
    parameters = _rows(html, "pog-parameters")
    assert [row[1:5] for row in parameters] == [
        ["1,2", "16 m", "40%", "30%"],
        ["0,6", "12 m", "35%", "50%"],
        ["1", "nie określono", "60%", "0%"],
    ]
    soup = BeautifulSoup(html, "html.parser")
    footers = [re.sub(r"\s+", " ", item.get_text(" ", strip=True)) for item in soup.select("p.shares-sum")]
    # Suma surowych udziałów (99,9999%) nie jest „poprawiana” — wiersze pokazują 33,33%.
    assert "Suma udziałów wierszy: 100,00% (odchylenie od 100%: 0,00 pp; bez korekty zaokrągleń)." in footers
    mpzp = _rows(html, "mpzp-zone")
    assert len(mpzp) == 2
    assert [(row[1].split(" ")[0], row[3], row[4]) for row in mpzp] == [
        ("MN.1", "1 500,00 m²", "62,50%"), ("U.2", "900,00 m²", "37,50%"),
    ]
    assert [row[2].split(" ")[0] for row in mpzp] == [
        "PL.ZIPOZ.1261.MPZP-2019-17/wydzielenie/MN.1", "PL.ZIPOZ.1261.MPZP-2019-17/wydzielenie/U.2",
    ]
    # Te same wartości trafiły do PDF.
    squashed = _squash(_text(pdf))
    for row in [*zones, *parameters, *mpzp]:
        for cell in row[1:5]:
            assert _squash(cell) in squashed, cell


def test_mpzp_evidence_refs_pages_hashes_and_conflict_candidates() -> None:
    html, pdf = _render("multizone")
    evidence = _rows(html, "mpzp-evidence")
    assert [row[0] for row in evidence] == [f"E{index}" for index in range(1, 10)]
    assert evidence[0][3].startswith("dokument [D1] str. 12, segment §8 ust. 2 pkt 1, jednostka #120")
    assert evidence[8][3].startswith("dokument [D2] str. 15")
    assert "sprzeczna kandydatura" in evidence[1][1] and "sprzeczna kandydatura" in evidence[2][1]
    parameters = {(row[0], row[1]): row for row in _rows(html, "mpzp-parameter")}
    floors = parameters[("MN.1", "Maksymalna liczba kondygnacji nadziemnych")]
    assert floors[2] == "sprzeczność wymaga weryfikacji — kandydaci: 2 kondygn. [E2]; 3 kondygn. [E3]"
    assert floors[3] == "[E2], [E3]"
    squashed = _squash(_text(pdf))
    assert _squash("wymaga weryfikacji — kandydaci: 2 kondygn. [E2]; 3 kondygn. [E3]") in squashed
    assert ("a" * 63 + "1") in squashed and ("b" * 63 + "2") in squashed
    assert _squash("„Maksymalna wysokość zabudowy mieszkaniowej: 9 m, dla budynków gospodarczych 5 m.”") in squashed


def test_zero_is_numeric_and_null_is_not_specified() -> None:
    html, pdf = _render("multizone")
    parameters = {(row[0], row[1]): row[2:4] for row in _rows(html, "mpzp-parameter")}
    assert parameters[("MN.1", "Minimalny wskaźnik intensywności zabudowy")] == ["0", "[E7]"]
    assert parameters[("U.2", "Maksymalny udział powierzchni zabudowy")] == ["0%", "[E9]"]
    assert parameters[("MN.1", "Przeznaczenie uzupełniające")] == ["nie określono", "—"]
    assert parameters[("U.2", "Minimalny udział powierzchni biologicznie czynnej")] == ["nie określono", "—"]
    squashed = _squash(_text(pdf))
    assert _squash("Minimalny wskaźnik intensywności zabudowy 0 [E7]") in squashed
    assert _squash("Maksymalny udział powierzchni zabudowy 0% [E9]") in squashed
    assert _squash("3SU PL.ZIPOG.1261.POG/strefa/3SU 1 nie określono 60% 0%") in squashed


def test_no_parameter_average_across_zones() -> None:
    html, _ = _render("multizone")
    # Tyle wierszy parametrów, ile stref — bez wiersza zbiorczego ani średniej.
    assert len(_rows(html, "pog-parameters")) == len(_rows(html, "pog-zone")) == 3
    for kind in ("pog-zone", "pog-parameters", "mpzp-zone", "mpzp-parameter"):
        for row in _rows(html, kind):
            assert not any(re.search(r"średni[aeo]?\b|przeciętn", cell.lower()) for cell in row), row
    soup = BeautifulSoup(html, "html.parser")
    assert not soup.select("tfoot")
    for note in soup.select("p.shares-sum"):
        assert "Suma udziałów" in note.get_text()


def test_project_is_never_presented_as_binding_act() -> None:
    html, pdf = _render("project")
    text = _text(pdf)
    for forbidden in ("akt obowiązujący", "obowiązuje (potwierdzone", "Początek obowiązywania"):
        assert forbidden not in text
    assert text.count("projekt / dane niewiążące") >= 3
    assert "projekt aktu — niewiążący" in text
    assert "Data początkowa w atrybucie APP (akt niewiążący)" in text
    binding = _text(_render("multizone")[1])
    assert "Początek obowiązywania wersji (wg APP)" in binding
    _save_evidence("project", pdf)


def test_long_tables_keep_every_row_and_long_polish_text() -> None:
    response, _ = load_fixture("long_tables")
    html, pdf = _render("long_tables")
    assert html.count('data-row="pog-zone"') == 32
    assert html.count('data-row="mpzp-evidence"') >= 36
    text = _text(pdf)
    squashed = _squash(text)
    assert response.pog is not None
    for zone in response.pog.zones:
        assert _squash(zone.id) in squashed, zone.id
    for parameter in response.mpzp_zones[0].parameters:
        assert _squash(parameter.evidence_text or "") in squashed, parameter.name
    with _doc(pdf) as doc:
        assert doc.page_count >= 15
        header_pages = [
            number for number, page in enumerate(doc) if "Fragment dowodowy" in page.get_text()
        ]
        mapping_pages = [
            number for number, page in enumerate(doc) if "Pole API (wzorzec)" in page.get_text()
        ]
        zone_header_pages = [
            number for number, page in enumerate(doc) if "Rodzaj strefy" in page.get_text()
        ]
    # Nagłówek długich tabel powtarza się na każdej stronie, na którą tabela przechodzi.
    assert len(header_pages) >= 2 and len(mapping_pages) >= 2 and len(zone_header_pages) >= 2
    _save_evidence("long_tables", pdf)


# --- Układ i znaki: PyMuPDF ---------------------------------------------------------


@pytest.mark.parametrize("fixture", ["multizone", "project", "long_tables"])
def test_pages_render_without_clipping_overlaps_or_lost_polish_letters(fixture: str) -> None:
    html, pdf = _render(fixture)
    assert_clean_pages(html, pdf, pangram=fixture != "long_tables")


def assert_clean_pages(html: str, pdf: bytes, *, pangram: bool = True) -> None:
    """Brak obcięć, nakładania, pustych stron, obcych fontów i zgubionych polskich liter (też dla PV3-18)."""
    with _doc(pdf) as doc:
        for page in doc:
            width = page.rect.width
            lines: list[tuple[pymupdf.Rect, str]] = []
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    content = "".join(span["text"] for span in line["spans"]).strip()
                    if not content:
                        continue
                    rect = pymupdf.Rect(line["bbox"])
                    # Brak obcięć: tekst w obszarze strony i w poziomych marginesach.
                    assert rect.x0 >= _MARGIN_PT - 2 and rect.x1 <= width - _MARGIN_PT + 2, (
                        page.number, content, rect)
                    assert rect.y0 >= 0 and rect.y1 <= page.rect.height, (page.number, content)
                    lines.append((rect, content))
            # Brak nakładania: linie tekstu nie zachodzą na siebie.
            for index, (rect, content) in enumerate(lines):
                for other, other_content in lines[index + 1:]:
                    overlap = rect & other
                    if overlap.is_empty:
                        continue
                    assert overlap.width * overlap.height < 2.0, (page.number, content, other_content)
            images = [pymupdf.Rect(item["bbox"]) for item in page.get_image_info()]
            for image in images:
                assert image.x0 >= _MARGIN_PT - 2 and image.x1 <= width - _MARGIN_PT + 2
                for rect, content in lines:
                    overlap = rect & image
                    assert overlap.is_empty or overlap.width * overlap.height < 2.0, (page.number, content)
            pixmap = page.get_pixmap(dpi=50)
            assert pixmap.width > 0 and len(set(pixmap.samples)) > 1, f"pusta strona {page.number + 1}"
            fonts = {font[3] for font in page.get_fonts()}
            assert all("DejaVu" in name for name in fonts), fonts
        text = "\n".join(page.get_text() for page in doc)
    assert "�" not in text
    visible = re.sub(r"<[^>]+>", " ", html)
    assert {char for char in visible if char in _POLISH} <= set(text)
    if pangram:
        assert "zażółć gęślą jaźń" in re.sub(r"\s+", " ", text)




def test_maps_are_embedded_with_legend_scale_and_release_metadata() -> None:
    _, pdf = _render("multizone")
    text = _text(pdf)
    with _doc(pdf) as doc:
        assert sum(len(page.get_image_info()) for page in doc) == 4
    assert "Mapa 4. Strefy POG oraz OUZ, OZS i OSDIS." in text
    assert "Tryb tematyczny: Strefy planistyczne" in text
    assert "podziałka 10 m" in text and "EPSG:2180" in text
    assert "wydania danych: #12" in text and "wydania danych: #7" in text
    assert "pobrane 20.09.2026 09:29" in text
    assert "neutralne (bez pobierania WMS)" in text
    assert "OUZ — obszar uzupełnienia zabudowy" in text
    assert "ukośne kreskowanie" in text


def test_provenance_section_lists_versions_releases_and_map_hashes() -> None:
    _, pdf = _render("multizone")
    section = _sections(pdf)[9]
    assert "analysis-v9" in section and "report-v2/" in section
    assert "Wydania: #7, #12." in section and "sig-123" in section
    snapshot = reference_snapshot("multizone", "zones")
    assert _squash(snapshot["semantic_sha256"]) in _squash(section)
    assert "zgodność z zapisem: tak" in section
    assert "neutralne tło — PDF nie pobiera WMS/OSM/KIUT" in section
    assert "c" * 63 + "3" in _squash(section)


def test_limitations_include_map_warnings_and_rebuilt_snapshot_notice() -> None:
    response, _ = load_fixture("multizone")
    snapshot = reference_snapshot("multizone", "zones")
    rebuilt = render_report_maps(snapshot, from_snapshot=False, basemap_dir="")
    limitations = _build_limitations(response, rebuilt)
    assert any("Analiza sprzed zamrażania map" in item for item in limitations)
    html = _render_report_html(_build_report_context(response, rebuilt))
    assert "Mapa odtworzona z danych snapshotu" in html
    assert "odtworzona z danych snapshotu (analiza sprzed BK-503)" in html


def test_template_escapes_untrusted_values() -> None:
    response, _ = load_fixture("multizone")
    zone = response.mpzp_zones[0].model_copy(update={"zone_symbol": "<script>alert(1)</script>"})
    html = _render_report_html(_build_report_context(response.model_copy(update={"mpzp_zones": [zone]})))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_offline_fetcher_refuses_network_and_files() -> None:
    fetcher = _offline_url_fetcher()
    for url in ("https://example.test/a.png", "file:///etc/passwd", "http://127.0.0.1/"):
        with pytest.raises(Exception):
            fetcher.fetch(url) if hasattr(fetcher, "fetch") else fetcher(url)
    html = '<html><body><img src="https://example.test/tracker.png"><p>Tekst ąę</p></body></html>'
    assert _html_to_pdf(html).startswith(b"%PDF")


def _context_rows(response: AnalyzeResponse) -> dict[str, Any]:
    return {row["domain"]: row for row in _build_report_context(response)["summary"]["rows"]}


def test_summary_statuses_are_consistent_with_quality_matrix() -> None:
    response, _ = load_fixture("multizone")
    context = _build_report_context(response)
    assert context["summary"]["rows"] is context["quality"]["rows"]
    rows = _context_rows(response)
    # Sprzeczność parametrów: dane są, ale niepełne; ręczna weryfikacja to flaga.
    assert rows["MPZP"]["status"] == "partial"
    assert rows["MPZP"]["manual_review"] is True
    assert rows["POG — strefy planistyczne"]["status"] == "available"
    assert rows["Zagrożenie powodziowe (ISOK)"]["status"] == "available"
    assert rows["Transport i dostęp do drogi"]["status"] == "out_of_scope"
    assert rows["Uzbrojenie terenu (KIUT)"]["status"] == "partial"  # tylko podgląd
    project = _context_rows(load_fixture("project")[0])
    assert project["POG — strefy planistyczne"]["status"] == "available"
    assert project["POG — strefy planistyczne"]["manual_review"] is True
    sparse = _context_rows(_sparse_response())
    assert sparse["MPZP"]["status"] == "unknown"
    assert sparse["Zagrożenie powodziowe (ISOK)"]["status"] == "unavailable"
    assert sparse["Teren (NMT)"]["status"] == "unavailable"
    assert sparse["Uzbrojenie terenu (KIUT)"]["status"] == "unknown"
