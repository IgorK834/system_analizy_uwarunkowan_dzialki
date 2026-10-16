"""Nagrywa zamrożone odpowiedzi publicznego API Rejestru Urbanistycznego dla importu krajowego POG (ADR-026).

Zapisuje do katalogu wyjściowego (domyślnie ``backend/tests/fixtures/ru_national``):

* ``listing_current_page_N.json`` — strony wykazu bieżących wersji aktów POG (``POST /api/public/published/query``);
* ``act_<publishedActId>.gml`` — pliki GML wybranych aktów (``GET /api/public/published/<id>/download``), bajt w bajt;
* ``manifest.json`` — adres, czas pobrania UTC, SHA-256, rozmiar i nagłówki każdego pliku oraz przebieg ``HEAD``.

Żądania są rozstawione w czasie (RU odpowiada HTTP 429 powyżej kilku żądań na sekundę). Skrypt jest narzędziem
operatora; zwykłe testy i CI czytają zamrożone pliki i nie wykonują żądań sieciowych. Pobrane pliki są niezaufane —
skrypt ich nie parsuje.

Użycie (z kontenera ``backend-test``, który ma dostęp do sieci)::

    python scripts/capture_ru_pog_national.py --out /out --acts 1152,1735,1894,295
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

BASE = "https://rejestr-urbanistyczny.gov.pl/api/public/published"
DEFAULT_ACTS = (1152, 1735, 1894, 295)
INTERVAL_SECONDS = 1.5


def _query_body(number: int) -> dict[str, object]:
    return {
        "page": {"number": number, "size": 100, "sort": {"sortBy": "publishDate", "direction": "desc"}},
        "criteria": {
            "query": None,
            "parcelIdentifier": None,
            "statusList": [],
            "publishedActVersions": "CURRENT",
            "tercCodeList": [],
            "publishDateFrom": None,
            "publishDateTo": None,
            "planValidFrom": None,
            "planValidTo": None,
            "planTypeList": ["MUNICIPAL_GENERAL_PLAN"],
            "plansGroup": "MUNICIPAL_GENERAL_PLAN",
        },
    }


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _get(client: httpx.Client, method: str, url: str, **kwargs: object) -> httpx.Response:
    for attempt in range(5):
        response = client.request(method, url, **kwargs)  # type: ignore[arg-type]
        if response.status_code != 429:
            return response
        time.sleep(5.0 * (attempt + 1))
    raise SystemExit(f"RU odpowiada 429 mimo odczekań: {url}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--acts", default=",".join(str(item) for item in DEFAULT_ACTS))
    parser.add_argument("--pages", type=int, default=0, help="Ile stron wykazu zapisać (0 = wszystkie).")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, object] = {
        "source": "Rejestr Urbanistyczny — publiczne API (frontend), zasób ru_published katalogu pog_app",
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files": [],
    }
    files: list[dict[str, object]] = manifest["files"]  # type: ignore[assignment]
    with httpx.Client(timeout=httpx.Timeout(30.0, read=180.0), follow_redirects=False) as client:
        number, total_pages = 1, 1
        while number <= total_pages and (args.pages <= 0 or number <= args.pages):
            url = f"{BASE}/query"
            response = _get(client, "POST", url, json=_query_body(number))
            response.raise_for_status()
            payload = response.json()
            total_pages = int(payload["pageInfo"]["totalPages"])
            name = f"listing_current_page_{number}.json"
            (args.out / name).write_bytes(response.content)
            files.append(
                {
                    "file": name, "url": url, "method": "POST", "status": response.status_code,
                    "bytes": len(response.content), "sha256": _sha256(response.content),
                    "total_elements": payload["pageInfo"]["totalElements"], "total_pages": total_pages,
                    "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
            )
            print(f"strona wykazu {number}/{total_pages}: {len(payload['publishedActs'])} wierszy", file=sys.stderr)
            number += 1
            time.sleep(INTERVAL_SECONDS)
        for raw in args.acts.split(","):
            if not raw.strip():
                continue
            act_id = int(raw)
            url = f"{BASE}/{act_id}/download"
            head = _get(client, "HEAD", url)
            time.sleep(INTERVAL_SECONDS)
            response = _get(client, "GET", url)
            response.raise_for_status()
            name = f"act_{act_id}.gml"
            (args.out / name).write_bytes(response.content)
            files.append(
                {
                    "file": name, "url": url, "method": "GET", "status": response.status_code,
                    "bytes": len(response.content), "sha256": _sha256(response.content),
                    "head_content_length": head.headers.get("content-length"),
                    "content_disposition": response.headers.get("content-disposition"),
                    "content_type": response.headers.get("content-type"),
                    "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
            )
            print(f"akt {act_id}: {len(response.content)} B", file=sys.stderr)
            time.sleep(INTERVAL_SECONDS)
    (args.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
