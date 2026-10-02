"""Warstwa normalizacji tekstu dokumentu: surowy tekst niezmienny, odstępy ujednolicone (PV3-05).

Tekst stron z PDF, OCR i HTML ma twarde spacje, tabulatory, różne końce wiersza i
artefakty ekstrakcji. Ten moduł tworzy ZNORMALIZOWANY tekst dokumentu, z którego
korzysta drzewo struktury (``document_tree``) i bloki stref, oraz **mapę pozycji**
z powrotem do tekstu surowego. Surowy tekst nigdy nie jest zmieniany.

Zasady:

* normalizacja dotyczy wyłącznie odstępów: końce wiersza do ``\\n``, twarde i inne
  spacje Unicode oraz tabulatory do spacji, zwinięcie powtórzonych spacji, usunięcie
  spacji na brzegach wiersza i znaków formatujących (zerowej szerokości, miękkiego
  łącznika). Każdy znak wyniku wskazuje swój znak w tekście surowym;
* **poprawki artefaktów nie są nakładane po cichu.** ``°`` wyekstrahowane jako ``0``
  albo ``o`` (``300`` zamiast ``30°``), litery rozstrzelone spacjami, dzielenie wyrazów
  na końcu wiersza i znaki zastępcze są tylko FLAGOWANE (``TextFlag``) z sugestią;
  tekst zostaje taki, jaki jest;
* każda strona kończy się znakiem nowej linii (``\\n``), więc teksty stron łączą się
  bez separatora, a zakresy stron dzielą tekst dokumentu bez luk.
"""

from __future__ import annotations

import re
import unicodedata
from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

_FORMAT_CHARACTERS: Final[frozenset[str]] = frozenset("­​‌‍⁠﻿")


@dataclass(frozen=True)
class TextFlag:
    """Podejrzany fragment znormalizowanego tekstu; tekst nie jest zmieniany."""

    kind: str
    page_number: int
    start: int
    end: int
    message: str
    suggestion: str | None = None


@dataclass(frozen=True)
class NormalizedPage:
    """Jedna strona: tekst surowy (niezmienny), znormalizowany i mapa pozycji."""

    page_number: int
    raw: str
    text: str
    origin: tuple[int, ...]

    def normalized_index(self, raw_index: int) -> int:
        """Pierwszy znak znormalizowany, którego pozycja surowa jest >= ``raw_index``."""
        return bisect_left(self.origin, raw_index)


@dataclass(frozen=True)
class NormalizedDocument:
    """Znormalizowany tekst dokumentu z zakresami stron i flagami artefaktów."""

    pages: tuple[NormalizedPage, ...]
    text: str
    page_starts: tuple[int, ...]
    flags: tuple[TextFlag, ...]
    statistics: tuple[tuple[str, int], ...]

    def page_index_at(self, offset: int) -> int:
        if not self.pages:
            raise ValueError("Dokument nie ma stron.")
        return max(0, bisect_right(self.page_starts, max(0, offset)) - 1)

    def page_number_at(self, offset: int) -> int:
        return self.pages[self.page_index_at(offset)].page_number

    def page_span(self, page_number: int) -> tuple[int, int]:
        for index, page in enumerate(self.pages):
            if page.page_number == page_number:
                return self.page_starts[index], self.page_starts[index] + len(page.text)
        raise KeyError(page_number)

    def raw_spans(self, start: int, end: int) -> tuple[tuple[int, int, int], ...]:
        """Zakres znormalizowany jako ``(strona, początek, koniec)`` w tekście SUROWYM.

        Zakres przechodzący przez kilka stron daje po jednym wpisie na stronę. Znaki
        syntetyczne (końcowy ``\\n`` strony) nie wchodzą do zakresu surowego.
        """
        spans: list[tuple[int, int, int]] = []
        if end <= start:
            return ()
        first, last = self.page_index_at(start), self.page_index_at(end - 1)
        for index in range(first, last + 1):
            page = self.pages[index]
            local_start = max(start, self.page_starts[index]) - self.page_starts[index]
            local_end = min(end, self.page_starts[index] + len(page.text)) - self.page_starts[index]
            chars = [i for i in range(local_start, local_end) if page.origin[i] < len(page.raw)]
            if not chars:
                continue
            spans.append((page.page_number, page.origin[chars[0]], page.origin[chars[-1]] + 1))
        return tuple(spans)

    def stat(self, name: str) -> int:
        return dict(self.statistics).get(name, 0)


