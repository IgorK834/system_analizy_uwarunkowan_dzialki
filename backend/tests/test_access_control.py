"""Kontrola dostępu: tokeny analiz i klucze administracyjne."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.access_control import make_analysis_token, verify_analysis_token
from app.core.settings import settings
from app.main import app

client = TestClient(app)


# --- Tokeny analiz -----------------------------------------------------------


def test_token_is_deterministic_and_bound_to_analysis_id() -> None:
    assert make_analysis_token(7) == make_analysis_token(7)
    assert make_analysis_token(7) != make_analysis_token(8)
    assert verify_analysis_token(7, make_analysis_token(7)) is True
    assert verify_analysis_token(8, make_analysis_token(7)) is False


@pytest.mark.parametrize("token", [None, "", "abc", "A" * 43, "zły-token", "\ud800"])
def test_invalid_token_is_rejected(token: str | None) -> None:
    assert verify_analysis_token(7, token) is False


def test_token_depends_on_configured_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import access_control

    monkeypatch.setattr(settings, "access_token_secret", "sekret-a")
    access_control._signing_key.cache_clear()
    first = make_analysis_token(1)
    monkeypatch.setattr(settings, "access_token_secret", "sekret-b")
    access_control._signing_key.cache_clear()
    second = make_analysis_token(1)
    monkeypatch.setattr(settings, "access_token_secret", "")
    access_control._signing_key.cache_clear()

    assert first != second


@pytest.mark.parametrize(
    "path", ["/report/{id}", "/analyze/{id}/pending-document"]
)
def test_analysis_resources_require_matching_token(path: str) -> None:
    url = path.format(id=5)

    assert client.get(url).status_code == 403
    assert client.get(url, params={"access_token": "zły-token"}).status_code == 403
    # Token innej analizy nie daje dostępu (nie da się zgadywać sąsiednich ID).
    other = make_analysis_token(6)
    assert client.get(url, params={"access_token": other}).status_code == 403


def test_forbidden_response_does_not_reveal_whether_analysis_exists() -> None:
    existing_style = client.get("/report/1")
    missing_style = client.get("/report/999999999")

    assert existing_style.status_code == missing_style.status_code == 403
    assert existing_style.json() == missing_style.json()


# --- Klucze administracyjne --------------------------------------------------


def _post(path: str, headers: dict[str, str] | None = None, **payload: str):
    body = {"reason": "test", **payload}
    return client.post(path, json=body, headers=headers or {})


@pytest.mark.parametrize("action", ["accept", "reject"])
def test_admin_endpoints_are_disabled_without_configured_keys(
    monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    monkeypatch.setattr(settings, "admin_api_keys", "")

    response = _post(f"/api/v1/raster-assets/1/{action}", {"X-Admin-Key": "cokolwiek"})

    assert response.status_code == 403


@pytest.mark.parametrize("action", ["accept", "reject"])
def test_admin_endpoints_reject_missing_or_wrong_key(
    monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    monkeypatch.setattr(settings, "admin_api_keys", "alice:klucz-alice")

    assert _post(f"/api/v1/raster-assets/1/{action}").status_code == 401
    wrong = _post(f"/api/v1/raster-assets/1/{action}", {"X-Admin-Key": "zly-klucz"})
    assert wrong.status_code == 401
    assert wrong.headers["WWW-Authenticate"] == "ApiKey"


def test_audit_operator_comes_from_key_not_from_request_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.routers import raster_admin

    monkeypatch.setattr(settings, "admin_api_keys", "alice:klucz-alice,bob:klucz-bob")
    seen: dict[str, object] = {}

    def fake_accept(repository, *, raster_asset_id, operator_id, reason):  # noqa: ANN001
        seen.update(operator_id=operator_id, reason=reason)
        return SimpleNamespace(
            raster_asset_id=raster_asset_id, review_status="verified", manual_review_id=9
        )

    monkeypatch.setattr(raster_admin, "accept_raster_asset", fake_accept)

    response = _post("/api/v1/raster-assets/3/accept", {"X-Admin-Key": "klucz-bob"})

    assert response.status_code == 200
    assert seen["operator_id"] == "bob"


def test_operator_id_in_body_must_match_authenticated_operator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "admin_api_keys", "alice:klucz-alice,bob:klucz-bob")

    forged = _post(
        "/api/v1/raster-assets/3/accept", {"X-Admin-Key": "klucz-bob"}, operator_id="alice"
    )

    assert forged.status_code == 403
