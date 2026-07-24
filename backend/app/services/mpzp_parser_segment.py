"""Segmentacja dokumentu MPZP i lokalizacja sekcji stref planistycznych.

Moduł implementuje stub ``segment_document`` z ``mpzp_parser.py`` i stanowi
etap 3 pipeline'u: ``classify_document → extract_text → segment_document →
extract_parameters → validate_result``. Reguły zostały zweryfikowane na realnej
uchwale MPZP z Bielska-Białej zapisanej w ``tests/fixtures/mpzp_documents/``.

Wzorce regex mają jawne poziomy confidence i są sprawdzane od najbardziej
wiarygodnego. Kandydaci są następnie deterministycznie rankingowani, nigdy
wybierani losowo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Final, Literal

from app.services.mpzp_parser_extract import TextExtractionResult

_MULTI_CANDIDATE_CONFIDENCE_PENALTY: Final[float] = 0.7

# Pierwszy wzorzec pochodzi z realnej uchwały Bielska-Białej. Pozostałe
# zabezpieczają warianty językowe spotykane w polskich dokumentach planistycznych.
_ZONE_REFERENCE_PATTERNS: Final[tuple[tuple[str, float], ...]] = (
    (r"oznaczon\w*\s+symbolem\s+{symbol}\b", 0.9),
    (r"\bteren\w*\s+{symbol}\b", 0.75),
    (r"\bsymbol\w*\s+{symbol}\b", 0.7),
    (r"\bjednostka\s+planistyczna\s+{symbol}\b", 0.65),
    (r"\bobszar\s+oznaczon\w*\s+{symbol}\b", 0.6),
    (r"\b{symbol}\b", 0.5),
)

_CHAPTER_HEADING_PATTERN: Final[re.Pattern[str]] = re.compile(r"Rozdział\s+\d+\.[^\n]*")
_PARAGRAPH_MARKER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"§\s*\d+[a-zA-Ząśźżółęćń]*\."
)
_SOURCE_EXCERPT_MAX_CHARS: Final[int] = 200
_SOURCE_EXCERPT_LEFT_CONTEXT_CHARS: Final[int] = 80
_DISCOVER_ZONE_SYMBOL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:oznaczon\w*\s+symbolem|teren\w*\s+oznaczon\w*)\s+"
    r"(?P<symbol>[0-9A-ZĄĆĘŁŃÓŚŹŻ][0-9A-ZĄĆĘŁŃÓŚŹŻ._/-]{0,30})\b",
    re.IGNORECASE,
)
_SYMBOL_STOP_WORDS: Final[frozenset[str]] = frozenset(
    {"PLANU", "TERENU", "OBSZARU", "NR", "N", "M"}
)


@dataclass(frozen=True)
class DocumentSegment:
    """Fragment dokumentu ze stroną i kontekstem, jeszcze bez przypisania strefy."""

    segment_id: str
    text: str
    page_number: int | None
    heading: str | None
    source: Literal["paragraph", "table"]


@dataclass(frozen=True)
class ZoneSectionCandidate:
    """Jeden dopasowany segment wraz ze śladem wzorca i poziomem pewności."""

    zone_symbol: str
    segment_id: str
    source_text: str
    page_number: int | None
    confidence: float
    match_pattern: str


@dataclass(frozen=True)
class ZoneSectionResult:
    """Ranking lokalizacji sekcji jednej żądanej strefy planistycznej.

    Brak kandydata oraz wiele kandydatów wymagają ręcznej weryfikacji. Lista
    ``candidates`` jest zawsze deterministycznym rankingiem malejącym po
    confidence.
    """

    zone_symbol: str
    candidates: list[ZoneSectionCandidate] = field(default_factory=list)
    manual_review_required: bool = False
    warnings: list[str] = field(default_factory=list)


def segment_document(text_result: TextExtractionResult) -> list[DocumentSegment]:
    """Dzieli strony na paragrafy i wiersze tabel, zachowując nagłówki i strony.

    Markery ``§ N.`` rozpoczynają segmenty paragrafowe. Tekst przed pierwszym
    markerem na stronie również jest zachowywany. Nagłówek ``Rozdział N.``
    obowiązuje kolejne segmenty, także na następnych stronach. Każdy wiersz
    każdej ``ExtractedTable`` staje się osobnym segmentem tabelarycznym.
    """
    segments: list[DocumentSegment] = []
    current_heading: str | None = None
    running_index = 0

    def append_segment(
        text: str,
        page_number: int | None,
        heading: str | None,
        source: Literal["paragraph", "table"],
    ) -> None:
        nonlocal running_index
        stripped = text.strip()
        if not stripped and source == "paragraph":
            return
        segments.append(
            DocumentSegment(
                segment_id=f"seg-{running_index:04d}",
                text=stripped,
                page_number=page_number,
                heading=heading,
                source=source,
            )
        )
        running_index += 1

    for page_number, page_text in enumerate(text_result.pages, start=1):
        chapter_matches = list(_CHAPTER_HEADING_PATTERN.finditer(page_text))
        paragraph_matches = list(_PARAGRAPH_MARKER_PATTERN.finditer(page_text))

        if not paragraph_matches:
            page_heading = (
                chapter_matches[-1].group(0).strip()
                if chapter_matches
                else current_heading
            )
            append_segment(page_text, page_number, page_heading, "paragraph")
        else:
            first_paragraph_start = paragraph_matches[0].start()
            if first_paragraph_start > 0:
                preamble_heading = _heading_at_offset(
                    chapter_matches,
                    first_paragraph_start,
                    current_heading,
                )
                append_segment(
                    page_text[:first_paragraph_start],
                    page_number,
                    preamble_heading,
                    "paragraph",
                )

            for index, paragraph_match in enumerate(paragraph_matches):
                end = (
                    paragraph_matches[index + 1].start()
                    if index + 1 < len(paragraph_matches)
                    else len(page_text)
                )
                heading = _heading_at_offset(
                    chapter_matches,
                    paragraph_match.start(),
                    current_heading,
                )
                append_segment(
                    page_text[paragraph_match.start() : end],
                    page_number,
                    heading,
                    "paragraph",
                )

        if chapter_matches:
            current_heading = chapter_matches[-1].group(0).strip()

    for table in text_result.tables:
        for row in table.rows:
            row_text = " ".join(cell for cell in row if cell)
            append_segment(row_text, table.page_number, None, "table")

    return segments


def _heading_at_offset(
    matches: list[re.Match[str]],
    offset: int,
    fallback: str | None,
) -> str | None:
    heading = fallback
    for match in matches:
        if match.start() >= offset:
            break
        heading = match.group(0).strip()
    return heading


def find_zone_sections(
    segments: list[DocumentSegment],
    zone_symbols: list[str],
) -> list[ZoneSectionResult]:
    """Lokalizuje i rankinguje wszystkie sekcje dla każdego symbolu strefy.

    Na segment przypada najwyżej jeden kandydat: pierwszy dopasowany wzorzec,
    czyli wzorzec o najwyższym confidence. Wieloznaczność obniża confidence
    całego rankingu o 30%, a brak wyniku nigdy nie jest cichym sukcesem.
    """
    results: list[ZoneSectionResult] = []
    for zone_symbol in zone_symbols:
        candidates = _find_candidates_for_symbol(segments, zone_symbol)
        warnings: list[str] = []
        manual_review_required = False

        if not candidates:
            manual_review_required = True
            warnings.append(
                "ZONE_SECTION_NOT_FOUND: nie znaleziono sekcji dokumentu dla "
                f"strefy {zone_symbol}."
            )
        elif len(candidates) > 1:
            # Kara dotyczy całego rankingu, bo sama wieloznaczność osłabia każdy
            # wybór i jednocześnie nie może zepsuć kolejności kandydatów.
            candidates = [
                replace(
                    candidate,
                    confidence=(
                        candidate.confidence * _MULTI_CANDIDATE_CONFIDENCE_PENALTY
                    ),
                )
                for candidate in candidates
            ]
            manual_review_required = True
            warnings.append(
                "ZONE_SECTION_AMBIGUOUS: znaleziono wiele kandydackich sekcji "
                f"dla strefy {zone_symbol} ({len(candidates)})."
            )

        results.append(
            ZoneSectionResult(
                zone_symbol=zone_symbol,
                candidates=candidates,
                manual_review_required=manual_review_required,
                warnings=warnings,
            )
        )
    return results


def discover_zone_symbols(segments: list[DocumentSegment]) -> list[str]:
    """Ostrożnie odkrywa symbole wyłącznie z jawnych fraz dokumentu.

    Funkcja nie zgaduje symbolu na podstawie dowolnego ciągu wielkich liter.
    Wynik pozostaje kandydatem do ręcznej weryfikacji w fasadzie parsera.
    """
    symbols: list[str] = []
    seen: set[str] = set()
    for segment in segments:
        for match in _DISCOVER_ZONE_SYMBOL_PATTERN.finditer(segment.text):
            symbol = match.group("symbol").strip(".,;:")
            normalized = symbol.upper()
            if normalized in _SYMBOL_STOP_WORDS or normalized in seen:
                continue
            if not any(character.isalpha() for character in symbol):
                continue
            seen.add(normalized)
            symbols.append(symbol)
    return symbols


def _find_candidates_for_symbol(
    segments: list[DocumentSegment],
    zone_symbol: str,
) -> list[ZoneSectionCandidate]:
    if not zone_symbol:
        return []

    escaped_symbol = re.escape(zone_symbol)
    candidates: list[ZoneSectionCandidate] = []
    for segment in segments:
        for pattern_template, confidence in _ZONE_REFERENCE_PATTERNS:
            pattern = pattern_template.format(symbol=escaped_symbol)
            match = re.search(pattern, segment.text, flags=re.IGNORECASE)
            if match is None:
                continue
            candidates.append(
                ZoneSectionCandidate(
                    zone_symbol=zone_symbol,
                    segment_id=segment.segment_id,
                    source_text=_source_excerpt(segment.text, match),
                    page_number=segment.page_number,
                    confidence=confidence,
                    match_pattern=pattern_template,
                )
            )
            break

    candidates.sort(
        key=lambda candidate: (
            -candidate.confidence,
            candidate.page_number if candidate.page_number is not None else 10**9,
            candidate.segment_id,
        )
    )
    return candidates


def _source_excerpt(text: str, match: re.Match[str]) -> str:
    start = max(0, match.start() - _SOURCE_EXCERPT_LEFT_CONTEXT_CHARS)
    end = min(len(text), start + _SOURCE_EXCERPT_MAX_CHARS)
    if end == len(text):
        start = max(0, end - _SOURCE_EXCERPT_MAX_CHARS)
    return " ".join(text[start:end].split())
