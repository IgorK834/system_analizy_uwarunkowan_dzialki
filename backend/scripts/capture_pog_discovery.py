#!/usr/bin/env python3
"""Przechwycenie wyniku discovery POG dla korpusu referencyjnego razem z liczbą żądań HTTP (AU-105, Task 22.5).

Skrypt przepuszcza geometrie działek korpusu (``manifest.json`` + ``artifacts/parcels/*.geojson``, EPSG:2180)
przez ``app.services.pog.discover_pog`` na **żywym** Rejestrze Urbanistycznym i zapisuje:

* pochodną wyniku (status prawny, kod statusu, numer i data uchwały, odnośniki, statusy czterech warstw
  logicznych, kody ostrzeżeń, źródło) — bez geometrii i bez treści odpowiedzi;
* liczbę żądań HTTP do ``rejestr-urbanistyczny.gov.pl`` na działkę (licznik na ``httpx.Client.send``, więc
  zlicza także ponowienia) oraz czas.

Ten sam skrypt służy do zamrożenia stanu *przed* zmianą (``--label legacy``) i do pomiaru stanu *po* zmianie
(``--label wfs``); porównanie wyniku odbywa się w ``tests/test_pog_discovery_baseline.py`` na zamrożonych
odpowiedziach, a nie na żywej usłudze.

Wymaga sieci. Działa w kontenerze ``backend-test`` (``REPO_ROOT`` wskazuje kopię repozytorium)::

    docker run --rm --entrypoint python -w /app -e REPO_ROOT=/repo \\
        -v "$PWD/backend/app:/app/app:ro" -v "$PWD/backend/scripts:/app/scripts:ro" \\
        -v "$PWD:/repo:ro" -v "$OUT:/out" dzialki-backend-test:latest \\
        scripts/capture_pog_discovery.py --label legacy --output /out/pog-discovery-legacy.json

Kod wyjścia: ``0`` — przechwycono wszystkie działki; ``2`` — błąd użycia.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

BACKEND_DIR = Path(__file__).resolve().parent.parent
os.chdir(BACKEND_DIR)  # Settings czyta ``.env`` względem bieżącego katalogu (zob. check_llm_pin.py).
sys.path.insert(0, str(BACKEND_DIR))

from shapely.geometry import shape  # noqa: E402

CORPUS_DIR = BACKEND_DIR / "tests" / "fixtures" / "reference_corpus"
SCHEMA_VERSION = "pog-discovery-capture/1"
RU_HOST = "rejestr-urbanistyczny.gov.pl"


class RequestCounter:
    """Zlicza żądania HTTP per host na ``httpx.Client.send`` (także ponowienia i przekierowania)."""

    def __init__(self) -> None:
        self.hosts: Counter[str] = Counter()

    def reset(self) -> None:
        self.hosts.clear()

    @property
    def ru_requests(self) -> int:
        return self.hosts[RU_HOST]


@contextmanager
def count_requests() -> Iterator[RequestCounter]:
    counter = RequestCounter()
    original = httpx.Client.send

    def counting_send(self: httpx.Client, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        counter.hosts[(urlsplit(str(request.url)).hostname or "").casefold()] += 1
        return original(self, request, **kwargs)

    httpx.Client.send = counting_send  # type: ignore[method-assign]
    try:
        yield counter
    finally:
        httpx.Client.send = original  # type: ignore[method-assign]


def load_cases(manifest_path: Path, only: Sequence[str]) -> list[dict[str, Any]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cases = list(manifest["cases"])
    if only:
        wanted = set(only)
        cases = [case for case in cases if case["case_id"] in wanted]
    return cases


def load_geometry(case: dict[str, Any]) -> Any:
    feature = json.loads((CORPUS_DIR / "artifacts" / "parcels" / f"{case['case_id']}.geojson").read_text("utf-8"))
    return shape(feature["geometry"])


def project(result: Any) -> dict[str, Any]:
    """Pochodna ``PogDiscoveryResult`` do porównań: bez geometrii, bez treści odpowiedzi."""
    sections = {
        name: {
            "status": section.status,
            "feature_count": section.feature_count,
            "raw_legal_status": section.raw_legal_status,
        }
        for name, section in (
            ("planning_act", result.planning_act),
            ("downtown_area", result.downtown_area),
            ("ouz", result.ouz),
            ("planning_zones", result.planning_zones),
        )
    }
    source = result.source_metadata
    return {
        "legal_status": result.legal_status,
        "raw_legal_status": result.raw_legal_status,
        "uchwala_nr": result.uchwala_nr,
        "uchwala_date": result.uchwala_date,
        "links": list(result.links),
        "source_responded": result.source_responded,
        "is_discovery_only": result.is_discovery_only,
        "act_found": result.act_found,
        "preview_features_found": result.preview_features_found,
        "sections": sections,
        "warning_codes": sorted({warning.code for warning in result.warnings}),
        "source": {
            "source_id": source.source_id,
            "source_name": source.source_name,
            "source_url": source.source_url,
            "manual_review_required": source.manual_review_required,
        },
    }


def _clear_query_cache() -> None:
    """Zimny start każdej działki (cache zapytań RU z AU-105; stary silnik go nie ma)."""
    try:
        from app.modules.planning.composition import clear_ru_pog_query_cache
    except ImportError:
        return
    clear_ru_pog_query_cache()


async def capture(cases: Sequence[dict[str, Any]], label: str, *, cold: bool) -> dict[str, Any]:
    from app.services.pog import PogGminaSources, discover_pog

    rows: list[dict[str, Any]] = []
    with count_requests() as counter:
        for case in cases:
            geometry = load_geometry(case)
            if cold:
                _clear_query_cache()
            counter.reset()
            started = time.monotonic()
            error: str | None = None
            projected: dict[str, Any] | None = None
            try:
                result = await discover_pog(geometry, PogGminaSources(teryt=str(case["teryt"])))
                projected = project(result)
            except Exception as exc:  # noqa: BLE001 - przechwycenie nie może przerwać serii
                error = type(exc).__name__
            rows.append(
                {
                    "case_id": case["case_id"],
                    "teryt": case["teryt"],
                    "area_sqm": round(geometry.area, 1),
                    "elapsed_ms": round((time.monotonic() - started) * 1000),
                    "ru_requests": counter.ru_requests,
                    "other_requests": sum(counter.hosts.values()) - counter.ru_requests,
                    "error": error,
                    "result": projected,
                }
            )
            print(
                f"{case['case_id']}: ru={counter.ru_requests} "
                f"{rows[-1]['elapsed_ms']} ms {projected['legal_status'] if projected else error}",
                file=sys.stderr,
            )
    return {
        "schema_version": SCHEMA_VERSION,
        "label": label,
        "cold_cache": cold,
        "captured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "cases": rows,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", maxsplit=1)[0])
    parser.add_argument("--label", required=True, help="Etykieta przebiegu, np. legacy albo wfs.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=CORPUS_DIR / "manifest.json")
    parser.add_argument("--case", action="append", default=[], help="Ogranicz do wskazanego case_id (powtarzalne).")
    parser.add_argument("--warm", action="store_true", help="Nie czyść cache zapytań RU między działkami.")
    args = parser.parse_args(argv)
    cases = load_cases(args.manifest, args.case)
    if not cases:
        print("Brak działek do przechwycenia.", file=sys.stderr)
        return 2
    report = asyncio.run(capture(cases, args.label, cold=not args.warm))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
