from __future__ import annotations

from concurrent.futures import Future
import io
import shutil

import fitz
import pytest
from PIL import Image, ImageDraw

from app.modules.documents.domain.parser_models import DocumentInput
from app.modules.documents.infrastructure.ocr_tesseract import (
    TesseractOcrProvider,
    _OcrPayload,
    _ocr_pdf_sync,
    _pdf_page_count,
)
from app.modules.documents.infrastructure.parser_adapter import (
    LegacyMpzpParserAdapter,
)
from app.modules.documents.infrastructure.worker import (
    DocumentPageLimitError,
    DocumentWorkerError,
    DocumentWorkerTimeoutError,
    ProcessDocumentWorker,
    WorkerLimits,
    _run_with_resource_limits,
    _terminate_executor_workers,
)
from app.services.mpzp_parser_extract import (
    OcrExtractionResult,
    OcrPageResult,
    TextExtractionResult,
)
from app.services.mpzp_parser_segment import DocumentSegment


def _pdf_bytes(page_count: int = 1) -> bytes:
    document = fitz.open()
    for _ in range(page_count):
        document.new_page(width=300, height=200)
    content = document.tobytes()
    document.close()
    return content


class _Ocr:
    async def extract_text_from_scan(self, pdf_bytes: bytes):
        return OcrExtractionResult(
            pages=[OcrPageResult(1, "§ 1. OCR", 0.9, [{"left": 1}])],
            engine_version="Tesseract fake",
            quality_score=0.9,
        )


@pytest.mark.asyncio
async def test_legacy_adapter_integrates_existing_parser_stages(
    monkeypatch,
) -> None:
    adapter = LegacyMpzpParserAdapter(_Ocr())
    document = DocumentInput(b"%PDF", "application/pdf", "plan.pdf")
    extracted = TextExtractionResult(
        pages=[""],
        tables=[],
        quality_score=0.0,
        needs_ocr=True,
        manual_review_required=True,
    )
    monkeypatch.setattr(
        "app.modules.documents.infrastructure.parser_adapter.classify_document",
        lambda blob: "pdf",
    )

    async def fake_extract(blob):
        return extracted

    async def fake_ocr(blob, legacy, provider):
        return TextExtractionResult(
            pages=["§ 1. Tekst"],
            quality_score=0.9,
            needs_ocr=False,
            ocr_used=True,
            extraction_method="ocr",
            ocr_engine_version="Tesseract fake",
            page_qualities=[0.9],
            blocks=[[{"left": 2}]],
            manual_review_required=True,
        )

    monkeypatch.setattr(
        "app.modules.documents.infrastructure.parser_adapter.extract_document_text",
        fake_extract,
    )
    monkeypatch.setattr(
        "app.modules.documents.infrastructure.parser_adapter.apply_ocr_to_extraction",
        fake_ocr,
    )
    monkeypatch.setattr(
        "app.modules.documents.infrastructure.parser_adapter.segment_document",
        lambda result: [
            DocumentSegment("seg-1", result.pages[0], 1, None, "paragraph")
        ],
    )

    assert adapter.classify(document) == "pdf"
    first = await adapter.extract(document)
    assert first.needs_ocr is True
    after_ocr = await adapter.ocr(document, first)
    assert after_ocr.ocr_engine_version == "Tesseract fake"
    assert after_ocr.blocks[0][0]["left"] == 2
    assert adapter.segment(after_ocr)[0].segment_id == "seg-1"


class _Worker:
    def __init__(self) -> None:
        self.call = None

    async def run(self, function, payload):
        self.call = (function, payload)
        return OcrExtractionResult(
            pages=[OcrPageResult(1, "tekst", 0.8)],
            engine_version="Tesseract worker",
            quality_score=0.8,
        )


@pytest.mark.asyncio
async def test_ocr_provider_checks_page_limit_and_dispatches_worker() -> None:
    worker = _Worker()
    provider = TesseractOcrProvider(
        worker=worker,
        limits=WorkerLimits(max_pages=1),
        dpi=220,
    )

    result = await provider.extract_text_from_scan(_pdf_bytes())

    assert result.engine_version == "Tesseract worker"
    assert worker.call is not None
    assert worker.call[1].dpi == 220
    with pytest.raises(DocumentPageLimitError):
        await provider.extract_text_from_scan(_pdf_bytes(2))


