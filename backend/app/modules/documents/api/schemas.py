"""Schematy transportowe dokumentów prawnych."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class DocumentVersionResponse(BaseModel):
    id: int
    source_document_id: int
    source_artifact_id: int
    content_hash: str
    media_type: str | None
    extraction_method: str | None
    ocr_engine_version: str | None
    quality_score: float | None
    original_uri: str | None
    valid_from: datetime | None
    valid_to: datetime | None
    published_at: datetime | None
    review_status: str
    legal_units_url: str


class LegalUnitResponse(BaseModel):
    id: int
    document_version_id: int
    unit_type: str
    number: str | None
    parent_id: int | None
    order_index: int
    page_from: int | None
    page_to: int | None
    source_text: str
    normalized_text: str | None
    children: list["LegalUnitResponse"] = Field(default_factory=list)
