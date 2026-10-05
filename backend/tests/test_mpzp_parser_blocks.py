"""Tryb blokowy parsera: prawdziwe źródło każdej wartości, zakresy niemieszane (PV3-06)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.schemas.mpzp import MpzpParameter, MpzpParserWarning, MpzpParseResult, MpzpZoneResult
from app.services.mpzp_parser import MPZP_PARSER_VERSION, parse_mpzp_document
from app.services.mpzp_parser_blocks import MPZP_PARSER_VERSION_BLOCKS
from app.services.mpzp_parser_numeric import extract_numeric_matches
from app.services.mpzp_parser_validate import validate_mpzp_result
from scripts import evaluate_mpzp_parser as ev
from tests.parcel_fixtures_config import find_repo_root

FIXTURES = find_repo_root() / "backend/tests/fixtures/mpzp_evaluation"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))


def _parse(doc_id: str, symbols: list[str], mode: str) -> tuple[MpzpParseResult, dict[str, Any]]:
    loaded = ev.load_document(FIXTURES, MANIFEST["documents"][doc_id])
    return asyncio.run(ev.run_parser(loaded, symbols, mode)), loaded


def _zone(result: MpzpParseResult, symbol: str) -> MpzpZoneResult:
    return next(zone for zone in result.zones if zone.zone_symbol == symbol)


# --- strefa MN.11 z Krakowa: strona i zakres znaków z miejsca dopasowania ------------------------------------------


def test_krakow_mn11_height_has_its_own_page_and_character_range() -> None:
    result, loaded = _parse("krakow_morelowa", ["MN.11"], "blocks")
    heights = [p for p in _zone(result, "MN.11").parameters if p.name == "max_building_height_m"]
    by_value = {p.normalized_value: p for p in heights}
    assert set(by_value) == {11.0, 9.5}  # dokładnie wartości sekcji MN.11 (nie 5 m z odległej strony)
    page_texts = dict(zip(loaded["page_numbers"], loaded["pages"], strict=True))
    for parameter in heights:
        assert parameter.page_number == 19  # punkt „11) dla terenu MN.11:” leży na stronie 19
        assert parameter.scope_kind == "zone_section" and parameter.scope_strategy == 3
        raw = page_texts[parameter.page_number][parameter.char_start : parameter.char_end]
        assert raw.replace("\xa0", " ") == parameter.raw_value  # zakres wskazuje dokładnie dopasowaną wartość
        assert parameter.block_id and parameter.segment_id == parameter.block_id
    assert by_value[11.0].raw_value == "11 m" and by_value[9.5].raw_value == "9,5 m"


def test_blocks_mode_does_not_take_mn11_heights_from_the_neighbouring_zone_pages() -> None:
    legacy, _ = _parse("krakow_morelowa", ["MN.11"], "legacy")
    blocks, _ = _parse("krakow_morelowa", ["MN.11"], "blocks")
    legacy_pages = {p.page_number for p in _zone(legacy, "MN.11").parameters if p.name == "max_building_height_m"}
    block_pages = {p.page_number for p in _zone(blocks, "MN.11").parameters if p.name == "max_building_height_m"}
    assert block_pages == {19} and block_pages != legacy_pages  # tryb dotychczasowy sięga po strony innych stref
    assert all(p.page_number == 19 for p in _zone(blocks, "MN.11").parameters if p.scope_kind == "zone_section" and p.char_start is not None)


# --- kontrakt trybu ----------------------------------------------------------------------------------------------------


def test_default_mode_is_the_unchanged_legacy_pipeline() -> None:
    result, _ = _parse("bielsko_viii_187_2024", ["230_U"], "legacy")
    assert {p.parser_version for z in result.zones for p in z.parameters} == {MPZP_PARSER_VERSION}
    assert all(p.block_id is None and p.char_start is None and p.scope_kind is None for z in result.zones for p in z.parameters)
    blocks, _ = _parse("bielsko_viii_187_2024", ["230_U"], "blocks")
    assert {p.parser_version for z in blocks.zones for p in z.parameters} == {MPZP_PARSER_VERSION_BLOCKS}
    assert blocks.status in {"complete", "partial"} and blocks.document_audit is not None


def _parse_text(pages: list[str], symbols: list[str]) -> MpzpParseResult:
    from unittest.mock import AsyncMock, patch

    from app.schemas.source import SourceMetadata
    from app.services.mpzp_fetch import DocumentBlob
    from app.services.mpzp_parser_extract import TextExtractionResult

    blob = DocumentBlob(content=b"%PDF", media_type="application/pdf", filename="a.pdf",
                        source_metadata=SourceMetadata(source_name="MPZP_BIP", source_url="https://example.invalid/a.pdf",
                                                       confidence=0.9, manual_review_required=False))
    with patch("app.services.mpzp_parser.extract_document_text", new=AsyncMock(return_value=TextExtractionResult(pages=pages))):
        return asyncio.run(parse_mpzp_document(blob, symbols, scope_mode="blocks"))


_GENERAL = (
    "§ 2. 1. Dla terenu oznaczonego symbolem MN ustala się:\n1) maksymalna wysokość zabudowy do 9 m.\n",
    "§ 11. Ustala się wskaźniki, dla terenów oznaczonych symbolami:\n"
    "a) MN – maksymalna wysokość zabudowy do 12 m,\nb) US – maksymalna wysokość zabudowy do 15 m,\n",
)


def test_values_of_a_general_clause_keep_their_scope_and_never_mix_with_the_zone_section() -> None:
    result = _parse_text(list(_GENERAL), ["MN", "US"])
    mn = [p for p in _zone(result, "MN").parameters if p.name == "max_building_height_m"]
    by_scope = {p.scope_kind: p for p in mn}
    assert set(by_scope) == {"zone_section", "general_clause"}
    section, general = by_scope["zone_section"], by_scope["general_clause"]
    assert (section.normalized_value, section.page_number, section.scope_strategy) == (9.0, 1, 1)
    assert (general.normalized_value, general.page_number, general.scope_strategy) == (12.0, 2, 5)
    assert general.manual_review_required and not section.manual_review_required  # ogólna klauzula wymaga weryfikacji
    assert general.confidence < section.confidence and section.scope_confidence == 0.9
    # różne wartości z różnych zakresów nie są konfliktem i nie obniżają pewności sekcji strefy
    assert section.conflict_group_id is None and general.conflict_group_id is None and result.conflict_flags == []
    us = [p for p in _zone(result, "US").parameters if p.name == "max_building_height_m"]
    assert [(p.normalized_value, p.scope_kind) for p in us] == [(15.0, "general_clause")]  # US nie dostaje 9 ani 12 m


def test_conflicting_values_inside_one_scope_are_flagged() -> None:
    result = _parse_text(["§ 2. Dla terenu oznaczonego symbolem MN ustala się:\n1) wysokość zabudowy do 9 m;\n2) wysokość zabudowy do 12 m.\n"], ["MN"])
    heights = [p for p in _zone(result, "MN").parameters if p.name == "max_building_height_m"]
    assert {p.normalized_value for p in heights} == {9.0, 12.0}
    assert all(p.manual_review_required and p.conflict_group_id == "MN:max_building_height_m" for p in heights)
    assert any(w.code == "PARAMETER_CONFLICT" for w in result.warnings)


def test_unfound_symbol_yields_a_warning_and_no_values() -> None:
    result, _ = _parse("bielsko_viii_187_2024", ["999_NIE"], "blocks")
    assert [p for z in result.zones for p in z.parameters] == []
    assert any(w.code == "ZONE_SECTION_NOT_FOUND" and w.zone_symbol == "999_NIE" for w in result.warnings)


def test_scope_warnings_for_unresolved_scope_and_ocr_symbols_reach_the_result() -> None:
    result, _ = _parse("falenica_sim_ocr", ["A1MN"], "blocks")
    codes = {w.code for w in result.warnings if w.zone_symbol == "A1MN"}
    assert codes & {"ZONE_SYMBOL_OCR_MATCH", "ZONE_SCOPE_AMBIGUOUS"}
    lodz, _ = _parse("lodz_lxxviii_2337_23", ["6.6.MW/U"], "blocks")
    assert "ZONE_SCOPE_AMBIGUOUS" in {w.code for w in lodz.warnings}  # pozycje „w strefie SWZ …” zależą od rysunku


# --- wartości z różnych zakresów nie są ze sobą sprzeczne ----------------------------------------------------------------


def _parameter(value: float, scope: str | None) -> MpzpParameter:
    return MpzpParameter(name="max_building_height_m", normalized_value=value, unit="m", raw_value=f"{value} m",
                         source_text="x", page_number=1, confidence=0.8, manual_review_required=False, scope_kind=scope)  # type: ignore[arg-type]


def test_validator_compares_values_only_within_the_same_scope() -> None:
    mixed = MpzpParseResult(plan_id=None, status="complete", zones=[MpzpZoneResult(
        zone_symbol="MN", parameters=[_parameter(9.0, "zone_section"), _parameter(12.0, "general_clause")])])
    validated = validate_mpzp_result(mixed)
    assert validated.conflict_flags == [] and not any(w.code == "PARAMETER_CONFLICT" for w in validated.warnings)
    same_scope = MpzpParseResult(plan_id=None, status="complete", zones=[MpzpZoneResult(
        zone_symbol="MN", parameters=[_parameter(9.0, "zone_section"), _parameter(12.0, "zone_section")])])
    conflicting = validate_mpzp_result(same_scope)
    assert len(conflicting.conflict_flags) == 1 and all(p.manual_review_required for p in conflicting.zones[0].parameters)
    legacy = MpzpParseResult(plan_id=None, status="complete", zones=[MpzpZoneResult(
        zone_symbol="MN", parameters=[_parameter(9.0, None), _parameter(12.0, None)])])
    assert len(validate_mpzp_result(legacy).conflict_flags) == 1  # tryb dotychczasowy: bez zmian


def test_the_ambiguous_scope_warning_lowers_confidence_like_the_legacy_ambiguity() -> None:
    from app.schemas.mpzp import MpzpParserWarning

    def validated(warnings: list[MpzpParserWarning]) -> Any:
        result = MpzpParseResult(
            plan_id=None, status="complete",
            zones=[MpzpZoneResult(zone_symbol="MN", parameters=[_parameter(9.0, "fallback")])], warnings=warnings,
        )
        return validate_mpzp_result(result).zones[0].parameters[0]

    ambiguous = validated([MpzpParserWarning(stage="segment_document", code="ZONE_SCOPE_AMBIGUOUS", message="m",
                                             zone_symbol="MN", severity="warning")])
    plain = validated([])
    # Od PV3-09 wieloznaczność zakresu to cecha ``zone_ambiguous`` modelu skalibrowanego, nie mnożnik 0,85.
    assert ambiguous.confidence < plain.confidence
    assert ambiguous.confidence_features["zone_ambiguous"] is True and plain.confidence_features["zone_ambiguous"] is False


# --- dopasowania mają zakres w tekście ---------------------------------------------------------------------------------------


def test_numeric_matches_report_the_span_of_their_own_raw_value() -> None:
    text = (
        "maksymalna wysokość zabudowy: 11 m, a dla dachu płaskiego: 9,5 m; maksymalnej powierzchni zabudowy 30%; "
        "powierzchni biologicznie czynnej 60%; nie więcej niż 3 kondygnacje; w odległości 6,0 m od granicy; "
        "intensywność zabudowy: minimalna – 0,2 maksymalna – 1,2; kąta nachylenia połaci dachowych od 30° do 45°"
    )
    spans = {
        (name, match.normalized_value): text[match.start : match.end]
        for name, _unit, matches in extract_numeric_matches(text)
        for match in matches
    }
    assert spans[("max_building_height_m", 11.0)] == "11 m" and spans[("max_building_height_m", 9.5)] == "9,5 m"
    assert spans[("max_storeys", 3.0)] == "nie więcej niż 3 kondygnacje"
    assert spans[("setback_m", 6.0)].startswith("w odległości 6,0 m")
    assert spans[("roof_angle_min_deg", 30.0)] == spans[("roof_angle_max_deg", 45.0)] == "od 30° do 45°"
    assert spans[("min_intensity", 0.2)].endswith("0,2") and spans[("max_intensity", 1.2)].endswith("1,2")
    assert all(text[m.start : m.end] for _, _, ms in extract_numeric_matches(text) for m in ms)
    assert [name for name, _, _ in extract_numeric_matches("")][0] == "max_building_height_m"
