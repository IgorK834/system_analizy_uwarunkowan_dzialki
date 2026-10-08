"""Klucz klienta limitera i pomiar z audytu (AU-006, B7/R3).

Audyt: przy ``RATE_LIMIT_TRUST_FORWARDED_FOR=true`` bez proxy limit 5/min przepuścił 50/50 żądań
z rotowanym nagłówkiem ``X-Forwarded-For``. Teraz nagłówek liczy się wyłącznie od zaufanego proxy.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.requests import Request

from app.core import rate_limit as rate_limit_module
from app.core.rate_limit import client_key, parse_trusted_proxies
from app.core.settings import Settings, settings
from app.main import app
from app.schemas.analyze import ParcelIdAnalyzeRequest  # noqa: F401  (kontrakt żądania analizy)

PROXY = "10.0.0.9"
PARCEL_REQUEST = {"method": "parcel_id", "parcel_identifier": "122101_1.0001.1"}


def _request(peer: str | None, *forwarded: str) -> Request:
    headers = [(b"x-forwarded-for", value.encode("ascii")) for value in forwarded]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
        "client": (peer, 40000) if peer is not None else None,
    }
    return Request(scope)


@pytest.fixture
def trust(monkeypatch: pytest.MonkeyPatch):
    def configure(*, enabled: bool = True, proxies: str = PROXY, count: int = 1) -> None:
        monkeypatch.setattr(settings, "rate_limit_trust_forwarded_for", enabled)
        monkeypatch.setattr(settings, "rate_limit_trusted_proxies", proxies)
        monkeypatch.setattr(settings, "trusted_proxy_count", count)

    rate_limit_module._warn_forwarded_for_without_proxies.cache_clear()
    return configure


# --- client_key ---------------------------------------------------------------


def test_forwarded_for_is_ignored_when_trust_is_off(trust) -> None:
    trust(enabled=False)

    assert client_key(_request(PROXY, "203.0.113.5")) == PROXY


def test_default_configuration_does_not_trust_forwarded_for() -> None:
    assert Settings(_env_file=None).rate_limit_trust_forwarded_for is False
    assert Settings(_env_file=None).rate_limit_trusted_proxies == ""


def test_trust_without_any_trusted_proxy_ignores_the_header_and_warns(
    trust, caplog: pytest.LogCaptureFixture
) -> None:
    trust(proxies="")

    with caplog.at_level(logging.WARNING, logger="app.core.rate_limit"):
        key = client_key(_request("198.51.100.4", "203.0.113.5"))

    assert key == "198.51.100.4"
    assert "RATE_LIMIT_TRUSTED_PROXIES" in caplog.text


def test_trust_with_a_peer_outside_the_trusted_list_ignores_the_header(trust) -> None:
    trust()

    assert client_key(_request("198.51.100.4", "203.0.113.5")) == "198.51.100.4"


def test_trusted_peer_makes_the_last_entry_the_client(trust) -> None:
    trust()

    # Wpisy z przodu mógł podać klient; ostatni dopisał zaufany proxy.
    assert client_key(_request(PROXY, "6.6.6.6, 7.7.7.7, 203.0.113.5")) == "203.0.113.5"


def test_entry_is_counted_from_the_end_by_trusted_proxy_count(trust) -> None:
    trust(count=2)

    # klient → proxy A (zaufany) → proxy B (peer): XFF = "klient, A".
    assert client_key(_request(PROXY, "9.9.9.9, 203.0.113.5, 192.0.2.10")) == "203.0.113.5"


def test_chain_shorter_than_the_proxy_count_falls_back_to_the_peer(trust) -> None:
    trust(count=3)

    assert client_key(_request(PROXY, "203.0.113.5, 192.0.2.10")) == PROXY


@pytest.mark.parametrize("entry", ["unknown", "not-an-ip", "", "203.0.113.5:8080", "<script>"])
def test_unparseable_entry_falls_back_to_the_peer(trust, entry: str) -> None:
    trust()

    assert client_key(_request(PROXY, f"1.1.1.1, {entry}")) == PROXY


def test_multiple_header_lines_are_combined_in_order(trust) -> None:
    trust()

    assert client_key(_request(PROXY, "6.6.6.6", "203.0.113.5")) == "203.0.113.5"


def test_trusted_proxies_accept_cidr_networks_of_both_families(trust) -> None:
    trust(proxies="172.18.0.0/16, fd00::/8")

    assert client_key(_request("172.18.0.3", "203.0.113.5")) == "203.0.113.5"
    assert client_key(_request("fd00::7", "203.0.113.6")) == "203.0.113.6"
    assert client_key(_request("172.19.0.3", "203.0.113.5")) == "172.19.0.3"


def test_ipv6_clients_are_bucketed_by_their_64_bit_prefix() -> None:
    first = client_key(_request("2001:db8:1:2::1"))
    second = client_key(_request("2001:db8:1:2:ffff:ffff:ffff:ffff"))
    other = client_key(_request("2001:db8:1:3::1"))

    assert first == second == "2001:db8:1:2::/64"
    assert other != first


def test_ipv4_mapped_ipv6_peer_uses_the_ipv4_key() -> None:
    assert client_key(_request("::ffff:203.0.113.5")) == "203.0.113.5"


def test_peer_that_is_not_an_ip_is_used_verbatim_and_missing_client_is_unknown() -> None:
    assert client_key(_request("testclient")) == "testclient"
    assert client_key(_request(None)) == "unknown"


def test_trusted_proxy_networks_are_validated_at_startup() -> None:
    assert parse_trusted_proxies("10.0.0.1, 172.18.0.0/16") != ()
    with pytest.raises(ValidationError, match="RATE_LIMIT_TRUSTED_PROXIES"):
        Settings(_env_file=None, rate_limit_trusted_proxies="10.0.0.1, nie-adres")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, trusted_proxy_count=0)


# --- Pomiar z audytu: 50 żądań, limit 5/min ------------------------------------

AUDIT_REQUESTS = 50


def _post_rotating(client: TestClient, header_for) -> list[int]:
    with patch("app.routers.analyze.run_analysis", AsyncMock(side_effect=_not_found)):
        return [
            client.post(
                "/analyze?force_refresh=true",
                json=PARCEL_REQUEST,
                headers={"X-Forwarded-For": header_for(index)},
            ).status_code
            for index in range(AUDIT_REQUESTS)
        ]


def _not_found(*_args, **_kwargs):
    from app.services.uldk import ParcelNotFoundError

    raise ParcelNotFoundError("brak")


def test_audit_measurement_trust_off_passes_at_most_the_limit() -> None:
    assert settings.rate_limit_refresh_per_minute == 5
    client = TestClient(app, client=("198.51.100.4", 50000))

    codes = _post_rotating(client, lambda index: f"203.0.113.{index}")

    assert sum(code != 429 for code in codes) <= 5
    assert codes.count(429) >= AUDIT_REQUESTS - 5


def test_audit_measurement_trust_on_without_a_trusted_peer_passes_at_most_the_limit(trust) -> None:
    trust(proxies="")
    client = TestClient(app, client=("198.51.100.4", 50000))

    codes = _post_rotating(client, lambda index: f"203.0.113.{index}")

    assert sum(code != 429 for code in codes) <= 5


def test_audit_measurement_trust_on_with_a_peer_outside_the_list_passes_at_most_the_limit(trust) -> None:
    trust(proxies="10.0.0.0/24")
    client = TestClient(app, client=("198.51.100.4", 50000))

    codes = _post_rotating(client, lambda index: f"203.0.113.{index}")

    assert sum(code != 429 for code in codes) <= 5


def test_audit_measurement_rotating_the_client_supplied_prefix_behind_a_trusted_proxy(trust) -> None:
    trust()
    client = TestClient(app, client=(PROXY, 50000))

    # Proxy zawsze dopisuje prawdziwy adres klienta jako ostatni wpis; rotacja przodu nic nie zmienia.
    codes = _post_rotating(client, lambda index: f"10.1.1.{index}, 203.0.113.77")

    assert sum(code != 429 for code in codes) <= 5


def test_distinct_real_clients_behind_a_trusted_proxy_keep_separate_budgets(trust) -> None:
    trust()
    client = TestClient(app, client=(PROXY, 50000))

    codes = _post_rotating(client, lambda index: f"203.0.113.{index}")

    assert sum(code != 429 for code in codes) == AUDIT_REQUESTS


# --- Przypadki brzegowe konfiguracji i adresów ----------------------------------


def test_limiter_requires_positive_limit_and_window() -> None:
    from app.core.rate_limit import FixedWindowRateLimiter

    with pytest.raises(ValueError):
        FixedWindowRateLimiter(limit=0, window_seconds=60)
    with pytest.raises(ValueError):
        FixedWindowRateLimiter(limit=1, window_seconds=0)


def test_limiter_starts_a_new_window_and_evicts_the_oldest_key() -> None:
    from app.core.rate_limit import FixedWindowRateLimiter

    limiter = FixedWindowRateLimiter(limit=1, window_seconds=10, max_tracked_keys=2)

    assert limiter.check("a", now=0).allowed
    assert not limiter.check("a", now=1).allowed
    assert limiter.check("a", now=11).allowed  # nowe okno
    limiter.check("b", now=12)
    limiter.check("c", now=12)  # eksmisja najstarszego klucza ("a")
    assert limiter.check("a", now=13).allowed


def test_invalid_trusted_proxy_configuration_fails_closed(trust) -> None:
    trust(proxies="to-nie-jest-adres")

    assert client_key(_request(PROXY, "203.0.113.5")) == PROXY


def test_non_ip_peer_is_never_a_trusted_proxy(trust) -> None:
    trust(proxies="10.0.0.0/8")

    assert client_key(_request("testclient", "203.0.113.5")) == "testclient"


def test_bracketed_ipv6_forwarded_entry_is_accepted(trust) -> None:
    trust()

    assert client_key(_request(PROXY, "[2001:db8:7:7::9]")) == "2001:db8:7:7::/64"


def test_address_families_do_not_cross_match_trusted_networks(trust) -> None:
    trust(proxies="::ffff:10.0.0.0/104, fd00::/8")

    assert client_key(_request("fd00::1", "203.0.113.5")) == "203.0.113.5"
    assert client_key(_request("10.0.0.9", "203.0.113.5")) == "10.0.0.9"
