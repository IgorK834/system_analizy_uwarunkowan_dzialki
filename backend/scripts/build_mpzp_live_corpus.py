"""Buduje zamrożony zbiór realnych uchwał MPZP ``tests/fixtures/mpzp_live`` (AU-102, Task 22.2).

Uruchomienie z katalogu ``backend`` (obraz backendu ma zaufany łańcuch TLS i Tesseract)::

    python -m scripts.build_mpzp_live_corpus [--only SLUG ...] [--force]

Dla każdego dokumentu z ``manifest.json`` skrypt pobiera uchwałę produkcyjnym fetcherem (z weryfikacją TLS),
ekstrahuje tekst produkcyjnym ekstraktorem (``extract_document_text``, bez OCR — skan zostaje skanem, jak w
produkcji) i zapisuje ``<slug>/pages.json``, ``source.json`` (adres, SHA-256 bajtów, rozmiar, metoda ekstrakcji)
oraz ewentualne ``tables.json``. Surowe PDF-y nie trafiają do repozytorium (rozmiar do ok. 43 MB) — przechowywane
są tylko tekst stron i skróty. Po budowie ``manifest.json`` dostaje skróty ``pages_sha256`` i ``document_sha256``.

Adnotacje stref w ``manifest.json`` (``documents[].zones``) wskazują strony i cytaty sekcji oraz oczekiwane parametry;
test ``tests/test_mpzp_live_corpus.py`` sprawdza je względem zamrożonych stron.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

LIVE_DIR = BACKEND_DIR / "tests" / "fixtures" / "mpzp_live"
MANIFEST = LIVE_DIR / "manifest.json"


def _sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def _build(slugs: list[str] | None, force: bool) -> None:
    from scripts.build_mpzp_text_fixture import build_fixture

    manifest = json.loads(MANIFEST.read_text("utf-8"))
    for document in manifest["documents"]:
        slug = document["slug"]
        if slugs and slug not in slugs:
            continue
        target = LIVE_DIR / slug
        if (target / "pages.json").exists() and not force:
            print(f"pominięto (istnieje): {slug}")
        else:
            print(f"pobieranie: {slug} <- {document['source_url']}")
            await build_fixture(
                document["source_url"],
                target,
                document["title"],
                voivodeship=document["voivodeship"],
            )
        source = json.loads((target / "source.json").read_text("utf-8"))
        document["document_sha256"] = source["document_sha256"]
        document["content_length"] = source["content_length"]
        document["fetched_at"] = source["fetched_at"]
        document["extraction_method"] = source["extraction_method"]
        document["needs_ocr"] = source["needs_ocr"]
        document["page_count"] = len(json.loads((target / "pages.json").read_text("utf-8"))["pages"])
        document["pages_sha256"] = _sha256_of(target / "pages.json")
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", "utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", nargs="*", help="slugi dokumentów do zbudowania")
    parser.add_argument("--force", action="store_true", help="pobierz ponownie istniejące")
    args = parser.parse_args()
    asyncio.run(_build(args.only, args.force))


if __name__ == "__main__":
    main()
