"""Symbol strefy: forma kanoniczna, zgodność z UI, korpusy i dopasowanie w tekście (PV3-04)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.schemas.analyze import AnalyzeResumeRequest, ManualZoneContext
from app.services import cache
from app.services.mpzp_parser_segment import (
    DocumentSegment,
    find_zone_sections,
    segment_document,
)
from app.services.mpzp_parser_extract import TextExtractionResult
from app.services.mpzp_zones import (
    ZONE_SYMBOL_ALLOWED_PATTERN,
    ZONE_SYMBOL_MAX_LENGTH,
    validate_zone_symbol_format,
)
from app.shared import zone_symbol as zs
from tests.parcel_fixtures_config import find_repo_root

REPO_ROOT = find_repo_root()
RULES = json.loads((REPO_ROOT / "shared" / "zone-symbol-rules.json").read_text(encoding="utf-8"))
REFERENCE_CORPUS = REPO_ROOT / "backend/tests/fixtures/reference_corpus"
PARSER_CORPUS = REPO_ROOT / "backend/tests/fixtures/mpzp_evaluation/manifest.json"


# --- jedno źródło reguł dla backendu, API i UI ------------------------------------------------


def test_backend_rules_equal_the_shared_file_used_by_the_ui() -> None:
    assert RULES["pattern"] == zs.ZONE_SYMBOL_ALLOWED_PATTERN == ZONE_SYMBOL_ALLOWED_PATTERN
    assert RULES["max_length"] == zs.ZONE_SYMBOL_MAX_LENGTH == ZONE_SYMBOL_MAX_LENGTH
    assert RULES["max_raw_length"] == zs.ZONE_SYMBOL_MAX_RAW_LENGTH
    assert RULES["min_length"] == zs.ZONE_SYMBOL_MIN_LENGTH
    assert RULES["rules_version"] == zs.ZONE_SYMBOL_RULES_VERSION


def test_api_publishes_the_same_pattern_the_ui_reads_from_the_file() -> None:
    context = ManualZoneContext(
        document_status="not_provided",
        symbol_allowed_pattern=ZONE_SYMBOL_ALLOWED_PATTERN,
        notice="n",
    )
    assert context.symbol_allowed_pattern == RULES["pattern"]
    assert context.symbol_max_length == RULES["max_length"]
    assert context.symbol_rules_version == RULES["rules_version"]
    field = AnalyzeResumeRequest.model_fields["zone_symbol"]
    assert any(getattr(m, "max_length", None) == RULES["max_raw_length"] for m in field.metadata)


@pytest.mark.parametrize("case", RULES["cases"], ids=lambda case: repr(case["input"])[:40])
def test_shared_conformance_cases(case: dict[str, object]) -> None:
    """Te same przypadki sprawdza test UI (``lib/zoneSymbol.test.ts``)."""
    raw = str(case["input"])
    if case["valid"]:
        assert zs.validate_zone_symbol(raw) == case["canonical"]
        assert zs.canonicalize_zone_symbol(raw) == case["canonical"]
        assert re.fullmatch(RULES["pattern"], str(case["canonical"]))
    else:
        with pytest.raises(zs.InvalidZoneSymbolError) as error:
            zs.validate_zone_symbol(raw)
        assert error.value.code == case["code"]


def test_the_shared_pattern_is_a_valid_javascript_unicode_regex_without_python_only_syntax() -> None:
    pattern = RULES["pattern"]
    assert not re.search(r"\(\?[<P=#]|\(\?i|\\Z|\\A|\(\?\(", pattern)  # tylko podzbiór wspólny z JS
    assert pattern.startswith("^") and pattern.endswith("$")


# --- korpusy: wszystkie realne symbole przechodzą -------------------------------------------------


def _discovery_candidates() -> list[str]:
    found: list[str] = []

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "candidate_zone_symbols" and isinstance(item, list):
                    found.extend(str(symbol) for symbol in item)
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for path in sorted(REFERENCE_CORPUS.rglob("*.json")):
        walk(json.loads(path.read_text(encoding="utf-8")))
    return sorted(set(found))


def test_all_fifteen_discovery_candidates_of_the_reference_corpus_pass() -> None:
    candidates = _discovery_candidates()
    assert len(candidates) == 15
    assert {"22 KD G1/2(Z1/4)", "6KD L", "7 UC,U,M", "KL 1/2"} <= set(candidates)  # odrzucane dotąd
    for symbol in candidates:
        assert validate_zone_symbol_format(symbol) == symbol  # forma kanoniczna = zapis źródła


def test_all_forty_two_plan_symbols_of_the_parser_corpus_pass() -> None:
    manifest = json.loads(PARSER_CORPUS.read_text(encoding="utf-8"))
    symbols = sorted({zone["symbol"] for sample in manifest["samples"] for zone in sample["zones"]})
    assert len(symbols) == 42
    assert {"146 MN", "1 PK", "1.4U,MN"} <= set(symbols)
    for symbol in symbols:
        assert validate_zone_symbol_format(symbol) == symbol


@pytest.mark.parametrize("raw", ["", "   ", "\t", "\n", "230\u0000U", "230\x07U", "230\nU", "a\x7fb"])
def test_empty_and_control_characters_are_rejected(raw: str) -> None:
    with pytest.raises(zs.InvalidZoneSymbolError):
        validate_zone_symbol_format(raw)


# --- zgodność wsteczna ----------------------------------------------------------------------------


def test_every_symbol_valid_under_the_old_rule_keeps_its_stored_form() -> None:
    """Zapisane symbole nie zmieniają postaci: stara reguła przyjmowała tylko ``[A-Za-zĄ…0-9._/-]``."""
    old = re.compile(r"^[A-Za-z0-9ĄąĆćĘęŁłŃńÓóŚśŹźŻż._/-]+$")
    alphabet = "AaZz09ĄąĆćĘęŁłŃńÓóŚśŹźŻż._/-"
    samples = {"".join(chars) for chars in zip(alphabet, alphabet[1:] + alphabet[:1], alphabet[2:] + alphabet[:2])}
    samples |= {alphabet[:n] for n in range(1, len(alphabet) + 1)} | {"230_U", "MN.1", "6.8.MW/U", "ŁŻ-1"}
    for symbol in sorted(samples):
        assert old.fullmatch(symbol)
        assert validate_zone_symbol_format(symbol) == symbol
        assert zs.canonicalize_zone_symbol(symbol) == symbol


def test_symbols_rejected_by_the_old_rule_are_the_only_ones_that_can_change_form() -> None:
    # tylko to, co dawniej odrzucano (spacje, NFKC pełnych szerokości), ma teraz inną postać
    assert validate_zone_symbol_format("146  MN") == "146 MN"
    assert validate_zone_symbol_format("ＭＮ") == "MN"


def test_result_contract_version_changes_the_cache_signature() -> None:
    assert "mpzp-v2.2" in cache.RESULT_CONTRACT_VERSION  # wynik z reguł sprzed PV3-04 nie jest serwowany z cache


# --- klucz porównania ---------------------------------------------------------------------------------


def test_comparison_key_ignores_spacing_and_case_but_keeps_similar_symbols_apart() -> None:
    assert zs.same_zone_symbol("146 MN", "146MN")
    assert zs.same_zone_symbol(" 146  mn ", "146 MN")
    assert zs.same_zone_symbol("KL 1/2", "kl1/2")
    assert not zs.same_zone_symbol("MN", "MN.1")
    assert not zs.same_zone_symbol("MN", "146 MN")
    assert not zs.same_zone_symbol("U", "MW/U")


# --- dopasowanie w tekście ------------------------------------------------------------------------------


def _hits(symbol: str, text: str) -> list[str]:
    return [text[start:end] for start, end in zs.find_zone_symbol_mentions(text, symbol)]


@pytest.mark.parametrize(
    ("symbol", "text"),
    [
        ("146 MN", "Dla terenu oznaczonego symbolem 146MN ustala się:"),
        ("146MN", "Dla terenu oznaczonego symbolem 146 MN ustala się:"),
        ("146 MN", "Dla terenu oznaczonego symbolem 146  MN ustala się:"),
        ("146 MN", "Dla terenu oznaczonego symbolem 146\nMN ustala się:"),  # złamanie wiersza
        ("146 MN", "teren 146 MN"),
        ("1 PK", "Dla terenu 1PK:"),
        ("22 KD G1/2(Z1/4)", "tereny 22KD G1/2(Z1/4) oraz"),
        ("1.4U,MN", "Teren oznaczony na rysunku planu symbolem 1.4U,MN."),
        ("MN", "Dla terenu MN. Wysokość"),
        ("MN", "§ 5. Dla terenu MN obowiązuje"),
        ("MN", "teren 146 MN oraz teren MN"),  # drugi zapis jest osobną strefą MN
        ("MN.11", "w terenach: MN.3, MN.5 MN.11, MNi.1"),  # cyfra kończąca inny symbol nie jest samodzielna
        ("MN", "146 MN i MN"),
    ],
)
def test_symbol_is_found_regardless_of_spacing(symbol: str, text: str) -> None:
    assert _hits(symbol, text), (symbol, text)


@pytest.mark.parametrize(
    ("symbol", "text"),
    [
        ("MN", "teren MN.1 oraz MN/U"),  # obecne gwarancje: bez scalania przez podciąg
        ("MN", "teren 1.4U,MN"),  # przecinek przyklejony do symbolu należy do niego
        ("U", "teren MW/U oraz 1.4U,MN"),
        ("U", "teren 7 UC,U,M"),
        ("G1/2", "teren 22 KD G1/2(Z1/4)"),  # nawias przyklejony: część symbolu złożonego
        ("Z1/4", "teren 22 KD G1/2(Z1/4)"),
        ("MN.1", "teren MN.11"),
        ("MN", "teren MNU"),
        ("146 MN", "teren 1146 MN"),
        ("146 MN", "teren 146 MNU"),
        ("MN", "teren 146 MN"),  # ogon symbolu ze spacją nie jest osobną strefą
        ("MN", "teren\n146\u00a0MN"),
        ("MN", "tereny 3 MN, 4 MN"),
        ("PK", "teren 1 PK"),
    ],
)
def test_similar_symbols_are_not_merged_by_substring(symbol: str, text: str) -> None:
    assert _hits(symbol, text) == [], (symbol, text)


def test_comma_with_space_is_a_list_separator_not_a_part_of_the_symbol() -> None:
    assert _hits("1Up", "oznaczonych w planie symbolami 1Up, 2Up:")
    assert _hits("2Up", "oznaczonych w planie symbolami 1Up, 2Up:")
    assert _hits("MN", "tereny U, MN i MW")


def test_parser_finds_the_zone_section_with_and_without_a_space_and_mn_still_not_mn_1() -> None:
    def extraction(text: str) -> TextExtractionResult:
        return TextExtractionResult(pages=[text])

    document = (
        "§ 5. Dla terenu oznaczonego symbolem 146MN ustala się wysokość 9 m.\n"
        "§ 6. Dla terenu oznaczonego symbolem MN.1 ustala się wysokość 12 m."
    )
    segments: list[DocumentSegment] = segment_document(extraction(document))
    spaced = find_zone_sections(segments, ["146 MN"])[0]
    unspaced = find_zone_sections(segments, ["146MN"])[0]
    assert [c.segment_id for c in spaced.candidates] == [c.segment_id for c in unspaced.candidates] != []
    assert not spaced.manual_review_required
    mn = find_zone_sections(segments, ["MN"])[0]
    assert mn.candidates == [] and mn.manual_review_required  # `MN` nie dopasowuje `MN.1` ani `146MN`
    mn1 = find_zone_sections(segments, ["MN.1"])[0]
    assert [c.page_number for c in mn1.candidates] == [1] and len(mn1.candidates) == 1
    # ten sam dokument ze symbolem zapisanym ze spacją w tekście
    segments_spaced = segment_document(extraction(document.replace("146MN", "146 MN")))
    assert find_zone_sections(segments_spaced, ["146MN"])[0].candidates


def test_real_stare_miasto_scan_zone_is_found_with_and_without_a_space() -> None:
    manifest = json.loads(PARSER_CORPUS.read_text(encoding="utf-8"))
    path = (PARSER_CORPUS.parent / manifest["documents"]["stare_miasto_xliv_305_2002"]["path"]).resolve()
    pages = json.loads((path / "pages.json").read_text(encoding="utf-8"))["pages"]
    segments = segment_document(TextExtractionResult(pages=pages))
    with_space = find_zone_sections(segments, ["146 MN"])[0]
    without = find_zone_sections(segments, ["146MN"])[0]
    assert with_space.candidates and [c.segment_id for c in with_space.candidates] == [c.segment_id for c in without.candidates]
    # ``MN`` nie jest strefą w tekście ``146 MN``
    assert find_zone_sections(segments, ["MN"])[0].candidates == []


def test_migration_027_widens_the_resolved_symbol_column_and_follows_head_026() -> None:
    versions = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    migration = (versions / "027_zone_symbol_length.py").read_text(encoding="utf-8")
    assert 'revision: str = "027_zone_symbol_length"' in migration
    assert 'down_revision: Union[str, None] = "026_section_quality_matrix"' in migration
    upgrade = migration.split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]
    assert "length=20" in upgrade and "length=50" in upgrade and "UPDATE" not in upgrade.upper()
    from app.models.analysis import Analysis

    assert Analysis.__table__.c.resolved_zone_symbol.type.length == 50
    assert len(list(versions.glob("02[0-9]_*.py"))) >= 8  # historia nie została przepisana
