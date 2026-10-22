"""Raport PDF: sekcje „Transport i dostęp do drogi” i „Zabudowa sąsiednia” (Task 23.2–23.3) — treść, stany bez wyniku i
zdanie o braku potwierdzenia prawnego dostępu do drogi. Renderowany jest HTML raportu z zamrożonej odpowiedzi API."""

from __future__ import annotations

import re
from pathlib import Path

from bs4 import BeautifulSoup

from app.modules.analysis.domain.roads import ROAD_ACCESS_DISCLAIMER
from app.schemas.bdot_context import NeighbourBuildingsResult, RoadContextResult
from app.services.bdot_context import bdot_sources, bdot_warnings, disabled_neighbour_buildings, disabled_road_context
from app.services.report import _build_report_context, _render_report_html
from tests.report_map_reference import load_fixture

TEMPLATE = Path(__file__).resolve().parents[1] / "app" / "templates" / "report.html"


def _html(**update):  # noqa: ANN001, ANN202
    response, _ = load_fixture("multizone")
    return _render_report_html(_build_report_context(response.model_copy(update=update)))


def _text(html: str, selector: str) -> str:
    node = BeautifulSoup(html, "html.parser").select_one(selector)
    assert node is not None, selector
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True))


def _road(status: str, reason: str, **extra: object) -> RoadContextResult:
    response, _ = load_fixture("multizone")
    return response.road_context.model_copy(
        update={"status": status, "reason_code": reason, "nearest": None, "intersecting_road_count": None, **extra}
    )


def test_the_nearest_road_table_shows_distance_relation_category_class_and_the_release() -> None:
    html = _html()

    table = _text(html, '[data-block="road-nearest"]')
    assert "12,00 m" in table and "oś jezdni leży poza działką" in table
    assert "gminna" in table and "droga lokalna" in table and "ul. Przykładowa" in table
    assert "od poligonu działki do osi jezdni w EPSG:2180" in table and "nie do granicy pasa drogowego" in table
    assert "25 m" in table and "maksymalnie 500 m" in table
    assert ROAD_ACCESS_DISCLAIMER in _text(html, '[data-block="road-note"]')
    assert "bdot10k-1261-1355c5a51538" in html and "22.01.2026" in html  # stan danych i wydanie


def test_every_road_state_without_a_road_says_that_data_does_not_mean_no_road() -> None:
    expectations = {
        "no_match": ("nic nie znaleziono w ograniczonym zasięgu", "nie dowodzi braku drogi"),
        "no_coverage": ("brak pełnego pokrycia", "Brak danych nie oznacza braku drogi"),
        "unavailable": ("źródło niedostępne", "Brak danych nie oznacza braku drogi"),
    }
    for status, (label, phrase) in expectations.items():
        reason = {"no_match": "SEARCH_RADIUS_EXHAUSTED", "no_coverage": "OUTSIDE_COVERAGE", "unavailable": "QUERY_TIMEOUT"}[status]
        extra = {"search_radius_m": 500.0} if status == "no_match" else {}
        html = _html(road_context=_road(status, reason, **extra))
        empty = _text(html, '[data-block="road-empty"]')
        assert label in empty.lower() or label in empty, status
        assert phrase in empty, status
        assert "Tabela 7.3. Najbliższa oś jezdni" not in html, status  # brak tabeli wartości: nie wymyślamy zer
        assert ROAD_ACCESS_DISCLAIMER in _text(html, '[data-block="road-note"]'), status  # uwaga jest zawsze


def test_a_snapshot_from_before_the_section_is_unknown_not_a_missing_road() -> None:
    html = _html(road_context=None, neighbour_buildings=None)

    assert "zapis sprzed tej sekcji" in _text(html, '[data-block="road-empty"]').lower()
    assert "Brak danych nie oznacza braku drogi" in _text(html, '[data-block="road-empty"]')
    assert "Brak danych nie oznacza braku budynków" in _text(html, '[data-block="buildings-empty"]')


def test_the_buildings_tables_show_the_parcel_the_three_buffers_and_the_nearest_buildings() -> None:
    html = _html()

    buffers = BeautifulSoup(html, "html.parser").select_one('[data-block="buildings-buffers"]')
    rows = [[cell.get_text(" ", strip=True).replace("\xa0", " ") for cell in row.find_all("td")] for row in buffers.select("tbody tr")]
    assert [row[0] for row in rows] == ["50 m", "100 m", "200 m"]
    assert [row[1] for row in rows] == ["2", "3", "3"]  # liczby z fixture (kumulatywnie)
    assert "Na działce" in html and "pokrycie działki 8,1%" in html and "wynik obliczenia, nie ocena prawna" in html
    listed = _text(html, '[data-block="buildings-list"]')
    assert "zachodzi na działkę" in listed and "sąsiedni" in listed and "budynki mieszkalne" in listed
    assert "buffer(parcel, r) - parcel" in html and "kumulatywne" in html
    assert "nie ocena możliwości zabudowy" in _text(html, '[data-block="buildings-note"]')


