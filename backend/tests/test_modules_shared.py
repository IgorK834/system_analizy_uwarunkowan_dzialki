"""Testy współdzielonych typów (app.shared) i szkieletu modułów (Faza 10.2).

Pokrywają publiczne typy CRS/geometrii/provenance oraz przykładowe typy domenowe
i porty modułów, aby nowe pakiety spełniały próg pokrycia i były realnie używalne.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.modules.analysis.application.ports import AnalysisSnapshotWriter
from app.modules.parcels.application.ports import ParcelRepository
from app.modules.parcels.domain.models import (
    ParcelGeometry,
    ParcelIdentifier,
)
from app.shared import (
    CANONICAL_CRS,
    BoundingBox,
    GeometryPayload,
    Provenance,
    is_allowed_crs,
)


def test_canonical_crs_is_2180() -> None:
    assert CANONICAL_CRS == "EPSG:2180"


def test_is_allowed_crs() -> None:
    assert is_allowed_crs("EPSG:2180") is True
    assert is_allowed_crs("EPSG:4326") is True
    assert is_allowed_crs("EPSG:9999") is False


def test_geometry_payload_canonical_flag() -> None:
    canonical = GeometryPayload(wkt="POINT(0 0)")
    non_canonical = GeometryPayload(wkt="POINT(0 0)", crs="EPSG:4326")
    assert canonical.is_canonical() is True
    assert non_canonical.is_canonical() is False


def test_bounding_box_as_tuple() -> None:
    box = BoundingBox(0.0, 1.0, 2.0, 3.0)
    assert box.as_tuple() == (0.0, 1.0, 2.0, 3.0)
    assert box.crs == CANONICAL_CRS


def test_provenance_with_release() -> None:
    now = datetime.now(timezone.utc)
    base = Provenance(source_id="uldk", fetched_at=now, content_hash="abc")
    linked = base.with_release(42)
    assert linked.data_release_id == 42
    assert linked.source_id == "uldk"
    assert linked.content_hash == "abc"
    assert base.data_release_id is None  # niezmienność oryginału


def test_parcel_identifier_valid() -> None:
    identifier = ParcelIdentifier("122101_1.0001.1234")
    assert identifier.value == "122101_1.0001.1234"


def test_parcel_identifier_invalid_raises() -> None:
    with pytest.raises(ValueError):
        ParcelIdentifier("niepoprawny")


def test_parcel_geometry_is_canonical() -> None:
    parcel = ParcelGeometry(
        identifier=ParcelIdentifier("122101_1.0001.1234"),
        geometry=GeometryPayload(wkt="MULTIPOLYGON(((0 0,1 0,1 1,0 0)))"),
    )
    assert parcel.is_canonical() is True


def test_ports_are_importable_protocols() -> None:
    # Import portów potwierdza, że warstwa application jest spójna i typowalna.
    assert ParcelRepository is not None
    assert AnalysisSnapshotWriter is not None
