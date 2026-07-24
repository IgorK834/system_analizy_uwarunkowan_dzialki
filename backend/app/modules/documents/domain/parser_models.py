"""Czyste wartości przekazywane między etapami parsera dokumentów."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class DocumentInput:
    content: bytes
    media_type: str
    filename: str | None = None


@dataclass(frozen=True)
class ExtractedDocument:
    pages: tuple[str, ...]
    tables: tuple[tuple[int, tuple[tuple[str | None, ...], ...]], ...] = ()
    page_qualities: tuple[float | None, ...] = ()
    blocks: tuple[tuple[dict[str, Any], ...], ...] = ()
    quality_score: float = 0.0
    needs_ocr: bool = False
    ocr_used: bool = False
    extraction_method: Literal[
        "pdf_text", "html", "ocr", "unsupported"
    ] = "unsupported"
    ocr_engine_version: str | None = None
    manual_review_required: bool = False
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class DocumentTextSegment:
    segment_id: str
    text: str
    page_number: int | None
    heading: str | None
    source: Literal["paragraph", "table"]
