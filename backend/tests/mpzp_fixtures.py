"""Wspólne fixtures testów BK-202/BK-203: dokumenty uchwał i import wektora MPZP.

Dokumenty są generowane lokalnie (PyMuPDF), bez internetu: tekstowy PDF
przechodzi realną ekstrakcję pdfplumber, a skan (sam obraz strony) wymaga OCR.
"""

from __future__ import annotations

import os
import random
from datetime import date, datetime, timezone
from pathlib import Path

import fitz
from sqlalchemy.orm import Session

from app.modules.imports.application.common import ImportRelease
from app.modules.imports.application.mpzp_import import run_mpzp_import
from app.modules.imports.domain.mpzp import PlanningActRecord, ZoneRecord
from app.modules.imports.infrastructure.artifacts import LocalArtifactStore
from app.modules.imports.infrastructure.repository import SqlAlchemyImportRepository
from app.schemas.source import SourceMetadata
from app.services.mpzp_fetch import DocumentBlob
from app.services.mpzp_parser_extract import OcrExtractionResult, OcrPageResult
from app.shared.geometry import GeometryPayload

_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
)

RESOLUTION_PAGES = (
    "UCHWAŁA NR XII/100/2026 RADY GMINY TESTOWEJ\n"
    "w sprawie miejscowego planu zagospodarowania przestrzennego.\n"
    "§ 1. Plan obejmuje obszar oznaczony na rysunku planu.",
    "§ 5. Dla terenu oznaczonego symbolem 1MN ustala się:\n"
    "1) maksymalna wysokość zabudowy: 9 m;\n"
    "2) minimalny udział powierzchni biologicznie czynnej: 40%.\n"
    "§ 6. Dla terenu oznaczonego symbolem 2MN ustala się:\n"
    "1) maksymalna wysokość zabudowy: 12 m;\n"
    "2) minimalny udział powierzchni biologicznie czynnej: 30%.",
)


def _font() -> str:
    return next(path for path in _FONT_CANDIDATES if os.path.exists(path))


def text_pdf(pages: tuple[str, ...] = RESOLUTION_PAGES) -> bytes:
    document = fitz.open()
    for text in pages:
        page = document.new_page()
        page.insert_font(fontname="dv", fontfile=_font())
        page.insert_textbox(fitz.Rect(50, 50, 550, 800), text, fontname="dv", fontsize=11)
    return document.tobytes()


def scanned_pdf(pages: tuple[str, ...] = RESOLUTION_PAGES) -> bytes:
    """Ten sam dokument jako skan: strony są wyłącznie obrazami."""
    source = fitz.open("pdf", text_pdf(pages))
    scan = fitz.open()
    for page in source:
        pixmap = page.get_pixmap(dpi=72)
        target = scan.new_page(width=page.rect.width, height=page.rect.height)
        target.insert_image(target.rect, pixmap=pixmap)
    return scan.tobytes()


class TranscribingOcr:
    """Fałszywy OCR zwracający dokładnie tekst stron (równoważny dokument)."""

    def __init__(self, pages: tuple[str, ...] = RESOLUTION_PAGES) -> None:
        self.pages = pages
        self.calls = 0

    async def extract_text_from_scan(self, pdf_bytes: bytes) -> OcrExtractionResult:
        self.calls += 1
        return OcrExtractionResult(
            pages=[
                OcrPageResult(page_number=index, text=text, quality=0.97)
                for index, text in enumerate(self.pages, start=1)
            ],
            engine_version="tesseract-fixture 5.3",
            quality_score=0.97,
        )


def document_blob(content: bytes, url: str = "https://bip.example.gov.pl/uchwala.pdf") -> DocumentBlob:
    return DocumentBlob(
        content=content,
        media_type="application/pdf",
        filename="uchwala.pdf",
        source_metadata=SourceMetadata(
            source_name="MPZP_BIP",
            source_url=url,
            fetched_at=datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc),
            response_status=200,
            confidence=0.9,
            manual_review_required=False,
        ),
    )


def origin() -> tuple[float, float]:
    """Losowy, odległy obszar EPSG:2180, aby testy nie widziały cudzych danych."""
    return 300000.0 + random.randint(0, 3000) * 100.0, 600000.0 + random.randint(0, 3000) * 100.0


def rect(x0: float, y0: float, x1: float, y1: float) -> str:
    return f"POLYGON(({x0} {y0},{x1} {y0},{x1} {y1},{x0} {y1},{x0} {y0}))"


def zone(symbol: str, wkt: str, category: str | None = None, **attributes: object) -> ZoneRecord:
    return ZoneRecord(symbol, category, GeometryPayload(wkt), attributes)


def act(
    identifier: str,
    boundary: str,
    zones: tuple[ZoneRecord, ...],
    *,
    document_url: str | None = None,
) -> PlanningActRecord:
    return PlanningActRecord(
        act_identifier=identifier,
        resolution_number="XII/100/2026",
        resolution_date=date(2026, 3, 1),
        teryt="1261011",
        name="Plan testowy",
        boundary=GeometryPayload(boundary),
        zones=zones,
        document_url=document_url,
    )


class StaticReader:
    def __init__(self, *acts: PlanningActRecord, content: bytes) -> None:
        self.acts = acts
        self.content = content

    def read(self):
        from app.modules.imports.application.mpzp_import import MpzpSourceBatch

        return MpzpSourceBatch(self.content, "mpzp.gml", "application/gml+xml", tuple(self.acts))


def import_acts(
    session: Session,
    tmp_path: Path,
    source_id: str,
    *acts: PlanningActRecord,
    label: str = "v1",
):
    from tests.test_imports_mpzp import _source

    repository = SqlAlchemyImportRepository(session, _source(source_id), LocalArtifactStore(tmp_path))
    return run_mpzp_import(
        StaticReader(*acts, content=f"{source_id}:{label}:{random.random()}".encode()),
        source_id,
        repository,
        release=ImportRelease(
            source_id,
            label,
            datetime.now(timezone.utc),
            publication_allowed=True,
            dry_run=False,
            teryt_scope=("1261011",),
        ),
    )
