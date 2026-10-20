"""Normalizacja i dopasowanie tekstu adresowego (czysta logika).

Normalizacja służy wyłącznie wyszukiwaniu i rankingowi — NIE zmienia tekstu
wyświetlanego użytkownikowi (label pozostaje oryginalny). Fałdowanie znaków jest
1:1 (długość zachowana), dzięki czemu indeksy dopasowań (match_ranges) odnoszą
się do oryginalnego label.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping

from app.modules.location.domain.models import MatchRange

# Mapowanie polskich znaków diakrytycznych na podstawowe litery (1:1).
_PL_FOLD = str.maketrans(
    {
        "ą": "a",
        "ć": "c",
        "ę": "e",
        "ł": "l",
        "ń": "n",
        "ó": "o",
        "ś": "s",
        "ź": "z",
        "ż": "z",
        "Ą": "a",
        "Ć": "c",
        "Ę": "e",
        "Ł": "l",
        "Ń": "n",
        "Ó": "o",
        "Ś": "s",
        "Ź": "z",
        "Ż": "z",
    }
)


def fold_char(char: str) -> str:
    """Fałduje pojedynczy znak: diakrytyk → litera bazowa, wielkość → mała (1:1)."""
    return char.translate(_PL_FOLD).lower()


def fold_text(text: str) -> str:
    """Fałduje tekst zachowując długość (indeksy pozostają zgodne z oryginałem)."""
    return "".join(fold_char(char) for char in text)


def tokenize(normalized: str) -> list[str]:
    """Dzieli znormalizowany tekst na tokeny alfanumeryczne."""
    tokens: list[str] = []
    current: list[str] = []
    for char in normalized:
        if char.isalnum():
            current.append(char)
        elif current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return tokens


def uug_query_variants(query: str, localities: Mapping[str, str]) -> tuple[str, ...]:
    """Ograniczona lista zapisów UUG: miejscowość, ulica numer.

    Nazwy z PRG przywracają diakrytyki miejscowości; ulic nie zgadujemy.
    Przy nieznanej miejscowości próbujemy podziały tekstu, bez usuwania
    numeru i bez zastępowania adresu samym środkiem miejscowości.
    """
    text = " ".join(unicodedata.normalize("NFC", query).split())
    text = re.sub(r"\b(?:ul\.|ulica)\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bal\.\s*", "aleja ", text, flags=re.IGNORECASE)
    variants: list[str] = []

    def add(city: str, street: str = "") -> None:
        city = localities.get(fold_text(city), city)
        candidate = f"{city}, {street}" if street else city
        if candidate and candidate not in variants:
            variants.append(candidate)

    if "," in text:
        left, right = (part.strip() for part in text.split(",", 1))
        if fold_text(right) in localities and fold_text(left) not in localities:
            add(right, left)
        else:
            add(left, right)
        return tuple(variants)

    words = text.split()
    # Najdłuższa znana nazwa: „Góra Kalwaria”, „Nowy Dwór Mazowiecki”…
    for count in range(min(len(words), 4), 0, -1):
        prefix, suffix = " ".join(words[:count]), " ".join(words[-count:])
        if fold_text(prefix) in localities:
            add(prefix, " ".join(words[count:]))
        if fold_text(suffix) in localities:
            add(suffix, " ".join(words[:-count]))
    if variants:
        return tuple(variants)
    # Numer między ulicą a miejscowością wskazuje granicę również dla wsi
    # nieobecnych w słowniku nazw gmin. „3 Maja” jest częścią nazwy ulicy.
    for index, word in enumerate(words[:-1]):
        if index > 0 and re.fullmatch(r"\d+[a-zA-Z]?(?:/\d+[a-zA-Z]?)?", word):
            add(" ".join(words[index + 1:]), " ".join(words[:index + 1]))
    add(text)
    for count in range(1, min(3, len(words))):
        add(" ".join(words[:count]), " ".join(words[count:]))
        add(" ".join(words[-count:]), " ".join(words[:-count]))
    return tuple(variants[:6])


def compute_match_ranges(label: str, query: str) -> tuple[MatchRange, ...]:
    """Wyznacza zakresy w oryginalnym ``label`` pasujące do tokenów ``query``.

    Fałdowanie jest 1:1, więc pozycje znalezione w tekście złożonym odpowiadają
    dokładnie indeksom w oryginalnym label. Zakresy nakładające się i stykające
    są scalane, a wynik posortowany rosnąco.
    """
    folded_label = fold_text(label)
    tokens = [token for token in tokenize(fold_text(query)) if len(token) >= 2]
    raw_ranges: list[tuple[int, int]] = []
    for token in tokens:
        start = folded_label.find(token)
        while start != -1:
            raw_ranges.append((start, start + len(token)))
            start = folded_label.find(token, start + 1)
    return _merge_ranges(raw_ranges)


def _merge_ranges(ranges: list[tuple[int, int]]) -> tuple[MatchRange, ...]:
    if not ranges:
        return ()
    ordered = sorted(ranges)
    merged: list[list[int]] = [list(ordered[0])]
    for start, end in ordered[1:]:
        last = merged[-1]
        if start <= last[1]:
            last[1] = max(last[1], end)
        else:
            merged.append([start, end])
    return tuple(MatchRange(start=start, end=end) for start, end in merged)
