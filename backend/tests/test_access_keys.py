"""Parser listy kluczy podpisu tokenów dostępu (AU-012)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.core.access_keys import SigningKey, parse_token_secrets
from app.core.settings import Settings


def test_empty_value_means_no_keys() -> None:
    assert parse_token_secrets("") == ()
    assert parse_token_secrets(" , ,") == ()


def test_first_entry_is_active_and_the_rest_are_listed_in_order() -> None:
    keys = parse_token_secrets("k2:sekret-nowy, k1:sekret-stary")

    assert [key.kid for key in keys] == ["k2", "k1"]
    assert keys[0].secret == b"sekret-nowy"
    assert all(key.valid_until is None for key in keys)


def test_secret_may_contain_colons() -> None:
    (key,) = parse_token_secrets("k1:a:b:c")

    assert key.secret == b"a:b:c"


def test_date_only_retirement_lasts_until_the_end_of_that_day_utc() -> None:
    _, old = parse_token_secrets("k2:nowy,k1:stary|2026-12-01")

    assert old.valid_until == int(datetime(2026, 12, 2, tzinfo=UTC).timestamp())
    assert old.is_usable_at(old.valid_until - 1) is True
    assert old.is_usable_at(old.valid_until) is False


def test_full_timestamp_retirement_is_taken_literally_and_naive_means_utc() -> None:
    _, with_zone, naive = parse_token_secrets(
        "k3:c,k2:b|2026-12-01T10:30:00+00:00,k1:a|2026-12-01T10:30:00"
    )

    expected = int(datetime(2026, 12, 1, 10, 30, tzinfo=UTC).timestamp())
    assert with_zone.valid_until == naive.valid_until == expected


@pytest.mark.parametrize(
    "raw",
    [
        "bez-dwukropka",
        ":sekret",
        "kid z spacją:sekret",
        "k" * 33 + ":sekret",
        "k1:",
        "k1:a,k1:b",
        "k2:a,k1:b|nie-data",
        "k2:a,k1:b|2026-12-01|2027-01-01",
        "k1:a|2026-12-01",
    ],
)
def test_invalid_lists_are_rejected(raw: str) -> None:
    with pytest.raises(ValueError, match="ACCESS_TOKEN_SECRETS"):
        parse_token_secrets(raw)


def test_error_messages_never_repeat_the_secret() -> None:
    with pytest.raises(ValueError) as caught:
        parse_token_secrets("k2:bardzo-tajny-sekret,k1:inny-sekret|nie-data")

    assert "tajny" not in str(caught.value)
    assert "inny-sekret" not in str(caught.value)


def test_settings_reject_an_invalid_list_at_startup() -> None:
    with pytest.raises(ValidationError):
        Settings(access_token_secrets="k1:a,k1:b")


def test_a_rejected_settings_list_never_echoes_the_secrets_in_the_startup_error() -> None:
    with pytest.raises(ValidationError) as caught:
        Settings(access_token_secrets="k1:bardzo-tajne-A,k1:bardzo-tajne-B")

    text = str(caught.value)
    assert "powtórzony kid" in text and "tajne" not in text and "input_value" not in text


def test_signing_key_without_date_is_usable_forever() -> None:
    assert SigningKey(kid="k", secret=b"s").is_usable_at(10**12) is True
