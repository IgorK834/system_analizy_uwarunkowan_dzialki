"""Realny adapter OCR: PyMuPDF rasteryzacja + Tesseract z językiem polskim."""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

from app.modules.documents.infrastructure.worker import (
    DocumentPageLimitError,
    ProcessDocumentWorker,
    WorkerLimits,
)
from app.services.mpzp_parser_extract import (
    OcrExtractionResult,
    OcrPageResult,
)


@dataclass(frozen=True)
class _OcrPayload:
    pdf_bytes: bytes
    language: str
    dpi: int


def _pdf_page_count(pdf_bytes: bytes) -> int:
    import fitz

    with fitz.open(stream=pdf_bytes, filetype="pdf") as document:
        return document.page_count


def _ocr_pdf_sync(payload: _OcrPayload) -> OcrExtractionResult:
    import fitz
    import pytesseract
    from PIL import Image
    from pytesseract import Output

    scale = payload.dpi / 72.0
    pages: list[OcrPageResult] = []
    with fitz.open(stream=payload.pdf_bytes, filetype="pdf") as document:
        for page_number, page in enumerate(document, start=1):
            pixmap = page.get_pixmap(
                matrix=fitz.Matrix(scale, scale),
                alpha=False,
            )
            image = Image.open(io.BytesIO(pixmap.tobytes("png")))
            data: dict[str, list[Any]] = pytesseract.image_to_data(
                image,
                lang=payload.language,
                config="--oem 1 --psm 6",
                output_type=Output.DICT,
            )
            words: list[str] = []
            blocks: list[dict[str, object]] = []
            confidences: list[float] = []
            for index, raw_text in enumerate(data["text"]):
                text = str(raw_text).strip()
                try:
                    confidence = float(data["conf"][index])
                except (TypeError, ValueError):
                    confidence = -1.0
                if not text:
                    continue
                words.append(text)
                if confidence >= 0:
                    confidences.append(confidence)
                blocks.append(
                    {
                        "text": text,
                        "left": int(data["left"][index]),
                        "top": int(data["top"][index]),
                        "width": int(data["width"][index]),
                        "height": int(data["height"][index]),
                        "confidence": (
                            round(confidence / 100.0, 4)
                            if confidence >= 0
                            else None
                        ),
                    }
                )
            page_quality = (
                sum(confidences) / len(confidences) / 100.0
                if confidences
                else 0.0
            )
            pages.append(
                OcrPageResult(
                    page_number=page_number,
                    text=" ".join(words),
                    quality=page_quality,
                    blocks=blocks,
                )
            )

    quality_score = (
        sum(page.quality or 0.0 for page in pages) / len(pages)
        if pages
        else 0.0
    )
    return OcrExtractionResult(
        pages=pages,
        engine_version=f"Tesseract {pytesseract.get_tesseract_version()}",
        quality_score=quality_score,
    )


class TesseractOcrProvider:
    """Adapter wykonujący rasteryzację i OCR w izolowanym workerze."""

    def __init__(
        self,
        *,
        worker: ProcessDocumentWorker | None = None,
        limits: WorkerLimits | None = None,
        language: str = "pol",
        dpi: int = 300,
    ) -> None:
        active_limits = limits or WorkerLimits()
        self._worker = worker or ProcessDocumentWorker(active_limits)
        self._limits = active_limits
        self._language = language
        self._dpi = dpi

    async def extract_text_from_scan(
        self, pdf_bytes: bytes
    ) -> OcrExtractionResult:
        page_count = _pdf_page_count(pdf_bytes)
        if page_count > self._limits.max_pages:
            raise DocumentPageLimitError(
                f"Dokument ma {page_count} stron, limit wynosi "
                f"{self._limits.max_pages}."
            )
        return await self._worker.run(
            _ocr_pdf_sync,
            _OcrPayload(
                pdf_bytes=pdf_bytes,
                language=self._language,
                dpi=self._dpi,
            ),
        )
