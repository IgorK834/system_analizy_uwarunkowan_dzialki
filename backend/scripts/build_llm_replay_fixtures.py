#!/usr/bin/env python3
"""Buduje złote odpowiedzi do testów odtwarzania ekstrakcji modelem językowym (PV3-11).

Dla siedmiu przypadków korpusu BK-603 — po jednym na układ 1–6 zakresu strefy i przypadek
„nie znaleziono” (skan bez wartości katalogu) — skrypt wyznacza bloki stref tym samym kodem co
parser, buduje żądania produkcyjną usługą (``LlmExtractionService.plan``) i zapisuje odpowiedź
w formacie ``ReplayStore`` ewaluatora pod kluczem z (dostawca, model, wersja i skrót instrukcji,
wersja schematu, temperatura, skrót wiadomości z danymi).

**To nie są nagrania odpowiedzi modelu.** Odpowiedzi są złote: składa je skrypt z adnotacji
korpusu (cytat dowodu, surowa wartość, operator), czyli tak, jak odpowiedziałby model idealny.
Służą do testowania potoku (kontrakt, lokalizacja cytatów, scalanie, provenance) bez internetu.
Prawdziwe nagrania powstają ręcznie, poza CI, z ``--live`` ewaluatora i dostają ten sam klucz.
Ponieważ klucz zawiera skrót instrukcji, każda zmiana ``prompts/mpzp_extraction_v1.md``,
``USER_TEMPLATE`` albo schematu unieważnia te pliki; ``--check`` mówi, czy trzeba je odtworzyć.

Użycie (z katalogu ``backend/``)::

    python3 scripts/build_llm_replay_fixtures.py            # zapisuje do tests/fixtures/mpzp_evaluation/llm_replay
    python3 scripts/build_llm_replay_fixtures.py --check    # kończy się kodem 1, gdy pliki są nieaktualne
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_IMPORT_CWD = Path.cwd()
try:
    os.chdir(BACKEND_DIR)  # app.core.settings czyta względny .env przy imporcie
    from app.modules.planning.application.llm_extraction import ExtractionLimits, LlmExtractionService
    from app.modules.planning.domain import extraction_contract as contract
    from app.modules.planning.domain.zone_blocks import ZoneBlock
    from app.modules.planning.domain.zone_scope import resolve_zone_scope
    from app.modules.planning.infrastructure.llm.fake_provider import (
        ReplayStructuredExtractionProvider,
        canonical_json,
    )
    from app.services.mpzp_parser_structure import build_tree_from_extraction, structure_view
    from app.shared.zone_symbol import same_zone_symbol
finally:
    os.chdir(_IMPORT_CWD)

from scripts import evaluate_mpzp_parser as ev  # noqa: E402

DEFAULT_OUTPUT_DIR = BACKEND_DIR / "tests" / "fixtures" / "mpzp_evaluation" / "llm_replay"
MODEL = "gemini-3.8-flash"
RECORDED_AT = "2026-10-03T00:00:00Z"
ORIGIN = "golden_fixture"
_APPLICABILITY = {
    "zone_section": "zone_section",
    "general_clause": "general_clause",
    "residual_clause": "residual_clause",
    "fallback": "unresolved",
}


@dataclass(frozen=True)
class GoldenCase:
    case_id: str
    sample_id: str
    symbols: tuple[str, ...]
    note: str


CASES: tuple[GoldenCase, ...] = (
    GoldenCase("L1", "F13-szczytno-2023-multi", ("1MNW",), "układ 1: osobny § na strefę"),
    GoldenCase("L2", "F04-szczytno-2021-shared", ("1Up", "2Up"), "układ 2: wspólny § dla listy symboli (jedno żądanie, dwa symbole)"),
    GoldenCase("L3", "D01-krakow-mn-multi", ("MN.11",), "układ 3: podpunkty „N) dla terenu X:” i klauzula ogólna (5)"),
    GoldenCase("L4", "D04-lodz-html-multi", ("6.6.MW/U",), "układ 4: wartości per symbol w akapicie (blok, klauzula resztowa, zakres nierozstrzygnięty)"),
    GoldenCase("L5", "F06-raszkow-short-symbols", ("MN",), "układ 5: klauzule ogólne per symbol"),
    GoldenCase("L6", "D03-legnica-table", ("2UZ",), "układ 6: tabela"),
    GoldenCase("N1", "F10-pisz-ocr-real", ("1 PK",), "skan OCR bez wartości katalogu: jawne „nie znaleziono”"),
)


def corpus() -> tuple[Mapping[str, Any], Path]:
    manifest, _ = ev.load_corpus(ev.DEFAULT_MANIFEST)
    return manifest, ev.DEFAULT_MANIFEST.parent


def case_blocks(case: GoldenCase) -> list[ZoneBlock]:
    """Bloki stref przypadku (unikalne po ``block_id``) — ten sam resolver co w parserze."""
    manifest, base = corpus()
    sample = next(item for item in manifest["samples"] if item["sample_id"] == case.sample_id)
    loaded = ev.load_document(base, manifest["documents"][sample["document_id"]])
    extraction, _ = ev.build_extraction(loaded)
    tree = build_tree_from_extraction(extraction)
    resolution = resolve_zone_scope(structure_view(tree), list(case.symbols))
    blocks: dict[str, ZoneBlock] = {}
    for symbol in case.symbols:
        for block in resolution.blocks_for(symbol):
            blocks.setdefault(block.block_id, block)
    return list(blocks.values())


def sample_zones(case: GoldenCase) -> list[Mapping[str, Any]]:
    manifest, _ = corpus()
    sample = next(item for item in manifest["samples"] if item["sample_id"] == case.sample_id)
    return [zone for zone in sample["zones"] if any(same_zone_symbol(zone["symbol"], s) for s in case.symbols)]


def _condition(note: str, evidence: str) -> dict[str, str]:
    lowered = note.lower()
    kind = "other"
    if "dach" in lowered:
        kind = "roof_type"
    elif "budynk" in lowered:
        kind = "building_type"
    elif "podstref" in lowered:
        kind = "subzone"
    return {"kind": kind, "label": note.strip()[:80], "quote": evidence}


def golden_payload(block: ZoneBlock, rendered: str, zones: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Odpowiedź modelu idealnego dla jednego żądania: tylko to, co adnotacja ma w tekście wysłanym modelowi."""
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    applicability = _APPLICABILITY[block.scope_kind]
    for symbol in block.symbols:
        for zone in zones:
            if not same_zone_symbol(zone["symbol"], symbol):
                continue
            for annotation in zone["annotations"]:
                parameter, operator = annotation["parameter"], annotation["operator"]
                evidence, raw = annotation.get("evidence") or "", annotation.get("raw_value") or ""
                if parameter not in contract.CATALOG or operator not in contract.CATALOG[parameter].operators:
                    continue
                if annotation.get("normalized_value") is None or annotation.get("status") not in {"required", "acceptable"}:
                    continue
                if contract.locate_quote(rendered, evidence) is None or not contract.quote_contains(evidence, raw):
                    continue
                if contract.derive_value(operator, raw) != annotation["normalized_value"]:
                    continue
                anchor = annotation.get("anchor") or ""
                scope = anchor if contract.locate_quote(rendered, anchor) is not None else ""
                if not scope and applicability != "unresolved":
                    scope = block.text.strip().splitlines()[0][:160]
                    if contract.locate_quote(rendered, scope) is None:
                        continue
                ambiguity = annotation.get("ambiguity") or {}
                conditions = (
                    [_condition(str(ambiguity.get("note", "")), evidence)]
                    if ambiguity.get("kind") == "conditional_value"
                    else []
                )
                key = (symbol, parameter, operator, raw, evidence)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(
                    {
                        "zone_symbol": symbol,
                        "parameter": parameter,
                        "operator": operator,
                        "raw_value": raw,
                        "value": annotation["normalized_value"],
                        "unit": contract.CATALOG[parameter].unit,
                        "applicability": applicability,
                        "conditions": conditions,
                        "evidence_quote": evidence,
                        "scope_quote": scope,
                    }
                )
    found = {(item["zone_symbol"], item["parameter"]) for item in candidates}
    not_found = [
        {"zone_symbol": symbol, "parameter": parameter}
        for symbol in block.symbols
        for parameter in contract.PARAMETERS
        if (symbol, parameter) not in found
    ]
    return {"candidates": candidates, "not_found": not_found}


