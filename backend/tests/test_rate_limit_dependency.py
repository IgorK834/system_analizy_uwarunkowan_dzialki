"""Limity zapytań dla kosztownych endpointów (zależności FastAPI)."""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core.access_control import make_analysis_token
from app.core.rate_limit import rate_limit, rate_limit_refresh
from app.core.settings import settings


def _app(limit: int = 2, refresh_limit: int = 1) -> TestClient:
    app = FastAPI()

    @app.get("/limited", dependencies=[Depends(rate_limit(limit))])
    async def limited() -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/refresh", dependencies=[Depends(rate_limit_refresh(refresh_limit))])
    async def refresh(force_refresh: bool = False) -> dict[str, bool]:
        return {"force_refresh": force_refresh}

    return TestClient(app)


def test_requests_over_limit_get_429_with_retry_after() -> None:
    client = _app(limit=2)

    assert client.get("/limited").status_code == 200
    assert client.get("/limited").status_code == 200
    blocked = client.get("/limited")

    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1


def test_clients_have_independent_counters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "rate_limit_trust_forwarded_for", True)
    client = _app(limit=1)

    assert client.get("/limited", headers={"X-Forwarded-For": "10.0.0.1"}).status_code == 200
    assert client.get("/limited", headers={"X-Forwarded-For": "10.0.0.1"}).status_code == 429
    assert client.get("/limited", headers={"X-Forwarded-For": "10.0.0.2"}).status_code == 200


def test_forwarded_for_uses_last_entry_and_cannot_be_spoofed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "rate_limit_trust_forwarded_for", True)
    client = _app(limit=1)

    # Klient dopisuje własne wpisy z przodu; zaufany proxy dodaje ostatni.
    first = client.get("/limited", headers={"X-Forwarded-For": "1.1.1.1, 10.0.0.9"})
    second = client.get("/limited", headers={"X-Forwarded-For": "2.2.2.2, 10.0.0.9"})

    assert first.status_code == 200
    assert second.status_code == 429


def test_forwarded_for_is_ignored_unless_trusted() -> None:
    client = _app(limit=1)

    assert client.get("/limited", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
    assert client.get("/limited", headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 429


def test_refresh_limit_counts_only_force_refresh_requests() -> None:
    client = _app(refresh_limit=1)

    for _ in range(5):
        assert client.get("/refresh").status_code == 200
    assert client.get("/refresh?force_refresh=true").status_code == 200
    assert client.get("/refresh?force_refresh=true").status_code == 429
    assert client.get("/refresh").status_code == 200


def test_limits_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    client = _app(limit=1)

    assert all(client.get("/limited").status_code == 200 for _ in range(5))


def test_report_endpoint_is_rate_limited() -> None:
    from app.main import app

    client = TestClient(app)
    token = make_analysis_token(999999999)
    codes = [
        client.get(f"/report/999999999?access_token={token}").status_code
        for _ in range(31)
    ]

    assert codes[0] == 404
    assert codes[-1] == 429
    assert 429 not in codes[: settings.rate_limit_report_per_minute]
