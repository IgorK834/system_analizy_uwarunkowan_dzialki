"""Tokeny dostępu v2 (AU-012): wygasanie, rotacja kluczy ``kid``, linki, nagłówki i maskowanie logów."""

from __future__ import annotations

import io
import logging
import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from uvicorn.logging import AccessFormatter

from app.core import access_control
from app.core.access_control import (
    ANALYSIS_ACCESS_DENIED_DETAIL,
    check_analysis_token,
    ensure_analysis_access,
    issue_link_token,
    make_analysis_token,
    verify_analysis_token,
)
from app.core.logging import (
    ACCESS_TOKEN_MASK,
    UVICORN_ACCESS_LOGGER_NAME,
    AccessTokenMaskFilter,
    install_access_log_mask,
    mask_access_tokens,
)
from app.core.response_headers import SENSITIVE_RESPONSE_HEADERS, is_sensitive_path
from app.core.settings import settings
from app.db.session import get_db
from app.main import app

NOW = 1_800_000_000
DAY = 24 * 3600
TOKEN_FORMAT = re.compile(r"^v2\.(\d+)\.([A-Za-z0-9_-]{1,32})\.([A-Za-z0-9_-]{43})$")

client = TestClient(app)


class Clock:
    def __init__(self, start: float = NOW) -> None:
        self.value = float(start)

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    fake = Clock()
    monkeypatch.setattr(access_control, "_now", fake)
    return fake


@pytest.fixture
def configure_keys(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., None]]:
    def configure(secrets: str = "", legacy: str = "") -> None:
        monkeypatch.setattr(settings, "access_token_secrets", secrets)
        monkeypatch.setattr(settings, "access_token_secret", legacy)
        access_control.reset_key_cache()

    configure("k1:sekret-jeden")
    yield configure
    monkeypatch.undo()
    access_control.reset_key_cache()


# --- Format i wygasanie ------------------------------------------------------


def test_token_has_the_v2_format_with_expiry_and_active_kid(
    clock: Clock, configure_keys: Callable[..., None]
) -> None:
    match = TOKEN_FORMAT.fullmatch(make_analysis_token(7))

    assert match is not None
    assert int(match.group(1)) == NOW + settings.access_token_ttl_seconds
    assert match.group(2) == "k1"


def test_default_lifetimes_are_30_days_for_share_and_15_minutes_for_downloads() -> None:
    assert settings.access_token_ttl_seconds == 30 * DAY
    assert settings.access_token_download_ttl_seconds == 15 * 60


def test_token_is_valid_until_exp_and_rejected_from_exp_on(
    clock: Clock, configure_keys: Callable[..., None]
) -> None:
    token = make_analysis_token(7, ttl_seconds=60)

    assert check_analysis_token(7, token).reason == "ok"
    clock.advance(59)
    assert verify_analysis_token(7, token) is True
    clock.advance(1)
    verdict = check_analysis_token(7, token)
    assert (verdict.valid, verdict.reason) == (False, "expired")


def test_expired_token_gets_403_on_every_protected_resource(
    clock: Clock, configure_keys: Callable[..., None]
) -> None:
    token = make_analysis_token(5, ttl_seconds=60)
    clock.advance(61)

    for path in ("/report/5", "/report/5/audit.zip", "/analyze/5/pending-document"):
        by_query = client.get(path, params={"access_token": token})
        by_header = client.get(path, headers={"X-Analysis-Token": token})
        for response in (by_query, by_header):
            assert response.status_code == 403, path
            assert response.json()["detail"] == ANALYSIS_ACCESS_DENIED_DETAIL
    resumed = client.post(
        "/analyze/resume",
        json={"analysis_id": 5, "zone_symbol": "MN"},
        headers={"X-Analysis-Token": token},
    )
    assert resumed.status_code == 403


