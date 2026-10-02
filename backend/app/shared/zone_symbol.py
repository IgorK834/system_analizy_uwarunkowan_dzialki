"""Symbol strefy planistycznej: forma kanoniczna, walidacja i dopasowanie w tekście (PV3-04).

Symbole występują w planach i odpowiedziach KIMPZP w postaci, której nie da się
sprowadzić do jednego wzorca: ``146 MN``, ``1 PK``, ``1.4U,MN``, ``7 UC,U,M``,
``22 KD G1/2(Z1/4)``, ``KL 1/2``. Moduł jest jedynym źródłem reguł dla backendu,
API (``manual_zone_context``) i UI (``shared/zone-symbol-rules.json``; test
zgodności pilnuje, by nie mogły się rozjechać).

Forma kanoniczna: NFKC, przycięcie, zwinięcie białych znaków do jednej spacji.
Dozwolone: litery (łacińskie i polskie), cyfry, ``. _ / - , + ( )`` oraz pojedyncze
spacje wewnętrzne; do 40 znaków; znaki kontrolne są odrzucane. Oryginał wpisany
przez użytkownika jest zachowywany w dowodzie (``ManualZoneSelection``), a nie
tutaj. Pomyłki OCR (I/1, O/0) dotyczą parsera (Task 20.6), nie walidatora.

Moduł jest wolny od frameworków (może być importowany z warstwy domain).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

ZONE_SYMBOL_RULES_VERSION: Final[str] = "zone-symbol/2"
ZONE_SYMBOL_MIN_LENGTH: Final[int] = 1
ZONE_SYMBOL_MAX_LENGTH: Final[int] = 40
# Ile znaków surowego wejścia przyjmuje API, zanim reguła kanoniczna zwinie
# odstępy; chroni przed ogromnym ładunkiem, nie jest regułą symbolu.
ZONE_SYMBOL_MAX_RAW_LENGTH: Final[int] = 200

_LETTERS_AND_DIGITS: Final[str] = "0-9A-Za-zĄąĆćĘęŁłŃńÓóŚśŹźŻż"
_SYMBOL_CHARACTERS: Final[str] = rf"{_LETTERS_AND_DIGITS}._/\-,+()"
# Wzorzec formy kanonicznej: grupy znaków rozdzielone pojedynczymi spacjami, bez
# spacji na brzegach. Ten sam tekst trafia do UI przez API i do pliku wspólnego.
ZONE_SYMBOL_ALLOWED_PATTERN: Final[str] = (
    rf"^[{_SYMBOL_CHARACTERS}]+(?: [{_SYMBOL_CHARACTERS}]+)*$"
)
_ALLOWED_RE: Final[re.Pattern[str]] = re.compile(ZONE_SYMBOL_ALLOWED_PATTERN)
# Odstępy brzegowe i zwijane to jawny, przenośny zestaw (po NFKC zwykłe i twarde spacje
# są już spacją). Nie używamy ``str.strip()`` ani ``\s``: różnią się między Pythonem a
# JavaScriptem na znakach U+001C-U+001F i U+0085, a UI musi dać ten sam wynik co API.
_EDGE_WHITESPACE: Final[str] = " \t\n\v\f\r\u1680\u2028\u2029"
_WHITESPACE_RE: Final[re.Pattern[str]] = re.compile(r"[ \t\n\v\f\r\u1680\u2028\u2029]+")


class InvalidZoneSymbolError(ValueError):
    """Symbol strefy nie przechodzi walidacji formatu.

    Analogicznie do ``InvalidParcelIdentifierError`` w ``uldk.py`` — błąd dotyczy
    tylko formatu wejścia, nie treści planistycznej symbolu. ``code`` (``empty``,
    ``too_long``, ``control_characters``, ``disallowed_characters``) jest wspólny z UI.
    """

    def __init__(self, message: str, code: str = "invalid") -> None:
        super().__init__(message)
        self.code = code


def canonicalize_zone_symbol(raw: str) -> str:
    """NFKC, przycięcie i zwinięcie białych znaków do jednej spacji (bez walidacji)."""
    return _WHITESPACE_RE.sub(" ", unicodedata.normalize("NFKC", raw).strip(_EDGE_WHITESPACE))


def validate_zone_symbol(raw: str) -> str:
    """Zwraca formę kanoniczną symbolu albo zgłasza ``InvalidZoneSymbolError``.

    Znaki kontrolne (także tabulator i nowa linia wewnątrz symbolu) są odrzucane
    PRZED zwinięciem białych znaków; zwykłe i twarde spacje oraz inne odstępy
    Unicode zwija się do jednej spacji.
    """
    normalized = unicodedata.normalize("NFKC", raw).strip(_EDGE_WHITESPACE)
    if not normalized:
        raise InvalidZoneSymbolError("Symbol strefy nie może być pusty.", "empty")
    if any(unicodedata.category(char) == "Cc" for char in normalized):
        raise InvalidZoneSymbolError(
            "Symbol strefy nie może zawierać znaków kontrolnych.", "control_characters"
        )
    canonical = _WHITESPACE_RE.sub(" ", normalized)
    if len(canonical) > ZONE_SYMBOL_MAX_LENGTH:
        raise InvalidZoneSymbolError(
            "Symbol strefy musi mieć od "
            f"{ZONE_SYMBOL_MIN_LENGTH} do {ZONE_SYMBOL_MAX_LENGTH} znaków.",
            "too_long",
        )
    if not _ALLOWED_RE.fullmatch(canonical):
        raise InvalidZoneSymbolError(
            "Symbol strefy zawiera niedozwolone znaki (dozwolone są litery, cyfry, "
            "'.', '_', '/', '-', ',', '+', '(', ')' oraz pojedyncze spacje).",
            "disallowed_characters",
        )
    return canonical


def zone_symbol_key(symbol: str) -> str:
    """Klucz porównania niewrażliwy na odstępy i wielkość liter (``146 MN`` = ``146mn``)."""
    return "".join(canonicalize_zone_symbol(symbol).split()).casefold()


def same_zone_symbol(left: str, right: str) -> bool:
    """Czy dwa zapisy oznaczają ten sam symbol (odstępy i wielkość liter pomijane)."""
    return zone_symbol_key(left) == zone_symbol_key(right)


# --- dopasowanie w tekście ------------------------------------------------------------

# Symbol jest całym tokenem: kropka, ukośnik, podkreślnik i łącznik należą do
# symbolu (``MN.1``, ``6.8.MW/U``), więc ``MN`` nie dopasuje tekstu strefy ``MN.1``,
# a ``U`` nie dopasuje ``MW/U``. Przecinek, plus i nawias przyklejone do znaków
# symbolu (bez odstępu) też są jego częścią (``1.4U,MN``, ``G1/2(Z1/4)``), a przecinek
# z odstępem jest separatorem listy (``1Up, 2Up``).
_TOKEN_CHARACTERS: Final[str] = _LETTERS_AND_DIGITS
_TOKEN_BEFORE: Final[str] = (
    rf"(?<![{_TOKEN_CHARACTERS}_./\-])(?<![{_TOKEN_CHARACTERS})][,+(])"
)
_TOKEN_AFTER: Final[str] = (
    rf"(?![{_TOKEN_CHARACTERS}_\-]|[./][{_TOKEN_CHARACTERS}]|[,+(][{_TOKEN_CHARACTERS}])"
)
_FLEX_SPACE: Final[str] = r"\s*"


def _char_class(char: str) -> str:
    if char.isdigit():
        return "digit"
    if char.isalpha():
        return "letter"
    return "other"


_OCR_CONFUSIONS: Final[dict[str, str]] = {
    "1": "[1Il]", "I": "[1Il]", "l": "[1Il]", "L": "[1Il]", "0": "[0O]", "O": "[0O]", "o": "[0O]",
}


def zone_symbol_text_pattern(
    symbol: str, *, with_boundaries: bool = True, ocr_tolerant: bool = False
) -> str:
    """Wzorzec regex symbolu tolerancyjny na odstępy, z granicami całego tokenu.

    Odstęp jest opcjonalny w miejscu spacji symbolu i na styku cyfry z literą, więc
    ``146 MN`` i ``146MN`` dopasowują się nawzajem (także przez złamanie wiersza).
    Z ``ocr_tolerant`` znaki mylone przez OCR (``1``/``I``/``l``, ``0``/``O``) pasują
    do siebie; takie dopasowanie jest tylko podstawą kary pewności i flagi (parser,
    Task 20.6), nigdy równorzędnym dopasowaniem. Wzorzec jest przeznaczony do
    ``re.IGNORECASE``.
    """
    canonical = canonicalize_zone_symbol(symbol)
    if not canonical:
        raise InvalidZoneSymbolError("Symbol strefy nie może być pusty.", "empty")
    parts: list[str] = []
    previous: str | None = None
    pending_space = False
    for char in canonical:
        if char == " ":
            pending_space = True
            continue
        if previous is not None:
            transition = {_char_class(previous), _char_class(char)} == {"digit", "letter"}
            if pending_space or transition:
                parts.append(_FLEX_SPACE)
        parts.append(_OCR_CONFUSIONS.get(char, re.escape(char)) if ocr_tolerant else re.escape(char))
        previous = char
        pending_space = False
    body = "".join(parts)
    return f"{_TOKEN_BEFORE}{body}{_TOKEN_AFTER}" if with_boundaries else body


_DIGIT_RUN_BEFORE_RE: Final[re.Pattern[str]] = re.compile(r"([0-9]+)[ \t\n\u00a0]+$")
_SYMBOL_TEXT_CHARACTERS: Final[str] = _LETTERS_AND_DIGITS + "_./-,+()"
_TAIL_WINDOW: Final[int] = 24


def is_tail_of_spaced_symbol(text: str, start: int) -> bool:
    """Czy dopasowanie od ``start`` jest ogonem symbolu ze spacją (``MN`` w ``146 MN``).

    Dopasowanie poprzedzone samodzielną grupą cyfr i odstępem należy do symbolu
    ``146 MN``, a nie jest osobną strefą ``MN``. Cyfra będąca końcem innego symbolu
    (``MN.5 MN.11``) nie jest samodzielna, więc nie blokuje dopasowania.
    """
    begin = max(0, start - _TAIL_WINDOW)
    match = _DIGIT_RUN_BEFORE_RE.search(text[begin:start])
    if match is None:
        return False
    digits_start = begin + match.start(1)
    return digits_start == 0 or text[digits_start - 1] not in _SYMBOL_TEXT_CHARACTERS


def find_zone_symbol_mentions(
    text: str, symbol: str, *, ocr_tolerant: bool = False
) -> list[tuple[int, int]]:
    """Spans wszystkich wystąpień symbolu w tekście (cały token, tolerancyjnie na odstępy)."""
    pattern = re.compile(zone_symbol_text_pattern(symbol, ocr_tolerant=ocr_tolerant), re.IGNORECASE)
    return [
        (match.start(), match.end())
        for match in pattern.finditer(text)
        if not is_tail_of_spaced_symbol(text, match.start())
    ]
