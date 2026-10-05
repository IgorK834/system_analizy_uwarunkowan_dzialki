"""Zamrożony wynik parsera MPZP w trybach deterministycznych (PV3-14): punkt odniesienia regresji.

Uruchamia ``parse_mpzp_document`` na dokumentach regresji parsera (``tests/fixtures/mpzp``) w trybie
zakresu ``legacy`` i ``blocks`` i zapisuje wynik (kontrakt parsera i snapshot strefy API) do
``tests/fixtures/mpzp_parser_modes/frozen_parse_results.json``. Plik zamrożono 2026-10-05 na kodzie
SPRZED wprowadzenia flagi ``MPZP_PARSER_MODE``; test ``test_mpzp_parser_modes`` wymaga, żeby tryb
``legacy`` (domyślny) i ``v3`` dawały identyczny wynik. Ponowne zamrożenie jest dozwolone wyłącznie
przy świadomej zmianie parsera (z podniesieniem ``MPZP_RESULT_SCHEMA_VERSION``).

    python3 scripts/freeze_mpzp_parser_modes.py          # zapis
    python3 scripts/freeze_mpzp_parser_modes.py --check  # porównanie (kod 1 przy różnicy)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.schemas.source import SourceMetadata  # noqa: E402
from app.services.mpzp_parser import parse_mpzp_document  # noqa: E402
from app.services.mpzp_zones import map_parser_zone_to_analyze_response  # noqa: E402
from tests.test_mpzp_parser_regression import (  # noqa: E402
    FIXTURE_DIRS,
    _build_document_blob,
    _build_extraction,
    _load_json,
)

OUTPUT = BACKEND / "tests" / "fixtures" / "mpzp_parser_modes" / "frozen_parse_results.json"
FIXED_SOURCE = SourceMetadata(
    source_name="MPZP_BIP",
    source_url="https://bip.example.test/uchwala.pdf",
    fetched_at=datetime(2026, 10, 5, tzinfo=timezone.utc),
    confidence=0.9,
    manual_review_required=False,
)


async def parse_fixture(fixture_dir: Path, scope_mode: str, **options: Any) -> dict[str, Any]:
    extraction, source = _build_extraction(fixture_dir)
    symbols = _load_json(fixture_dir / "expected.json")["zone_symbols_to_test"]
    with patch("app.services.mpzp_parser.extract_document_text", new=AsyncMock(return_value=extraction)):
        result = await parse_mpzp_document(_build_document_blob(source), list(symbols), scope_mode=scope_mode, **options)
    zones = [
        map_parser_zone_to_analyze_response(zone, 1000.0, FIXED_SOURCE)[0].model_dump(mode="json")
        for zone in result.zones
    ]
    return {"parse_result": result.model_dump(mode="json"), "api_zones": zones}


async def build(**options: Any) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for fixture_dir in FIXTURE_DIRS:
        for scope_mode in ("legacy", "blocks"):
            output[f"{fixture_dir.name}:{scope_mode}"] = await parse_fixture(fixture_dir, scope_mode, **options)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    payload = json.dumps(asyncio.run(build()), ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    if args.check:
        same = OUTPUT.is_file() and OUTPUT.read_text(encoding="utf-8") == payload
        print("frozen parse results: " + ("up to date" if same else "DIFFERENT"))
        return 0 if same else 1
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(payload, encoding="utf-8")
    print(f"written {OUTPUT.relative_to(BACKEND)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
