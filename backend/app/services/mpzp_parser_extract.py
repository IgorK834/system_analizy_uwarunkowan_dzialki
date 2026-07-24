"""Klasyfikacja dokumentu MPZP i ekstrakcja tekstu z PDF albo HTML.

``classify_document`` rozpoznaje typ przed wyborem ekstraktora. Dla wartości
``unsupported`` funkcja ``extract_document_text`` kończy się wcześniej i nigdy
nie wywołuje pdfplumber; biblioteka jest używana wyłącznie dla dokumentów
sklasyfikowanych jako PDF.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from typing import Final, Literal, Protocol, runtime_checkable

import pdfplumber
from bs4 import BeautifulSoup

from app.services.mpzp_fetch import DocumentBlob

logger = logging.getLogger(__name__)

# Realna strona tekstowej uchwały zwykle znacznie przekracza 200 znaków.
# Próg jest heurystyką do korekty po zebraniu reprezentatywnych dokumentów BIP.
MIN_CHARS_PER_PAGE_FOR_TEXT_LAYER: Final[int] = 200
OCR_QUALITY_THRESHOLD: Final[float] = 0.5
_BIP_NAVIGATION_KEYWORDS: Final[tuple[str, ...]] = (
    "menu",
    "nav",
    "navigation",
    "header",
    "footer",
    "breadcrumb",
    "sidebar",
    "stopka",
    "okruszki",
)

DocumentKind = Literal["pdf", "html", "unsupported"]


@dataclass(frozen=True)
class ExtractedTable:
    page_number: int
    rows: list[list[str | None]]


@dataclass(frozen=True)
class TextExtractionResult:
    """Wynik ekstrakcji tekstu wraz z oceną potrzeby ręcznej weryfikacji.

    ``manual_review_required`` jest w MVP sprzężone z ``needs_ocr`` dla PDF,
    a dla HTML sygnalizuje brak treści po oczyszczeniu strony.
    """

    pages: list[str]
    tables: list[ExtractedTable] = field(default_factory=list)
    page_qualities: list[float | None] = field(default_factory=list)
    blocks: list[list[dict[str, object]]] = field(default_factory=list)
    quality_score: float = 0.0
    needs_ocr: bool = False
    ocr_used: bool = False
    extraction_method: Literal["pdf_text", "html", "ocr", "unsupported"] = (
        "pdf_text"
    )
    ocr_engine_version: str | None = None
    manual_review_required: bool = False
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class OcrPageResult:
    """Wynik OCR jednej strony wraz z confidence i pozycjami bloków."""

    page_number: int
    text: str
    quality: float | None = None
    blocks: list[dict[str, object]] = field(default_factory=list)


@dataclass(frozen=True)
class OcrExtractionResult:
    """Neutralny kontrakt adaptera OCR, niezależny od Tesseracta."""

    pages: list[OcrPageResult]
    engine_version: str
    quality_score: float


@runtime_checkable
class OcrProvider(Protocol):
    """Adapter OCR wywoływany dla PDF bez wystarczającej warstwy tekstowej."""

    async def extract_text_from_scan(
        self, pdf_bytes: bytes
    ) -> OcrExtractionResult | str: ...


def classify_document(document: DocumentBlob) -> DocumentKind:
    """Rozpoznaje typ po zweryfikowanym wcześniej ``DocumentBlob.media_type``.

    Sygnatury bajtowe weryfikuje ``mpzp_fetch.py``; ten etap celowo nie zgaduje
    typu na podstawie zawartości.
    """
    if document.media_type == "application/pdf":
        return "pdf"
    if document.media_type == "text/html":
        return "html"
    return "unsupported"


async def extract_document_text(
    document: DocumentBlob,
    ocr_provider: OcrProvider | None = None,
) -> TextExtractionResult:
    """Ekstrahuje tekst odpowiednią strategią dla sklasyfikowanego dokumentu.

    Typ ``unsupported`` kończy się przed jakimkolwiek użyciem pdfplumber. Dla
    PDF ``quality_score`` jest udziałem stron z wystarczającą warstwą tekstową.
    Gdy PDF wymaga OCR i przekazano provider, wynik OCR zastępuje puste strony,
    a wersja silnika i współrzędne bloków pozostają w śladzie audytowym.
    """
    kind = classify_document(document)
    if kind == "unsupported":
        return TextExtractionResult(
            pages=[],
            tables=[],
            quality_score=0.0,
            needs_ocr=False,
            extraction_method="unsupported",
            manual_review_required=True,
            warnings=[
                f"Nieobsługiwany typ dokumentu: {document.media_type!r} — "
                "pominięto ekstrakcję tekstu."
            ],
        )
    if kind == "html":
        return _extract_html_text(document)
    return await _extract_pdf_text(document, ocr_provider)


async def _extract_pdf_text(
    document: DocumentBlob,
    ocr_provider: OcrProvider | None,
) -> TextExtractionResult:
    pages: list[str] = []
    tables: list[ExtractedTable] = []
    pages_with_text_layer = 0

    with pdfplumber.open(io.BytesIO(document.content)) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            pages.append(text)
            if len(text.strip()) >= MIN_CHARS_PER_PAGE_FOR_TEXT_LAYER:
                pages_with_text_layer += 1

            for raw_table in page.extract_tables() or []:
                tables.append(
                    ExtractedTable(page_number=page_number, rows=raw_table)
                )

    total_pages = len(pages)
    quality_score = (
        pages_with_text_layer / total_pages if total_pages > 0 else 0.0
    )
    needs_ocr = quality_score < OCR_QUALITY_THRESHOLD
    warnings: list[str] = []
    if needs_ocr:
        warnings.append(
            "Dokument PDF ma bardzo mało tekstu na stronę — prawdopodobnie "
            "skan wymagający OCR. Wynik ekstrakcji może być niepełny."
        )
    extraction = TextExtractionResult(
        pages=pages,
        tables=tables,
        page_qualities=[
            (
                1.0
                if len(page.strip()) >= MIN_CHARS_PER_PAGE_FOR_TEXT_LAYER
                else 0.0
            )
            for page in pages
        ],
        blocks=[[] for _ in pages],
        quality_score=quality_score,
        needs_ocr=needs_ocr,
        extraction_method="pdf_text",
        manual_review_required=needs_ocr,
        warnings=warnings,
    )
    if needs_ocr and ocr_provider is not None:
        return await apply_ocr_to_extraction(document, extraction, ocr_provider)
    return extraction


async def apply_ocr_to_extraction(
    document: DocumentBlob,
    extraction: TextExtractionResult,
    ocr_provider: OcrProvider,
) -> TextExtractionResult:
    """Uruchamia realny provider OCR i zachowuje kontrolowany wynik awarii."""
    try:
        raw_result = await ocr_provider.extract_text_from_scan(document.content)
    except Exception as exc:
        logger.exception("Adapter OCR nie zdołał przetworzyć dokumentu MPZP.")
        return TextExtractionResult(
            pages=extraction.pages,
            tables=extraction.tables,
            page_qualities=extraction.page_qualities,
            blocks=extraction.blocks,
            quality_score=extraction.quality_score,
            needs_ocr=True,
            ocr_used=False,
            extraction_method=extraction.extraction_method,
            ocr_engine_version=None,
            manual_review_required=True,
            warnings=[
                *extraction.warnings,
                "OCR nie powiódł się; dokument wymaga ręcznej weryfikacji "
                f"({type(exc).__name__}).",
            ],
        )

    if isinstance(raw_result, str):
        texts = raw_result.split("\f")
        pages = [
            OcrPageResult(page_number=index, text=text)
            for index, text in enumerate(texts, start=1)
            if text or len(texts) == 1
        ]
        engine_version = type(ocr_provider).__name__
        quality_score = (
            sum(bool(page.text.strip()) for page in pages) / len(pages)
            if pages
            else 0.0
        )
    else:
        pages = raw_result.pages
        engine_version = raw_result.engine_version
        quality_score = raw_result.quality_score

    extracted_pages = [page.text for page in pages]
    if not any(page.strip() for page in extracted_pages):
        return TextExtractionResult(
            pages=extracted_pages,
            tables=extraction.tables,
            page_qualities=[page.quality for page in pages],
            blocks=[page.blocks for page in pages],
            quality_score=quality_score,
            needs_ocr=True,
            ocr_used=True,
            extraction_method="ocr",
            ocr_engine_version=engine_version,
            manual_review_required=True,
            warnings=[
                *extraction.warnings,
                "OCR zakończył się bez czytelnego tekstu; dokument wymaga "
                "ręcznej weryfikacji.",
            ],
        )

    return TextExtractionResult(
        pages=extracted_pages,
        tables=extraction.tables,
        page_qualities=[page.quality for page in pages],
        blocks=[page.blocks for page in pages],
        quality_score=quality_score,
        needs_ocr=False,
        ocr_used=True,
        extraction_method="ocr",
        ocr_engine_version=engine_version,
        # OCR nie jest automatycznie promowany do zweryfikowanego źródła.
        manual_review_required=True,
        warnings=[
            *extraction.warnings,
            "Tekst odczytano przez OCR; wynik zachowano do ręcznej weryfikacji.",
        ],
    )


def _extract_html_text(document: DocumentBlob) -> TextExtractionResult:
    soup = BeautifulSoup(document.content, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()

    # Szablony BIP nie mają wspólnego standardu, dlatego heurystyka usuwa
    # elementy według szerokiego zestawu nazw class/id typowych dla nawigacji.
    for element in soup.find_all(True):
        if element.parent is None:
            continue
        raw_classes = element.get("class")
        classes: list[str] = (
            [str(value) for value in raw_classes]
            if isinstance(raw_classes, list)
            else []
        )
        identifier = str(element.get("id") or "")
        identifiers = " ".join([*classes, identifier]).lower()
        if any(keyword in identifiers for keyword in _BIP_NAVIGATION_KEYWORDS):
            element.decompose()

    cleaned_text = soup.get_text(separator="\n", strip=True)
    quality_score = 1.0 if cleaned_text else 0.0
    warnings = (
        []
        if cleaned_text
        else [
            "Nie znaleziono treści tekstowej po oczyszczeniu strony HTML z "
            "elementów nawigacyjnych."
        ]
    )
    return TextExtractionResult(
        pages=[cleaned_text],
        tables=[],
        page_qualities=[quality_score],
        blocks=[[]],
        quality_score=quality_score,
        needs_ocr=False,
        extraction_method="html",
        manual_review_required=not bool(cleaned_text),
        warnings=warnings,
    )
