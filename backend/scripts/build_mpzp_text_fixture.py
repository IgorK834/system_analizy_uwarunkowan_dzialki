"""Buduje statyczny fixture tekstu i tabel z publicznego dokumentu MPZP.

``tables.json`` jest opcjonalny: skrypt zapisuje go tylko wtedy, gdy
produkcyjna ekstrakcja ``pdfplumber`` rzeczywiście znalazła co najmniej jedną
tabelę. Dzięki temu zwykłe dokumenty tekstowe nie dostają pustego pliku.

``source.json`` zawiera także skrót SHA-256 pobranych bajtów całego dokumentu,
jego rozmiar, metodę ekstrakcji i numery stron źródła zachowanych w fragmencie
(BK-603). Surowy PDF nie trafia do repozytorium; ``--keep-pdf`` zapisuje go
poza repozytorium wyłącznie do ręcznej anotacji skanów.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
from pathlib import Path

import fitz
from PIL import Image, ImageFilter

from app.modules.documents.infrastructure.ocr_tesseract import TesseractOcrProvider
from app.services.mpzp_fetch import DocumentBlob, fetch_mpzp_document
from app.services.mpzp_parser_extract import extract_document_text

_LEGAL_BASIS = (
    "Akt prawa miejscowego, jawny publicznie (BIP urzędu miasta), pobrany "
    "zgodnie z ustawą o dostępie do informacji publicznej."
)


def parse_page_selection(spec: str) -> list[int]:
    """``"4-6,9"`` → ``[4, 5, 6, 9]`` (numeracja stron źródła od 1)."""
    pages: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            low, high = int(start), int(end)
            if low < 1 or high < low:
                raise ValueError(f"Nieprawidłowy zakres stron: {part!r}")
            pages.extend(range(low, high + 1))
        else:
            if int(part) < 1:
                raise ValueError(f"Nieprawidłowy numer strony: {part!r}")
            pages.append(int(part))
    return sorted(dict.fromkeys(pages))


def slice_pdf(content: bytes, pages: list[int]) -> tuple[bytes, int]:
    """Zwraca PDF zawierający wyłącznie wskazane strony i liczbę stron źródła."""
    with fitz.open(stream=content, filetype="pdf") as source:
        total = source.page_count
        if pages and pages[-1] > total:
            raise ValueError(f"Dokument ma {total} stron, żądano {pages[-1]}.")
        source.select([page - 1 for page in pages])
        return source.tobytes(), total


SIMULATED_SCAN_PARAMETERS: dict[str, float | int | str] = {
    "dpi": 110,
    "grayscale": "L",
    "blur_radius": 0.8,
    "rotation_deg": 0.6,
    "jpeg_quality": 55,
}


def simulate_scan(content: bytes) -> bytes:
    """Zamienia PDF z warstwą tekstową na deterministyczny skan obrazowy.

    Każda strona jest rasteryzowana z niską rozdzielczością, rozmywana,
    lekko obracana i kompresowana stratnie, a wynik to PDF zawierający wyłącznie
    obrazy. To symulacja skanu prawdziwego tekstu, nie prawdziwy skan; wynik
    OCR trzeba raportować osobno od realnych skanów.
    """
    params = SIMULATED_SCAN_PARAMETERS
    output = fitz.open()
    with fitz.open(stream=content, filetype="pdf") as source:
        for page in source:
            pixmap = page.get_pixmap(dpi=int(params["dpi"]))
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            image = image.convert(str(params["grayscale"]))
            image = image.filter(ImageFilter.GaussianBlur(float(params["blur_radius"])))
            image = image.rotate(
                float(params["rotation_deg"]), resample=Image.BICUBIC, fillcolor=255
            )
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=int(params["jpeg_quality"]))
            target = output.new_page(width=page.rect.width, height=page.rect.height)
            target.insert_image(target.rect, stream=buffer.getvalue())
    return output.tobytes()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Pobiera publiczny dokument MPZP produkcyjnym fetcherem i zapisuje "
            "wynik ekstrakcji tekstu oraz opcjonalnych tabel jako fixture JSON."
        )
    )
    parser.add_argument("--url", required=True, help="Publiczny URL dokumentu MPZP.")
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Katalog docelowy dla pages.json, opcjonalnego tables.json i source.json.",
    )
    parser.add_argument(
        "--note",
        default=None,
        help="Opcjonalny opis dokumentu, gminy albo regionu.",
    )
    parser.add_argument(
        "--voivodeship",
        default=None,
        help="Województwo gminy (wymagane dla dokumentów zbioru końcowego, PV3-02).",
    )
    parser.add_argument(
        "--pages",
        default=None,
        help="Zakres stron źródła zachowanych we fragmencie, np. 4-9,12.",
    )
    parser.add_argument(
        "--ocr",
        action="store_true",
        help="Dla skanu użyj produkcyjnego TesseractOcrProvider.",
    )
    parser.add_argument(
        "--simulate-scan",
        action="store_true",
        help="Zastąp strony obrazami (symulacja skanu) i użyj OCR; wynik oznaczony jako symulowany.",
    )
    parser.add_argument(
        "--keep-pdf",
        type=Path,
        default=None,
        help="Zapisz pobrane bajty poza repozytorium (ręczna anotacja skanów).",
    )
    return parser.parse_args()


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


async def build_fixture(
    url: str,
    output_dir: Path,
    note: str | None,
    pages: list[int] | None = None,
    use_ocr: bool = False,
    keep_pdf: Path | None = None,
    simulate: bool = False,
    voivodeship: str | None = None,
) -> None:
    """Pobiera dokument i zapisuje niezmieniony wynik produkcyjnej ekstrakcji."""
    document_blob = await fetch_mpzp_document(url)
    document_sha256 = hashlib.sha256(document_blob.content).hexdigest()
    content_length = len(document_blob.content)
    if keep_pdf is not None:
        keep_pdf.parent.mkdir(parents=True, exist_ok=True)
        keep_pdf.write_bytes(document_blob.content)

    page_count: int | None = None
    fragment_sha256: str | None = None
    extraction_blob: DocumentBlob = document_blob
    if pages and document_blob.media_type == "application/pdf":
        fragment, page_count = slice_pdf(document_blob.content, pages)
        fragment_sha256 = hashlib.sha256(fragment).hexdigest()
        extraction_blob = DocumentBlob(
            content=fragment,
            media_type=document_blob.media_type,
            filename=document_blob.filename,
            source_metadata=document_blob.source_metadata,
        )

    scan_fragment_sha256: str | None = None
    if simulate:
        scanned = simulate_scan(extraction_blob.content)
        scan_fragment_sha256 = hashlib.sha256(scanned).hexdigest()
        extraction_blob = DocumentBlob(
            content=scanned,
            media_type=extraction_blob.media_type,
            filename=extraction_blob.filename,
            source_metadata=extraction_blob.source_metadata,
        )
        use_ocr = True
        if keep_pdf is not None:
            keep_pdf.with_suffix(".scan.pdf").write_bytes(scanned)

    if use_ocr:
        text_result = await extract_document_text(
            extraction_blob, TesseractOcrProvider()
        )
    else:
        text_result = await extract_document_text(extraction_blob)
    output_dir.mkdir(parents=True, exist_ok=True)

    fetched_at = document_blob.source_metadata.fetched_at
    _write_json(output_dir / "pages.json", {"pages": text_result.pages})
    if text_result.tables:
        _write_json(
            output_dir / "tables.json",
            [
                {"page_number": table.page_number, "rows": table.rows}
                for table in text_result.tables
            ],
        )
    source: dict[str, object] = {
        "url": url,
        "fetched_at": fetched_at.isoformat() if fetched_at is not None else None,
        "media_type": document_blob.media_type,
        "filename": document_blob.filename,
        "quality_score": text_result.quality_score,
        "needs_ocr": text_result.needs_ocr,
        "note": note,
        "legal_basis": _LEGAL_BASIS,
        # Produkcyjny fetcher używa domyślnej weryfikacji TLS httpx; błąd certyfikatu
        # kończy pobranie (dokument jest odrzucany, weryfikacji nie wyłącza się).
        "tls_verification": "enabled",
        "voivodeship": voivodeship,
        "document_sha256": document_sha256,
        "content_length": content_length,
        "extraction_method": text_result.extraction_method,
        "ocr_used": text_result.ocr_used,
        "ocr_engine_version": text_result.ocr_engine_version,
        "manual_review_required": text_result.manual_review_required,
    }
    if simulate:
        source["ocr_origin"] = "simulated_scan_of_real_text_pdf"
        source["simulated_scan"] = dict(SIMULATED_SCAN_PARAMETERS)
        source["scan_fragment_sha256"] = scan_fragment_sha256
    elif text_result.ocr_used:
        source["ocr_origin"] = "real_scan"
    if pages:
        source["source_page_numbers"] = pages
        source["source_page_count"] = page_count
        source["fragment_sha256"] = fragment_sha256
    else:
        source["source_page_numbers"] = list(range(1, len(text_result.pages) + 1))
    _write_json(output_dir / "source.json", source)


def main() -> None:
    args = _parse_args()
    pages = parse_page_selection(args.pages) if args.pages else None
    asyncio.run(
        build_fixture(
            args.url,
            args.output_dir,
            args.note,
            pages=pages,
            use_ocr=args.ocr,
            keep_pdf=args.keep_pdf,
            simulate=args.simulate_scan,
            voivodeship=args.voivodeship,
        )
    )


if __name__ == "__main__":
    main()