def test_zero_buildings_is_a_checked_zero_with_undefined_statistics_shown_as_not_specified() -> None:
    response, _ = load_fixture("multizone")
    base = response.neighbour_buildings
    empty_buffers = [
        item.model_copy(update={"count": 0, "straddling_parcel_count": 0, "footprint_area_in_ring_sqm": 0.0,
                                "union_area_in_ring_sqm": 0.0, "built_share_pct": 0.0, "min_distance_m": None,
                                "footprint_min_sqm": None, "footprint_median_sqm": None, "footprint_max_sqm": None})
        for item in base.buffers
    ]
    empty = base.model_copy(
        update={"buffers": empty_buffers, "buildings": [],
                "on_parcel": base.on_parcel.model_copy(update={"count": 0, "union_area_in_parcel_sqm": 0.0, "coverage_pct": 0.0})}
    )

    html = _html(neighbour_buildings=empty)

    buffers = BeautifulSoup(html, "html.parser").select_one('[data-block="buildings-buffers"]')
    first = [cell.get_text(" ", strip=True) for cell in buffers.select("tbody tr")[0].find_all("td")]
    assert first[1] == "0" and first[6] == "nie określono"  # min. odległość: null ≠ 0
    assert BeautifulSoup(html, "html.parser").select_one('[data-block="buildings-list"]') is None


def test_the_pdf_lists_at_most_25_nearest_buildings_and_points_to_the_audit_package() -> None:
    response, _ = load_fixture("multizone")
    base = response.neighbour_buildings
    many = base.model_copy(update={"buildings": [base.buildings[0].model_copy(update={"building_id": f"B-{i:04d}-XXXXXXXX"}) for i in range(40)],
                                   "buildings_truncated": True})

    html = _html(neighbour_buildings=many)

    rows = BeautifulSoup(html, "html.parser").select('[data-block="buildings-list"] tbody tr')
    assert len(rows) == 25
    assert "Pokazano 25 z 40" in html and "pełna lista jest w pakiecie audytowym" in html


def test_partial_coverage_and_failures_appear_in_the_limitations_section() -> None:
    response, _ = load_fixture("multizone")
    partial_road = response.road_context.model_copy(update={"search_area_within_coverage": False})
    unavailable = response.neighbour_buildings.model_copy(
        update={"status": "unavailable", "reason_code": "QUERY_TIMEOUT", "on_parcel": None, "buffers": [], "buildings": [],
                "search_area_within_coverage": None}
    )

    html = _html(road_context=partial_road, neighbour_buildings=unavailable)

    limitations = _text(html, "#sec-limitations")
    assert ROAD_ACCESS_DISCLAIMER in limitations
    assert "wykracza poza zasięg zaimportowanego powiatu" in limitations
    assert "Zabudowa sąsiednia" in limitations and "nie oznacza braku budynków" in limitations
    assert "stan na 22.01.2026" in limitations


def test_template_and_context_do_not_define_a_legal_road_access_field() -> None:
    """Kryterium 308: w szablonie PDF i w kontekście raportu nie ma pola prawnego dostępu do drogi publicznej."""
    response, _ = load_fixture("multizone")
    context = _build_report_context(response)

    assert "legal_public_road_access" not in TEMPLATE.read_text(encoding="utf-8")
    assert "legal_access" not in str(context.keys()) and "legal_public_road_access" not in str(context)
    assert not [key for key in context["road"] if "legal" in key]


def test_sources_and_warnings_of_the_two_sections_reach_the_registry_and_the_warning_list() -> None:
    response, _ = load_fixture("multizone")

    sources = bdot_sources(response.road_context, response.neighbour_buildings)
    assert {item.source_id for item in sources} == {"bdot10k"} and len(sources) == 2

    partial = response.road_context.model_copy(update={"warnings": ["Obszar poza zasięgiem."], "search_area_within_coverage": False})
    failed = disabled_neighbour_buildings([50.0])
    warnings = bdot_warnings(partial, failed)
    codes = {item.code: item.severity for item in warnings}
    assert codes["ROAD_CONTEXT_WARNING"] == "warning"  # częściowe pokrycie: ostrzeżenie, nie informacja
    assert codes["NEIGHBOUR_BUILDINGS_FEATURE_DISABLED"] == "warning"
    assert all(item.source_name == "bdot10k" for item in warnings)
    assert bdot_warnings(None, None) == [] and disabled_road_context().reason_code == "FEATURE_DISABLED"
    assert isinstance(NeighbourBuildingsResult.model_validate(failed.model_dump()), NeighbourBuildingsResult)