def test_expired_and_missing_tokens_look_identical_to_the_client(
    clock: Clock, configure_keys: Callable[..., None]
) -> None:
    token = make_analysis_token(5, ttl_seconds=1)
    clock.advance(5)

    def body(response) -> dict:  # noqa: ANN001
        return {k: v for k, v in response.json().items() if k != "request_id"}

    assert body(client.get("/report/5", params={"access_token": token})) == body(client.get("/report/5"))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: ["v2", str(int(p[1]) + 1), p[2], p[3]],  # przedłużony termin
        lambda p: ["v2", p[1], "k9", p[3]],  # nieznany kid
        lambda p: ["v2", p[1], p[2], p[3][:-1] + ("A" if p[3][-1] != "A" else "B")],  # zmieniony podpis
        lambda p: ["v1", p[1], p[2], p[3]],  # inna wersja
        lambda p: ["v2", p[1], p[2]],  # brak segmentu
        lambda p: ["v2", "-5", p[2], p[3]],  # ujemny termin
        lambda p: ["v2", "9" * 40, p[2], p[3]],  # absurdalnie długi termin
        lambda p: ["v2", p[1], "kid z spacją", p[3]],
    ],
)
def test_tampered_tokens_are_rejected(
    mutate, clock: Clock, configure_keys: Callable[..., None]
) -> None:
    parts = make_analysis_token(7).split(".")

    assert verify_analysis_token(7, ".".join(mutate(parts))) is False


def test_token_is_bound_to_the_analysis_id(clock: Clock, configure_keys: Callable[..., None]) -> None:
    assert verify_analysis_token(8, make_analysis_token(7)) is False
    assert check_analysis_token(8, make_analysis_token(7)).reason == "bad_signature"


def test_legacy_v1_tokens_without_expiry_are_no_longer_accepted(
    clock: Clock, configure_keys: Callable[..., None]
) -> None:
    import base64
    import hashlib
    import hmac

    digest = hmac.new(b"sekret-jeden", b"analysis-access:v1:7", hashlib.sha256).digest()
    legacy = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    assert check_analysis_token(7, legacy).reason == "malformed"


def test_ensure_analysis_access_returns_the_longest_validity(
    clock: Clock, configure_keys: Callable[..., None]
) -> None:
    short = make_analysis_token(7, ttl_seconds=60)
    long = make_analysis_token(7, ttl_seconds=3600)

    assert ensure_analysis_access(7, short, long, "zły") == NOW + 3600


# --- Rotacja kluczy ----------------------------------------------------------


def test_rotation_keeps_old_links_valid_during_the_transition_and_drops_them_after(
    clock: Clock, configure_keys: Callable[..., None]
) -> None:
    configure_keys("old:sekret-stary")
    old_token = make_analysis_token(7, ttl_seconds=90 * DAY)
    assert TOKEN_FORMAT.fullmatch(old_token).group(2) == "old"  # type: ignore[union-attr]

    # Rotacja: nowy klucz aktywny, stary tylko do weryfikacji — do końca dnia 2026-12-01 (UTC).
    configure_keys("new:sekret-nowy,old:sekret-stary|2026-12-01")
    retirement = datetime(2026, 12, 2, tzinfo=UTC).timestamp()
    clock.value = retirement - 3600

    assert verify_analysis_token(7, old_token) is True  # okres przejściowy
    fresh = make_analysis_token(7)
    assert TOKEN_FORMAT.fullmatch(fresh).group(2) == "new"  # type: ignore[union-attr]
    assert verify_analysis_token(7, fresh) is True

    clock.value = retirement  # koniec okresu przejściowego
    verdict = check_analysis_token(7, old_token)
    assert (verdict.valid, verdict.reason) == (False, "key_retired")
    assert verify_analysis_token(7, make_analysis_token(7)) is True  # nowe tokeny działają dalej


def test_old_tokens_stay_valid_until_the_old_key_is_removed_from_the_list(
    clock: Clock, configure_keys: Callable[..., None]
) -> None:
    configure_keys("old:sekret-stary")
    old_token = make_analysis_token(7)

    configure_keys("new:sekret-nowy,old:sekret-stary")
    assert verify_analysis_token(7, old_token) is True

    configure_keys("new:sekret-nowy")
    verdict = check_analysis_token(7, old_token)
    assert (verdict.valid, verdict.reason) == (False, "unknown_kid")


def test_retired_key_never_signs_new_tokens(clock: Clock, configure_keys: Callable[..., None]) -> None:
    configure_keys("new:sekret-nowy,old:sekret-stary")

    for _ in range(3):
        assert TOKEN_FORMAT.fullmatch(make_analysis_token(7)).group(2) == "new"  # type: ignore[union-attr]


def test_a_kid_cannot_be_verified_with_another_keys_secret(
    clock: Clock, configure_keys: Callable[..., None]
) -> None:
    configure_keys("a:sekret-a,b:sekret-b")
    token = make_analysis_token(7)  # kid=a
    forged = token.replace(".a.", ".b.")

    assert verify_analysis_token(7, forged) is False


