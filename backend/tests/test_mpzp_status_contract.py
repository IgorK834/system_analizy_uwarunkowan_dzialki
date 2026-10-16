"""Kontrakt ``mpzp_acts[]`` i ``mpzp_status`` w ``AnalyzeResponse`` (AU-101, AU-103)."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from app.modules.planning.domain import mpzp_acts as acts_domain
from app.modules.planning.domain import mpzp_status as status_domain
from app.schemas import analyze as schemas
from app.schemas.analyze import AnalyzeResponse, MpzpActResult, MpzpStatus
from app.services import cache
from app.services.section_quality import build_section_quality
from app.shared.mpzp_status_text import MPZP_STATUS_VALUES
from tests.report_map_reference import load_fixture


def test_contract_versions_follow_the_three_tasks() -> None:
    assert schemas.MPZP_RESULT_SCHEMA_VERSION == "3.0"
    assert f"mpzp-v{schemas.MPZP_RESULT_SCHEMA_VERSION}" in cache.RESULT_CONTRACT_VERSION
    assert len(cache.RESULT_CONTRACT_VERSION) <= 224  # analyses.result_contract_version (String(256) od migracji 036)
    assert schemas.MPZP_ACTS_SCHEMA_VERSION == acts_domain.MPZP_ACTS_SCHEMA_VERSION
    assert schemas.MPZP_STATUS_SCHEMA_VERSION == status_domain.MPZP_STATUS_SCHEMA_VERSION
    assert {"mpzp_acts", "mpzp_status"} <= set(AnalyzeResponse.model_fields)


def test_status_values_match_the_shared_vocabulary_and_are_disjoint() -> None:
    literal = set(MpzpStatus.model_fields["status"].annotation.__args__)  # type: ignore[union-attr]
    assert literal == set(MPZP_STATUS_VALUES) and len(MPZP_STATUS_VALUES) == 6


def test_act_share_is_bounded_and_null_is_allowed() -> None:
    base = {"legal_status": "binding", "precedence": "applies"}
    assert MpzpActResult(intersection_pct=None, **base).intersection_pct is None
    assert MpzpActResult(intersection_pct=0.0, intersection_area_sqm=0.0, **base).intersection_pct == 0.0
    with pytest.raises(ValidationError):
        MpzpActResult(intersection_pct=100.5, **base)
    with pytest.raises(ValidationError):
        MpzpActResult(intersection_area_sqm=-1.0, **base)


def test_default_response_has_no_state_and_no_acts_meaning_a_snapshot_before_the_change() -> None:
    response, _ = load_fixture("multizone")
    assert response.mpzp_acts == [] and response.mpzp_status is None


def test_only_https_links_of_an_act_are_marked_clickable() -> None:
    act = MpzpActResult(
        document_url="https://example.gov.pl/uchwala.pdf",
        legend_url="http://example.gov.pl/legenda.pdf",
        drawing_url="javascript:alert(1)",
    )
    assert act.verified_links == ["document_url"]


def test_quality_matrix_uses_the_state_source_instead_of_missing_source() -> None:
    from tests.test_mpzp_acts_service import _discovery, _status
    from app.services import mpzp_acts as service

    response, _ = load_fixture("multizone")
    for discovery_status, expected_quality, expected_source in (
        ("unavailable", "unavailable", "kimpzp"),
        ("no_coverage", "no_coverage", "kimpzp"),
        ("no_match", "unknown", "kimpzp"),
    ):
        resolution = service.resolve_mpzp_acts(
            _discovery([], status=discovery_status), service.RuChannelResult(queried=False), as_of=date(2026, 10, 10)
        )
        updated = response.model_copy(
            update={"mpzp_zones": [], "mpzp_status": _status(resolution), "section_quality": None, "mpzp_discovery": None}
        )
        mpzp = next(item for item in build_section_quality(updated).sections if item.section == "mpzp")
        assert mpzp.status == expected_quality, discovery_status
        assert mpzp.source_id == expected_source, discovery_status
        assert mpzp.source_name is not None
    ambiguous = service.resolve_mpzp_acts(
        _discovery([_a("A/1/2020"), _a("B/2/2021")]), service.RuChannelResult(queried=False), as_of=date(2026, 10, 10)
    )
    updated = response.model_copy(
        update={"mpzp_zones": [], "mpzp_status": _status(ambiguous), "section_quality": None, "mpzp_discovery": None}
    )
    mpzp = next(item for item in build_section_quality(updated).sections if item.section == "mpzp")
    assert mpzp.status == "partial" and mpzp.manual_review_required is True
    assert "MPZP_ACTS_UNRESOLVED" in mpzp.reason_codes and "MPZP_ACT_WITHOUT_ZONE" in mpzp.reason_codes


def _a(number: str):  # noqa: ANN202
    from tests.test_mpzp_acts_service import _kimpzp_act

    return _kimpzp_act(number)
