"""Jawne powody braku sekcji strefy w dokumencie (AU-102, Task 22.2).

Parser zgłaszał jeden ogólny ``ZONE_SECTION_NOT_FOUND`` zarówno wtedy, gdy dokument jest skanem bez
warstwy tekstowej (symbolu nie da się wyszukać), jak i wtedy, gdy tekst jest czytelny, a symbolu w nim nie
ma (inny plan, strefa oznaczona tylko na rysunku). Użytkownik i audyt potrzebują rozróżnienia, bo wymagają
innej reakcji: pierwszy przypadek to „uruchom OCR albo sprawdź ręcznie”, drugi — „to nie jest ta uchwała albo
symbol zapisano inaczej”. Brak sekcji nigdy nie jest „brakiem ograniczeń”.

``ZONE_SECTION_NOT_FOUND`` zostaje wyłącznie dla symbolu, który w tekście JEST (także w zakresie albo z pomyłką
OCR), a mimo to nie udało się wyznaczyć jego sekcji.
"""

from __future__ import annotations

from typing import Final

from app.schemas.mpzp import MpzpParserWarning
from app.services.mpzp_parser_extract import TextExtractionResult
from app.shared.zone_symbol import find_zone_symbol_mentions, find_zone_symbol_range_mentions

ZONE_SECTION_NOT_FOUND: Final[str] = "ZONE_SECTION_NOT_FOUND"
ZONE_NOT_IN_DOCUMENT: Final[str] = "ZONE_NOT_IN_DOCUMENT"
SCANNED_NO_OCR: Final[str] = "SCANNED_NO_OCR"
MISSING_SECTION_REASON_CODES: Final[tuple[str, ...]] = (ZONE_NOT_IN_DOCUMENT, SCANNED_NO_OCR)

_MESSAGES: Final[dict[str, str]] = {
    SCANNED_NO_OCR: (
        "Dokument jest skanem bez czytelnej warstwy tekstowej, a OCR nie został użyty albo nie dał tekstu — "
        "symbolu strefy {symbol} nie można wyszukać. To nie znaczy, że strefy nie ma w dokumencie; "
        "wymagana ręczna weryfikacja albo OCR."
    ),
    ZONE_NOT_IN_DOCUMENT: (
        "W czytelnym tekście dokumentu ({pages} str.) nie występuje symbol strefy {symbol} — ani wprost, ani w "
        "zakresie symboli. Dokument może dotyczyć innego planu albo strefa jest oznaczona wyłącznie na rysunku; "
        "brak sekcji nie oznacza braku ustaleń."
    ),
}


def symbol_in_text(text: str, symbol: str) -> bool:
    """Czy symbol jest w tekście: wprost, w zakresie symboli albo z pomyłką OCR (I/1, O/0)."""
    return bool(
        find_zone_symbol_mentions(text, symbol)
        or find_zone_symbol_range_mentions(text, symbol)
        or find_zone_symbol_mentions(text, symbol, ocr_tolerant=True)
    )


def refine_missing_section_warnings(
    warnings: list[MpzpParserWarning], extraction: TextExtractionResult
) -> list[MpzpParserWarning]:
    """Zamienia ogólny ``ZONE_SECTION_NOT_FOUND`` na jawny powód, gdy da się go ustalić z tekstu dokumentu."""
    if not any(w.code == ZONE_SECTION_NOT_FOUND for w in warnings):
        return warnings
    text = "\n".join(extraction.pages)
    refined: list[MpzpParserWarning] = []
    for warning in warnings:
        symbol = warning.zone_symbol
        if warning.code != ZONE_SECTION_NOT_FOUND or not symbol or symbol_in_text(text, symbol):
            refined.append(warning)
            continue
        code = SCANNED_NO_OCR if extraction.needs_ocr else ZONE_NOT_IN_DOCUMENT
        refined.append(
            warning.model_copy(
                update={"code": code, "message": _MESSAGES[code].format(symbol=symbol, pages=len(extraction.pages))}
            )
        )
    return refined


__all__ = [
    "MISSING_SECTION_REASON_CODES",
    "SCANNED_NO_OCR",
    "ZONE_NOT_IN_DOCUMENT",
    "ZONE_SECTION_NOT_FOUND",
    "refine_missing_section_warnings",
    "symbol_in_text",
]
