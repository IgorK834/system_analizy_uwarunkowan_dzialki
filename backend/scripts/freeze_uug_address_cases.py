"""Manual AU-113 live probe; freezes unmodified UUG responses for 20 address forms."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.modules.location.domain.normalization import uug_query_variants  # noqa: E402
from app.modules.location.infrastructure.uug_adapter import _localities, _UUG_BASE_URL  # noqa: E402

QUERIES = (
    "Marszałkowska 1 Warszawa", "Kraków Floriańska 3", "Wrocław Rynek 1",
    "Piaseczno Puławska 10", "Lipkowska Góra Kalwaria", "Warszawa, Marszałkowska 1",
    "Warszawa ul. Marszalkowska 1", "ul. Marszałkowska 1 Warszawa",
    "Krakow Florianska 3", "Florianska 3 Krakow", "Wroclaw Rynek 1", "Rynek 1 Wroclaw",
    "Piaseczno ul. Pulawska 10", "ul. Pulawska 10 Piaseczno",
    "Gora Kalwaria Lipkowska", "Lipkowska Gora Kalwaria",
    "Warszawa al. Jana Pawła II 20", "al. Jana Pawła II 20 Warszawa",
    "Marszałkowska 1, Warszawa", "  Kraków,   ul. Floriańska   3  ",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    cases = []
    with httpx.Client(timeout=30) as client:
        for query in QUERIES:
            attempts = []
            count = 0
            for variant in uug_query_variants(query, _localities()):
                params = {"request": "GetAddress", "address": variant, "accuracy": "0.5", "exact_number": "0"}
                response = client.get(_UUG_BASE_URL, params=params)
                response.raise_for_status()
                digest = hashlib.sha256(response.content).hexdigest()
                filename = f"{digest}.json"
                (args.output / filename).write_bytes(response.content)
                results = response.json().get("results") or {}
                count = len(results)
                attempts.append({"params": params, "file": filename, "sha256": digest, "http_status": response.status_code})
                if count:
                    break
            cases.append({"query": query, "attempts": attempts, "result_count": count})
            print(f"{query}: {count}", flush=True)
    manifest = {"source": _UUG_BASE_URL, "fetched_at": datetime.now(timezone.utc).isoformat(),
                "attribution": "GUGiK — UUG/EMUiA", "cases": cases}
    (args.output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if all(case["result_count"] > 0 for case in cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
