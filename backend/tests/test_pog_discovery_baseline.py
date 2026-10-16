"""Porównanie discovery POG z WFS (AU-105) ze stanem zamrożonym PRZED zmianą — na 30 działkach korpusu.

* ``fixtures/pog_discovery/legacy_baseline.json`` — wynik poprzedniego silnika (WMS GetFeatureInfo, punkt × warstwa
  ``APP.POG.*``) na żywym RU z 2026-10-12, razem z liczbą żądań HTTP (46–288 na działkę);
* ``fixtures/source_contracts/ru_pog_features`` — zamrożone odpowiedzi WFS RU dla tych samych działek.

Kryterium „wynik dla działek ze statusem ``binding`` identyczny jak przed zmianą" jest sprawdzane wprost, ale test
rozdziela dwie grupy, bo poprzedni silnik miał błąd: WMS 1.3.0 RU czyta ``bbox`` dla EPSG:2180 w kolejności
północ,wschód, a kod wysyłał wschód,północ — odpytywał więc **lustrzane** współrzędne. Dla Bielska-Białej wynik się
zgadza (``binding``), natomiast dla 9 działek (Kraków, gmina 281603) poprzedni silnik raportował ``binding`` tam, gdzie
RU nie ma żadnego obiektu POG, a dla 5 działek (gmina 026201) przeoczył obowiązujący POG. Prawdę terenową liczy
niezależny parser w tym pliku (czysty ``ElementTree`` + Shapely, bez kodu produkcyjnego) z tych samych zamrożonych
odpowiedzi, więc zgodność nie jest tautologią.
"""

from __future__ import annotations

import asyncio
import json
import socket
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree

import pytest
import respx
from shapely.geometry import Polygon
from shapely.ops import unary_union

from app.core.settings import settings
from app.services.pog import PogGminaSources, discover_pog
from app.shared.planning_status import official_status_code
from tests import ru_pog_fixtures as fx

BASELINE = json.loads(
    (Path(__file__).resolve().parent / "fixtures/pog_discovery/legacy_baseline.json").read_text("utf-8")
)
LEGACY = {row["case_id"]: row for row in BASELINE["cases"]}

NS_APP = "https://www.gov.pl/static/zagospodarowanieprzestrzenne/schemas/app/3.0"
NS_GML = "http://www.opengis.net/gml/3.2"
NS_WFS = "http://www.opengis.net/wfs/2.0"
NS_XLINK = "http://www.w3.org/1999/xlink"
LAYER_OF_TYPE = {
    "AktPlanowaniaPrzestrzennego": "planning_act",
    "ObszarZabudowySrodmiejskiej": "downtown_area",
    "ObszarUzupelnieniaZabudowy": "ouz",
    "StrefaPlanistyczna": "planning_zones",
}


@pytest.fixture(autouse=True)
def ru_online(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ru_pog_discovery_enabled", True)
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (fx.PUBLIC_IP, 0))]
    )


def _independent_truth(case: str) -> dict[str, dict[str, object]]:
    """Co RU zwróciło dla działki — policzone bez kodu produkcyjnego (oś północ,wschód; przecięcie w EPSG:2180)."""
    parcel = fx.parcel(case)
    truth: dict[str, dict[str, object]] = {}
    for type_name, layer in LAYER_OF_TYPE.items():
        found = 0
        codes: set[str] = set()
        for item in fx.manifest()["cases"][case]["requests"]:
            if item["type_name"] != type_name:
                continue
            root = ElementTree.fromstring(fx.body(item["file"]))
            for member in root.findall(f"{{{NS_WFS}}}member"):
                feature = member[0]
                geometry_name = "zasiegPrzestrzenny" if type_name == "AktPlanowaniaPrzestrzennego" else "geometria"
                holder = feature.find(f"{{{NS_APP}}}{geometry_name}")
                polygons = []
                for polygon in holder.iter(f"{{{NS_GML}}}Polygon"):
                    def ring(node):  # noqa: ANN001, ANN202
                        values = [float(token) for token in node.find(f".//{{{NS_GML}}}posList").text.split()]
                        return [(values[i + 1], values[i]) for i in range(0, len(values), 2)]  # (E, N) z (N, E)

                    polygons.append(
                        Polygon(
                            ring(polygon.find(f"{{{NS_GML}}}exterior")),
                            [ring(hole) for hole in polygon.findall(f"{{{NS_GML}}}interior")],
                        )
                    )
                geometry = unary_union([p if p.is_valid else p.buffer(0) for p in polygons])
                if geometry.intersects(parcel) and geometry.intersection(parcel).area > 0.5:
                    found += 1
                    status = feature.find(f"{{{NS_APP}}}status")
                    codes.add(status.attrib[f"{{{NS_XLINK}}}href"].rsplit("/", 1)[-1])
        truth[layer] = {"found": found, "codes": codes}
    return truth


def _new(case: str):  # noqa: ANN202
    with respx.mock as router:
        route = fx.mount(router)
        result = asyncio.run(discover_pog(fx.parcel(case), PogGminaSources(teryt=fx.manifest()["cases"][case]["teryt"])))
    return result, route.call_count


