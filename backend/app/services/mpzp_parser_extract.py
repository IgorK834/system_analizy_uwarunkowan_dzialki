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
    quality_score: float = 0.0
    needs_ocr: bool = False
    manual_review_required: bool = False
    warnings: list[str] = field(default_factory=list)


@runtime_checkable
class OcrProvider(Protocol):
    """Opcjonalny adapter przyszłej integracji OCR; MVP go nie implementuje."""

    async def extract_text_from_scan(self, pdf_bytes: bytes) -> str: ...


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
    ``ocr_provider`` jest opcjonalnym kontraktem przyszłościowym: w MVP, gdy
    ``needs_ocr=True``, brak providera daje ostrzeżenie, a przekazany provider
    jest odnotowany, ale nie jest jeszcze wywoływany.
    """
    kind = classify_document(document)
    if kind == "unsupported":
        return TextExtractionResult(
            pages=[],
            tables=[],
            quality_score=0.0,
            needs_ocr=False,
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
        if ocr_provider is not None:
            logger.info(
                "OcrProvider przekazany, ale realna integracja OCR nie jest "
                "zaimplementowana w MVP."
            )

    return TextExtractionResult(
        pages=pages,
        tables=tables,
        quality_score=quality_score,
        needs_ocr=needs_ocr,
        manual_review_required=needs_ocr,
        warnings=warnings,
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
        classes = element.get("class") or []
        identifiers = " ".join([*classes, element.get("id") or ""]).lower()
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
        quality_score=quality_score,
        needs_ocr=False,
        manual_review_required=not bool(cleaned_text),
        warnings=warnings,
    )
