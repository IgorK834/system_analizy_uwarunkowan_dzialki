"""``clip_text`` i ``ClippedString`` (AU-001): przycinanie tekstu z zewnątrz przed zapisem."""

from __future__ import annotations

import logging

import pytest
from sqlalchemy import String
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable
from sqlalchemy import Column, Integer, MetaData, Table

from app.models.types import ClippedString
from app.shared.text import ELLIPSIS, clip_text


def test_none_and_short_values_are_returned_unchanged() -> None:
    assert clip_text(None, 10) is None
    assert clip_text("", 10) == ""
    assert clip_text("abc", 10) == "abc"
    assert clip_text("a" * 10, 10) == "a" * 10


@pytest.mark.parametrize("max_len", [1, 2, 120, 1000])
def test_value_one_char_over_the_limit_is_clipped_to_exactly_the_limit(max_len: int) -> None:
    clipped = clip_text("x" * (max_len + 1), max_len)

    assert clipped is not None
    assert len(clipped) == max_len
    assert clipped.endswith(ELLIPSIS)
    assert clipped[:-1] == "x" * (max_len - 1)


def test_clip_text_follows_the_documented_formula() -> None:
    value = "https://services.gugik.gov.pl/nmt/?polygon=" + "1 2," * 800
    assert clip_text(value, 1000) == value[:999] + "…"


def test_length_is_counted_in_characters_like_postgres_varchar() -> None:
    value = "ąęłóżź" * 10  # znaki wielobajtowe w UTF-8, ale pojedyncze znaki w VARCHAR(n)
    assert clip_text(value, 60) == value
    assert len(clip_text(value + "x", 60) or "") == 60


def test_clipping_is_idempotent() -> None:
    once = clip_text("y" * 500, 100)
    assert clip_text(once, 100) == once


@pytest.mark.parametrize("max_len", [0, -1])
def test_non_positive_limit_is_rejected(max_len: int) -> None:
    with pytest.raises(ValueError):
        clip_text("abc", max_len)


def test_clipped_string_clips_on_bind_and_logs_only_lengths(caplog: pytest.LogCaptureFixture) -> None:
    column_type = ClippedString(120)
    secret = "TAJNY-WIELOKAT " * 20

    with caplog.at_level(logging.WARNING, logger="app.models.types"):
        bound = column_type.process_bind_param(secret, postgresql.dialect())

    assert bound is not None and len(bound) == 120 and bound.endswith("…")
    assert "text_clipped max_len=120 original_len=300" in caplog.text
    assert "TAJNY" not in caplog.text


def test_clipped_string_leaves_values_within_the_limit_and_non_strings_alone(
    caplog: pytest.LogCaptureFixture,
) -> None:
    column_type = ClippedString(10)
    dialect = postgresql.dialect()

    with caplog.at_level(logging.WARNING, logger="app.models.types"):
        assert column_type.process_bind_param("0123456789", dialect) == "0123456789"
        assert column_type.process_bind_param(None, dialect) is None
        assert column_type.process_bind_param(12345678901234, dialect) == 12345678901234

    assert caplog.text == ""


def test_clipped_string_has_the_same_schema_as_a_plain_string() -> None:
    """Przycinanie jest wyłącznie w aplikacji: DDL (a więc migracje) się nie zmienia."""
    def ddl(column_type) -> str:
        table = Table("t", MetaData(), Column("id", Integer, primary_key=True), Column("v", column_type))
        return str(CreateTable(table).compile(dialect=postgresql.dialect()))

    assert ddl(ClippedString(120)) == ddl(String(120))
    assert ClippedString(120).max_length == 120


# --- identyfikator aktu z danych zewnętrznych --------------------------------------------------


def test_act_identifier_within_the_limit_is_unchanged_for_backward_compatibility() -> None:
    from app.shared.act_identifier import bounded_act_identifier

    assert bounded_act_identifier("MPZP/KR/12", "https://bip.example.test/u.pdf") == "MPZP/KR/12"
    assert (
        bounded_act_identifier(None, "https://bip.example.test/u.pdf")
        == "mpzp-document:https://bip.example.test/u.pdf"
    )
    exactly = "p" * 200
    assert bounded_act_identifier(exactly, None) == exactly


def test_long_act_identifier_is_bounded_deterministic_and_collision_resistant() -> None:
    from app.shared.act_identifier import ACT_IDENTIFIER_MAX_LENGTH, bounded_act_identifier

    prefix = "https://bip.example.gov.pl/dokumenty?plan="
    first = bounded_act_identifier(None, prefix + "a" * 400)
    second = bounded_act_identifier(None, prefix + "a" * 400 + "b")  # różni się dopiero za limitem

    assert len(first) == len(second) == ACT_IDENTIFIER_MAX_LENGTH
    assert first.startswith("mpzp-document:" + prefix) and "#" in first
    assert first == bounded_act_identifier(None, prefix + "a" * 400)
    assert first != second  # skrót całej wartości odróżnia akty, które różnią się za przyciętą częścią
    assert len(bounded_act_identifier("x" * 5000, None)) == ACT_IDENTIFIER_MAX_LENGTH


def test_clipped_strings_of_different_length_have_different_cache_keys() -> None:
    """Inaczej zapytania na kolumnach o różnym limicie mogłyby dzielić skompilowany bind processor."""
    assert ClippedString(120)._static_cache_key != ClippedString(255)._static_cache_key
    assert ClippedString(120)._static_cache_key == ClippedString(120)._static_cache_key
    assert ClippedString(120).copy().max_length == 120
