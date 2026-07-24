"""Deterministyczne mapowanie segmentów na hierarchię jednostek prawnych."""

from __future__ import annotations

import re

from app.modules.documents.domain.models import LegalUnitNode, LegalUnitType
from app.modules.documents.domain.parser_models import DocumentTextSegment

_CHAPTER_NUMBER = re.compile(r"Rozdział\s+(\d+[A-Za-z]?)", re.IGNORECASE)
_PARAGRAPH_NUMBER = re.compile(r"§\s*(\d+[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]*)")
_SECTION_MARKER = re.compile(
    r"(?m)(?:^|\n)\s*"
    r"(?:§\s*\d+[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]*\.\s*)?"
    r"(\d+[a-z]?)\.\s+"
)
_POINT_MARKER = re.compile(
    r"(?m)(?:^|\n|:\s+)\s*(\d+[a-z]?)\)\s+"
)


def build_legal_units(
    segments: tuple[DocumentTextSegment, ...],
) -> tuple[LegalUnitNode, ...]:
    """Buduje chapter→paragraph→section→point, zachowując strony i kolejność."""
    units: list[LegalUnitNode] = []
    current_chapter_key: str | None = None
    current_heading: str | None = None
    order_index = 0

    def append(
        *,
        unit_type: LegalUnitType,
        source_text: str,
        number: str | None,
        page_number: int | None,
        parent_key: str | None,
        key_suffix: str,
    ) -> str:
        nonlocal order_index
        node_key = f"unit-{order_index:06d}-{key_suffix}"
        units.append(
            LegalUnitNode(
                unit_type=unit_type,
                number=number,
                order_index=order_index,
                page_from=page_number,
                page_to=page_number,
                source_text=source_text,
                normalized_text=_normalize_legal_text(source_text),
                node_key=node_key,
                parent_key=parent_key,
            )
        )
        order_index += 1
        return node_key

    for segment in segments:
        if segment.heading and segment.heading != current_heading:
            chapter_match = _CHAPTER_NUMBER.search(segment.heading)
            current_chapter_key = append(
                unit_type="chapter",
                source_text=segment.heading,
                number=(
                    chapter_match.group(1) if chapter_match is not None else None
                ),
                page_number=segment.page_number,
                parent_key=None,
                key_suffix="chapter",
            )
            current_heading = segment.heading

        if segment.source == "table":
            append(
                unit_type="table_row",
                source_text=segment.text,
                number=None,
                page_number=segment.page_number,
                parent_key=current_chapter_key,
                key_suffix=segment.segment_id,
            )
            continue

        paragraph_match = _PARAGRAPH_NUMBER.search(segment.text)
        paragraph_key = append(
            unit_type="paragraph" if paragraph_match else "document_fragment",
            source_text=segment.text,
            number=(
                paragraph_match.group(1) if paragraph_match is not None else None
            ),
            page_number=segment.page_number,
            parent_key=current_chapter_key,
            key_suffix=segment.segment_id,
        )
        if paragraph_match is None:
            continue
        _append_sections_and_points(
            segment,
            paragraph_key,
            append,
        )

    return tuple(units)


def _append_sections_and_points(
    segment: DocumentTextSegment,
    paragraph_key: str,
    append,
) -> None:
    section_matches = list(_SECTION_MARKER.finditer(segment.text))
    for index, section_match in enumerate(section_matches):
        end = (
            section_matches[index + 1].start(1)
            if index + 1 < len(section_matches)
            else len(segment.text)
        )
        section_text = segment.text[section_match.start(1) : end].strip()
        section_key = append(
            unit_type="section",
            source_text=section_text,
            number=section_match.group(1),
            page_number=segment.page_number,
            parent_key=paragraph_key,
            key_suffix=f"{segment.segment_id}-section-{index}",
        )
        point_matches = list(_POINT_MARKER.finditer(section_text))
        for point_index, point_match in enumerate(point_matches):
            point_end = (
                point_matches[point_index + 1].start(1)
                if point_index + 1 < len(point_matches)
                else len(section_text)
            )
            append(
                unit_type="point",
                source_text=section_text[
                    point_match.start(1) : point_end
                ].strip(),
                number=point_match.group(1),
                page_number=segment.page_number,
                parent_key=section_key,
                key_suffix=(
                    f"{segment.segment_id}-section-{index}-point-{point_index}"
                ),
            )


def _normalize_legal_text(text: str) -> str:
    return " ".join(text.replace("\u00a0", " ").split())
