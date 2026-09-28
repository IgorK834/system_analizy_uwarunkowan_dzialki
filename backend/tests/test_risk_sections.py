"""Strukturalne wyniki powodzi i ochrony przyrody (BK-303) — adaptery i kontrakt.

Scenariusze obowiązkowe: brak dopasowania, przecięcie, sama granica, dwie
nakładające się formy ochrony i niedostępność obu źródeł — każdy ze statusem
i provenance sekcji niezależnym od listy obiektów.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from pydantic import ValidationError
from shapely.geometry import box

from app.schemas.analyze import RiskResult, RiskSectionResult
from app.schemas.source import SourceMetadata
from app.services.context import ContextSectionResult, _finalize_section
from app.services.gdos import (
    GdosServiceUnavailableError,
    fetch_nature_protection_section,
)
from app.services.isok import (
    IsokServiceUnavailableError,
    _return_period_years,
    fetch_flood_risk_section,
)
from app.services.risks import (
    LEGACY_RISK_WARNING,
    build_risk_section,
    flood_description,
    flood_risk_result,
    nature_description,
    nature_risk_result,
    risk_sections_from_snapshot,
)
from tests.risk_fixtures import (
    EMPTY_COLLECTION,
    Zone,
    gdos_collection,
    isok_collection,
    mock_gdos,
    mock_isok,
)

PARCEL = box(637000, 486000, 637100, 486100)
Q1 = "scenariusz Q 1% (raz na 100 lat)"
Q02 = "scenariusz Q 0,2% (raz na 500 lat)"


async def _flood(response: str | Exception):
    with respx.mock(assert_all_called=False) as router:
        mock_isok(router, response)
        return await fetch_flood_risk_section(PARCEL)


async def _nature(layers):
    with respx.mock(assert_all_called=False) as router:
        mock_gdos(router, layers)
        return await fetch_nature_protection_section(PARCEL)


def _section(name: str, outcome) -> RiskSectionResult:
    return build_risk_section(name, _finalize_section("isok" if name == "flood" else "gdos", outcome), PARCEL)


# --- ISOK --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flood_no_match_keeps_provenance_with_empty_features() -> None:
    outcome = await _flood(EMPTY_COLLECTION)
    section = _section("flood", outcome)

    assert outcome.features == []
    assert section.status == "available"
    assert section.relation == "no_match"
    assert (section.feature_count, section.union_intersection_area_sqm) == (0, 0.0)
    assert section.source is not None
    assert section.source.source_id == "isok"
    assert section.source.response_status == 200
    assert section.source.artifact_sha256 is not None
    assert "HazardArea" in (section.source.source_url or "")


@pytest.mark.asyncio
async def test_flood_intersection_boundary_and_duplicate_ids() -> None:
    outcome = await _flood(
        isok_collection(
            Zone("HA-1", (636990, 485990, 637040, 486110), Q1, "100.0"),
            Zone("HA-1", (636990, 485990, 637040, 486110), Q1, "100.0"),
            Zone("HA-2", (636900, 485900, 637200, 486200), Q02, "500.0"),
            Zone("HA-3", (637100, 486000, 637150, 486100), Q1, None),
        )
    )
    section = _section("flood", outcome)
    risks = {item.feature_id: flood_risk_result(item) for item in outcome.features}

    assert sorted(risks) == ["HA-1", "HA-2", "HA-3"]  # duplikat liczony raz
    q1 = risks["HA-1"]
    assert (q1.section, q1.severity, q1.probability_class) == ("flood", "high", Q1)
    assert q1.return_period_years == 100
    assert q1.intersection_area_sqm == 4000.0 and q1.intersection_pct == 40.0
    assert q1.touches_boundary is False
    assert risks["HA-2"].intersection_pct == 100.0 and risks["HA-2"].return_period_years == 500
    edge = risks["HA-3"]
    assert edge.touches_boundary is True
    assert edge.intersection_area_sqm == 0.0 and edge.severity == "low"
    assert edge.return_period_years is None
    assert section.relation == "intersection"
    assert (section.feature_count, section.intersecting_feature_count, section.boundary_feature_count) == (3, 2, 1)
    # Nakładające się scenariusze nie dają > 100% pokrycia działki.
    assert section.union_intersection_pct == 100.0
    assert section.union_intersection_area_sqm == 10000.0
    assert section.feature_ids == ["HA-1", "HA-2", "HA-3"]


@pytest.mark.asyncio
async def test_boundary_only_relation() -> None:
    outcome = await _flood(isok_collection(Zone("HA-9", (637100, 486000, 637150, 486100), Q1, "100")))
    section = _section("flood", outcome)

    assert section.relation == "boundary_only"
    assert section.union_intersection_area_sqm == 0.0
    assert section.intersecting_feature_count == 0


def test_return_period_comes_only_from_source_attribute() -> None:
    assert _return_period_years({"returnPeriod": "500.0"}) == 500
    assert _return_period_years({"returnperiod": "10"}) == 10
    assert _return_period_years({"qualitativeLikelihood": Q1}) is None
    assert _return_period_years({"returnPeriod": "12.5"}) is None
    assert _return_period_years({"returnPeriod": "abc"}) is None
    assert _return_period_years({"returnPeriod": "-10"}) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "reason", "status"),
    [
        (httpx.ReadTimeout("slow"), "SERVICE_TIMEOUT", None),
        (httpx.ConnectError("dns"), "SERVICE_HTTP_ERROR", None),
        ("<not><valid", "INVALID_RESPONSE", 200),
    ],
)
async def test_flood_unavailable_has_status_and_provenance(response, reason, status) -> None:
    with pytest.raises(IsokServiceUnavailableError) as raised:
        await _flood(response)

    section = _section("flood", raised.value)
    assert section.status == "unavailable" and section.reason_code == reason
    assert section.relation == "unknown" and section.feature_count is None
    assert section.source is not None and section.source.response_status == status
    assert section.source.manual_review_required is True


@pytest.mark.asyncio
async def test_flood_http_status_is_recorded() -> None:
    with respx.mock() as router:
        router.get(url__startswith="https://wody.isok.gov.pl").mock(return_value=httpx.Response(503))
        with pytest.raises(IsokServiceUnavailableError) as raised:
            await fetch_flood_risk_section(PARCEL)
    assert raised.value.source_metadata is not None
    assert raised.value.source_metadata.response_status == 503


# --- GDOŚ --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_overlapping_protection_forms_are_not_summed_above_100_percent() -> None:
    outcome = await _nature(
        {
            "GDOS:ParkiKrajobrazowe": gdos_collection(
                "ParkiKrajobrazowe", Zone("PK-1", (636900, 485900, 637200, 486200), name="Park Testowy")
            ),
            "GDOS:SpecjalneObszaryOchrony": gdos_collection(
                "SpecjalneObszaryOchrony", Zone("N2K-1", (637000, 486000, 637060, 486100), name="Dolina Testowa")
            ),
        }
    )
    section = _section("nature", outcome)
    risks = {item.feature_id: nature_risk_result(item) for item in outcome.features}

    assert set(risks) == {"PK-1", "N2K-1"}
    park, natura = risks["PK-1"], risks["N2K-1"]
    assert (park.protection_type, park.name, park.intersection_pct) == ("park_krajobrazowy", "Park Testowy", 100.0)
    assert (natura.risk_type, natura.protection_type, natura.intersection_pct) == ("natura_2000", "natura2000", 60.0)
    assert natura.section == "nature" and natura.touches_boundary is False
    # Suma udziałów obiektów = 160%, ale pokrycie działki to suma mnogościowa.
    assert park.intersection_pct + natura.intersection_pct == 160.0
    assert section.union_intersection_pct == 100.0
    assert section.relation == "intersection"
    assert section.source is not None and section.source.source_id == "gdos"
    assert "10 warstw" in (section.source.source_version or "")


@pytest.mark.asyncio
async def test_nature_no_match_and_boundary_only() -> None:
    empty = _section("nature", await _nature({}))
    edge = _section(
        "nature",
        await _nature(
            {
                "GDOS:Rezerwaty": gdos_collection(
                    "Rezerwaty", Zone("R-1", (637100, 486000, 637150, 486100), name="Rezerwat Brzeg")
                )
            }
        ),
    )

    assert (empty.status, empty.relation, empty.feature_count) == ("available", "no_match", 0)
    assert empty.source is not None and empty.source.artifact_sha256 is not None
    assert edge.relation == "boundary_only"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "reason"),
    [(httpx.ReadTimeout("slow"), "SERVICE_TIMEOUT"), (httpx.ConnectError("x"), "SERVICE_HTTP_ERROR")],
)
async def test_nature_unavailable_has_status_and_provenance(error, reason) -> None:
    with pytest.raises(GdosServiceUnavailableError) as raised:
        await _nature(error)

    section = _section("nature", raised.value)
    assert (section.status, section.reason_code, section.relation) == ("unavailable", reason, "unknown")
    assert section.source is not None and section.source.fetched_at is not None


@pytest.mark.asyncio
async def test_nature_invalid_layer_and_http_error_keep_reason() -> None:
    with pytest.raises(GdosServiceUnavailableError) as raised:
        await _nature({"GDOS:Rezerwaty": "<broken"})
    assert raised.value.reason_code == "INVALID_RESPONSE"
    assert raised.value.source_metadata is not None

    with respx.mock() as router:
        router.get(url__startswith="https://sdi.gdos.gov.pl").mock(return_value=httpx.Response(500))
        with pytest.raises(GdosServiceUnavailableError) as http_error:
            await fetch_nature_protection_section(PARCEL)
    assert http_error.value.source_metadata is not None
    assert http_error.value.source_metadata.response_status == 500


# --- Kontrakt i mapowanie ------------------------------------------------------


def _source() -> SourceMetadata:
    return SourceMetadata(source_name="ISOK", confidence=0.85, manual_review_required=False)


def test_error_section_uses_attempt_provenance_and_unknown_legacy() -> None:
    error = build_risk_section("nature", ContextSectionResult(section="gdos", status="error", warnings=["x"]), PARCEL)
    assert (error.status, error.reason_code) == ("error", "UNEXPECTED_ERROR")
    assert error.source is not None and error.source.source_name == "GDOS"

    legacy = risk_sections_from_snapshot(None)
    assert [(item.section, item.status, item.reason_code) for item in legacy] == [
        ("flood", "unknown", "LEGACY_SNAPSHOT"),
        ("nature", "unknown", "LEGACY_SNAPSHOT"),
    ]
    assert legacy[0].warnings == [LEGACY_RISK_WARNING]
    snapshot = [item.model_dump(mode="json") for item in legacy]
    assert risk_sections_from_snapshot(snapshot) == legacy


@pytest.mark.parametrize(
    "values",
    [
        {"status": "available", "relation": "no_match"},
        {"status": "available", "relation": "intersection", "feature_count": 0, "intersecting_feature_count": 0,
         "boundary_feature_count": 0, "union_intersection_area_sqm": 0.0, "union_intersection_pct": 0.0},
        {"status": "unavailable", "relation": "no_match"},
        {"status": "unavailable", "relation": "unknown", "feature_count": 0},
        {"status": "unknown", "relation": "unknown", "feature_ids": ["x"]},
    ],
)
def test_section_contract_rejects_zero_for_missing_data(values) -> None:
    payload = {"section": "flood", "source": _source(), **values}
    with pytest.raises(ValidationError):
        RiskSectionResult(**payload)


def test_available_section_requires_provenance_and_risk_boundary_rule() -> None:
    with pytest.raises(ValidationError):
        RiskSectionResult(
            section="flood", status="available", relation="no_match", feature_count=0,
            intersecting_feature_count=0, boundary_feature_count=0,
            union_intersection_area_sqm=0.0, union_intersection_pct=0.0,
        )
    with pytest.raises(ValidationError):
        RiskResult(risk_type="flood", touches_boundary=True, intersection_area_sqm=5.0,
                   description="x", source=_source())
    legacy = RiskResult(risk_type="flood", description="stary opis 12.5%", source=_source())
    assert legacy.intersection_pct is None and legacy.section is None


def test_descriptions_are_built_from_fields() -> None:
    assert flood_description(Q1, 100, "high", 4000.0, 40.0, False) == (
        f"Strefa zagrożenia powodziowego; {Q1}; okres powtarzalności 100 lat (ze źródła); "
        "poziom wysoki; przecięcie 4000,00 m² (40,00% działki)."
    )
    assert "klasa prawdopodobieństwa nieustalona" in flood_description(None, None, None, None, None, None)
    assert nature_description("park_krajobrazowy", "Park", "low", 0.0, 0.0, True) == (
        "Forma ochrony przyrody: park krajobrazowy; „Park”; poziom niski; "
        "wyłącznie styk z granicą działki (bez wspólnej powierzchni)."
    )
    assert nature_description("inna", None, None, None, None, False) == "Forma ochrony przyrody: inna."
