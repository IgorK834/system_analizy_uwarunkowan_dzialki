"""Kontrakt parsera odpowiedzi KIMPZP GetFeatureInfo na zamrożonych odpowiedziach (AU-004).

Fixtures w ``tests/fixtures/source_contracts/kimpzp/`` są nieprzetworzonymi odpowiedziami
usługi (manifest z SHA-256). Testy własności i odporności przekształcają rzeczywiste
odpowiedzi — kolejność wierszy, brakujące pola, nieznane nagłówki, zagnieżdżenie tabel,
ucięcie — i wymagają braku wyjątku oraz braku błędnego numeru uchwały.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from datetime import date
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from app.modules.planning.application.ports import KimpzpFeatureInfoParser
from app.modules.planning.composition import kimpzp_feature_info_parser
from app.modules.planning.domain.kimpzp_discovery import (
    KimpzpAct,
    KimpzpAmendment,
    KimpzpPointResult,
    combine_statuses,
    fold_text,
    legal_status_from_text,
    merge_acts,
    normalize_resolution_number,
    parse_source_date,
    resolution_number_from_text,
    sort_acts,
    summarize_points,
)
from app.modules.planning.infrastructure.kimpzp_feature_info import parse_kimpzp_feature_info

FIXTURES = Path(__file__).parent / "fixtures" / "source_contracts" / "kimpzp"
GORA_KALWARIA = "gora_kalwaria_141801_4.0701.23_8.html"

# plik -> (status, numery aktów w kolejności, symbole stref, numery wyłącznie jako zmiany)
EXPECTED: dict[str, tuple[str, list[str], tuple[str, ...], set[str]]] = {
    GORA_KALWARIA: (
        "available",
        ["IV/30/2024", "576/XLVII/2010"],
        (),
        {"LIV/467/2021", "XXXIX/366/2017"},
    ),
    "krakow_126105_9.0001.580_4.html": ("available", ["XII/131/11"], ("KP.1",), set()),
    "bielsko_biala_246101_1.0056.155_3.html": ("unavailable", [], (), set()),
    "pisz_281603_4.0001.496_5.html": ("available", ["XXI/232/20"], ("2KDZ",), set()),
    "legnica_026201_1.0009.1319_4.html": ("available", ["XXVI/277/04"], ("22 KD G1/2(Z1/4)",), set()),
    "warszawa_146510_8.0502.1_3.html": ("unavailable", [], (), set()),
    "dygowo_321606_2.0029.362.html": ("no_coverage", [], (), set()),
    "ruciane_nida_281604_5.0011.107.html": ("no_match", [], (), set()),
    "kalety_punkt_500000_300000.html": ("available", ["389/XLIII/2023", "101/XII/2011"], (), set()),
    "inowroclaw_punkt_450000_550000.html": ("available", ["XXIII/322/2012"], (), set()),
}
ALL_NUMBERS = {number for _, numbers, _, _ in EXPECTED.values() for number in numbers}


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _numbers(result: KimpzpPointResult) -> list[str | None]:
    return [act.resolution_number for act in result.acts]


# --- kontrakt fixtures --------------------------------------------------------------------


def test_fixtures_are_unmodified_service_responses_listed_in_the_manifest() -> None:
    manifest = json.loads((FIXTURES / "manifest.json").read_text("utf-8"))["files"]
    on_disk = {path.name for path in FIXTURES.glob("*.html")}
    assert on_disk == set(manifest) == set(EXPECTED)
    municipalities = {entry["municipality"] for entry in manifest.values()}
    assert {"Góra Kalwaria", "Kraków", "Bielsko-Biała", "Pisz", "Legnica", "Warszawa"} <= municipalities
    for name, entry in manifest.items():
        content = (FIXTURES / name).read_bytes()
        assert hashlib.sha256(content).hexdigest() == entry["sha256"], name
        assert len(content) == entry["size_bytes"]
        assert entry["http_status"] == 200
        assert "request=GetFeatureInfo" in entry["request_url"]
        assert "layers=plany_granice" in entry["request_url"]


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_frozen_response_is_classified_and_acts_are_extracted(name: str) -> None:
    status, numbers, symbols, amendment_only = EXPECTED[name]

    result = parse_kimpzp_feature_info(_read(name))

    assert result.status == status
    assert _numbers(result) == numbers
    assert result.zone_symbols == symbols
    amendments = {
        amendment.resolution_number for act in result.acts for amendment in act.amendments
    }
    assert amendment_only <= amendments
    assert not amendment_only & set(_numbers(result))


def test_gora_kalwaria_acceptance_values_from_the_audit() -> None:
    result = parse_kimpzp_feature_info(_read(GORA_KALWARIA))

    newest, oldest = result.acts
    assert newest.resolution_number == "IV/30/2024"
    assert newest.text_url == "http://mpzp.gorakalwaria.pl/portal/mpzp/uch/IV_30_2024.pdf"
    assert newest.legend_url == "http://mpzp.gorakalwaria.pl/portal/mpzp/leg/IV_30_2024_leg.pdf"
    assert newest.bip_url == "https://bip.gorakalwaria.pl/wiadomosci/14246/wiadomosc/757992"
    assert newest.resolution_date == date(2024, 6, 5)
    assert newest.valid_from == date(2024, 6, 26)
    assert newest.legal_status == "binding"  # nagłówek „Obowązujące MPZP” (pisownia usługi)
    assert newest.name and newest.name.startswith("Miejscowy plany zagospodarowania")
    assert newest.journal == "Dz. Urz. Woj. Maz. 2024 poz. 6032 z 11.06.2024r."
    assert newest.repealed_on is None  # wiersz „Utracił moc” jest komentarzem HTML
    assert newest.amendments == ()
    assert oldest.resolution_number == "576/XLVII/2010"
    assert oldest.valid_from == date(2010, 8, 24)
    note, text_change, older_change = oldest.amendments
    assert note.kind == "note" and "LIV/467/2021" in (note.raw_text or "")
    assert text_change == KimpzpAmendment(
        kind="text_change",
        resolution_number="LIV/467/2021",
        name="Zmiana miejscowego planu zagospodarowania przestrzennego dla fragmentu wsi Moczydłów – rejon ul. Lipkowskiej – ETAP I",
        adopted_on=date(2021, 6, 23),
        valid_from=date(2021, 7, 17),
        document_url="https://bip-v1-files.idcom-jst.pl/sites/47313/wiadomosci/582692/files/liv_467_2021.pdf",
        bip_url="https://bip.gorakalwaria.pl/wiadomosci/14246/wiadomosc/582692",
    )
    assert older_change.resolution_number == "XXXIX/366/2017"
    assert result.multiple_acts is True


def test_kalety_ignores_plan_ordinal_and_uuid_and_keeps_raster_flag() -> None:
    result = parse_kimpzp_feature_info(_read("kalety_punkt_500000_300000.html"))

    assert _numbers(result) == ["389/XLIII/2023", "101/XII/2011"]
    assert all(act.informatization == "raster" for act in result.acts)
    assert result.vector_available is False
    assert result.acts[0].drawing_url == "https://mkalety.e-mapa.net/wykazplanow/tiff/241301/008"
    # „brak wyniku” drugiej usługi nie jest powodem statusu, gdy pierwsza wskazała akty.
    assert result.reason_codes == ()


def test_pisz_info_layer_and_file_names_are_not_acts_or_links() -> None:
    result = parse_kimpzp_feature_info(_read("pisz_281603_4.0001.496_5.html"))

    (act,) = result.acts
    assert act.legal_status == "binding"
    assert act.text_url is None and act.legend_url is None and act.drawing_url is None
    assert act.zone_symbols == ("2KDZ",)


def test_inowroclaw_number_from_document_description_and_us_date() -> None:
    (act,) = parse_kimpzp_feature_info(_read("inowroclaw_punkt_450000_550000.html")).acts

    assert act.resolution_number == "XXIII/322/2012"
    assert act.valid_from == date(2012, 8, 11)
    assert act.legal_status == "binding"
    assert act.drawing_url and act.drawing_url.endswith("_rys_1_uch.tif")


def test_parser_is_exposed_through_the_planning_port() -> None:
    parser = kimpzp_feature_info_parser()

    assert isinstance(parser, KimpzpFeatureInfoParser)
    assert parser(_read(GORA_KALWARIA)) == parse_kimpzp_feature_info(_read(GORA_KALWARIA))


# --- własność: żaden numer uchwały nie pochodzi z tabeli zagnieżdżonej -------------------


def _nest_amendments_inside_plan_block(html: str) -> str:
    """Przenosi tabelę „Zmiany tekstowe” do komórki bloku planu (wariant zagnieżdżony)."""
    soup = BeautifulSoup(html, "html.parser")
    amendment_table = soup.find("table", class_="small")
    blocks = [table for table in soup.find_all("table") if "MPZP" in table.get_text()]
    assert amendment_table is not None and blocks
    detached = amendment_table.extract()
    row = soup.new_tag("tr")
    cell = soup.new_tag("td", colspan="2")
    cell.append(detached)
    row.append(cell)
    blocks[0].append(row)
    return str(soup)


def test_nested_amendment_table_never_yields_a_plan_id() -> None:
    result = parse_kimpzp_feature_info(_nest_amendments_inside_plan_block(_read(GORA_KALWARIA)))

    assert _numbers(result) == ["IV/30/2024", "576/XLVII/2010"]
    nested_into = {act.resolution_number: act for act in result.acts}["IV/30/2024"]
    assert {a.resolution_number for a in nested_into.amendments} == {"LIV/467/2021", "XXXIX/366/2017"}


def test_amendment_table_without_any_plan_block_is_dropped_not_promoted() -> None:
    html = _read(GORA_KALWARIA)
    amendments_only = html[html.index("<b>Zmiany tekstowe:</b>") :]

    result = parse_kimpzp_feature_info(amendments_only)

    assert result.acts == ()
    assert result.status == "no_match"


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_no_act_number_comes_from_a_nested_or_amendment_table(name: str) -> None:
    soup = BeautifulSoup(_read(name), "html.parser")
    amendment_numbers = {
        normalize_resolution_number(cell.get_text(" ", strip=True))
        for table in soup.find_all("table", class_="small")
        for cell in table.find_all("td")
    } - {None}

    result = parse_kimpzp_feature_info(str(soup))

    assert not amendment_numbers & set(_numbers(result))
    assert set(_numbers(result)) <= ALL_NUMBERS


# --- odporność (fuzz na rzeczywistych odpowiedziach) -------------------------------------

_UNKNOWN_KEYS = ("Nieznane pole", "Uwagi techniczne", "Numer porządkowy", "Kod systemowy", "Plan nr")
_TRAPS = ("LIV/999/2099", "XXX/1/2000", "1/A/1999")


def _mutate(html: str, rng: random.Random) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        rows = [row for row in table.find_all("tr") if row.find_parent("table") is table]
        if len(rows) < 2:
            continue
        header, body = rows[0], rows[1:]
        header_is_columns = len([c for c in header.find_all(["th"]) if c.find_parent("tr") is header]) > 1
        if header_is_columns:
            _mutate_columns(soup, rows, rng)
            continue
        for row in body:
            row.extract()
        rng.shuffle(body)
        kept = [row for row in body if rng.random() > 0.2]  # brakujące pola
        for _ in range(rng.randint(0, 2)):  # nieznane nagłówki z wartością podobną do numeru
            extra = soup.new_tag("tr")
            key = soup.new_tag(rng.choice(["td", "th"]))
            bold = soup.new_tag("b")
            bold.string = rng.choice(_UNKNOWN_KEYS) + ":"
            key.append(bold)
            value = soup.new_tag("td")
            value.string = rng.choice(_TRAPS)
            extra.append(key)
            extra.append(value)
            kept.insert(rng.randint(0, len(kept)), extra)
        anchor = header
        for row in kept:
            anchor.insert_after(row)
            anchor = row
    return str(soup)


def _mutate_columns(soup: BeautifulSoup, rows: list, rng: random.Random) -> None:
    width = len([c for c in rows[0].find_all(["th", "td"]) if c.find_parent("tr") is rows[0]])
    order = list(range(width))
    rng.shuffle(order)
    order = [index for index in order if rng.random() > 0.2] or order[:1]
    for row in rows:
        cells = [c for c in row.find_all(["th", "td"]) if c.find_parent("tr") is row]
        for cell in cells:
            cell.extract()
        for index in order:
            if index < len(cells):
                row.append(cells[index])
        tag = "th" if row is rows[0] else "td"
        extra = soup.new_tag(tag)
        extra.string = rng.choice(_UNKNOWN_KEYS) if tag == "th" else rng.choice(_TRAPS)
        row.append(extra)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_fuzzed_responses_never_raise_and_never_produce_a_wrong_plan_id(name: str) -> None:
    original = _read(name)
    _, numbers, _, amendment_only = EXPECTED[name]
    rng = random.Random(f"au-004:{name}")
    for _ in range(60):
        mutated = _mutate(original, rng)

        result = parse_kimpzp_feature_info(mutated)

        assert result.status in {"available", "no_match", "no_coverage", "unavailable", "unknown"}
        produced = {number for number in _numbers(result) if number is not None}
        assert produced <= set(numbers), (name, produced)
        assert not produced & amendment_only
        assert not produced & set(_TRAPS)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_truncated_and_garbled_responses_never_raise(name: str) -> None:
    """Ucięcie na granicy znacznika nie daje błędnego numeru; ucięcie w dowolnym miejscu — wyjątku.

    Ucięcie w środku wartości (``XXVI/277|/04``) uszkadza samą daną, czego parser nie
    może wykryć, więc tam wymagany jest wyłącznie brak wyjątku.
    """
    original = _read(name)
    _, numbers, _, _ = EXPECTED[name]
    boundaries = [index for index, char in enumerate(original) if char == "<"] + [len(original)]
    rng = random.Random(f"au-004-cut:{name}")
    for _ in range(40):
        start = rng.choice(boundaries[: max(1, len(boundaries) // 3)])
        end = rng.choice([boundary for boundary in boundaries if boundary >= start])
        fragment = original[start:end]
        if rng.random() < 0.3:
            fragment = re.sub(r"</?t[dhr][^>]*>", "", fragment, count=rng.randint(1, 5))

        result = parse_kimpzp_feature_info(fragment)

        assert set(_numbers(result)) - {None} <= set(numbers)

        raw_start = rng.randint(0, len(original))
        parse_kimpzp_feature_info(original[raw_start : rng.randint(raw_start, len(original))])


@pytest.mark.parametrize(
    "text",
    ["", "   ", "<hr/>", "<hr/><hr/>", "null", "[]", "{}", '{"features": "x"}', '{"features": [1, null]}',
     "<html><body></body></html>", "<table></table>", "plain text", "<oms_error>x</oms_error>",
     "<table><tr><td><b>Nr uchwały:</b></td></tr></table>"],
)
def test_degenerate_inputs_do_not_raise(text: str) -> None:
    result = parse_kimpzp_feature_info(text)

    assert result.acts == ()


# --- domena -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("IV/30/2024", "IV/30/2024"),
        (" Uchwała nr 576/XLVII/2010 ", "576/XLVII/2010"),
        ("Nr XII / 131 / 11", "XII/131/11"),
        ("XXI_232_20_rys", None),
        ("001", None),
        ("9fe05c8b-ebd4-49e1-b894-b1bff602fcee", None),
        ("NULL", None),
        ("abc/def", None),
        ("XV/151/11.", "XV/151/11"),
        ("A" * 70 + "/1", None),
        (None, None),
    ],
)
def test_normalize_resolution_number(raw: str | None, expected: str | None) -> None:
    assert normalize_resolution_number(raw) == expected


def test_resolution_number_from_description() -> None:
    assert resolution_number_from_text("Uchwała nr XXIII/322/2012 Rady Miejskiej") == "XXIII/322/2012"
    assert resolution_number_from_text("bez numeru") is None
    assert resolution_number_from_text(None) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2024-06-26", date(2024, 6, 26)),
        ("2023-06-02 15:47:22.4418", date(2023, 6, 2)),
        ("17.06.2011", date(2011, 6, 17)),
        ("2011.06.17", date(2011, 6, 17)),
        ("8/11/12, 12:00 AM", date(2012, 8, 11)),
        ("8/11/98, 12:00 AM", date(1998, 8, 11)),
        ("Aug 11, 2012, 3:56:00 PM", date(2012, 8, 11)),
        ("[utracil_moc]", None),
        ("2024-13-40", None),
        ("", None),
    ],
)
def test_parse_source_date(raw: str, expected: date | None) -> None:
    assert parse_source_date(raw) == expected


def test_legal_status_and_folding() -> None:
    assert fold_text("Obowązujące  MPZP") == "obowazujace mpzp"
    assert legal_status_from_text("Obowiązujące MPZP") == "binding"
    assert legal_status_from_text("Nieobowiązujące MPZP") == "not_binding"
    assert legal_status_from_text("Prawnie wiążący lub realizowany") == "binding"
    assert legal_status_from_text("w opracowaniu") == "unknown"
    assert legal_status_from_text(None) == "unknown"


def test_merge_keeps_first_values_and_unions_symbols_and_amendments() -> None:
    first = KimpzpAct(resolution_number="X/1/2020", zone_symbols=("MN",), legal_status="unknown")
    second = KimpzpAct(
        resolution_number="x/1/2020",
        name="Plan",
        zone_symbols=("MN", "U"),
        legal_status="binding",
        informatization="raster",
        amendments=(KimpzpAmendment(kind="change", resolution_number="Y/2/2021"),),
    )
    anonymous = KimpzpAct(resolution_number=None, name="bez numeru")

    merged = merge_acts([first, second, anonymous])

    assert len(merged) == 2
    act = next(act for act in merged if act.resolution_number)
    assert act.name == "Plan" and act.zone_symbols == ("MN", "U")
    assert act.legal_status == "binding" and act.informatization == "raster"
    assert [a.resolution_number for a in act.amendments] == ["Y/2/2021"]


def test_sort_acts_by_valid_from_then_adoption_date_missing_last() -> None:
    acts = [
        KimpzpAct(resolution_number="A/1/2000"),
        KimpzpAct(resolution_number="B/1/2010", valid_from=date(2010, 1, 1)),
        KimpzpAct(resolution_number="C/1/2020", valid_from=date(2020, 1, 1)),
        KimpzpAct(resolution_number="D/1/2015", resolution_date=date(2015, 1, 1)),
    ]

    assert [act.resolution_number for act in sort_acts(acts)] == [
        "C/1/2020",
        "B/1/2010",
        "D/1/2015",
        "A/1/2000",
    ]


def test_combine_statuses_precedence() -> None:
    assert combine_statuses(["no_match", "available"]) == "available"
    assert combine_statuses(["no_match", "unavailable"]) == "unavailable"
    assert combine_statuses(["no_coverage", "no_match"]) == "no_match"
    assert combine_statuses(["no_coverage"]) == "no_coverage"
    assert combine_statuses([]) == "unknown"


def test_summarize_points_flags_and_partial_errors() -> None:
    gk = parse_kimpzp_feature_info(_read(GORA_KALWARIA))
    no_match = parse_kimpzp_feature_info(_read("ruciane_nida_281604_5.0011.107.html"))

    summary = summarize_points([gk, no_match, None])

    assert summary.status == "available"
    assert summary.multiple_acts_at_point is True
    assert summary.single_act is None
    assert summary.failed_points == 1 and summary.queried_points == 3
    assert summary.reason_codes == ("MPZP_MULTIPLE_ACTS_AT_POINT", "KIMPZP_PARTIAL_SERVICE_ERROR")


def test_not_binding_act_does_not_count_as_a_second_act() -> None:
    binding = KimpzpAct(resolution_number="A/1/2020", legal_status="binding")
    repealed = KimpzpAct(resolution_number="B/1/2010", legal_status="not_binding")

    summary = summarize_points([KimpzpPointResult(status="available", acts=(binding, repealed))])

    assert summary.multiple_acts_at_point is False
    assert summary.single_act == binding
