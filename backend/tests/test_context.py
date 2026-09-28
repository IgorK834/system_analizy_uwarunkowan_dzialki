import asyncio
import logging
import time
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from shapely.geometry import Polygon

from app.schemas.analyze import SourceMetadata
from app.services.context import (
    ContextSectionResult,
    _finalize_section,
    analyze_context,
)
from app.services.gdos import GdosServiceUnavailableError
from app.services.isok import IsokServiceUnavailableError, RiskFeature
from app.services.kiut import NetworkFeature
from app.services.nmt import (
    NmtServiceUnavailableError,
    TerrainExtremes,
    TerrainNoCoverage,
)

PARCEL = Polygon.from_bounds(500000, 200000, 500100, 200100)


def _metadata(source_name: str) -> SourceMetadata:
    return SourceMetadata(
        source_name=source_name,
        confidence=0.8,
        manual_review_required=False,
    )


def _no_coverage() -> TerrainNoCoverage:
    return TerrainNoCoverage(
        grid_size_m=4.0,
        sampled_points=676,
        source_metadata=_metadata("NMT"),
        warnings=["NMT nie ma danych wysokościowych dla obszaru działki."],
    )


@pytest.fixture(autouse=True)
def mock_nmt_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zwykłe CI nie odpytuje zewnętrznego NMT."""
    monkeypatch.setattr(
        "app.services.context.fetch_terrain_extremes",
        AsyncMock(return_value=_no_coverage()),
    )


def _network_feature(warning: str | None = None) -> NetworkFeature:
    return NetworkFeature(
        network_type="water",
        geometry=PARCEL.boundary,
        source_metadata=_metadata("KIUT"),
        warning=warning,
    )


def _risk_feature(warnings: list[str] | None = None) -> RiskFeature:
    return RiskFeature(
        risk_type="flood",
        severity="low",
        geometry=PARCEL,
        intersection_area_sqm=PARCEL.area,
        area_ratio=1.0,
        probability_class="0,2%",
        source_metadata=_metadata("ISOK"),
        warnings=warnings or [],
    )


@pytest.mark.asyncio
async def test_analyze_context_runs_four_sections_in_parallel_close_to_slowest() -> (
    None
):
    async def _slow_kiut(*args: object, **kwargs: object) -> list[object]:
        await asyncio.sleep(0.2)
        return []

    async def _slow_other(*args: object, **kwargs: object) -> list[object]:
        await asyncio.sleep(0.05)
        return []

    async def _slow_nmt(*args: object, **kwargs: object) -> TerrainNoCoverage:
        await asyncio.sleep(0.05)
        return _no_coverage()

    with (
        patch(
            "app.services.context.fetch_kiut_networks",
            new=AsyncMock(side_effect=_slow_kiut),
        ),
        patch(
            "app.services.context.fetch_flood_risks",
            new=AsyncMock(side_effect=_slow_other),
        ),
        patch(
            "app.services.context.fetch_nature_protection_areas",
            new=AsyncMock(side_effect=_slow_other),
        ),
        patch(
            "app.services.context.fetch_terrain_extremes",
            new=AsyncMock(side_effect=_slow_nmt),
        ),
    ):
        start = time.monotonic()
        await analyze_context(PARCEL)
        elapsed = time.monotonic() - start

    assert 0.2 <= elapsed < 0.28


@pytest.mark.asyncio
async def test_analyze_context_isok_failure_does_not_abort_other_sections() -> None:
    kiut_feature = _network_feature()
    gdos_feature = SimpleNamespace(
        source_metadata=_metadata("GDOS"),
        warnings=[],
    )

    with (
        patch(
            "app.services.context.fetch_kiut_networks",
            new=AsyncMock(return_value=[kiut_feature]),
        ),
        patch(
            "app.services.context.fetch_flood_risks",
            new=AsyncMock(side_effect=IsokServiceUnavailableError("x")),
        ),
        patch(
            "app.services.context.fetch_nature_protection_areas",
            new=AsyncMock(return_value=[gdos_feature]),
        ),
    ):
        result = await analyze_context(PARCEL)

    assert result.isok.status == "unavailable"
    assert result.isok.data == []
    assert result.kiut.status == "available"
    assert result.gdos.status == "available"
    assert len(result.kiut.data) > 0


@pytest.mark.asyncio
async def test_analyze_context_unexpected_exception_maps_to_error_status() -> None:
    with (
        patch(
            "app.services.context.fetch_kiut_networks",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.context.fetch_flood_risks",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.context.fetch_nature_protection_areas",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ),
    ):
        result = await analyze_context(PARCEL)

    assert result.gdos.status == "error"
    assert len(result.gdos.warnings) >= 1
    assert result.kiut.status == "available"
    assert result.isok.status == "available"


@pytest.mark.asyncio
async def test_analyze_context_partial_result_contains_warnings_and_status_per_section() -> (
    None
):
    with (
        patch(
            "app.services.context.fetch_kiut_networks",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.context.fetch_flood_risks",
            new=AsyncMock(side_effect=IsokServiceUnavailableError("x")),
        ),
        patch(
            "app.services.context.fetch_nature_protection_areas",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ),
    ):
        result = await analyze_context(PARCEL)

    assert result.kiut.status == "available"
    assert result.isok.status == "unavailable"
    assert result.gdos.status == "error"
    assert result.isok.warnings
    assert result.gdos.warnings


@pytest.mark.asyncio
async def test_analyze_context_success_populates_data_and_source_metadata() -> None:
    kiut_feature = _network_feature()
    isok_feature = _risk_feature()
    gdos_feature = SimpleNamespace(
        source_metadata=_metadata("GDOS"),
        warnings=[],
    )

    with (
        patch(
            "app.services.context.fetch_kiut_networks",
            new=AsyncMock(return_value=[kiut_feature]),
        ),
        patch(
            "app.services.context.fetch_flood_risks",
            new=AsyncMock(return_value=[isok_feature]),
        ),
        patch(
            "app.services.context.fetch_nature_protection_areas",
            new=AsyncMock(return_value=[gdos_feature]),
        ),
    ):
        result = await analyze_context(PARCEL)

    assert result.kiut.source_metadata is not None
    assert result.isok.source_metadata is not None
    assert result.gdos.source_metadata is not None
    assert result.kiut.source_metadata.source_name == "KIUT"
    assert result.isok.source_metadata.source_name == "ISOK"
    assert result.gdos.source_metadata.source_name == "GDOS"
    assert len(result.kiut.data) == 1


@pytest.mark.asyncio
async def test_analyze_context_collects_per_feature_warnings_from_both_shapes() -> None:
    with (
        patch(
            "app.services.context.fetch_kiut_networks",
            new=AsyncMock(return_value=[_network_feature("w1")]),
        ),
        patch(
            "app.services.context.fetch_flood_risks",
            new=AsyncMock(return_value=[_risk_feature(["w2", "w3"])]),
        ),
        patch(
            "app.services.context.fetch_nature_protection_areas",
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = await analyze_context(PARCEL)

    assert result.kiut.warnings == ["w1"]
    assert result.isok.warnings == ["w2", "w3"]


@pytest.mark.asyncio
async def test_analyze_context_uses_single_shared_httpx_client_instance() -> None:
    kiut_mock = AsyncMock(return_value=[])
    isok_mock = AsyncMock(return_value=[])
    gdos_mock = AsyncMock(return_value=[])

    with (
        patch("app.services.context.fetch_kiut_networks", new=kiut_mock),
        patch("app.services.context.fetch_flood_risks", new=isok_mock),
        patch("app.services.context.fetch_nature_protection_areas", new=gdos_mock),
    ):
        await analyze_context(PARCEL)

    kiut_client = kiut_mock.await_args.kwargs["client"]
    isok_client = isok_mock.await_args.kwargs["client"]
    gdos_client = gdos_mock.await_args.kwargs["client"]
    assert kiut_client is isok_client
    assert isok_client is gdos_client


@pytest.mark.asyncio
async def test_analyze_context_logs_elapsed_time_and_source_name(caplog) -> None:
    context_logger = logging.getLogger("app.services.context")
    caplog.set_level(logging.INFO, logger=context_logger.name)
    with (
        patch.object(context_logger, "disabled", False),
        patch(
            "app.services.context.fetch_kiut_networks",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.context.fetch_flood_risks",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.context.fetch_nature_protection_areas",
            new=AsyncMock(return_value=[]),
        ),
    ):
        await analyze_context(PARCEL)

    assert "KIUT" in caplog.text
    assert "ISOK" in caplog.text
    assert "GDOŚ" in caplog.text or "GDOS" in caplog.text


@pytest.mark.asyncio
async def test_analyze_context_logs_source_name_when_section_fails(caplog) -> None:
    context_logger = logging.getLogger("app.services.context")
    caplog.set_level(logging.INFO, logger=context_logger.name)
    with (
        patch.object(context_logger, "disabled", False),
        patch(
            "app.services.context.fetch_kiut_networks",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.context.fetch_flood_risks",
            new=AsyncMock(side_effect=IsokServiceUnavailableError("x")),
        ),
        patch(
            "app.services.context.fetch_nature_protection_areas",
            new=AsyncMock(side_effect=GdosServiceUnavailableError("x")),
        ),
    ):
        await analyze_context(PARCEL)

    assert "KIUT" in caplog.text
    assert "ISOK" in caplog.text
    assert "GDOŚ" in caplog.text or "GDOS" in caplog.text


def test_finalize_section_maps_isok_unavailable_error_directly() -> None:
    result = _finalize_section("isok", IsokServiceUnavailableError("x"))

    assert result.status == "unavailable"


def test_finalize_section_maps_generic_exception_to_error_directly() -> None:
    result = _finalize_section("gdos", RuntimeError("boom"))

    assert result.status == "error"


def test_finalize_section_empty_list_is_available_with_none_source_metadata() -> None:
    assert _finalize_section("kiut", []) == ContextSectionResult(
        section="kiut",
        status="available",
        data=[],
        source_metadata=None,
        warnings=[],
    )


def test_context_section_result_is_frozen() -> None:
    result = ContextSectionResult(section="kiut", status="available")

    with pytest.raises((FrozenInstanceError, AttributeError)):
        result.status = "error"


@pytest.mark.asyncio
async def test_nmt_no_coverage_is_explicit_measurement_not_empty_list() -> None:
    """Brak pokrycia NMT nie jest pustą listą, którą można wziąć za płaski teren."""
    with (
        patch("app.services.context.fetch_kiut_networks", new=AsyncMock(return_value=[])),
        patch("app.services.context.fetch_flood_risks", new=AsyncMock(return_value=[])),
        patch(
            "app.services.context.fetch_nature_protection_areas",
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = await analyze_context(PARCEL)

    assert result.nmt.status == "available"
    [measurement] = result.nmt.data
    assert isinstance(measurement, TerrainNoCoverage)
    assert result.nmt.source_metadata is not None
    assert result.nmt.warnings == [
        "NMT nie ma danych wysokościowych dla obszaru działki."
    ]


@pytest.mark.asyncio
async def test_nmt_measurement_is_single_item_with_source() -> None:
    extremes = TerrainExtremes(
        min_height_m=112.3,
        max_height_m=115.7,
        height_difference_m=3.4,
        grid_size_m=4.0,
        sampled_points=676,
        source_metadata=_metadata("NMT"),
    )
    with (
        patch("app.services.context.fetch_kiut_networks", new=AsyncMock(return_value=[])),
        patch("app.services.context.fetch_flood_risks", new=AsyncMock(return_value=[])),
        patch(
            "app.services.context.fetch_nature_protection_areas",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.context.fetch_terrain_extremes",
            new=AsyncMock(return_value=extremes),
        ),
    ):
        result = await analyze_context(PARCEL)

    assert result.nmt.data == [extremes]
    assert result.nmt.source_metadata == extremes.source_metadata


def test_finalize_section_keeps_nmt_failure_provenance_and_reason() -> None:
    attempt = SourceMetadata(
        source_name="NMT",
        source_url="https://services.gugik.gov.pl/nmt/?request=GetMinMaxByPolygon",
        confidence=0.0,
        manual_review_required=True,
    )
    result = _finalize_section(
        "nmt",
        NmtServiceUnavailableError(
            "timeout", reason_code="SERVICE_TIMEOUT", source_metadata=attempt
        ),
    )

    assert result.status == "unavailable"
    assert result.source_metadata == attempt
    assert result.reason_code == "SERVICE_TIMEOUT"
    assert result.data == []


def test_finalize_section_without_failure_provenance_keeps_none() -> None:
    result = _finalize_section("isok", IsokServiceUnavailableError("x"))

    assert result.source_metadata is None
    assert result.reason_code is None
