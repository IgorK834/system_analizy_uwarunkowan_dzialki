"""Każdy endpoint publiczny ma limit per klient, a przekroczenie daje 429 z ``Retry-After`` (AU-006, R4).

Lista tras pochodzi z OpenAPI aplikacji, więc nowy endpoint bez limitera (albo router
niewłączony do testu) wywraca test, zamiast po cichu zostać bez limitu.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.rate_limit import FixedWindowRateLimiter
from app.core.settings import settings
from app.main import app

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
_PATH_PARAM = re.compile(r"\{[^}]+\}")
_PATH_CONVERTER = re.compile(r"\{(\w+):\w+\}")


def _routers() -> list:
    return [value for name, value in vars(main_module).items() if name.endswith("_router")]


def _routes() -> dict[tuple[str, str], APIRoute]:
    found: dict[tuple[str, str], APIRoute] = {}
    for router in _routers():
        for route in router.routes:
            if isinstance(route, APIRoute):
                for method in route.methods:
                    # OpenAPI zapisuje ``{id:path}`` jako ``{id}``.
                    path = _PATH_CONVERTER.sub(r"{\1}", route.path)
                    found[(method.lower(), path)] = route
    return found


def _limiters(route: APIRoute) -> list[FixedWindowRateLimiter]:
    pending = list(route.dependant.dependencies)
    limiters: list[FixedWindowRateLimiter] = []
    while pending:
        dependant = pending.pop()
        limiter = getattr(dependant.call, "limiter", None)
        if isinstance(limiter, FixedWindowRateLimiter):
            limiters.append(limiter)
        pending.extend(dependant.dependencies)
    return limiters


def _openapi_operations() -> list[tuple[str, str]]:
    return sorted(
        (method, path)
        for path, item in app.openapi()["paths"].items()
        for method in item
        if method in HTTP_METHODS
    )


OPERATIONS = _openapi_operations()


def test_every_openapi_operation_belongs_to_a_known_router() -> None:
    routes = _routes()

    missing = [operation for operation in OPERATIONS if operation not in routes]

    assert missing == [], f"trasy bez routera w main.py (nie da się sprawdzić limitu): {missing}"


@pytest.mark.parametrize(("method", "path"), OPERATIONS, ids=[f"{m.upper()} {p}" for m, p in OPERATIONS])
def test_every_public_operation_has_a_rate_limiter(method: str, path: str) -> None:
    route = _routes()[(method, path)]

    assert _limiters(route), f"{method.upper()} {path} nie ma limitu zapytań"


@pytest.fixture
def client() -> Iterator[TestClient]:
    # Bez ``with``: nie uruchamiamy lifespan (zamykanie klienta WMS) przy każdym teście.
    yield TestClient(app)


@pytest.mark.parametrize(("method", "path"), OPERATIONS, ids=[f"{m.upper()} {p}" for m, p in OPERATIONS])
def test_exceeding_the_limit_returns_429_with_retry_after(
    method: str, path: str, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    for limiter in _limiters(_routes()[(method, path)]):
        monkeypatch.setattr(limiter, "_limit", 1)
        limiter.reset()
    url = _PATH_PARAM.sub("1", path)
    kwargs = {"json": {}} if method in {"post", "put", "patch"} else {}

    # Zależność limitera działa przed walidacją parametrów, więc pierwsze żądanie (z byle jakimi
    # argumentami) zużywa budżet, a drugie dostaje 429 — bez wołania usług zewnętrznych.
    first = client.request(method.upper(), url, **kwargs)
    second = client.request(method.upper(), url, **kwargs)

    assert first.status_code != 429, first.text
    assert second.status_code == 429, second.text
    assert int(second.headers["Retry-After"]) >= 1
    assert second.json()["error"] == "RATE_LIMITED"


def test_tile_endpoints_have_a_higher_threshold_than_the_general_ones() -> None:
    routes = _routes()
    tile_paths = [
        "/api/v1/map/tiles/{source}/{z}/{x}/{y}.png",
        "/api/v1/map/pog/releases/{release_id}/{z}/{x}/{y}.mvt",
    ]

    for path in tile_paths:
        (limiter,) = _limiters(routes[("get", path)])
        assert limiter._limit == settings.rate_limit_tiles_per_minute
        assert limiter._limit > settings.rate_limit_analyze_per_minute

    (geocode,) = _limiters(routes[("get", "/geocode/suggest")])
    assert geocode._limit == settings.rate_limit_geocode_per_minute
    (search,) = _limiters(routes[("get", "/api/v1/search/addresses")])
    assert search._limit == settings.rate_limit_address_search_per_minute


def test_limits_apply_per_client_not_globally(monkeypatch: pytest.MonkeyPatch) -> None:
    (limiter,) = _limiters(_routes()[("get", "/geocode/suggest")])
    monkeypatch.setattr(limiter, "_limit", 1)
    limiter.reset()

    first = TestClient(app, client=("198.51.100.1", 50000))
    second = TestClient(app, client=("198.51.100.2", 50000))

    assert first.get("/geocode/suggest").status_code != 429
    assert first.get("/geocode/suggest").status_code == 429
    assert second.get("/geocode/suggest").status_code != 429