def build_records(output_dir: Path) -> dict[str, dict[str, Any]]:
    """Rekordy ``ReplayStore`` dla wszystkich przypadków: ``{nazwa pliku: rekord}`` (deterministyczne)."""
    provider = ReplayStructuredExtractionProvider(output_dir, model=MODEL)
    service = LlmExtractionService(provider, ExtractionLimits())
    records: dict[str, dict[str, Any]] = {}
    for case in CASES:
        zones = sample_zones(case)
        for block in case_blocks(case):
            for planned in service.plan(block):
                request = planned.request
                payload = golden_payload(block, planned.chunk.rendered(block.text), zones)
                key = provider.cache_key(request)
                records[f"{key}.json"] = {
                    "schema_version": "1.0.0",
                    "cache_key": key,
                    "origin": ORIGIN,
                    "case": case.case_id,
                    "block": {"strategy": block.strategy, "scope_kind": block.scope_kind, "symbols": list(block.symbols)},
                    "request": {
                        "provider": provider.recorded_provider,
                        "model": MODEL,
                        "prompt_version": request.prompt_version,
                        "prompt_sha256": request.prompt_sha256,
                        "schema_version": request.schema_version,
                        "temperature": request.temperature,
                        "input_sha256": request.input_sha256,
                    },
                    "recorded_at": RECORDED_AT,
                    "response_sha256": contract.sha256_text(canonical_json(payload)),
                    "response": {
                        "content": payload,
                        "input_tokens": None,
                        "output_tokens": None,
                        "latency_ms": None,
                        "cost_usd": None,
                        "model_returned": MODEL,
                        "finish_reason": "STOP",
                    },
                }
    return records


def render(record: Mapping[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def stale_files(output_dir: Path, records: Mapping[str, Mapping[str, Any]]) -> list[str]:
    problems = []
    for name, record in records.items():
        path = output_dir / name
        if not path.is_file() or path.read_text(encoding="utf-8") != render(record):
            problems.append(f"brak lub nieaktualny: {name}")
    existing = {path.name for path in output_dir.glob("*.json")} if output_dir.is_dir() else set()
    problems.extend(f"zbędny: {name}" for name in sorted(existing - set(records)))
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--check", action="store_true", help="nie zapisuje; kod 1, gdy pliki są nieaktualne")
    args = parser.parse_args(argv)
    records = build_records(args.output_dir)
    problems = stale_files(args.output_dir, records)
    if args.check:
        for line in problems:
            print(line, file=sys.stderr)
        print(f"{len(records)} rekordów; {'zgodne' if not problems else 'NIEAKTUALNE'}")
        return 1 if problems else 0
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for path in args.output_dir.glob("*.json"):
        if path.name not in records:
            path.unlink()
    for name, record in records.items():
        (args.output_dir / name).write_text(render(record), encoding="utf-8")
    print(f"zapisano {len(records)} rekordów w {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
