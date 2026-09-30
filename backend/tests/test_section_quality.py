"""Macierz kompletności i świeżości sekcji (BK-504): kontrakty, zegar, hash, katalog.

Testy używają zamrożonych fixtures raportu (bez bazy) i jawnych punktów
odniesienia czasu — żaden nie czyta bieżącego zegara.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
import yaml

from app.core.data_sources import (
    CatalogFileError,
    CatalogValidationError,
    DataSourceCatalog,
    load_catalog,
    parse_catalog,
)
from app.modules.reporting.domain.sections import QUALITY_SECTIONS
from app.schemas.analyze import (
    AnalyzeResponse,
    RiskSectionResult,
    TerrainReliefResult,
    TerrainResult,
    UtilitiesPreviewResult,
)
from app.schemas.source import SectionQualityMatrix, SourceMetadata
from app.services import section_quality as quality_module
from app.services.section_quality import (
    NO_CATALOG_POLICY_VERSION,
    build_section_quality,
    quality_from_snapshot,
    quality_to_snapshot,
    with_section_quality,
)
from tests.repo_structure import find_repo_root
from tests.report_map_reference import load_fixture

CATALOG_PATH = find_repo_root() / "docs" / "data_sources" / "catalog.yaml"
POLICY_7D = {
    "max_age_days": 7,
    "basis": "project_decision",
    "rationale": "Reguła testowa: decyzja projektowa dla usługi na żywo.",
}


def _catalog(**policies: dict[str, Any] | None) -> DataSourceCatalog:
    """Prawdziwy katalog z podmienionymi regułami świeżości (tylko podane źródła)."""
    data = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8"))
    for entry in data["sources"]:
        entry.pop("freshness_policy", None)
        policy = policies.get(entry["source_id"])
        if policy is not None:
            entry["freshness_policy"] = policy
    return parse_catalog(data)


def _response(name: str = "multizone") -> AnalyzeResponse:
    response, _ = load_fixture(name)
    return response.model_copy(update={"section_quality": None})


def _by_key(matrix: SectionQualityMatrix) -> dict[str, Any]:
    return {item.section: item for item in matrix.sections}


def _shift_sources(response: AnalyzeResponse, fetched_at: datetime | None) -> AnalyzeResponse:
    """Wszystkie źródła sekcji dostają ten sam czas pobrania."""
    def moved(source: SourceMetadata | None) -> SourceMetadata | None:
        return source.model_copy(update={"fetched_at": fetched_at}) if source else None

    assert response.parcel and response.terrain and response.utilities_preview and response.pog
    return response.model_copy(
        update={
            "parcel": response.parcel.model_copy(update={"source": moved(response.parcel.source)}),
            "terrain": response.terrain.model_copy(update={"source": moved(response.terrain.source)}),
            "utilities_preview": response.utilities_preview.model_copy(
                update={"source": moved(response.utilities_preview.source)}
            ),
            "pog": response.pog.model_copy(update={"source": moved(response.pog.source)}),
            "mpzp_zones": [
                zone.model_copy(update={"source": moved(zone.source)}) for zone in response.mpzp_zones
            ],
            "risk_sections": [
                item.model_copy(update={"source": moved(item.source)}) for item in response.risk_sections
            ],
        }
    )


# --- Kontrakt: każda sekcja ma status, źródło albo powód, czas i flagę manual -----------------


@pytest.mark.parametrize("fixture", ["multizone", "project", "long_tables"])
def test_every_section_has_status_source_or_reason_and_manual_flag(fixture: str) -> None:
    matrix = build_section_quality(_response(fixture), catalog=_catalog(isok=POLICY_7D))
    assert [item.section for item in matrix.sections] == [item.key for item in QUALITY_SECTIONS]
    for item in matrix.sections:
        assert item.status
        assert isinstance(item.manual_review_required, bool)
        assert item.source_id or item.source_name or item.reason_codes, item.section
        assert item.policy_version == matrix.policy_version
        assert item.freshness.reference_at == matrix.reference_at
        assert (item.fetched_at is None) == (item.source_name is None) or item.source_name


def test_empty_response_has_every_section_with_explicit_reasons() -> None:
    response = _response().model_copy(
        update={
            "parcel": None, "mpzp_zones": [], "pog": None, "risks": [], "risk_sections": [],
            "terrain": None, "utilities_preview": None, "infrastructure": [], "sources": [],
        }
    )
    matrix = build_section_quality(response, catalog=_catalog())
    sections = _by_key(matrix)
    assert len(sections) == len(QUALITY_SECTIONS)
    for key, item in sections.items():
        assert item.source_id is None and item.source_name is None, key
        assert item.reason_codes, key  # powód braku źródła
        assert item.freshness.state == "unknown"
        assert item.freshness.reason_code == "FRESHNESS_NO_SOURCE"
        assert item.status in {"unknown", "out_of_scope"}, key
    assert sections["parcel"].reason_codes == ["PARCEL_GEOMETRY_MISSING"]
    assert sections["transport"].status == "out_of_scope"
    assert sections["transport"].reason_codes == ["NO_SOURCE_CONTRACT"]
    assert sections["flood"].status == "unknown" and sections["nature"].status == "unknown"
    assert sections["terrain"].status == "unknown"


def test_reason_is_mandatory_when_source_is_missing_in_every_path() -> None:
    """Niezmiennik: sekcja bez źródła zawsze nosi kod powodu (także w nietypowych ścieżkach)."""
    base = _response()
    cases = [
        base.model_copy(update={"manual_zone_required": True, "mpzp_zones": []}),
        base.model_copy(update={"pog": None}),
        base.model_copy(update={"utilities_preview": None}),
        base.model_copy(update={"terrain": None}),
    ]
    for response in cases:
        for item in build_section_quality(response, catalog=_catalog()).sections:
            if item.source_name is None:
                assert item.reason_codes, (item.section, response.manual_zone_required)


# --- Świeżość na zamrożonym zegarze -----------------------------------------------------------


def test_fresh_stale_future_invalid_and_no_policy_on_frozen_clock() -> None:
    response = _response()
    analyzed = response.analyzed_at
    catalog = _catalog(isok=POLICY_7D, gdos=POLICY_7D, nmt=POLICY_7D)

    fresh = _by_key(build_section_quality(response, catalog=catalog))
    assert fresh["flood"].freshness.state == "fresh"
    assert fresh["flood"].freshness.age_seconds == 60
    assert fresh["flood"].freshness.max_age_days == 7
    assert fresh["flood"].freshness.basis == "project_decision"
    assert "FRESHNESS_OLDER_THAN_POLICY" not in fresh["flood"].reason_codes
    # Źródło bez reguły: świeżość nieustalona, ale wiek zmierzony.
    assert fresh["parcel"].freshness.state == "unknown"
    assert fresh["parcel"].freshness.reason_code == "FRESHNESS_NO_POLICY"
    assert fresh["parcel"].freshness.age_seconds == 60

    old = _by_key(build_section_quality(response, reference_at=analyzed + timedelta(days=30), catalog=catalog))
    assert old["flood"].freshness.state == "stale"
    assert old["flood"].freshness.reason_code == "FRESHNESS_OLDER_THAN_POLICY"
    assert "FRESHNESS_OLDER_THAN_POLICY" in old["flood"].reason_codes
    assert old["terrain"].freshness.state == "stale"
    # Status kontraktowy nie zależy od świeżości — stary pomiar nie jest błędem.
    assert old["flood"].status == fresh["flood"].status == "available"
    # Brak reguły nie daje „stale” nawet po 30 dniach: nie ma globalnego TTL.
    assert old["parcel"].freshness.state == "unknown"
    assert old["parcel"].freshness.age_seconds == 30 * 86_400 + 60

    future = _shift_sources(response, analyzed + timedelta(hours=1))
    future_matrix = _by_key(build_section_quality(future, catalog=catalog))
    assert future_matrix["flood"].freshness.state == "unknown"
    assert future_matrix["flood"].freshness.reason_code == "FRESHNESS_FUTURE_TIME"
    assert "FRESHNESS_FUTURE_TIME" in future_matrix["flood"].reason_codes
    assert future_matrix["flood"].freshness.age_seconds is None

    naive = _shift_sources(response, datetime(2026, 9, 20, 9, 29))
    invalid = _by_key(build_section_quality(naive, catalog=catalog))
    assert invalid["terrain"].freshness.reason_code == "FRESHNESS_INVALID_TIME"
    assert "FRESHNESS_INVALID_TIME" in invalid["terrain"].reason_codes

    missing = _by_key(build_section_quality(_shift_sources(response, None), catalog=catalog))
    assert missing["terrain"].freshness.reason_code == "FRESHNESS_NO_FETCH_TIME"
    assert missing["terrain"].fetched_at is None


def test_no_global_ttl_only_sources_with_rules_can_become_stale() -> None:
    response = _response()
    analyzed = response.analyzed_at
    only_isok = _catalog(isok=POLICY_7D)
    matrix = _by_key(build_section_quality(response, reference_at=analyzed + timedelta(days=900), catalog=only_isok))
    assert matrix["flood"].freshness.state == "stale"
    for key in ("parcel", "mpzp", "pog", "nature", "terrain", "utilities"):
        assert matrix[key].freshness.state == "unknown", key
        assert matrix[key].freshness.reason_code == "FRESHNESS_NO_POLICY", key
        assert matrix[key].freshness.max_age_days is None, key


def test_reference_point_is_analysis_time_by_default_and_naive_reference_is_utc() -> None:
    response = _response()
    matrix = build_section_quality(response, catalog=_catalog())
    assert matrix.reference_at == response.analyzed_at
    naive = build_section_quality(response, reference_at=datetime(2026, 9, 20, 9, 30), catalog=_catalog())
    assert naive.reference_at == datetime(2026, 9, 20, 9, 30, tzinfo=timezone.utc)


def test_missing_catalog_degrades_to_unknown_freshness_without_failing(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> DataSourceCatalog:
        raise CatalogFileError("brak pliku")

    monkeypatch.setattr(quality_module, "get_catalog", broken)
    matrix = build_section_quality(_response())
    assert matrix.policy_version == NO_CATALOG_POLICY_VERSION
    assert {item.freshness.state for item in matrix.sections} == {"unknown"}
    assert _by_key(matrix)["flood"].freshness.reason_code == "FRESHNESS_NO_POLICY"


# --- Katalog: polityka świeżości i redystrybucji -----------------------------------------------


def test_real_catalog_has_explicit_per_source_rules_and_never_for_unknown_interval() -> None:
    catalog = load_catalog(CATALOG_PATH)
    with_rules = {e.source_id: e.freshness_policy for e in catalog.sources if e.freshness_policy}
    assert set(with_rules) == {"isok", "gdos", "nmt", "nmt_wcs"}
    for source_id, policy in with_rules.items():
        assert policy.rationale and policy.basis == "project_decision", source_id
    for entry in catalog.sources:
        if entry.expected_update_interval.strip().casefold() == "unknown":
            assert entry.freshness_policy is None, entry.source_id
    # Źródła bez reguły nie dostają TTL.
    for source_id in ("uldk", "kimpzp", "pog_app", "mpzp_ru", "kiut_wms", "egib", "kiut_gesut"):
        assert catalog.freshness_rule(source_id) is None
    assert catalog.freshness_rule(None) is None
    assert catalog.freshness_rule("nieistnieje") is None
    assert catalog.freshness_rule("isok").max_age_days == 7  # type: ignore[union-attr]


def test_policy_version_changes_with_any_rule_and_is_stable_otherwise() -> None:
    base = _catalog(isok=POLICY_7D)
    assert base.quality_policy_version() == _catalog(isok=POLICY_7D).quality_policy_version()
    assert base.quality_policy_version().startswith("quality-policy/1+")
    changed = _catalog(isok={**POLICY_7D, "max_age_days": 8})
    assert changed.quality_policy_version() != base.quality_policy_version()
    assert _catalog().quality_policy_version() != base.quality_policy_version()


def _catalog_data() -> dict[str, Any]:
    return copy.deepcopy(yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8")))


def _entry(data: dict[str, Any], source_id: str) -> dict[str, Any]:
    return next(item for item in data["sources"] if item["source_id"] == source_id)


def test_catalog_rejects_invented_ttl_for_unknown_interval() -> None:
    data = _catalog_data()
    _entry(data, "egib")["freshness_policy"] = POLICY_7D  # expected_update_interval: unknown
    with pytest.raises(CatalogValidationError, match="wymyślonego TTL"):
        parse_catalog(data)


def test_catalog_rejects_declared_interval_basis_without_declared_interval() -> None:
    data = _catalog_data()
    _entry(data, "isok")["freshness_policy"] = {**POLICY_7D, "basis": "source_declared_interval"}
    with pytest.raises(CatalogValidationError, match="zadeklarowanej częstotliwości"):
        parse_catalog(data)


def test_catalog_accepts_declared_interval_basis_when_interval_is_declared() -> None:
    data = _catalog_data()
    entry = _entry(data, "mpzp_pilot_krakow")  # expected_update_interval: daily
    entry["freshness_policy"] = {
        "max_age_days": 1,
        "basis": "source_declared_interval",
        "rationale": "Źródło deklaruje aktualizację dzienną (daily).",
    }
    catalog = parse_catalog(data)
    rule = catalog.freshness_rule("mpzp_pilot_krakow")
    assert rule is not None and rule.max_age_days == 1 and rule.basis == "source_declared_interval"


@pytest.mark.parametrize("field", ["max_age_days", "basis", "rationale"])
def test_catalog_policy_requires_all_fields(field: str) -> None:
    data = _catalog_data()
    policy = dict(POLICY_7D)
    policy.pop(field)
    _entry(data, "isok")["freshness_policy"] = policy
    with pytest.raises(CatalogValidationError):
        parse_catalog(data)


def test_catalog_redistribution_rules() -> None:
    catalog = load_catalog(CATALOG_PATH)
    assert catalog.redistribution_of("egib") == "forbidden"  # wynika ze statusu
    assert catalog.redistribution_of("pog_app") == "derived_only"
    assert catalog.redistribution_of("isok") == "allowed"
    assert catalog.redistribution_of("kiut_gesut") == "unconfirmed"
    assert catalog.redistribution_of("nieistnieje") == "unconfirmed"
    assert catalog.redistribution_of(None) == "unconfirmed"

    data = _catalog_data()
    _entry(data, "egib")["redistribution"] = "allowed"
    with pytest.raises(CatalogValidationError, match="no_redistribution"):
        parse_catalog(data)
    data = _catalog_data()
    _entry(data, "kiut_gesut")["redistribution"] = "allowed"  # contract_required
    with pytest.raises(CatalogValidationError, match="kontraktu produkcyjnego"):
        parse_catalog(data)
    data = _catalog_data()
    _entry(data, "egib").pop("redistribution", None)
    assert parse_catalog(data).redistribution_of("egib") == "forbidden"


# --- Status według kontraktu źródła ----------------------------------------------------------------


def _with(response: AnalyzeResponse, **update: Any) -> dict[str, Any]:
    return _by_key(build_section_quality(response.model_copy(update=update), catalog=_catalog()))


def test_risk_sections_distinguish_unavailable_error_and_unknown() -> None:
    response = _response()
    sections = [
        RiskSectionResult(section="flood", status="unavailable", reason_code="SERVICE_TIMEOUT",
                          relation="unknown", source=SourceMetadata(
                              source_id="isok", source_name="ISOK", confidence=0.0,
                              manual_review_required=True, fetched_at=response.analyzed_at)),
        RiskSectionResult(section="nature", status="error", reason_code="UNEXPECTED_ERROR", relation="unknown"),
    ]
    matrix = _with(response, risk_sections=sections)
    assert matrix["flood"].status == "unavailable"
    assert matrix["flood"].reason_codes[0] == "SERVICE_TIMEOUT"
    assert matrix["flood"].manual_review_required is True  # źródło próby wymaga weryfikacji
    assert matrix["nature"].status == "error"
    assert matrix["nature"].reason_codes == ["UNEXPECTED_ERROR"]

    only_flood = _with(response, risk_sections=[sections[0]])
    assert only_flood["nature"].status == "unknown"  # brak zapisu = unknown, nie „brak ryzyka”
    assert only_flood["nature"].reason_codes == ["LEGACY_SNAPSHOT"]


def test_unknown_risk_without_reason_gets_explicit_code() -> None:
    section = RiskSectionResult(section="flood", status="unknown", relation="unknown")
    matrix = _with(_response(), risk_sections=[section])
    assert matrix["flood"].reason_codes == ["RISK_SECTION_NOT_RECORDED"]


def test_terrain_no_coverage_is_not_an_error_and_relief_failure_is_partial() -> None:
    response = _response()
    source = SourceMetadata(source_id="nmt", source_name="NMT", confidence=0.9,
                            manual_review_required=False, fetched_at=response.analyzed_at)
    no_coverage = _with(response, terrain=TerrainResult(status="no_coverage", reason_code="NO_COVERAGE_SENTINEL", source=source))
    assert no_coverage["terrain"].status == "no_coverage"
    assert no_coverage["terrain"].reason_codes == ["NO_COVERAGE_SENTINEL"]
    unavailable = _with(response, terrain=TerrainResult(status="unavailable", reason_code="SERVICE_TIMEOUT", source=source))
    assert unavailable["terrain"].status == "unavailable"
    assert no_coverage["terrain"].status != unavailable["terrain"].status

    available = TerrainResult(status="available", min_height_m=100.0, max_height_m=103.0,
                              height_difference_m=3.0, source=source)
    assert _with(response, terrain=available)["terrain"].status == "available"
    relief = TerrainReliefResult.model_construct(status="unavailable", reason_code="RASTER_TOO_LARGE")
    partial = _with(response, terrain=available.model_copy(update={"relief": relief}))
    assert partial["terrain"].status == "partial"
    assert partial["terrain"].reason_codes == ["TERRAIN_RELIEF_NOT_AVAILABLE", "RASTER_TOO_LARGE"]


def test_terrain_snapshot_missing_is_unknown_with_legacy_reason() -> None:
    matrix = _with(_response(), terrain=None)
    assert matrix["terrain"].status == "unknown"
    assert matrix["terrain"].reason_codes == ["LEGACY_SNAPSHOT"]


def test_utilities_status_follows_the_kiut_preview_contract() -> None:
    response = _response()
    preview = response.utilities_preview
    assert preview is not None
    covered = _with(response)["utilities"]
    assert covered.status == "partial"  # podgląd nie jest geometrią sieci
    assert covered.reason_codes == ["KIUT_PREVIEW_ONLY"]
    assert covered.source_id == "kiut_wms"

    not_covered = preview.model_copy(update={"coverage_status": "not_covered", "layer_available": False})
    assert _with(response, utilities_preview=not_covered)["utilities"].status == "no_coverage"
    unknown = preview.model_copy(update={"coverage_status": "unknown", "layer_available": False})
    assert _with(response, utilities_preview=unknown)["utilities"].status == "unknown"
    none = _with(response, utilities_preview=None)["utilities"]
    assert (none.status, none.reason_codes) == ("unknown", ["KIUT_COVERAGE_NOT_CHECKED"])
    assert isinstance(UtilitiesPreviewResult, type)


@pytest.mark.parametrize(
    ("coverage", "availability", "status", "code"),
    [
        ("available", "current", "available", None),
        ("no_act_confirmed", "current", "available", "POG_NO_ACT_CONFIRMED"),
        ("partial", "current", "partial", "POG_COVERAGE_PARTIAL"),
        ("act_without_spatial_data", "current", "partial", "POG_ACT_WITHOUT_SPATIAL_DATA"),
        ("available", "stale", "partial", "POG_DATA_STALE"),
        ("unknown", "current", "unknown", "POG_COVERAGE_UNKNOWN"),
        ("available", "unavailable", "unavailable", "POG_SOURCE_UNAVAILABLE"),
    ],
)
def test_pog_status_follows_coverage_and_availability_contract(
    coverage: str, availability: str, status: str, code: str | None
) -> None:
    response = _response()
    assert response.pog is not None
    pog = response.pog.model_copy(update={"coverage_status": coverage, "data_availability": availability})
    matrix = _with(response, pog=pog)
    for key in ("pog", "pog_overlays"):
        assert matrix[key].status == status
        assert matrix[key].reason_codes[:1] == ([code] if code else [])


def test_pog_missing_is_unknown_and_review_flag_is_orthogonal_to_status() -> None:
    response = _response()
    missing = _with(response, pog=None)
    assert missing["pog"].status == "unknown" and missing["pog"].reason_codes == ["POG_RESULT_MISSING"]
    assert missing["mpzp_pog_relation"].status == "unknown"
    assert response.pog is not None
    review = _with(response, pog=response.pog.model_copy(update={"manual_review_required": True}))
    assert review["pog"].status == "available"
    assert review["pog"].manual_review_required is True
    assert "POG_REVIEW_REQUIRED" in review["pog"].reason_codes


def test_mpzp_status_variants() -> None:
    response = _response()
    zone = response.mpzp_zones[0].model_copy(
        update={"parameters": [], "manual_review_required": False,
                "source": response.mpzp_zones[0].source.model_copy(update={"manual_review_required": False})}
    )
    clean = _with(response, mpzp_zones=[zone])["mpzp"]
    assert (clean.status, clean.manual_review_required, clean.reason_codes) == ("available", False, [])

    review = _with(response, mpzp_zones=[zone.model_copy(update={"manual_review_required": True})])["mpzp"]
    assert review.status == "available"
    assert review.manual_review_required is True
    assert review.reason_codes == ["MPZP_REVIEW_REQUIRED"]

    manual = _with(response, mpzp_zones=[zone.model_copy(update={"assignment_method": "manual_user_input"})])["mpzp"]
    assert manual.status == "partial" and "MPZP_MANUAL_ZONE" in manual.reason_codes
    candidate = _with(response, mpzp_zones=[zone.model_copy(update={"assignment_method": "document_candidate"})])["mpzp"]
    assert candidate.status == "partial" and candidate.reason_codes == ["MPZP_DOCUMENT_CANDIDATE"]
    legacy = _with(response, mpzp_zones=[zone.model_copy(update={"assignment_method": "legacy"})])["mpzp"]
    assert legacy.status == "partial" and legacy.reason_codes == ["MPZP_LEGACY_SNAPSHOT"]
    share = _with(response, mpzp_zones=[zone.model_copy(update={"intersection_pct": None, "touches_boundary": False})])["mpzp"]
    assert share.status == "partial" and share.reason_codes == ["MPZP_SHARE_UNDETERMINED"]

    conflict = _with(response)["mpzp"]  # fixture zawiera sprzeczność parametrów
    assert conflict.status == "partial" and "MPZP_PARAMETER_CONFLICT" in conflict.reason_codes
    assert conflict.manual_review_required is True

    waiting = _with(response, mpzp_zones=[], manual_zone_required=True)["mpzp"]
    assert (waiting.status, waiting.manual_review_required) == ("awaiting_input", True)
    assert waiting.reason_codes == ["MPZP_MANUAL_ZONE_REQUIRED"]
    none = _with(response, mpzp_zones=[])["mpzp"]
    assert (none.status, none.reason_codes) == ("unknown", ["MPZP_NOT_DETERMINED"])


def test_parcel_cache_placeholder_source_is_not_treated_as_a_source() -> None:
    response = _response()
    assert response.parcel is not None
    placeholder = SourceMetadata(source_name="cache", confidence=0.0, manual_review_required=True,
                                 fetched_at=response.analyzed_at)
    matrix = _with(response, parcel=response.parcel.model_copy(update={"source": placeholder}))
    parcel = matrix["parcel"]
    assert parcel.source_id is None and parcel.source_name is None and parcel.fetched_at is None
    assert parcel.manual_review_required is True
    assert parcel.reason_codes == ["SOURCE_RECORD_MISSING"]


def test_repaired_geometry_is_flagged_without_changing_status() -> None:
    response = _response()
    assert response.parcel is not None
    metrics = response.parcel.metrics.model_copy(update={"geometry_repaired": True})
    parcel = _with(response, parcel=response.parcel.model_copy(update={"metrics": metrics}))["parcel"]
    assert parcel.status == "available"
    assert parcel.reason_codes == ["GEOMETRY_REPAIRED"]


def test_relation_section_is_derived_and_carries_review_flag() -> None:
    response = _response()
    assert response.pog is not None
    from app.schemas.analyze import CompatibilityAssessment

    assessment = CompatibilityAssessment(
        status="unknown", reason_code="NO_RULE_MATCH", aggregation="najsłabsze ogniwo",
        rationale="brak reguły", informational_notice="informacyjnie", manual_review_required=True,
    )
    relation = _with(response, pog=response.pog.model_copy(update={"compatibility_assessment": assessment}))[
        "mpzp_pog_relation"
    ]
    assert relation.status == "unknown"
    assert relation.manual_review_required is True
    assert relation.reason_codes == ["NO_RULE_MATCH", "COMPATIBILITY_REVIEW_REQUIRED", "DERIVED_SECTION"]
    assert relation.source_id is None


# --- Identyfikator źródła ---------------------------------------------------------------------------


def test_source_resolution_explicit_legacy_and_unresolved() -> None:
    response = _response()
    matrix = _by_key(build_section_quality(response, catalog=_catalog()))
    assert matrix["parcel"].source_id == "uldk"
    assert matrix["pog"].source_id == "pog_app"
    assert matrix["flood"].source_id == "isok"
    assert matrix["nature"].source_id == "gdos"
    assert matrix["terrain"].source_id == "nmt"
    assert matrix["mpzp"].source_id == "mpzp_ru"
    assert matrix["pog"].data_release_id == 12

    assert response.parcel is not None
    legacy = SourceMetadata(source_name="ULDK", confidence=1.0, manual_review_required=False,
                            fetched_at=response.analyzed_at)
    resolved = _with(response, parcel=response.parcel.model_copy(update={"source": legacy}))["parcel"]
    assert resolved.source_id == "uldk" and resolved.reason_codes == []

    odd = legacy.model_copy(update={"source_name": "Geoportal XYZ"})
    unresolved = _with(response, parcel=response.parcel.model_copy(update={"source": odd}))["parcel"]
    assert unresolved.source_id is None and unresolved.source_name == "Geoportal XYZ"
    assert unresolved.reason_codes == ["SOURCE_ID_UNRESOLVED"]

    unknown_id = legacy.model_copy(update={"source_id": "nie_ma_w_katalogu"})
    outside = _with(response, parcel=response.parcel.model_copy(update={"source": unknown_id}))["parcel"]
    assert outside.source_id == "nie_ma_w_katalogu"
    assert outside.reason_codes == ["SOURCE_NOT_IN_CATALOG"]


def test_manual_user_source_needs_no_catalog_id() -> None:
    response = _response()
    manual = response.mpzp_zones[0].source.model_copy(
        update={"source_id": None, "source_name": "manual_user_input", "manual_review_required": True}
    )
    zone = response.mpzp_zones[0].model_copy(update={"source": manual, "assignment_method": "manual_user_input"})
    item = _with(response, mpzp_zones=[zone])["mpzp"]
    assert item.source_id is None and item.source_name == "manual_user_input"
    assert "SOURCE_ID_UNRESOLVED" not in item.reason_codes


# --- Hash, zapis, odczyt ----------------------------------------------------------------------------


def test_matrix_hash_is_stable_across_roundtrip_and_timezone_representation() -> None:
    response = _response()
    matrix = build_section_quality(response, catalog=_catalog(isok=POLICY_7D))
    assert matrix.integrity_ok()
    assert len(matrix.matrix_sha256) == 64

    restored = SectionQualityMatrix.model_validate(quality_to_snapshot(matrix))
    assert restored.matrix_sha256 == matrix.matrix_sha256
    assert restored.integrity_ok()

    warsaw = timezone(timedelta(hours=2))
    moved = matrix.model_copy(update={"reference_at": matrix.reference_at.astimezone(warsaw)})
    assert moved.compute_sha256() == matrix.matrix_sha256  # ten sam moment = ten sam hash


def test_matrix_hash_detects_any_change_and_origin_is_not_content() -> None:
    matrix = build_section_quality(_response(), catalog=_catalog())
    tampered_section = matrix.sections[1].model_copy(update={"status": "available"})
    tampered = matrix.model_copy(update={"sections": [matrix.sections[0], tampered_section, *matrix.sections[2:]]})
    assert not tampered.integrity_ok()
    assert matrix.model_copy(update={"origin": "reconstructed"}).integrity_ok()
    assert not matrix.model_copy(update={"policy_version": "quality-policy/1+inny"}).integrity_ok()


def test_snapshot_excludes_legend_and_legend_lists_reasons_present() -> None:
    matrix = build_section_quality(_response(), catalog=_catalog())
    snapshot = quality_to_snapshot(matrix)
    assert "legend" not in snapshot
    assert snapshot["matrix_sha256"] == matrix.matrix_sha256
    legend = matrix.legend
    assert [item.id for item in legend.statuses][:3] == ["available", "partial", "no_coverage"]
    assert {item.id for item in legend.freshness} == {"fresh", "stale", "unknown"}
    codes = {item.code for item in legend.reasons}
    assert {"MPZP_PARAMETER_CONFLICT", "NO_SOURCE_CONTRACT", "FRESHNESS_NO_POLICY"} <= codes
    assert all(item.label for item in legend.reasons)


def test_with_section_quality_keeps_existing_matrix_and_adds_missing() -> None:
    response = _response()
    added = with_section_quality(response, catalog=_catalog())
    assert added.section_quality is not None
    assert with_section_quality(added, catalog=_catalog(isok=POLICY_7D)) is added


def test_stored_matrix_is_read_back_and_legacy_row_is_reconstructed_not_stored() -> None:
    response = _response()
    stored = build_section_quality(response, catalog=_catalog(isok=POLICY_7D))
    snapshot = quality_to_snapshot(stored)
    read = quality_from_snapshot(snapshot, response, response.analyzed_at + timedelta(days=90))
    assert read == SectionQualityMatrix.model_validate(snapshot)
    assert read.origin == "stored"
    assert read.reference_at == stored.reference_at  # odczyt po 90 dniach nie zmienia oceny

    legacy = quality_from_snapshot(None, response, response.analyzed_at)
    assert legacy.origin == "reconstructed"
    assert all("LEGACY_QUALITY_RECONSTRUCTED" in item.reason_codes for item in legacy.sections)
    assert legacy.integrity_ok()


def test_every_reason_code_the_builder_can_emit_has_a_polish_label() -> None:
    """Kod bez etykiety trafiłby do legendy jako „kod źródła: X” — jawnie wychwytujemy to w CI."""
    import re
    from pathlib import Path

    from app.shared.data_quality import QUALITY_REASON_LABELS_PL

    source = Path(quality_module.__file__).read_text(encoding="utf-8")
    emitted = set(re.findall(r'"([A-Z][A-Z0-9_]{5,})"', source))
    missing = sorted(code for code in emitted if code not in QUALITY_REASON_LABELS_PL)
    assert missing == [], f"brak etykiet PL dla kodów: {missing}"
    from app.shared import data_quality

    freshness_codes = {name for name in dir(data_quality) if name.startswith("FRESHNESS_") and name.isupper()}
    for name in freshness_codes:
        code = getattr(data_quality, name)
        if isinstance(code, str) and code.startswith("FRESHNESS_"):
            assert code in QUALITY_REASON_LABELS_PL, code


@pytest.mark.parametrize(
    ("status", "code", "section_status"),
    [
        ("unknown", "POG_SOURCE_MISSING", "unknown"),
        ("unknown", "POG_STATUS_UNKNOWN", "unknown"),
        ("unknown", "MPZP_ZONES_MISSING", "unknown"),
        ("unknown", "POG_ZONES_MISSING", "unknown"),
        ("unknown", "NO_SPATIAL_PAIRS", "unknown"),
        ("not_applicable", "POG_PROJECT_NOT_BINDING", "available"),
        ("not_applicable", "POG_PROCEDURE_IN_PROGRESS", "available"),
        ("not_applicable", "POG_SUPERSEDED", "available"),
        ("not_applicable", "POG_NO_ACT_CONFIRMED", "available"),
    ],
)
def test_relation_decision_paths_carry_a_labelled_reason(status: str, code: str, section_status: str) -> None:
    from app.schemas.analyze import CompatibilityAssessment
    from app.shared.data_quality import QUALITY_REASON_LABELS_PL

    response = _response()
    assert response.pog is not None
    assessment = CompatibilityAssessment(
        status=status, reason_code=code, aggregation="najsłabsze ogniwo", rationale="uzasadnienie",
        informational_notice="informacyjnie", manual_review_required=False,
    )
    relation = _with(response, pog=response.pog.model_copy(update={"compatibility_assessment": assessment}))[
        "mpzp_pog_relation"
    ]
    assert relation.status == section_status
    assert relation.reason_codes == [code, "DERIVED_SECTION"]
    assert code in QUALITY_REASON_LABELS_PL


def test_compatible_relation_needs_no_reason_besides_being_derived() -> None:
    from app.schemas.analyze import CompatibilityAssessment

    response = _response()
    assert response.pog is not None
    assessment = CompatibilityAssessment.model_construct(
        status="compatible", reason_code="PAIRS_EVALUATED", manual_review_required=False
    )
    relation = _with(response, pog=response.pog.model_copy(update={"compatibility_assessment": assessment}))[
        "mpzp_pog_relation"
    ]
    assert (relation.status, relation.reason_codes) == ("available", ["DERIVED_SECTION"])
