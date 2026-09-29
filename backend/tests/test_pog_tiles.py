"""Domena i przypadek użycia kafli MVT POG bez bazy danych (BK-401)."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from app.modules.planning.application.pog_tiles import (
    PogReleaseNotFoundError,
    PogTileLimits,
    PogTileService,
    PogTileTooLargeError,
)
from app.modules.planning.domain.pog_features import (
    POG_PARAMETER_NAMES,
    RAW_ATTRIBUTE_KEYS,
    float_parameter,
    normalize_zone_code,
    pog_feature_presentation,
    release_feature_attributes,
)
from app.modules.planning.domain.pog_tiles import (
    InvalidPogTileRequestError,
    PogReleaseInfo,
    PogTile,
    PogTileCandidate,
    PogTileRequest,
    tile_etag,
    tile_feature_properties,
)
from app.modules.planning.infrastructure.mvt import (
    InMemoryPogTileCache,
    _canonical_legal_status_sql,
)
from app.services.pog_analyzer import analyze_pog_vectors
from app.services.pog_fetch import PogVectorData, PogVectorFeature
from app.schemas.source import SourceMetadata
from shapely.geometry import box


def _candidate(**updates: object) -> PogTileCandidate:
    values: dict[str, object] = {
        "pk": 7,
        "feature_type": "planning_zone",
        "feature_identifier": "PL.X/1POG-1SJ",
        "feature_version": "v1",
        "symbol": "SJ",
        "label": "strefa wielofunkcyjna z zabudową mieszkaniową jednorodzinną",
        "parameters": {
            "max_overground_floor_area_ratio": 0.0,
            "max_building_height_m": None,
            "max_building_coverage_pct": 30.0,
            "min_biologically_active_pct": 45.5,
        },
        "primary_profiles": [
            {"code": "KPT-MPZP-MN", "label": "teren zabudowy", "dictionary_source": "https://s"}
        ],
        "additional_profiles": [],
        "raw_attributes": {"symbol": "SJ"},
        "act_identifier": "PL.X/1POG",
        "teryt": "1261011",
        "legal_status": "binding",
    }
    values.update(updates)
    return PogTileCandidate(**values)  # type: ignore[arg-type]


# --- Walidacja adresu kafla ----------------------------------------------------


@pytest.mark.parametrize(
    ("z", "x", "y", "edition"),
    [(19, 0, 0, "all"), (-1, 0, 0, "all"), (2, 4, 0, "all"), (2, 0, 4, "all"),
     (2, -1, 0, "all"), (2, 0, 0, "projekty")],
)
def test_request_validation_rejects_out_of_range(z: int, x: int, y: int, edition: str) -> None:
    with pytest.raises(InvalidPogTileRequestError):
        PogTileRequest(1, z, x, y, edition).validate(min_zoom=0, max_zoom=18)


def test_request_validation_rejects_non_positive_release() -> None:
    with pytest.raises(InvalidPogTileRequestError):
        PogTileRequest(0, 0, 0, 0).validate(min_zoom=0, max_zoom=18)


def test_request_accepts_last_tile_and_builds_cache_key() -> None:
    request = PogTileRequest(5, 3, 7, 7, "binding")
    request.validate(min_zoom=0, max_zoom=18)
    assert request.legal_statuses == ("binding",)
    assert request.cache_key == "pog-mvt/1|release=5|edition=binding|3/7/7"
    assert PogTileRequest(5, 3, 7, 7).legal_statuses is None


def test_etag_depends_on_key_and_content() -> None:
    assert tile_etag("a", b"x") == tile_etag("a", b"x")
    assert tile_etag("a", b"x") != tile_etag("b", b"x")
    assert tile_etag("a", b"x") != tile_etag("a", b"y")
    assert tile_etag("a", b"").startswith('"') and tile_etag("a", b"").endswith('"')


# --- Atrybuty kafla ------------------------------------------------------------


def test_zone_properties_keep_null_distinct_from_zero() -> None:
    props = tile_feature_properties(_candidate(), release_id=11)
    assert props["max_overground_floor_area_ratio"] == 0.0
    assert props["max_building_height_m"] is None
    assert props["max_building_coverage_pct"] == 30.0
    assert props["zone_code"] == "SJ"
    assert props["primary_profiles"] == "KPT-MPZP-MN"
    assert props["additional_profiles"] is None
    assert props["parameters_informational"] is None
    assert (props["layer"], props["pk"], props["data_release_id"]) == ("zones", 7, 11)


def test_area_properties_have_no_zone_parameters_and_fallback_id() -> None:
    props = tile_feature_properties(
        _candidate(feature_type="ouz", feature_identifier=None, raw_attributes={},
                   legal_status="outdated", label="x" * 500),
        release_id=3,
    )
    assert props["layer"] == "ouz"
    assert props["feature_id"] == "planning_feature:7"
    assert props["legal_status"] == "superseded"
    assert len(str(props["label"])) == 200
    assert not set(POG_PARAMETER_NAMES) & set(props)


def test_unknown_feature_type_is_rejected() -> None:
    with pytest.raises(ValueError):
        tile_feature_properties(_candidate(feature_type="line"), release_id=1)


def test_raw_attribute_subset_gives_same_presentation_as_full_record() -> None:
    raw = {
        "SYMBOL": "SU",
        "Maksymalna_Wysokosc_Zabudowy": "12,5",
        "maksymalny_udzial_powierzchni_zabudowy": {"value": "40"},
        "zrodlo_parametrow": "uzasadnienie PDF",
        "geometria": "470007 732582 " * 500,
        "inne_pole": "nie trafia do kafla",
    }
    subset = {key: value for key, value in raw.items() if key.lower() in RAW_ATTRIBUTE_KEYS}
    assert "geometria" not in subset and "inne_pole" not in subset

    def build(attributes: dict[str, object]) -> object:
        return pog_feature_presentation(
            release_feature_attributes(
                attributes, feature_identifier=None, feature_version=None, symbol=None,
                label=None, primary_profiles=None, additional_profiles=None, parameters=None,
            )
        )

    full, slim = build(raw), build(subset)
    assert full == slim
    assert (slim.zone_code, slim.max_building_height_m, slim.max_building_coverage_pct) == (
        "SU", 12.5, 40.0
    )
    assert slim.parameters_informational is True


def test_zone_code_and_parameter_normalization() -> None:
    assert normalize_zone_code("Strefa usługowa") == "SU"
    assert normalize_zone_code("strefa mieszkaniowa") == "unknown"
    assert normalize_zone_code(None) == "unknown"
    assert float_parameter({"x": "abc"}, "x") is None
    assert float_parameter({"x": None}, "x") is None
    assert pog_feature_presentation({}).parameter("max_building_height_m") is None
    with pytest.raises(KeyError):
        pog_feature_presentation({}).parameter("area")


def test_analysis_and_tile_share_parameter_extraction() -> None:
    """Analiza wektorów i atrybuty kafla dają te same liczby z tych samych atrybutów."""
    candidate = _candidate(
        parameters=None,
        raw_attributes={"maksymalna_nadziemna_intensywnosc_zabudowy": "0,8", "symbol": "SW"},
    )
    attributes = release_feature_attributes(
        candidate.raw_attributes, feature_identifier=candidate.feature_identifier,
        feature_version=candidate.feature_version, symbol=candidate.symbol,
        label=candidate.label, primary_profiles=candidate.primary_profiles,
        additional_profiles=candidate.additional_profiles, parameters=candidate.parameters,
    )
    source = SourceMetadata(source_name="test", confidence=1.0, manual_review_required=False)
    analysis = analyze_pog_vectors(
        box(0, 0, 10, 10),
        PogVectorData(
            planning_zones=[PogVectorFeature(box(0, 0, 10, 10), attributes, "EPSG:2180", "planning_zone")],
            ouz_areas=[], downtown_areas=[], app_metadata={}, status="available",
            wms_fallback_required=False, source_metadata=source, warnings=[],
        ),
    )
    zone = analysis.zones[0]
    props = tile_feature_properties(candidate, release_id=1)
    assert props["max_overground_floor_area_ratio"] == 0.8
    assert zone.parameters["max_overground_floor_area_ratio"] == "0,8"
    assert (props["zone_code"], props["feature_id"]) == (zone.zone_type.value, zone.zone_id)


def test_canonical_legal_status_sql_is_generated_from_shared_constants() -> None:
    sql = _canonical_legal_status_sql("c")
    assert "'binding', 'project', 'in_progress', 'superseded', 'unknown'" in sql
    assert "WHEN c = 'outdated' THEN 'superseded'" in sql


# --- Cache LRU -------------------------------------------------------------------


def _tile(content: bytes, key: str = "k") -> PogTile:
    return PogTile(content, tile_etag(key, content), 1, "MISS", 1, "all")


def test_cache_evicts_least_recently_used_by_bytes() -> None:
    cache = InMemoryPogTileCache(max_bytes=10)
    cache.put("a", _tile(b"12345"))
    cache.put("b", _tile(b"12345"))
    assert cache.get("a") is not None  # a staje się najświeższy
    cache.put("c", _tile(b"123"))
    assert cache.get("b") is None and cache.get("a") is not None and len(cache) == 2
    cache.put("a", _tile(b"1"))
    cache.put("big", _tile(b"x" * 11))
    assert cache.get("big") is None
    cache.clear()
    assert len(cache) == 0


# --- Przypadek użycia ------------------------------------------------------------


class FakeRepository:
    def __init__(self, candidates: list[PogTileCandidate], content: bytes = b"mvt") -> None:
        self.candidates = candidates
        self.content = content
        self.encoded: list[Sequence[dict[str, object]]] = []
        self.releases = {1: PogReleaseInfo(1, "pog_app", "v1", None, True, "sha", None, {})}

    def release_info(self, release_id: int) -> PogReleaseInfo | None:
        return self.releases.get(release_id)

    def active_release_id(self, source_id: str) -> int | None:
        return 1 if source_id == "pog_app" else None

    def tile_candidates(self, request: PogTileRequest, *, limit: int) -> list[PogTileCandidate]:
        return self.candidates[:limit]

    def encode_tile(self, request, features, *, boundary_limit: int) -> bytes:  # type: ignore[no-untyped-def]
        self.encoded.append(features)
        return self.content


def _service(repository: FakeRepository, **limits: int) -> PogTileService:
    values = {"min_zoom": 0, "max_zoom": 18, "max_features": 10, "max_bytes": 100}
    values.update(limits)
    return PogTileService(
        repository, InMemoryPogTileCache(1024), PogTileLimits(**values), source_id="pog_app"
    )


def test_service_caches_tiles_and_reports_hit() -> None:
    repository = FakeRepository([_candidate()])
    service = _service(repository)
    first = service.get_tile(PogTileRequest(1, 10, 1, 1))
    second = service.get_tile(PogTileRequest(1, 10, 1, 1))
    assert (first.cache_status, second.cache_status) == ("MISS", "HIT")
    assert first.etag == second.etag and len(repository.encoded) == 1
    assert repository.encoded[0][0]["zone_code"] == "SJ"


def test_service_enforces_feature_and_byte_limits() -> None:
    with pytest.raises(PogTileTooLargeError) as features:
        _service(FakeRepository([_candidate(), _candidate(pk=8)]), max_features=1).get_tile(
            PogTileRequest(1, 10, 1, 1)
        )
    assert features.value.limit == "features"
    with pytest.raises(PogTileTooLargeError) as size:
        _service(FakeRepository([], content=b"x" * 101)).get_tile(PogTileRequest(1, 10, 1, 1))
    assert size.value.limit == "bytes"


def test_service_release_lookup() -> None:
    service = _service(FakeRepository([]))
    assert service.active_release().release_id == 1
    with pytest.raises(PogReleaseNotFoundError):
        service.release(2)
    with pytest.raises(PogReleaseNotFoundError):
        service.get_tile(PogTileRequest(2, 1, 0, 0))
    inactive = PogTileService(
        FakeRepository([]), InMemoryPogTileCache(10),
        PogTileLimits(0, 18, 10, 100), source_id="other",
    )
    with pytest.raises(PogReleaseNotFoundError):
        inactive.active_release()


def test_every_official_zone_label_maps_to_its_code() -> None:
    """Etykiety słownika RodzajStrefyPlanistycznejKod (też z „ł”) dają kod strefy."""
    from app.core.pog_presentation import load_pog_presentation

    for zone in load_pog_presentation().presentation.zones:
        assert normalize_zone_code(zone.label) == zone.code
        assert normalize_zone_code(zone.code.lower()) == zone.code
