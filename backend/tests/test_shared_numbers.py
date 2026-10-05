"""Wspólne prymitywy liczbowe (PV3-07): jedno miejsce zamiany zapisu liczby na wartość."""

from __future__ import annotations

import re

import pytest

from app.shared.numbers import (
    NUMBER_PATTERN,
    parse_number_prefix,
    parse_number_word,
    parse_numeric_range,
    parse_polish_number,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1 234,50", 1234.5), ("1 234,5", 1234.5), ("0,01", 0.01), ("12.5", 12.5), ("1.234,5", 1234.5), ("2000", 2000.0)],
)
def test_parse_polish_number(raw: str, expected: float) -> None:
    assert parse_polish_number(raw) == pytest.approx(expected)


def test_parse_polish_number_rejects_empty_and_garbage() -> None:
    for raw in ("", "   ", "abc", "1,2,3"):
        with pytest.raises(ValueError):
            parse_polish_number(raw)


def test_number_pattern_keeps_thousands_but_does_not_glue_separate_numbers() -> None:
    pattern = re.compile(NUMBER_PATTERN)
    assert [m.group(0) for m in pattern.finditer("od 12 do 45 oraz 2 000 m i 4 500,5")] == ["12", "45", "2 000", "4 500,5"]
    assert [m.group(0) for m in pattern.finditer("0,01 – 0,9")] == ["0,01", "0,9"]


@pytest.mark.parametrize(
    ("word", "expected"),
    [("jeden", 1), ("jedna", 1), ("dwie", 2), ("dwóch", 2), ("dwu", 2), ("trzech", 3), ("czterech", 4), ("pięciu", 5), ("DZIESIĘĆ", 10)],
)
def test_number_words(word: str, expected: int) -> None:
    assert parse_number_word(word) == expected


def test_number_words_are_whole_words_not_prefixes() -> None:
    assert parse_number_word("dwanaście") is None and parse_number_word("trzydzieści") is None
    assert parse_number_word("działka") is None and parse_number_word("") is None


@pytest.mark.parametrize(
    ("word", "expected"),
    [("jednokondygnacyjne", 1), ("dwukondygnacyjny", 2), ("trzykondygnacyjna", 3), ("czterokondygnacyjne", 4), ("pięciokondygnacyjny", 5)],
)
def test_compound_prefixes(word: str, expected: int) -> None:
    assert parse_number_prefix(word) == expected
    assert parse_number_prefix("kondygnacyjny") is None


def test_numeric_range_keeps_its_legacy_behaviour() -> None:
    assert parse_numeric_range("0.20–1.50").maximum == pytest.approx(1.5)
    reversed_range = parse_numeric_range("2,5 do 0,5")
    assert (reversed_range.minimum, reversed_range.maximum) == (0.5, 2.5)
    assert parse_numeric_range("brak zakresu") is None