def _classify(case: str, new_status: str) -> str:
    legacy_status = LEGACY[case]["result"]["legal_status"]
    return {
        ("binding", "binding"): "identical_binding",
        ("unknown", "unknown"): "identical_unknown",
        ("binding", "unknown"): "legacy_false_binding",
        ("unknown", "binding"): "legacy_missed_binding",
    }[(legacy_status, new_status)]


def test_baseline_covers_exactly_the_corpus_and_records_the_request_avalanche() -> None:
    assert set(LEGACY) == set(fx.case_ids())
    legacy_requests = [row["ru_requests"] for row in LEGACY.values()]
    assert min(legacy_requests) >= 46 and max(legacy_requests) == 288
    assert BASELINE["label"] == "legacy"


def test_new_discovery_matches_the_independent_ground_truth_for_every_corpus_parcel() -> None:
    for case in fx.case_ids():
        result, _calls = _new(case)
        truth = _independent_truth(case)
        for layer, expected in truth.items():
            section = getattr(result, layer)
            # Usługa odpowiedziała poprawnie na wszystkie żądania: warstwa jest ``found`` albo ``empty`` — nigdy
            # ``unavailable`` (obiekt z obwiednią w kaflu, którego wielokąt nie dotyka kafla, nie jest błędem).
            assert section.status == ("found" if expected["found"] > 0 else "empty"), (case, layer, section.status)
            if section.status == "found":
                assert section.feature_count == expected["found"], (case, layer)
        any_found = any(item["found"] for item in truth.values())
        codes = {code for item in truth.values() for code in item["codes"]}
        if any_found:
            assert result.legal_status == "binding" and codes == {"legalForce"}, case
        else:
            assert result.legal_status == "unknown", case


def test_binding_results_are_unchanged_where_the_legacy_engine_looked_at_the_right_place() -> None:
    identical = [case for case in fx.case_ids() if _classify(case, _new(case)[0].legal_status) == "identical_binding"]

    assert len(identical) == 7
    assert {case.split("-")[2] for case in identical} == {"246101"}  # Bielsko-Biała
    for case in identical:
        legacy = LEGACY[case]["result"]
        new, _calls = _new(case)
        # Pola kontraktu POG widoczne w API są takie same jak przed zmianą ...
        assert new.legal_status == legacy["legal_status"] == "binding"
        assert official_status_code(new.raw_legal_status) == official_status_code(legacy["raw_legal_status"])
        assert new.uchwala_nr == legacy["uchwala_nr"] and new.uchwala_date == legacy["uchwala_date"]
        assert new.links == legacy["links"] == []
        assert new.source_responded == legacy["source_responded"] and new.is_discovery_only == legacy["is_discovery_only"]
        assert new.planning_act.status == legacy["sections"]["planning_act"]["status"] == "found"
        assert new.planning_zones.status == legacy["sections"]["planning_zones"]["status"] == "found"
        assert new.act_found and new.preview_features_found >= 1
        assert set(legacy["warning_codes"]) <= {warning.code for warning in new.warnings}
        # ... a źródłem jest teraz WFS, nie WMS.
        assert new.source_metadata.source_name == "REJESTR_URBANISTYCZNY_WFS"
        assert legacy["source"]["source_name"] == "REJESTR_URBANISTYCZNY_WMS"


def test_every_difference_from_the_legacy_baseline_is_a_documented_axis_defect_of_the_legacy_engine() -> None:
    groups: dict[str, list[str]] = {
        "identical_binding": [],
        "identical_unknown": [],
        "legacy_false_binding": [],
        "legacy_missed_binding": [],
    }
    for case in fx.case_ids():
        groups[_classify(case, _new(case)[0].legal_status)].append(case)

    assert {name: len(cases) for name, cases in groups.items()} == {
        "identical_binding": 7,
        "identical_unknown": 9,
        "legacy_false_binding": 9,
        "legacy_missed_binding": 5,
    }
    # Fałszywe „binding" poprzedniego silnika: RU nie ma w ogóle obiektów POG dla tych działek (Kraków i gmina 281603).
    assert {case.split("-")[2] for case in groups["legacy_false_binding"]} == {"126105", "281603"}
    for case in groups["legacy_false_binding"]:
        assert all(item["found"] == 0 for item in _independent_truth(case).values()), case
    # Przeoczony przez poprzedni silnik obowiązujący POG (gmina 026201): RU ma akt, strefy i obszar zabudowy śródmiejskiej.
    assert {case.split("-")[2] for case in groups["legacy_missed_binding"]} == {"026201"}
    for case in groups["legacy_missed_binding"]:
        truth = _independent_truth(case)
        assert truth["planning_act"]["found"] == 1 and truth["planning_zones"]["found"] >= 1, case


def test_request_budget_is_met_for_all_corpus_parcels_against_the_legacy_avalanche() -> None:
    new_counts = {case: _new(case)[1] for case in fx.case_ids()}

    assert max(new_counts.values()) <= 6
    assert Counter(new_counts.values()) == {4: 28, 5: 2}  # 5 = strefy na dwóch stronach (RU zwraca po 10)
    legacy_total = sum(row["ru_requests"] for row in LEGACY.values())
    assert legacy_total >= 4000
    assert sum(new_counts.values()) * 25 < legacy_total  # co najmniej 25 × mniej żądań HTTP do RU