def test_single_legacy_secret_still_works_as_the_default_kid(
    clock: Clock, configure_keys: Callable[..., None]
) -> None:
    configure_keys(secrets="", legacy="stary-pojedynczy-sekret")

    token = make_analysis_token(7)

    assert TOKEN_FORMAT.fullmatch(token).group(2) == "default"  # type: ignore[union-attr]
    assert verify_analysis_token(7, token) is True


def test_without_any_secret_a_process_key_is_used(
    clock: Clock, configure_keys: Callable[..., None], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="app.core.access_control"):
        configure_keys(secrets="", legacy="")
        token = make_analysis_token(7)

    assert TOKEN_FORMAT.fullmatch(token).group(2) == "ephemeral"  # type: ignore[union-attr]
    assert verify_analysis_token(7, token) is True
    assert "ACCESS_TOKEN_SECRETS" in caplog.text


# --- Linki: POST /analyze/{id}/links ----------------------------------------


class _FakeSession:
    """Sesja z jedną znaną analizą (id=5); ``db.get`` jest jedynym odczytem endpointu."""

    def get(self, model, identifier):  # noqa: ANN001, ANN201
        return object() if identifier == 5 else None


@pytest.fixture
def fake_db() -> Iterator[None]:
    app.dependency_overrides[get_db] = lambda: _FakeSession()
    yield
    app.dependency_overrides.pop(get_db, None)


def test_links_require_a_token_and_do_not_reveal_whether_the_analysis_exists(
    clock: Clock, configure_keys: Callable[..., None], fake_db: None
) -> None:
    existing = client.post("/analyze/5/links")
    missing = client.post("/analyze/999999/links")
    wrong = client.post("/analyze/5/links", headers={"X-Analysis-Token": make_analysis_token(6)})

    assert existing.status_code == missing.status_code == wrong.status_code == 403
    assert existing.json()["detail"] == missing.json()["detail"] == ANALYSIS_ACCESS_DENIED_DETAIL


def test_links_default_to_a_15_minute_download_token(
    clock: Clock, configure_keys: Callable[..., None], fake_db: None
) -> None:
    response = client.post("/analyze/5/links", headers={"X-Analysis-Token": make_analysis_token(5)})

    assert response.status_code == 200
    body = response.json()
    assert body["purpose"] == "download"
    assert body["expires_in_seconds"] == 15 * 60
    assert datetime.fromisoformat(body["expires_at"]) == datetime.fromtimestamp(NOW + 900, tz=UTC)
    assert body["report_url"] == f"/report/5?access_token={body['access_token']}"
    assert body["audit_package_url"] == f"/report/5/audit.zip?access_token={body['access_token']}"
    assert verify_analysis_token(5, body["access_token"]) is True
    assert verify_analysis_token(6, body["access_token"]) is False
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_share_links_last_30_days_and_the_token_works_in_the_url(
    clock: Clock, configure_keys: Callable[..., None], fake_db: None
) -> None:
    response = client.post(
        "/analyze/5/links?access_token=" + make_analysis_token(5),
        json={"purpose": "share"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["purpose"] == "share"
    assert body["expires_in_seconds"] == 30 * DAY
    # Sprawdzenie dostępu wykonuje się przed odczytem bazy: 404 oznacza, że token przeszedł.
    gone = client.get(body["report_url"].replace("/report/5", "/report/999999999"))
    assert gone.status_code == 403  # token jest związany z analizą 5, nie z 999999999
    clock.advance(30 * DAY + 1)
    assert client.get(body["report_url"]).status_code == 403


def test_a_link_never_outlives_the_token_it_was_issued_from(
    clock: Clock, configure_keys: Callable[..., None], fake_db: None
) -> None:
    short = make_analysis_token(5, ttl_seconds=120)

    response = client.post(
        "/analyze/5/links", json={"purpose": "share"}, headers={"X-Analysis-Token": short}
    )

    assert response.status_code == 200
    assert response.json()["expires_in_seconds"] == 120  # nie 30 dni
    clock.advance(121)
    assert client.post("/analyze/5/links", headers={"X-Analysis-Token": short}).status_code == 403


def test_links_for_an_unknown_analysis_return_404_only_after_a_valid_token(
    clock: Clock, configure_keys: Callable[..., None], fake_db: None
) -> None:
    response = client.post("/analyze/77/links", headers={"X-Analysis-Token": make_analysis_token(77)})

    assert response.status_code == 404


def test_links_reject_an_unknown_purpose(
    clock: Clock, configure_keys: Callable[..., None], fake_db: None
) -> None:
    response = client.post(
        "/analyze/5/links", json={"purpose": "forever"}, headers={"X-Analysis-Token": make_analysis_token(5)}
    )

    assert response.status_code == 422


def test_issue_link_token_is_capped_by_the_presented_expiry(
    clock: Clock, configure_keys: Callable[..., None]
) -> None:
    token, exp = issue_link_token(5, "share", presented_expires_at=NOW + 10)

    assert exp == NOW + 10
    assert check_analysis_token(5, token).expires_at == NOW + 10


# --- Nagłówki ochronne -------------------------------------------------------


def test_sensitive_path_matcher() -> None:
    assert is_sensitive_path("/analyze")
    assert is_sensitive_path("/analyze/resume")
    assert is_sensitive_path("/analyze/5/links")
    assert is_sensitive_path("/report/5")
    assert is_sensitive_path("/report/5/audit.zip")
    assert not is_sensitive_path("/health")
    assert not is_sensitive_path("/analyzer")
    assert not is_sensitive_path("/map/tiles/1")


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/report/5"),
        ("get", "/report/5/audit.zip"),
        ("get", "/analyze/5/pending-document"),
        ("post", "/analyze/5/links"),
        ("post", "/analyze/resume"),
        ("post", "/analyze"),
    ],
)
def test_report_and_token_responses_carry_protective_headers_even_on_errors(
    method: str, path: str
) -> None:
    response = getattr(client, method)(path)

    assert response.status_code in {403, 422}
    for name, value in SENSITIVE_RESPONSE_HEADERS.items():
        assert response.headers[name] == value, (path, name)