def _normalize_page(raw: str) -> tuple[str, tuple[int, ...], dict[str, int]]:
    """Normalizuje jedną stronę; zwraca tekst, mapę pozycji i liczniki zmian."""
    chars: list[str] = []
    origin: list[int] = []
    stats = {"space_variants_replaced": 0, "space_runs_collapsed": 0, "format_characters_removed": 0,
             "line_ends_unified": 0, "edge_spaces_removed": 0}
    index = 0
    length = len(raw)
    while index < length:
        char = raw[index]
        if char in "\r\n\v\f":
            if char != "\n":
                stats["line_ends_unified"] += 1
            chars.append("\n")
            origin.append(index)
            index += 2 if char == "\r" and index + 1 < length and raw[index + 1] == "\n" else 1
        elif char in _FORMAT_CHARACTERS:
            stats["format_characters_removed"] += 1
            index += 1
        elif char == "\t" or unicodedata.category(char) == "Zs":
            if char != " ":
                stats["space_variants_replaced"] += 1
            if chars and chars[-1] not in " \n":
                chars.append(" ")
                origin.append(index)
            elif chars and chars[-1] == " ":
                stats["space_runs_collapsed"] += 1
            else:  # początek strony albo wiersza
                stats["edge_spaces_removed"] += 1
            index += 1
        else:
            chars.append(char)
            origin.append(index)
            index += 1
    # spacje przed końcem wiersza i na końcu strony nie niosą treści
    cleaned_chars: list[str] = []
    cleaned_origin: list[int] = []
    for position, char in enumerate(chars):
        if char == " " and (position + 1 == len(chars) or chars[position + 1] == "\n"):
            stats["edge_spaces_removed"] += 1
            continue
        cleaned_chars.append(char)
        cleaned_origin.append(origin[position])
    if not cleaned_chars or cleaned_chars[-1] != "\n":
        cleaned_chars.append("\n")
        cleaned_origin.append(len(raw))  # znak syntetyczny: nie ma odpowiednika w tekście surowym
    return "".join(cleaned_chars), tuple(cleaned_origin), stats


_ROOF_CONTEXT = re.compile(r"k[ąa](?:t\w*|ci\w*)|nachylen\w*|połaci\w*|spadk\w*|dach\w*", re.IGNORECASE)
_DEGREE_AS_ZERO = re.compile(r"(?<![\d,.])([1-8]\d)0(?![\d%]|\s*(?:m\b|mm\b|cm\b|zł|szt|kg))")
_DEGREE_AS_LETTER = re.compile(r"(?<![\d,.])(\d{1,2})[oO](?=[\s,.;:)]|$)(?!\w)")
_SPACED_LETTERS = re.compile(r"(?:(?<=\s)|^)(?:[^\W\d_]\s){5,}[^\W\d_](?=\s|$)", re.MULTILINE)
_LINE_HYPHENATION = re.compile(r"[^\W\d_]-\n[^\W\d_]")
_REPLACEMENT = re.compile("[�-]")
_ROOF_WINDOW = 60


def detect_text_artifacts(text: str, page_starts: Sequence[int], page_numbers: Sequence[int]) -> tuple[TextFlag, ...]:
    """Flaguje artefakty ekstrakcji; zwraca znaczniki, nie zmienia tekstu."""
    flags: list[TextFlag] = []

    def page_of(offset: int) -> int:
        return page_numbers[max(0, bisect_right(page_starts, offset) - 1)]

    for pattern, kind, message in (
        (_DEGREE_AS_ZERO, "degree_sign_as_zero", "Znak stopnia mógł zostać odczytany jako „0”."),
        (_DEGREE_AS_LETTER, "degree_sign_as_letter", "Znak stopnia mógł zostać odczytany jako litera „o”."),
    ):
        for match in pattern.finditer(text):
            window = text[max(0, match.start() - _ROOF_WINDOW) : match.end() + _ROOF_WINDOW]
            if _ROOF_CONTEXT.search(window) is None:
                continue
            flags.append(TextFlag(kind, page_of(match.start()), match.start(), match.end(), message, f"{match.group(1)}°"))
    for match in _SPACED_LETTERS.finditer(text):
        flags.append(TextFlag("spaced_letters", page_of(match.start()), match.start(), match.end(),
                              "Litery rozdzielone spacjami (rozstrzelony druk lub błąd odczytu).",
                              "".join(match.group(0).split())))
    for match in _LINE_HYPHENATION.finditer(text):
        flags.append(TextFlag("line_hyphenation", page_of(match.start()), match.start(), match.end(),
                              "Wyraz podzielony łącznikiem na końcu wiersza.", None))
    for match in _REPLACEMENT.finditer(text):
        flags.append(TextFlag("replacement_character", page_of(match.start()), match.start(), match.end(),
                              "Znak zastępczy: uszkodzone kodowanie tekstu.", None))
    return tuple(sorted(flags, key=lambda flag: (flag.start, flag.kind)))


def normalize_document(pages: Sequence[str], page_numbers: Sequence[int] | None = None) -> NormalizedDocument:
    """Normalizuje strony (numerowane od 1 albo wg ``page_numbers``) i flaguje artefakty."""
    numbers = list(page_numbers) if page_numbers is not None else list(range(1, len(pages) + 1))
    if len(numbers) != len(pages):
        raise ValueError("Liczba numerów stron musi równać się liczbie stron.")
    normalized: list[NormalizedPage] = []
    totals: dict[str, int] = {}
    starts: list[int] = []
    offset = 0
    for number, raw in zip(numbers, pages, strict=True):
        text, origin, stats = _normalize_page(raw)
        normalized.append(NormalizedPage(page_number=number, raw=raw, text=text, origin=origin))
        starts.append(offset)
        offset += len(text)
        for key, value in stats.items():
            totals[key] = totals.get(key, 0) + value
    document_text = "".join(page.text for page in normalized)
    flags = detect_text_artifacts(document_text, starts, numbers)
    return NormalizedDocument(
        pages=tuple(normalized),
        text=document_text,
        page_starts=tuple(starts),
        flags=flags,
        statistics=tuple(sorted(totals.items())),
    )
