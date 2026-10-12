"""Zamrożony zbiór realnych uchwał MPZP ``tests/fixtures/mpzp_live`` (AU-102, Task 22.2).

Trzy grupy testów:

1. **Spójność zbioru** — adnotacje (strony, cytaty sekcji i wartości) zgadzają się z zamrożonym tekstem stron,
   skróty pasują, w repozytorium nie ma surowych PDF, a status adnotacji jest uczciwie opisany
   (``independent_human_review: pending``: adnotacje przygotował asystent AI, nie człowiek).
2. **Progi metryk** — udział stref z co najmniej jednym parametrem albo jawnym, poprawnym powodem braku
   (≥ 70% wg zadania) oraz metryka surowa (parametr z sekcji strefy), osobno dla ``legacy`` i ``v3``.
3. **Regresje per przyczyna** — każda przyczyna zerowego wyniku z ``docs/evaluation/au-102-zone-section-causes.md``
   ma test na REALNYM tekście uchwały, a nie na zmyślonym przykładzie.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.services.mpzp_parser import parse_mpzp_document
from app.services.mpzp_parser_reasons import SCANNED_NO_OCR, ZONE_NOT_IN_DOCUMENT, symbol_in_text
from scripts import evaluate_mpzp_live as live

LIVE_DIR = live.LIVE_DIR
MANIFEST = json.loads((LIVE_DIR / "manifest.json").read_text("utf-8"))
DOCUMENTS = {document["slug"]: document for document in MANIFEST["documents"]}


def _squash(text: str) -> str:
    return " ".join(text.replace(" ", " ").split())


def _pages(slug: str) -> list[str]:
    return json.loads((LIVE_DIR / slug / "pages.json").read_text("utf-8"))["pages"]


async def _parse(slug: str, symbols: list[str], mode: str):  # noqa: ANN202
    document = DOCUMENTS[slug]
    extraction, source = live.build_extraction(document)
    with patch("app.services.mpzp_parser.extract_document_text", new=AsyncMock(return_value=extraction)):
        return await parse_mpzp_document(live.document_blob(source), symbols, mode=mode)  # type: ignore[arg-type]


def _zone(result: Any, symbol: str) -> Any:
    return next(zone for zone in result.zones if zone.zone_symbol == symbol)


def _codes(result: Any, symbol: str) -> set[str]:
    return {warning.code for warning in result.warnings if warning.zone_symbol == symbol}


# --- 1. spójność zbioru ----------------------------------------------------------------------


def test_the_manifest_discloses_that_annotations_were_made_by_an_assistant() -> None:
    assert MANIFEST["schema"] == "mpzp-live-annotations/1"
    assert MANIFEST["independent_human_review"] == "pending"
    assert "asystent AI" in MANIFEST["annotator"] and "brak niezależnego przeglądu" in MANIFEST["annotator"]


def test_the_corpus_covers_the_required_municipalities_and_both_splits() -> None:
    municipalities = {document["municipality"] for document in MANIFEST["documents"]}
    assert {"Kraków", "Bielsko-Biała", "Pisz", "Legnica", "Góra Kalwaria", "Czarny Bór", "Ruciane-Nida"} <= municipalities
    assert {document["split"] for document in MANIFEST["documents"]} == {"development", "holdout"}
    assert len(DOCUMENTS) == len(MANIFEST["documents"]) >= 9
    zones = {(document["slug"], zone["zone_symbol"]) for document in MANIFEST["documents"] for zone in document["zones"]}
    assert len(zones) >= 40
    assert sum(len(zone["parameters"]) for document in MANIFEST["documents"] for zone in document["zones"]) >= 100


def test_no_raw_pdf_or_oversized_file_is_stored_in_the_repository() -> None:
    files = [path for path in LIVE_DIR.rglob("*") if path.is_file()]
    assert not [path for path in files if path.suffix.lower() in {".pdf", ".zip", ".png", ".jpg"}]
    assert sum(path.stat().st_size for path in files) < 3 * 1024 * 1024


@pytest.mark.parametrize("slug", sorted(DOCUMENTS))
def test_hashes_and_page_counts_match_the_frozen_text(slug: str) -> None:
    import hashlib

    document = DOCUMENTS[slug]
    pages_file = LIVE_DIR / slug / "pages.json"
    assert hashlib.sha256(pages_file.read_bytes()).hexdigest() == document["pages_sha256"]
    assert len(_pages(slug)) == document["page_count"]
    source = json.loads((LIVE_DIR / slug / "source.json").read_text("utf-8"))
    assert source["document_sha256"] == document["document_sha256"] and source["url"] == document["source_url"]
    assert re.fullmatch(r"[0-9a-f]{64}", document["document_sha256"])
    assert source["tls_verification"] == "enabled"


def _annotation_cases() -> list[Any]:
    return [
        pytest.param(document["slug"], zone, id=f"{document['slug']}:{zone['zone_symbol']}:{index}")
        for document in MANIFEST["documents"]
        for index, zone in enumerate(document["zones"])
    ]


@pytest.mark.parametrize(("slug", "zone"), _annotation_cases())
def test_every_annotation_points_at_quotes_that_exist_on_the_stated_pages(slug: str, zone: dict[str, Any]) -> None:
    pages = [_squash(page) for page in _pages(slug)]
    assert zone["expected"] in {"parameters", ZONE_NOT_IN_DOCUMENT, SCANNED_NO_OCR}
    section = zone["section"]
    if zone["expected"] != "parameters":
        assert section is None and zone["parameters"] == []
        return
    assert section is not None and 1 <= section["first_page"] <= section["last_page"] <= len(pages)
    assert _squash(section["start_quote"]) in pages[section["first_page"] - 1]
    assert zone["parameters"], "strefa z oczekiwanymi parametrami musi je wymieniać"
    for parameter in zone["parameters"]:
        assert section["first_page"] <= parameter["page"] <= section["last_page"]
        assert _squash(parameter["quote"]) in pages[parameter["page"] - 1], parameter


def test_zones_expected_to_be_absent_are_really_absent_and_scans_really_have_no_text() -> None:
    for document in MANIFEST["documents"]:
        text = "\n".join(_pages(document["slug"]))
        for zone in document["zones"]:
            if zone["expected"] == ZONE_NOT_IN_DOCUMENT:
                assert text.strip() and not symbol_in_text(text, zone["zone_symbol"]), zone["zone_symbol"]
            if zone["expected"] == SCANNED_NO_OCR:
                assert not text.strip() and document["needs_ocr"] is True


def test_the_corpus_is_separate_from_the_bk_603_samples() -> None:
    bk603 = json.loads((Path(__file__).parent / "fixtures" / "mpzp_evaluation" / "manifest.json").read_text("utf-8"))
    bk603_hashes = {
        json.loads((Path(__file__).parent / "fixtures" / "mpzp_evaluation" / meta["path"] / "source.json").read_text("utf-8")).get("document_sha256")
        for meta in bk603["documents"].values()
        if (Path(__file__).parent / "fixtures" / "mpzp_evaluation" / meta["path"] / "source.json").exists()
    } - {None}
    assert not bk603_hashes & {document["document_sha256"] for document in MANIFEST["documents"]}


# --- 2. progi metryk -------------------------------------------------------------------------


@pytest.fixture(scope="module")
async def summaries() -> dict[str, dict[str, Any]]:
    return {mode: await live.evaluate(mode) for mode in ("legacy", "v3")}


@pytest.mark.parametrize("mode", ["legacy", "v3"])
async def test_at_least_70_percent_of_zones_have_a_parameter_or_a_correct_reason(
    summaries: dict[str, dict[str, Any]], mode: str
) -> None:
    summary = summaries[mode]
    assert summary["overall"]["zones"] >= 40
    assert summary["overall"]["share"] >= 0.70  # kryterium zadania
    for split, share in summary["by_split"].items():  # także osobno na stronach rozwojowych i „wstrzymanych”
        assert share["share"] >= 0.70, split
    assert summary["verdicts"].get("fail_unexpected_parameters", 0) == 0  # powód braku nigdy obok wartości


@pytest.mark.parametrize("mode", ["legacy", "v3"])
async def test_a_parameter_counts_only_when_it_comes_from_the_zones_own_section(
    summaries: dict[str, dict[str, Any]], mode: str
) -> None:
    assert summaries[mode]["strict"]["share"] >= 0.90


async def test_v3_reads_values_from_the_zone_section_and_not_from_other_places(summaries: dict[str, dict[str, Any]]) -> None:
    v3 = summaries["v3"]
    assert v3["parameters_outside_section"] == 0
    assert v3["expected_recall"] >= 0.85
    # tryb legacy skleja segmenty z wielu miejsc: znany, zmierzony kompromis (patrz au-102-zone-section-causes.md)
    assert summaries["legacy"]["parameters_outside_section"] > 0


# --- 3. regresje per przyczyna ---------------------------------------------------------------


@pytest.mark.parametrize("mode", ["legacy", "blocks"])
async def test_mw_u_4_has_at_least_three_parameters_with_page_and_quote(mode: str) -> None:
    """Kryterium zadania: Kraków, strefa ``MW/U.4`` (wcześniej 0 parametrów w obu trybach)."""
    result = await _parse("krakow_stare_miasto_xii_131_11", ["MW/U.4"], mode)
    parameters = _zone(result, "MW/U.4").parameters
    assert len(parameters) >= 3
    pages = _pages("krakow_stare_miasto_xii_131_11")
    for parameter in parameters:
        assert parameter.page_number is not None and 42 <= parameter.page_number <= 44  # sekcja § 58, str. 42–44
        assert parameter.source_text and _squash(parameter.source_text) in _squash(pages[parameter.page_number - 1])
    assert {"primary_use"} <= {p.name for p in parameters}
    assert not {"ZONE_SECTION_NOT_FOUND", ZONE_NOT_IN_DOCUMENT} & _codes(result, "MW/U.4")


async def test_mw_u_4_in_v3_has_the_prohibition_from_page_43_not_from_a_same_worded_sentence_elsewhere() -> None:
    """Przyczyna: dowód opisowy był szukany od początku dokumentu i trafiał w cudzą sekcję (str. 39)."""
    result = await _parse("krakow_stare_miasto_xii_131_11", ["MW/U.4"], "blocks")
    by_name = {p.name: p for p in _zone(result, "MW/U.4").parameters}
    assert by_name["prohibition"].page_number == 43 and by_name["prohibition"].normalized_value.startswith("zakaz lokalizacji lokali mieszkalnych")
    assert by_name["prohibition"].char_start is not None and by_name["prohibition"].block_id
    permissions = [p for p in _zone(result, "MW/U.4").parameters if p.name == "permission"]
    assert any(p.page_number == 43 and p.normalized_value.startswith("możliwość lokalizacji lokali usługowych") for p in permissions)


@pytest.mark.parametrize("mode", ["legacy", "blocks"])
async def test_a_symbol_named_only_by_a_range_gets_the_shared_section(mode: str) -> None:
    """Przyczyna: ``Dla terenów oznaczonych symbolami 1KDZ-11KDZ`` (Pisz) — symbol ``2KDZ`` nigdzie nie stoi wprost."""
    result = await _parse("pisz_xxi_232_20", ["2KDZ", "2PU", "2ZP", "3E"], mode)
    for symbol in ("2KDZ", "2PU", "2ZP", "3E"):
        assert _zone(result, symbol).parameters, symbol
        assert not {"ZONE_SECTION_NOT_FOUND", ZONE_NOT_IN_DOCUMENT} & _codes(result, symbol)
    use = next(p for p in _zone(result, "2KDZ").parameters if p.name == "primary_use")
    assert use.normalized_value == "droga publiczna - zbiorcza" and use.page_number == 26


async def test_range_sections_keep_the_mnu_and_mnu_families_apart() -> None:
    """Pisz ma rodziny ``MNu`` (§ 17) i ``MNU`` (§ 18); numeryczne wskaźniki § 18 dotyczą 1MNU–20MNU, nie 21MNU–23MNU."""
    result = await _parse("pisz_xxi_232_20", ["12MNU", "5MNu"], "blocks")
    twelve = {p.name: p.normalized_value for p in _zone(result, "12MNU").parameters}
    assert twelve["max_intensity"] == 0.75 and twelve["max_building_coverage_percent"] == 50  # 1MNU–20MNU, nie 21–23MNU (2,0 / 100%)
    five = {p.name: p.normalized_value for p in _zone(result, "5MNu").parameters}
    assert five["max_building_coverage_percent"] == 70 and five["max_intensity"] == 0.75
    assert {p.page_number for p in _zone(result, "12MNU").parameters if p.name == "max_intensity"} == {12}


async def test_detailed_provisions_intro_opens_the_zone_section() -> None:
    """Przyczyna: ``Ustalenia szczegółowe dla terenu oznaczonego symbolem ZC:`` (Ruciane-Nida) nie było wprowadzeniem strefy."""
    result = await _parse("ruciane_nida_lxxiv_686_2023", ["ZC", "KDD"], "blocks")
    zc = {p.name: p.normalized_value for p in _zone(result, "ZC").parameters}
    assert zc["max_building_height_m"] == 5.0 and zc["max_intensity"] == 0.01 and zc["primary_use"] == "teren cmentarza"
    assert {p.page_number for p in _zone(result, "ZC").parameters} == {4}
    assert _zone(result, "KDD").parameters and "ZONE_SCOPE_AMBIGUOUS" not in _codes(result, "ZC")


async def test_a_list_of_symbols_after_a_keyword_chain_is_one_shared_section() -> None:
    """Przyczyna: słowo „oznaczone” przed „symbolami” zjadało listę, więc punkty 7/9/10 uchodziły za sekcję własną."""
    result = await _parse("ruciane_nida_lxxiv_686_2023", ["2ZL"], "blocks")
    parameters = _zone(result, "2ZL").parameters
    assert {p.name for p in parameters} >= {"primary_use", "prohibition"}
    assert {p.page_number for p in parameters} == {4}
    assert "ZONE_SCOPE_MULTIPLE_SECTIONS" not in _codes(result, "2ZL")


@pytest.mark.parametrize("mode", ["legacy", "blocks"])
async def test_road_zones_get_a_primary_use_from_the_bare_and_terrain_labels(mode: str) -> None:
    gk = await _parse("gora_kalwaria_iv_30_2024", ["2KR"], mode)
    assert any(p.name == "primary_use" and p.normalized_value == "komunikacja drogowa wewnętrzna" for p in _zone(gk, "2KR").parameters)
    legnica = await _parse("legnica_xxiv_220_16", ["1KDL1/2"], mode)
    assert any(
        p.name == "primary_use" and p.normalized_value.startswith("tereny drogi publicznej klasy lokalnej L1/2")
        for p in _zone(legnica, "1KDL1/2").parameters
    )


@pytest.mark.parametrize("mode", ["legacy", "blocks"])
async def test_missing_sections_carry_an_explicit_reason(mode: str) -> None:
    kraków = await _parse("krakow_stare_miasto_xii_131_11", ["MW/U.99"], mode)
    assert _codes(kraków, "MW/U.99") == {ZONE_NOT_IN_DOCUMENT} and _zone(kraków, "MW/U.99").parameters == []
    bielsko = await _parse("bielsko_biala_liii_749_1998", ["A1", "A6", "B1"], mode)
    for symbol in ("A1", "A6", "B1"):
        assert _codes(bielsko, symbol) == {SCANNED_NO_OCR} and _zone(bielsko, symbol).parameters == []
    assert "NEEDS_OCR" in {w.code for w in bielsko.warnings}


@pytest.mark.parametrize("mode", ["legacy", "blocks"])
async def test_the_holdout_documents_are_parsed_by_the_same_code_as_the_development_ones(mode: str) -> None:
    for slug in ("legnica_xxiv_220_16", "czarny_bor_lxxxi_420_2024", "ruciane_nida_lxxiv_686_2023"):
        assert DOCUMENTS[slug]["split"] == "holdout"
        symbols = [zone["zone_symbol"] for zone in DOCUMENTS[slug]["zones"] if zone["expected"] == "parameters"]
        result = await _parse(slug, list(dict.fromkeys(symbols)), mode)
        assert all(_zone(result, symbol).parameters for symbol in dict.fromkeys(symbols)), slug


async def test_a_one_sentence_designating_paragraph_opens_the_section_czarny_bor() -> None:
    """Przyczyna: „§ 14. 1. Wyznacza się teren … symbolem 1MN.” nie miało końca wstępu, więc zakres był „cały paragraf”."""
    result = await _parse("czarny_bor_lxxxi_420_2024", ["1MN"], "blocks")
    parameters = {p.name: p for p in _zone(result, "1MN").parameters}
    assert parameters["primary_use"].normalized_value == "zabudowa mieszkaniowa jednorodzinna"
    biologically_active = {p.normalized_value for p in _zone(result, "1MN").parameters if p.name == "min_biologically_active_percent"}
    assert 50 in biologically_active and parameters["max_building_coverage_percent"].page_number == 9
    assert "ZONE_SCOPE_AMBIGUOUS" not in _codes(result, "1MN")
    assert all(p.scope_kind == "zone_section" for p in _zone(result, "1MN").parameters)