def test_other_endpoints_are_not_forced_to_no_store() -> None:
    response = client.get("/health")

    assert response.headers.get("cache-control") != "private, no-store"


# --- Maskowanie tokenu w logu dostępu uvicorna ------------------------------


@pytest.fixture
def access_log(monkeypatch: pytest.MonkeyPatch) -> Iterator[io.StringIO]:
    install_access_log_mask()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    # Formatter uvicorna rozpakowuje argumenty (adres, metoda, ścieżka, wersja HTTP, status).
    handler.setFormatter(
        AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False)
    )
    logger = logging.getLogger(UVICORN_ACCESS_LOGGER_NAME)
    monkeypatch.setattr(logger, "level", logging.INFO)
    logger.addHandler(handler)
    yield stream
    logger.removeHandler(handler)


def test_uvicorn_access_log_masks_the_access_token(access_log: io.StringIO) -> None:
    secret = "v2.1900000000.k1.SEKRETNYPODPISSEKRETNYPODPISSEKRETNYPODPISAB"
    logging.getLogger(UVICORN_ACCESS_LOGGER_NAME).info(
        '%s - "%s %s HTTP/%s" %d',
        "127.0.0.1:5000",
        "GET",
        f"/report/1?x=1&access_token={secret}&y=2",
        "1.1",
        200,
    )

    line = access_log.getvalue()
    assert secret not in line and "SEKRETNY" not in line
    assert f"GET /report/1?x=1&access_token={ACCESS_TOKEN_MASK}&y=2 HTTP/1.1" in line
    assert line.rstrip().endswith("200 OK")


def test_access_log_mask_also_handles_encoded_names_and_is_idempotent(access_log: io.StringIO) -> None:
    install_access_log_mask()
    install_access_log_mask()
    logger = logging.getLogger(UVICORN_ACCESS_LOGGER_NAME)
    assert sum(isinstance(f, AccessTokenMaskFilter) for f in logger.filters) == 1

    logger.info('%s - "%s %s HTTP/%s" %d', "c", "GET", "/report/1?Access%5Ftoken=TAJNE", "1.1", 200)

    assert "TAJNE" not in access_log.getvalue()


def test_app_startup_installs_the_access_log_mask() -> None:
    logger = logging.getLogger(UVICORN_ACCESS_LOGGER_NAME)

    assert any(isinstance(item, AccessTokenMaskFilter) for item in logger.filters)


def test_mask_leaves_other_parameters_and_plain_text_untouched() -> None:
    assert mask_access_tokens("/report/1?page=2&sort=asc") == "/report/1?page=2&sort=asc"
    assert mask_access_tokens("brak query-stringu") == "brak query-stringu"
    assert mask_access_tokens("/r?token=abc") == "/r?token=abc"  # inne parametry maskuje redakcja sekretów
