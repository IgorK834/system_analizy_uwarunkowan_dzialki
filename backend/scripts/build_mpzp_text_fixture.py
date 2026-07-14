"""Buduje statyczny fixture tekstowy z publicznego dokumentu MPZP."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.services.mpzp_fetch import fetch_mpzp_document
from app.services.mpzp_parser_extract import extract_document_text

_LEGAL_BASIS = (
    "Akt prawa miejscowego, jawny publicznie (BIP urzędu miasta), pobrany "
    "zgodnie z ustawą o dostępie do informacji publicznej."
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Pobiera publiczny dokument MPZP produkcyjnym fetcherem i zapisuje "
            "wynik ekstrakcji tekstu jako fixture JSON."
        )
    )
    parser.add_argument("--url", required=True, help="Publiczny URL dokumentu MPZP.")
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Katalog docelowy dla pages.json i source.json.",
    )
    parser.add_argument(
        "--note",
        default=None,
        help="Opcjonalny opis dokumentu, gminy albo regionu.",
    )
    return parser.parse_args()


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


async def build_fixture(url: str, output_dir: Path, note: str | None) -> None:
    """Pobiera dokument i zapisuje niezmieniony wynik produkcyjnej ekstrakcji."""
    document_blob = await fetch_mpzp_document(url)
    text_result = await extract_document_text(document_blob)
    output_dir.mkdir(parents=True, exist_ok=True)

    fetched_at = document_blob.source_metadata.fetched_at
    _write_json(output_dir / "pages.json", {"pages": text_result.pages})
    _write_json(
        output_dir / "source.json",
        {
            "url": url,
            "fetched_at": fetched_at.isoformat() if fetched_at is not None else None,
            "media_type": document_blob.media_type,
            "filename": document_blob.filename,
            "quality_score": text_result.quality_score,
            "needs_ocr": text_result.needs_ocr,
            "note": note,
            "legal_basis": _LEGAL_BASIS,
        },
    )


def main() -> None:
    args = _parse_args()
    asyncio.run(build_fixture(args.url, args.output_dir, args.note))


if __name__ == "__main__":
    main()
