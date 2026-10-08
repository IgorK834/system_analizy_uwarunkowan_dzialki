"""Testy wyszukiwarki adresów (Faza 11.1): domena, aplikacja i API."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.location.api import router as router_module
from app.modules.location.api.schemas import SourceInfo
from app.modules.location.application.ports import AddressSearchProviderError
from app.modules.location.application.service import AddressSearchService
from app.modules.location.domain.models import (
    AddressParts,
    AddressQuery,
    BBox,
    GeoPoint,
    RawAddressCandidate,
    ResultType,
)
from app.modules.location.domain.normalization import (
    compute_match_ranges,
    fold_text,
    tokenize,
)
from app.modules.location.domain.ranking import (
    rank_candidates,
    stable_result_id,
)


# --- Domena: normalizacja ----------------------------------------------------


def test_fold_text_normalizes_polish_diacritics_length_preserving() -> None:
    assert fold_text("Łódź") == "lodz"
    assert fold_text("Gąska Ćma Żółw") == "gaska cma zolw"
    # Fałdowanie jest 1:1 — długość zachowana (klucz dla match_ranges).
    assert len(fold_text("Łódź")) == len("Łódź")


def test_tokenize_splits_on_non_alnum() -> None:
    assert tokenize("marki, andersa 1") == ["marki", "andersa", "1"]


def test_match_ranges_index_into_original_label() -> None:
    label = "Marki, Generała Władysława Andersa 1"
    ranges = compute_match_ranges(label, "andersa")
    assert ranges
    # Zakres wskazuje oryginalny (niefałdowany) fragment label.
    assert any(fold_text(label[r.start : r.end]) == "andersa" for r in ranges)


def test_match_ranges_preserve_diacritics_in_label() -> None:
    label = "Łódź, Główna 5"
    ranges = compute_match_ranges(label, "glowna")
    assert any(label[r.start : r.end] == "Główna" for r in ranges)


# --- Domena: ranking ---------------------------------------------------------


def _candidate(
    label: str, lon: float, lat: float, confidence: float = 0.5, number: str | None = None
) -> RawAddressCandidate:
    return RawAddressCandidate(
        label=label,
        point=GeoPoint(lon=lon, lat=lat),
        parts=AddressParts(country="Polska", city="Marki", house_number=number),
        result_type=ResultType.HOUSE_NUMBER if number else ResultType.CITY,
        provider_confidence=confidence,
    )


def test_stable_result_id_is_deterministic_hash_without_source_id() -> None:
    candidate = _candidate("Marki", 21.1, 52.3)
    assert stable_result_id(candidate) == stable_result_id(candidate)
    assert stable_result_id(candidate).startswith("hash:")


def test_stable_result_id_prefers_source_identifier() -> None:
    candidate = RawAddressCandidate(
        label="Marki",
        point=GeoPoint(21.1, 52.3),
        parts=AddressParts(),
        result_type=ResultType.CITY,
        provider_confidence=0.5,
        source_identifier="ABC123",
    )
    assert stable_result_id(candidate) == "uug:ABC123"


def test_bias_point_changes_ranking_deterministically() -> None:
    near = _candidate("Marki A", 21.10, 52.30)
    far = _candidate("Marki B", 22.50, 53.50)
    without_bias = rank_candidates([near, far], "marki", limit=10)
    with_bias = rank_candidates(
        [near, far], "marki", limit=10, bias=GeoPoint(21.10, 52.30)
    )
    # Bias faworyzuje bliższy wynik na pierwszej pozycji.
    assert with_bias[0].label == "Marki A"
    # Wynik jest powtarzalny.
    assert with_bias == rank_candidates(
        [near, far], "marki", limit=10, bias=GeoPoint(21.10, 52.30)
    )
    assert {r.label for r in without_bias} == {"Marki A", "Marki B"}


def test_bbox_changes_ranking_deterministically() -> None:
    inside = _candidate("Marki A", 21.10, 52.30)
    outside = _candidate("Marki B", 10.0, 48.0)
    bbox = BBox(min_lon=21.0, min_lat=52.0, max_lon=21.2, max_lat=52.5)
    ranked = rank_candidates([outside, inside], "marki", limit=10, bbox=bbox)
    assert ranked[0].label == "Marki A"


def test_rank_respects_limit() -> None:
    candidates = [_candidate(f"Marki {i}", 21.0 + i / 100, 52.0) for i in range(5)]
    ranked = rank_candidates(candidates, "marki", limit=3)
    assert len(ranked) == 3


# --- Aplikacja: serwis z atrapą dostawcy ------------------------------------


class _FakeProvider:
    def __init__(self, candidates: list[RawAddressCandidate]) -> None:
        self._candidates = candidates
        self.last_query: AddressQuery | None = None

    async def search(self, query: AddressQuery) -> list[RawAddressCandidate]:
        self.last_query = query
        return self._candidates


async def test_service_applies_type_filter() -> None:
    house = _candidate("Marki, Andersa 1", 21.1, 52.3, number="1")
    city = _candidate("Marki", 21.1, 52.3)
    service = AddressSearchService(_FakeProvider([house, city]))
    query = AddressQuery(q="marki", type_filter=frozenset({ResultType.HOUSE_NUMBER}))
    results = await service.search(query)
    assert [r.result_type for r in results] == [ResultType.HOUSE_NUMBER]


async def test_service_unique_ids_and_deterministic_order() -> None:
    candidates = [
        _candidate("Marki A", 21.10, 52.30, confidence=0.9),
        _candidate("Marki B", 21.20, 52.40, confidence=0.2),
    ]
    service = AddressSearchService(_FakeProvider(candidates))
    results = await service.search(AddressQuery(q="marki"))
    ids = [r.id for r in results]
    assert len(ids) == len(set(ids))
    assert results[0].label == "Marki A"  # wyższa pewność dostawcy


# --- API ---------------------------------------------------------------------


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _install_fake(monkeypatch, candidates: list[RawAddressCandidate]) -> _FakeProvider:
    provider = _FakeProvider(candidates)
    monkeypatch.setattr(
        router_module, "build_address_search_service", lambda: AddressSearchService(provider)
    )
    monkeypatch.setattr(
        router_module,
        "address_source_info",
        lambda: SourceInfo(source_id="emuia_uug", attribution="GUGiK / EMUiA"),
    )
    return provider


def test_openapi_exposes_address_search_endpoint(client) -> None:
    schema = client.get("/openapi.json").json()
    assert "/api/v1/search/addresses" in schema["paths"]
    operation = schema["paths"]["/api/v1/search/addresses"]["get"]
    # Kontrakt deklaruje odpowiedzi błędów przez ErrorResponse.
    assert "429" in operation["responses"]
    assert "503" in operation["responses"]


def test_api_returns_results_with_point_geojson_wgs84(client, monkeypatch) -> None:
    _install_fake(monkeypatch, [_candidate("Marki, Andersa 1", 21.10, 52.30, number="1")])
    response = client.get("/api/v1/search/addresses", params={"q": "marki andersa"})
    assert response.status_code == 200
    body = response.json()
    assert body["total_returned"] == 1
    result = body["results"][0]
    assert result["point"]["type"] == "Point"
    lon, lat = result["point"]["coordinates"]
    assert -180 <= lon <= 180 and -90 <= lat <= 90
    assert result["id"]
    assert result["source"]["source_id"] == "emuia_uug"


def test_api_unique_ids(client, monkeypatch) -> None:
    _install_fake(
        monkeypatch,
        [
            _candidate("Marki A", 21.10, 52.30),
            _candidate("Marki B", 21.20, 52.40),
        ],
    )
    response = client.get("/api/v1/search/addresses", params={"q": "marki"})
    ids = [r["id"] for r in response.json()["results"]]
    assert len(ids) == len(set(ids))


def test_api_limit_out_of_range_returns_error_response(client, monkeypatch) -> None:
    _install_fake(monkeypatch, [])
    response = client.get("/api/v1/search/addresses", params={"q": "marki", "limit": 21})
    assert response.status_code == 422
    assert response.json()["error"] == "VALIDATION_ERROR"


def test_api_match_ranges_map_to_label(client, monkeypatch) -> None:
    _install_fake(
        monkeypatch,
        [_candidate("Marki, Generała Władysława Andersa 1", 21.1, 52.3, number="1")],
    )
    response = client.get("/api/v1/search/addresses", params={"q": "andersa"})
    result = response.json()["results"][0]
    label = result["label"]
    assert result["match_ranges"]
    assert any(
        fold_text(label[r["start"] : r["end"]]) == "andersa"
        for r in result["match_ranges"]
    )


def test_api_bbox_changes_ranking(client, monkeypatch) -> None:
    _install_fake(
        monkeypatch,
        [
            _candidate("Marki B", 10.0, 48.0),
            _candidate("Marki A", 21.10, 52.30),
        ],
    )
    response = client.get(
        "/api/v1/search/addresses",
        params={"q": "marki", "bbox": "21.0,52.0,21.2,52.5"},
    )
    assert response.json()["results"][0]["label"] == "Marki A"


def test_api_invalid_bbox_returns_error_response(client, monkeypatch) -> None:
    _install_fake(monkeypatch, [])
    response = client.get(
        "/api/v1/search/addresses", params={"q": "marki", "bbox": "1,2,3"}
    )
    assert response.status_code == 422
    assert response.json()["error"] == "VALIDATION_ERROR"


def test_api_invalid_type_filter_returns_error_response(client, monkeypatch) -> None:
    _install_fake(monkeypatch, [])
    response = client.get(
        "/api/v1/search/addresses", params={"q": "marki", "types": "galaxy"}
    )
    assert response.status_code == 422
    assert response.json()["error"] == "VALIDATION_ERROR"


def test_api_non_numeric_bbox_returns_error_response(client, monkeypatch) -> None:
    _install_fake(monkeypatch, [])
    response = client.get(
        "/api/v1/search/addresses", params={"q": "marki", "bbox": "a,b,c,d"}
    )
    assert response.status_code == 422


def test_api_accepts_bias_and_type_filter(client, monkeypatch) -> None:
    provider = _install_fake(
        monkeypatch, [_candidate("Marki, Andersa 1", 21.1, 52.3, number="1")]
    )
    response = client.get(
        "/api/v1/search/addresses",
        params={
            "q": "marki",
            "bias_lon": 21.1,
            "bias_lat": 52.3,
            "types": "house_number,city",
            "limit": 5,
        },
    )
    assert response.status_code == 200
    assert provider.last_query is not None
    assert provider.last_query.bias is not None
    assert ResultType.HOUSE_NUMBER in provider.last_query.type_filter


def test_api_source_unavailable_returns_503_error_response(client, monkeypatch) -> None:
    class _FailingProvider:
        async def search(self, query: AddressQuery) -> list[RawAddressCandidate]:
            raise AddressSearchProviderError("boom")

    monkeypatch.setattr(
        router_module,
        "build_address_search_service",
        lambda: AddressSearchService(_FailingProvider()),
    )
    monkeypatch.setattr(
        router_module,
        "address_source_info",
        lambda: SourceInfo(source_id="emuia_uug", attribution="GUGiK / EMUiA"),
    )
    response = client.get("/api/v1/search/addresses", params={"q": "marki"})
    assert response.status_code == 503
    body = response.json()
    assert body["error"] == "SOURCE_UNAVAILABLE"
    # Brak stack trace / szczegółów technicznych.
    assert "boom" not in body["detail"]


# --- Cache i kompozycja ------------------------------------------------------


def test_bounded_ttl_cache_evicts_oldest() -> None:
    from app.modules.location.infrastructure.cache import BoundedTTLCache

    cache: BoundedTTLCache[int] = BoundedTTLCache(max_entries=2, ttl_seconds=60)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("c", 3)  # wypycha najstarszy "a"
    assert cache.get("a") is None
    assert cache.get("b") == 2
    assert cache.get("c") == 3
    assert len(cache) == 2
    cache.clear()
    assert len(cache) == 0


def test_bounded_ttl_cache_expires() -> None:
    from app.modules.location.infrastructure.cache import BoundedTTLCache

    cache: BoundedTTLCache[int] = BoundedTTLCache(max_entries=4, ttl_seconds=0.01)
    cache.set("a", 1)
    import time

    time.sleep(0.02)
    assert cache.get("a") is None


def test_bounded_ttl_cache_rejects_bad_config() -> None:
    from app.modules.location.infrastructure.cache import BoundedTTLCache

    with pytest.raises(ValueError):
        BoundedTTLCache(max_entries=0, ttl_seconds=1)
    with pytest.raises(ValueError):
        BoundedTTLCache(max_entries=1, ttl_seconds=0)


def test_composition_source_info_from_catalog() -> None:
    from app.core.data_sources import get_catalog
    from app.modules.location.composition import address_source_info

    get_catalog.cache_clear()
    info = address_source_info()
    assert info.source_id == "emuia_uug"
    assert info.attribution
    get_catalog.cache_clear()


def test_api_rate_limit_returns_429_with_retry_after(client, monkeypatch) -> None:
    _install_fake(monkeypatch, [])
    limiter = router_module._address_search_limit.limiter
    monkeypatch.setattr(limiter, "_limit", 1)
    limiter.reset()

    first = client.get("/api/v1/search/addresses", params={"q": "marki"})
    second = client.get("/api/v1/search/addresses", params={"q": "marki"})
    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"] == "RATE_LIMITED"
    assert int(second.headers["Retry-After"]) >= 1
