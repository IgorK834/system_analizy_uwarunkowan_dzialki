"""Ekstraktory parametrów liczbowych z segmentów dokumentu MPZP.

Ekstraktory w tym module operują na PEŁNYM tekście segmentu
(``DocumentSegment.text``), a nie na krótkim wycinku
``ZoneSectionCandidate.source_text``. ``source_text`` z ``mpzp_parser_segment.py``
to jedynie ~200-znakowy kontekst dopasowania symbolu strefy, zbyt krótki, aby
zawrzeć parametry z dalszej części paragrafu (np. wysokość zabudowy podaną
kilka wierszy po zdaniu wprowadzającym symbol terenu).

Każda znaleziona odmienna wartość liczbowa dla tego samego parametru w tej
samej strefie jest zwracana jako OSOBNY ``MpzpParameter`` — moduł nigdy nie
wybiera cicho jednej wartości z wielu. Gdy ekstraktor znajdzie więcej niż
jedną dystynktywną wartość dla danej nazwy parametru, wszystkie wpisy tej
nazwy są oznaczane ``manual_review_required=True`` i otrzymują karę
confidence, analogicznie do wzorca kary za wieloznaczność zastosowanego w
``mpzp_parser_segment.py`` dla wielu kandydatów sekcji strefy.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Final

from app.schemas.mpzp import MpzpParameter
from app.services.mpzp_parser_segment import DocumentSegment, ZoneSectionResult
from app.shared.numbers import parse_polish_number

# Tymczasowy alias kompatybilności dla istniejących testów/wywołań prywatnej
# funkcji; jedyna implementacja pozostaje w app.shared.numbers.
_parse_polish_number = parse_polish_number

logger = logging.getLogger(__name__)

# Kara analogiczna do _MULTI_CANDIDATE_CONFIDENCE_PENALTY w mpzp_parser_segment.py:
# sprzeczne wartości tego samego parametru w tej samej strefie nie mogą zachować
# wysokiego confidence, bo żadna z nich nie została jednoznacznie wybrana.
_CONFLICTING_VALUE_CONFIDENCE_PENALTY: Final[float] = 0.6

_BASE_CONFIDENCE: Final[float] = 0.85
_WINDOW_AFTER_ANCHOR_CHARS: Final[int] = 400
_INTENSITY_WINDOW_CHARS: Final[int] = 150
_HEIGHT_SECTION_BOUNDARY_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\.\s+(?=(?:Wskaźnik|Maksymalna|Minimalna|Udział|Powierzchnia|"
    r"Dachy|Ustala|Linię|Zapewnia|Obowiązuje|Nakaz|Zakaz)\b)"
    r"|^\s*[a-z]\)\s+",
    re.MULTILINE | re.IGNORECASE,
)

_HEIGHT_ANCHOR_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"maksymaln\w*\s+wysoko\w*\s+zabudowy|wysoko\w*\s+(?:zabudowy\s+)?do\s+",
    re.IGNORECASE,
)
# Wyklucza 'm2'/'m²'/'m kw' żeby nie pomylić metrów wysokości z metrami kwadratowymi.
_METERS_VALUE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"([\d]+(?:[,.\s]\d+)*)\s*m\b(?!\s*(?:2|²|kw))", re.IGNORECASE
)

_INTENSITY_ANCHOR_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"intensywno\w*\s+zabudowy", re.IGNORECASE
)
_INTENSITY_MIN_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"minimalna\s*[–-]\s*([\d]+(?:[,.]\d+)?)", re.IGNORECASE
)
_INTENSITY_MAX_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"maksymalna\s*[–-]\s*([\d]+(?:[,.]\d+)?)", re.IGNORECASE
)

_COVERAGE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"maksymaln\w*\s+powierzchni\w*\s+zabudowy\D{0,40}?([\d]+(?:[,.]\d+)?)\s*%",
    re.IGNORECASE,
)

_BIO_ACTIVE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"powierzchni\w*\s+biologicznie\s+czynn\w*\D{0,40}?([\d]+(?:[,.]\d+)?)\s*%",
    re.IGNORECASE,
)

_ROOF_ANGLE_CONTEXT_PATTERN: Final[re.Pattern[str]] = re.compile(
    # Polska deklinacja przesuwa 'kąt' -> 'kącie' (t/ci), dlatego wzorzec
    # obejmuje obie postacie rdzenia, nie tylko dosłowne 'kąt'.
    r"k[ąa](?:t\w*|ci\w*)\s+nachylenia\s+po\w*\s+dachow\w*",
    re.IGNORECASE,
)
_ROOF_ANGLE_RANGE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"od\s+(\d+)\s*°?\s+do\s+(\d+)\s*°", re.IGNORECASE
)

_STOREYS_ANCHOR_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"kondygnacj\w*", re.IGNORECASE
)
# Wymagamy liczby bezpośrednio przy słowie 'kondygnacj*' albo jawnej frazy
# maksymalizującej ('nie więcej niż'/'maksymalnie'). Nie dopuszczamy fuzzy
# odległości między liczbą a słowem, bo to łapało przypadkowe odsyłacze do
# punktów uchwały (np. 'kondygnacją parteru, z zastrzeżeniem pkt 2').
_STOREYS_NUMBER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:nie\s+wi[eę]cej\s+ni[zż]|maksymalnie)\s+(\d+)\s*kondygnacj\w*"
    r"|(\d+)\s*kondygnacj\w*",
    re.IGNORECASE,
)

_SETBACK_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"w\s+odległości\s+([\d]+(?:[,.]\d+)?)\s*m\s+od\s+granicy", re.IGNORECASE
)

_PARKING_MINIMUM_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"([\d]+(?:[,.]\d+)?)\s*miejsc\w*\s+(?:parkingow\w*\s+)?na\s+"
    r"(?:[\d]+(?:[,.]\d+)?\s*)?([a-ząćęłńóśźż²0-9\s]+?)(?:[,.;]|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _RawMatch:
    """Pojedyncze dopasowanie liczbowe przed scaleniem konfliktów."""

    normalized_value: float
    raw_value: str
    source_text: str
    # Zakres dopasowania w tekście przekazanym ekstraktorowi (znaki, ``end`` wyłącznie);
    # tryb blokowy (PV3-06) odwzorowuje go na stronę i pozycję w dokumencie.
    start: int | None = None
    end: int | None = None


def _context_excerpt(text: str, start: int, end: int, pad: int = 40) -> str:
    left = max(0, start - pad)
    right = min(len(text), end + pad)
    return " ".join(text[left:right].split())


def _make_parameters(
    name: str,
    unit: str | None,
    matches: list[_RawMatch],
    page_number: int | None,
) -> list[MpzpParameter]:
    """Buduje listę MpzpParameter z ewentualną karą za sprzeczne wartości.

    Deduplikacja jest wyłącznie po normalized_value: dwa dopasowania tej samej
    wartości liczbowej w różnych miejscach tekstu nie są konfliktem, tylko
    powtórzeniem tej samej informacji.
    """
    if not matches:
        return []

    distinct_values = {match.normalized_value for match in matches}
    has_conflict = len(distinct_values) > 1

    deduped: list[_RawMatch] = []
    seen: set[float] = set()
    for match in matches:
        if match.normalized_value in seen:
            continue
        seen.add(match.normalized_value)
        deduped.append(match)

    parameters: list[MpzpParameter] = []
    for match in deduped:
        confidence = _BASE_CONFIDENCE
        manual_review_required = False
        if has_conflict:
            confidence *= _CONFLICTING_VALUE_CONFIDENCE_PENALTY
            manual_review_required = True
        parameters.append(
            MpzpParameter(
                name=name,
                normalized_value=match.normalized_value,
                unit=unit,
                raw_value=match.raw_value,
                source_text=match.source_text,
                page_number=page_number,
                confidence=confidence,
                manual_review_required=manual_review_required,
            )
        )
    return parameters


def _extract_max_building_height_m(text: str) -> list[_RawMatch]:
    matches: list[_RawMatch] = []
    for anchor in _HEIGHT_ANCHOR_PATTERN.finditer(text):
        window_start = anchor.end()
        window_end = min(len(text), window_start + _WINDOW_AFTER_ANCHOR_CHARS)
        window = text[window_start:window_end]
        boundary = _HEIGHT_SECTION_BOUNDARY_PATTERN.search(window)
        if boundary is not None:
            window = window[: boundary.start()]
        # Sekcja może zawierać kilka warunkowych wysokości (realny przypadek
        # Bielska-Białej: 15 m i 13 m), ale nie może przeciekać do kolejnego
        # zdania/elementu z odsunięciem albo szerokością drogi.
        for value_match in _METERS_VALUE_PATTERN.finditer(window):
            try:
                normalized = parse_polish_number(value_match.group(1))
            except ValueError:
                logger.debug(
                    "Nie udało się sparsować liczby wysokości: %r",
                    value_match.group(1),
                )
                continue
            absolute_start = window_start + value_match.start()
            absolute_end = window_start + value_match.end()
            matches.append(
                _RawMatch(
                    normalized_value=normalized,
                    raw_value=value_match.group(0).strip(),
                    source_text=_context_excerpt(
                        text, absolute_start, absolute_end
                    ),
                    start=absolute_start,
                    end=absolute_end,
                )
            )
    return matches


def _extract_intensity(text: str) -> tuple[list[_RawMatch], list[_RawMatch]]:
    min_matches: list[_RawMatch] = []
    max_matches: list[_RawMatch] = []
    for anchor in _INTENSITY_ANCHOR_PATTERN.finditer(text):
        window_start = anchor.end()
        window_end = min(len(text), window_start + _INTENSITY_WINDOW_CHARS)
        window = text[window_start:window_end]

        min_match = _INTENSITY_MIN_PATTERN.search(window)
        if min_match is not None:
            try:
                normalized = parse_polish_number(min_match.group(1))
            except ValueError:
                logger.debug(
                    "Nie udało się sparsować minimalnej intensywności: %r",
                    min_match.group(1),
                )
            else:
                absolute_start = window_start + min_match.start()
                absolute_end = window_start + min_match.end()
                min_matches.append(
                    _RawMatch(
                        normalized_value=normalized,
                        raw_value=min_match.group(0).strip(),
                        source_text=_context_excerpt(
                            text, absolute_start, absolute_end
                        ),
                        start=absolute_start,
                        end=absolute_end,
                    )
                )

        max_match = _INTENSITY_MAX_PATTERN.search(window)
        if max_match is not None:
            try:
                normalized = parse_polish_number(max_match.group(1))
            except ValueError:
                logger.debug(
                    "Nie udało się sparsować maksymalnej intensywności: %r",
                    max_match.group(1),
                )
            else:
                absolute_start = window_start + max_match.start()
                absolute_end = window_start + max_match.end()
                max_matches.append(
                    _RawMatch(
                        normalized_value=normalized,
                        raw_value=max_match.group(0).strip(),
                        source_text=_context_excerpt(
                            text, absolute_start, absolute_end
                        ),
                        start=absolute_start,
                        end=absolute_end,
                    )
                )
    return min_matches, max_matches


def _extract_percent_pattern(
    text: str, pattern: re.Pattern[str]
) -> list[_RawMatch]:
    matches: list[_RawMatch] = []
    for match in pattern.finditer(text):
        try:
            normalized = parse_polish_number(match.group(1))
        except ValueError:
            logger.debug("Nie udało się sparsować wartości procentowej: %r", match.group(1))
            continue
        matches.append(
            _RawMatch(
                normalized_value=normalized,
                raw_value=f"{match.group(1)}%",
                source_text=_context_excerpt(text, match.start(), match.end()),
                start=match.start(1),
                end=match.end(),
            )
        )
    return matches


def _extract_roof_angle(text: str) -> tuple[list[_RawMatch], list[_RawMatch]]:
    min_matches: list[_RawMatch] = []
    max_matches: list[_RawMatch] = []
    for context_match in _ROOF_ANGLE_CONTEXT_PATTERN.finditer(text):
        # Zakres kąta bywa podany PRZED anchorem (np. "od 30° do 50° ... kąta
        # nachylenia połaci dachowych") albo po nim, więc przeszukujemy okno
        # symetryczne wokół dopasowania kontekstu.
        window_start = max(0, context_match.start() - _WINDOW_AFTER_ANCHOR_CHARS)
        window_end = min(len(text), context_match.end() + _WINDOW_AFTER_ANCHOR_CHARS)
        window = text[window_start:window_end]
        for range_match in _ROOF_ANGLE_RANGE_PATTERN.finditer(window):
            try:
                min_value = float(range_match.group(1))
                max_value = float(range_match.group(2))
            except ValueError:
                logger.debug(
                    "Nie udało się sparsować zakresu kąta dachu: %r",
                    range_match.group(0),
                )
                continue
            absolute_start = window_start + range_match.start()
            absolute_end = window_start + range_match.end()
            excerpt = _context_excerpt(text, absolute_start, absolute_end)
            min_matches.append(
                _RawMatch(
                    normalized_value=min_value,
                    raw_value=range_match.group(0).strip(),
                    source_text=excerpt,
                    start=absolute_start,
                    end=absolute_end,
                )
            )
            max_matches.append(
                _RawMatch(
                    normalized_value=max_value,
                    raw_value=range_match.group(0).strip(),
                    source_text=excerpt,
                    start=absolute_start,
                    end=absolute_end,
                )
            )
    return min_matches, max_matches


def _extract_max_storeys(text: str) -> list[_RawMatch]:
    matches: list[_RawMatch] = []
    for match in _STOREYS_NUMBER_PATTERN.finditer(text):
        raw_group = next((g for g in match.groups() if g is not None), None)
        if raw_group is None:
            continue
        try:
            normalized = float(raw_group)
        except ValueError:
            logger.debug("Nie udało się sparsować liczby kondygnacji: %r", raw_group)
            continue
        matches.append(
            _RawMatch(
                normalized_value=normalized,
                raw_value=match.group(0).strip(),
                source_text=_context_excerpt(text, match.start(), match.end()),
                start=match.start(),
                end=match.end(),
            )
        )
    return matches


def _extract_setback_m(text: str) -> list[_RawMatch]:
    matches: list[_RawMatch] = []
    for match in _SETBACK_PATTERN.finditer(text):
        try:
            normalized = parse_polish_number(match.group(1))
        except ValueError:
            logger.debug("Nie udało się sparsować odległości od granicy: %r", match.group(1))
            continue
        matches.append(
            _RawMatch(
                normalized_value=normalized,
                raw_value=match.group(0).strip(),
                source_text=_context_excerpt(text, match.start(), match.end()),
                start=match.start(),
                end=match.end(),
            )
        )
    return matches


def _extract_parking_minimum(text: str) -> list[_RawMatch]:
    matches: list[_RawMatch] = []
    for match in _PARKING_MINIMUM_PATTERN.finditer(text):
        try:
            normalized = parse_polish_number(match.group(1))
        except ValueError:
            logger.debug("Nie udało się sparsować wskaźnika parkingowego: %r", match.group(1))
            continue
        matches.append(
            _RawMatch(
                normalized_value=normalized,
                raw_value=match.group(0).strip(),
                source_text=_context_excerpt(text, match.start(), match.end()),
                start=match.start(),
                end=match.end(),
            )
        )
    return matches


def extract_numeric_matches(text: str) -> list[tuple[str, str | None, list[_RawMatch]]]:
    """Dopasowania wszystkich parametrów liczbowych w tekście: ``(nazwa, jednostka, dopasowania)``.

    Każde dopasowanie niesie zakres w ``text``; kolejność parametrów jest stała. Funkcję
    współdzielą tryb dotychczasowy (tekst sklejony z segmentów) i tryb blokowy (tekst
    jednego bloku strefy).
    """
    min_intensity, max_intensity = _extract_intensity(text)
    min_roof, max_roof = _extract_roof_angle(text)
    return [
        ("max_building_height_m", "m", _extract_max_building_height_m(text)),
        ("min_intensity", None, min_intensity),
        ("max_intensity", None, max_intensity),
        ("max_building_coverage_percent", "percent", _extract_percent_pattern(text, _COVERAGE_PATTERN)),
        ("min_biologically_active_percent", "percent", _extract_percent_pattern(text, _BIO_ACTIVE_PATTERN)),
        ("roof_angle_min_deg", "deg", min_roof),
        ("roof_angle_max_deg", "deg", max_roof),
        ("max_storeys", None, _extract_max_storeys(text)),
        ("setback_m", "m", _extract_setback_m(text)),
        ("parking_minimum", "miejsca/lokal", _extract_parking_minimum(text)),
    ]


def extract_numeric_parameters(
    zone_symbol: str,
    zone_section: ZoneSectionResult,
    segments: list[DocumentSegment],
) -> list[MpzpParameter]:
    """Wyciąga znormalizowane parametry liczbowe dla jednej strefy MPZP.

    Ekstraktory działają na łączonym tekście wszystkich segmentów wskazanych
    przez kandydatów ``zone_section`` (zwykle jeden segment, ale wieloznaczność
    segmentacji może dać więcej). Brak kandydatów oznacza brak tekstu do
    przeszukania — to nie jest błąd tego modułu, tylko konsekwencja
    wcześniejszego etapu ``segment_document``/``find_zone_sections``.

    Uwaga (PV3-06): to jest tryb dotychczasowy — wartości wszystkich kandydatów dostają
    stronę pierwszego z nich. Tryb blokowy (``mpzp_parser_blocks``) przypisuje stronę i
    zakres znaków z miejsca każdego dopasowania.
    """
    segment_by_id = {segment.segment_id: segment for segment in segments}
    relevant_segments = [
        segment_by_id[candidate.segment_id]
        for candidate in zone_section.candidates
        if candidate.segment_id in segment_by_id
    ]
    if not relevant_segments:
        return []

    combined_text = "\n".join(segment.text for segment in relevant_segments)
    page_number = relevant_segments[0].page_number

    parameters: list[MpzpParameter] = []
    for name, unit, matches in extract_numeric_matches(combined_text):
        parameters.extend(_make_parameters(name, unit, matches, page_number))
    return parameters
