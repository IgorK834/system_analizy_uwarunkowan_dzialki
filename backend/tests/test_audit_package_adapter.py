"""Adapter pakietu audytowego (BK-505): warstwy, źródła, rejestr artefaktów i wydań."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from geoalchemy2.shape import from_shape

from app.core.data_sources import CatalogFileError
from app.db.session import SessionLocal
from app.models.versioned import DataRelease, DataSource, SourceArtifact
from app.schemas.analyze import (
    InfrastructureResult,
    RiskResult,
    TerrainProfileResult,
    TerrainReliefResult,
    TerrainResult,
)
from app.schemas.source import SourceMetadata
from app.services import audit_package
from app.services.audit_package import (
    _all_sources,
    _artifacts_by_hash,
    _catalog,
    _layers,
    _raw_blocks,
    _redistribution_map,
    _releases_by_id,
    _risk_section,
    _source_entries,
    build_audit_input,
)
from tests.report_map_reference import load_fixture
from tests.test_audit_export import _catalog as catalog_with


def _feature(name: str) -> dict[str, Any]:
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": [[19.9, 50.0], [19.91, 50.01]]},
        "properties": {"name": name},
    }


def _source(source_id: str | None, name: str = "Źródło") -> SourceMetadata:
    return SourceMetadata(
        source_id=source_id, source_name=name, confidence=0.9, manual_review_required=False,
        fetched_at=datetime(2026, 9, 20, 9, 0, tzinfo=timezone.utc),
    )


def test_missing_catalog_yields_no_policy_so_nothing_is_redistributable(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> None:
        raise CatalogFileError("brak katalogu")

    monkeypatch.setattr(audit_package, "get_catalog", broken)
    assert _catalog() is None

    response, parcel = load_fixture("multizone")
    assert _redistribution_map(response, None) == {}
    analysis = SimpleNamespace(
        id=1, parcel=SimpleNamespace(geometry=from_shape(parcel, srid=2180), parcel_identifier="X"),
        result_contract_version="c", data_release_ids=[], cache_signature=None,
    )
    audit_input = build_audit_input(analysis, response, None, artifacts={}, releases={})
    assert audit_input.redistribution == {}
    entries = _source_entries(response, None, {}, {})
    assert all(entry["catalog"] is None for entry in entries)


def test_layers_cover_infrastructure_terrain_profile_and_risk_sections() -> None:
    response, _ = load_fixture("multizone")
    infra = InfrastructureResult(
        network_type="water", buffer_m=3.0, source=_source("kiut_gesut", "KIUT"),
        network_geometry_geojson=_feature("siec"), protection_zone_geojson=_feature("bufor"),
    )
    relief = TerrainReliefResult.model_construct(
        profile=TerrainProfileResult(
            method="linia", start=(0.0, 0.0), end=(10.0, 0.0), length_m=10.0, step_m=1.0,
            line_geojson=_feature("profil"),
        ),
        source=_source("nmt_wcs", "NMT_WCS"),
    )
    terrain = TerrainResult.model_construct(
        status="available", source=_source("nmt", "NMT"), relief=relief
    )
    varied = response.model_copy(update={"infrastructure": [infra], "terrain": terrain})

    layers = {layer.name: layer for layer in _layers(varied, None)}
    assert {"infrastructure_buffers", "terrain_profile", "flood_risk", "nature_protection"} <= set(layers)
    buffers = layers["infrastructure_buffers"]
    assert {feature["properties"]["part"] for feature in buffers.features} == {"network", "protection_zone"}
    assert all(feature["properties"]["network_type"] == "water" for feature in buffers.features)
    assert buffers.source_ids == (None, None) or set(buffers.source_ids) <= {"kiut_gesut", None}
    profile = layers["terrain_profile"]
    assert profile.features[0]["properties"] == {"layer": "terrain_profile", "name": "profil"}
    assert layers["parcel"].kind == "parcel" and layers["parcel"].path == "parcel.geojson"

    sources = _all_sources(varied)
    names = {source.source_name for source in sources}
    assert {"NMT", "NMT_WCS", "KIUT", "ULDK"} <= names
    assert len(sources) > len(varied.sources)


def test_layers_without_terrain_relief_or_pog_are_simply_absent() -> None:
    response, _ = load_fixture("multizone")
    bare = response.model_copy(
        update={"pog": None, "terrain": None, "infrastructure": [], "risks": [], "mpzp_zones": [],
                "parcel": None}
    )
    assert _layers(bare, None) == []
    assert _raw_blocks(bare, None) == []
    assert _all_sources(bare) == list(bare.sources)


def test_risk_section_fallback_uses_the_risk_type_when_section_is_missing() -> None:
    assert _risk_section("flood", "x") == "flood" and _risk_section("nature", "x") == "nature"
    assert _risk_section(None, "flood_zone") == "flood"
    assert _risk_section(None, "natura2000") == "nature"
    response, _ = load_fixture("multizone")
    risk = RiskResult.model_validate(
        {**response.risks[0].model_dump(mode="json"), "section": None, "risk_type": "flood"}
    )
    layers = {layer.name for layer in _layers(response.model_copy(update={"risks": [risk]}), None)}
    assert "flood_risk" in layers


def test_raw_blocks_use_the_pog_source_identifier() -> None:
    response, _ = load_fixture("multizone")
    blocks = _raw_blocks(response, catalog_with())
    assert [(block.path, block.source_id) for block in blocks] == [(("pog", "raw_attributes"), "pog_app")]
    assert blocks[0].pointer == "/result/pog/raw_attributes"
    assert _redistribution_map(response, catalog_with())["pog_app"] == "derived_only"


@pytest.mark.integration
def test_artifact_and_release_references_come_from_the_registry_without_volatile_fields() -> None:
    suffix = uuid4().hex[:8]
    now = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
    with SessionLocal() as db:
        source = DataSource(source_id=f"audit_{suffix}", owner="Test", status="production")
        db.add(source)
        db.flush()
        artifact = SourceArtifact(
            data_source_id=source.id, uri=f"https://example.test/{suffix}.gml", media_type="application/gml+xml",
            content_hash=suffix * 8, size_bytes=321, fetched_at=now,
        )
        release = DataRelease(data_source_id=source.id, version_label=f"v-{suffix}", published_at=now,
                              is_active=True)
        db.add_all([artifact, release])
        db.commit()
        try:
            found = _artifacts_by_hash(db, {suffix * 8, "0" * 64})
            assert set(found) == {suffix * 8}
            assert found[suffix * 8]["size_bytes"] == 321
            assert found[suffix * 8]["fetched_at"] == "2026-09-19T12:00:00Z"
            releases = _releases_by_id(db, {release.id})
            assert releases[release.id] == {
                "release_id": release.id, "source_id": f"audit_{suffix}", "version_label": f"v-{suffix}",
                "published_at": "2026-09-19T12:00:00Z",
            }
            assert "is_active" not in releases[release.id]  # zmienne w czasie — łamałoby determinizm
            assert _artifacts_by_hash(db, set()) == {} and _releases_by_id(db, set()) == {}
        finally:
            db.delete(artifact)
            db.delete(release)
            db.flush()
            db.delete(source)
            db.commit()
