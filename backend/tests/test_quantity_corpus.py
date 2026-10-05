"""Silnik leksykonu na zamrożonym korpusie BK-603: progi akceptacji PV3-07.

Testy nie przypinają dokładnej dokładności, tylko progi z kryteriów akceptacji i brak
regresji względem pomiaru sprzed silnika (parser z rozproszonymi wzorcami: recall 0,248 =
62/250 na całym korpusie, 0,199 = 32/161 na podziale końcowym; precision 1,000).

Uwaga o uczciwości pomiaru: leksykon powstał po lekturze sformułowań z CAŁEGO korpusu
(21 próbek), więc wynik na podziale końcowym nie jest już oceną uogólnienia; niezależną ocenę
da dopiero nowy zbiór końcowy z PV3-02.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.modules.planning.domain.quantity_lexicon import FLAG_PENALTIES
from scripts import evaluate_mpzp_parser as ev
from tests.parcel_fixtures_config import find_repo_root

FIXTURES = find_repo_root() / "backend/tests/fixtures/mpzp_evaluation"
BASELINE_RECALL_OVERALL = 62 / 250  # parser sprzed PV3-07 (docs/evaluation/results/parser/report.md)
BASELINE_RECALL_FINAL = 32 / 161


@pytest.fixture(scope="module")
def manifest() -> dict[str, Any]:
    return json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def legacy(manifest: dict[str, Any]) -> dict[str, Any]:
    return ev.evaluate(manifest, FIXTURES, engine=ev.LegacyEngine())


@pytest.fixture(scope="module")
def v3(manifest: dict[str, Any]) -> dict[str, Any]:
    return ev.evaluate(manifest, FIXTURES, engine=ev.V3Engine())


def _split(evaluation: dict[str, Any], name: str) -> dict[str, Any]:
    return evaluation["metrics"]["by_split"][name]["detection"]


@pytest.mark.parametrize("fixture_name", ["legacy", "v3"])
def test_development_split_meets_the_acceptance_thresholds(request: pytest.FixtureRequest, fixture_name: str) -> None:
    detection = _split(request.getfixturevalue(fixture_name), "development")
    assert detection["precision"]["value"] >= 0.95, detection["counts"]
    assert detection["recall"]["value"] >= 0.60, detection["counts"]
    assert detection["recall"]["denominator"] == 89 and detection["precision"]["denominator"] >= 80


@pytest.mark.parametrize("fixture_name", ["legacy", "v3"])
def test_recall_improved_over_the_pre_engine_parser_without_losing_precision(
    request: pytest.FixtureRequest, fixture_name: str
) -> None:
    evaluation = request.getfixturevalue(fixture_name)
    overall = evaluation["metrics"]["overall"]["detection"]
    assert overall["recall"]["value"] > 2 * BASELINE_RECALL_OVERALL
    assert _split(evaluation, "final")["recall"]["value"] > 2 * BASELINE_RECALL_FINAL
    assert overall["precision"]["value"] >= 0.95


def test_v3_engine_never_returns_a_value_for_a_pair_the_document_does_not_contain(v3: dict[str, Any]) -> None:
    assert v3["metrics"]["overall"]["detection"]["counts"].get("fp", 0) == 0
    assert v3["metrics"]["overall"]["detection"]["cross_zone_errors"] == 0


def test_every_flag_the_engine_emits_has_a_penalty_and_numbers_are_traceable(v3: dict[str, Any]) -> None:
    flags: set[str] = set()
    for row in v3["rows"]:
        for output in row["parser_outputs"]:
            assert output["source_text"] and output["raw_value"]
    assert v3["values"]
    # Flagi przechodzą do wyniku parsera: sprawdzamy je na realnym przebiegu produkcyjnego parsera.
    import asyncio

    loaded = ev.load_document(FIXTURES, json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))["documents"]["krakow_morelowa"])
    result = asyncio.run(ev.run_parser(loaded, ["MN.11"], "blocks"))
    for zone in result.zones:
        for parameter in zone.parameters:
            flags.update(parameter.normalization_flags)
    assert flags <= set(FLAG_PENALTIES)


def test_corpus_runs_are_deterministic(manifest: dict[str, Any], v3: dict[str, Any]) -> None:
    again = ev.evaluate(manifest, FIXTURES, engine=ev.V3Engine())
    assert again["rows"] == v3["rows"] and again["metrics"] == v3["metrics"]