def test_ocr_sync_records_words_coordinates_confidence_and_version(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "pytesseract.image_to_data",
        lambda *args, **kwargs: {
            "text": ["", "Uchwała", "§", "8"],
            "conf": ["-1", "95", "bad", "85"],
            "left": [0, 10, 80, 95],
            "top": [0, 20, 20, 20],
            "width": [0, 60, 10, 10],
            "height": [0, 15, 15, 15],
        },
    )
    monkeypatch.setattr(
        "pytesseract.get_tesseract_version", lambda: "5.test"
    )

    result = _ocr_pdf_sync(_OcrPayload(_pdf_bytes(), "pol", 100))

    assert result.engine_version == "Tesseract 5.test"
    assert result.pages[0].text == "Uchwała § 8"
    assert result.pages[0].quality == pytest.approx(0.9)
    assert result.pages[0].blocks[0]["left"] == 10
    assert result.pages[0].blocks[1]["confidence"] is None
    assert _pdf_page_count(_pdf_bytes(2)) == 2


def _double(value: int) -> int:
    return value * 2


def _raise_value_error() -> None:
    raise ValueError("boom")


def _slow() -> str:
    import time

    time.sleep(0.2)
    return "done"


def test_worker_direct_wrapper_runs_function_on_current_platform() -> None:
    assert _run_with_resource_limits(
        _double, (3,), WorkerLimits()
    ) == 6


def test_timeout_termination_stops_live_executor_processes() -> None:
    class Process:
        terminated = False

        def is_alive(self) -> bool:
            return True

        def terminate(self) -> None:
            self.terminated = True

    process = Process()
    executor = object.__new__(_InlineExecutor)
    executor._processes = {1: process}

    _terminate_executor_workers(executor)

    assert process.terminated is True


@pytest.mark.asyncio
async def test_process_worker_success_error_and_timeout() -> None:
    try:
        result = await ProcessDocumentWorker(
            WorkerLimits(timeout_seconds=2)
        ).run(_double, 4)
    except PermissionError:
        pytest.skip("Sandbox hosta blokuje semafory multiprocessing.")
    assert result == 8

    with pytest.raises(DocumentWorkerError):
        await ProcessDocumentWorker(
            WorkerLimits(timeout_seconds=2)
        ).run(_raise_value_error)
    with pytest.raises(DocumentWorkerTimeoutError):
        await ProcessDocumentWorker(
            WorkerLimits(timeout_seconds=0.01)
        ).run(_slow)


class _InlineExecutor:
    def __init__(self, *args, **kwargs) -> None:
        self.shutdown_called = False

    def submit(self, function, *args):
        future = Future()
        try:
            future.set_result(function(*args))
        except Exception as exc:
            future.set_exception(exc)
        return future

    def shutdown(self, *args, **kwargs) -> None:
        self.shutdown_called = True


@pytest.mark.asyncio
async def test_worker_maps_executor_errors_and_timeout(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.modules.documents.infrastructure.worker.ProcessPoolExecutor",
        _InlineExecutor,
    )
    assert await ProcessDocumentWorker().run(_double, 5) == 10
    with pytest.raises(DocumentWorkerError):
        await ProcessDocumentWorker().run(_raise_value_error)

    async def timeout(awaitable, *, timeout):
        awaitable.cancel()
        raise TimeoutError

    monkeypatch.setattr(
        "app.modules.documents.infrastructure.worker.asyncio.wait_for",
        timeout,
    )
    with pytest.raises(DocumentWorkerTimeoutError):
        await ProcessDocumentWorker().run(_double, 5)


@pytest.mark.integration
@pytest.mark.skipif(
    shutil.which("tesseract") is None,
    reason="Tesseract jest dostępny w obrazie backendu, nie na każdym hoście.",
)
def test_real_tesseract_cli_is_invoked_and_returns_nonempty_text() -> None:
    """Syntetyczny smoke test silnika; realny skan jest osobnym fixture'em."""
    image = Image.new("RGB", (1200, 300), "white")
    ImageDraw.Draw(image).text((80, 100), "UCHWALA NR 8", fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PDF", resolution=150)

    result = _ocr_pdf_sync(_OcrPayload(buffer.getvalue(), "pol", 200))

    assert result.engine_version.startswith("Tesseract ")
    assert result.pages
    assert result.pages[0].text.strip()
