"""Kompletność mapowania pól ``AnalyzeResponse`` na raport (BK-501).

Kryterium akceptacji: każde istotne pole API ma jednoznaczny odpowiednik w
raporcie albo jawne uzasadnienie pominięcia. Test przechodzi po drzewie
modelu Pydantic, więc nowe pole kontraktu bez decyzji o raporcie zatrzymuje CI.
"""

from __future__ import annotations

from app.modules.reporting.domain.field_mapping import (
    FIELD_MAPPINGS,
    MAPPING_KIND_LABELS,
    mapping_for,
    unmapped,
    unused_patterns,
)
from app.modules.reporting.domain.sections import REPORT_SECTIONS, SECTION_BY_ID
from app.services.report_fields import (
    field_presence,
    render_field_mapping_markdown,
    schema_leaf_paths,
)
from tests.repo_structure import find_repo_root
from tests.report_map_reference import load_fixture


def test_every_api_leaf_field_has_a_report_counterpart_or_reason() -> None:
    paths = schema_leaf_paths()
    assert len(paths) > 400
    assert unmapped(paths) == []


def test_every_mapping_pattern_is_used_and_well_formed() -> None:
    paths = schema_leaf_paths()
    assert unused_patterns(paths) == []
    patterns = [item.pattern for item in FIELD_MAPPINGS]
    assert len(patterns) == len(set(patterns))
    for item in FIELD_MAPPINGS:
        assert item.section in SECTION_BY_ID
        assert item.kind in MAPPING_KIND_LABELS
        if item.omitted:
            assert item.omitted_reason and len(item.omitted_reason) > 20
            assert item.element == "pominięte"
        else:
            assert item.element and item.element != "pominięte"


def test_omissions_are_few_and_never_hide_findings() -> None:
    omitted = {item.pattern for item in FIELD_MAPPINGS if item.omitted}
    # Pomijane są wyłącznie sekrety, surowe atrybuty, aliasy i parametry UI.
    assert omitted == {
        "manual_zone_context.document.preview_path",
        "manual_zone_context.raster_preview_source_key",
        "manual_zone_context.symbol_max_length",
        "manual_zone_context.symbol_allowed_pattern",
        "pog.status",
        "pog.raw_attributes",
        "pog.act.metadata.references[]",
        "terrain.relief.profile.line_geojson",
        "access_token",
    }


def test_key_fields_map_to_expected_sections_and_kinds() -> None:
    expectations = {
        "parcel.metrics.area_sqm": ("parcel", "computed"),
        "buildable_area_sqm": ("parcel", "approximation"),
        "mpzp_zones[].intersection_pct": ("mpzp", "computed"),
        "mpzp_zones[].max_building_height_m": ("mpzp", "source_fact"),
        "mpzp_zones[].parameters[].document_sha256": ("mpzp", "source_fact"),
        "mpzp_zones[].manual_selection.entered_symbol": ("mpzp", "manual"),
        "pog.zones[].area_pct": ("pog", "computed"),
        "pog.zones[].max_building_height_m": ("pog", "source_fact"),
        "pog.legal_status": ("pog", "source_fact"),
        "pog.ouz[].area_sqm": ("pog", "computed"),
        "pog.social_infrastructure_standard_areas[].id": ("pog", "source_fact"),
        "risks[].intersection_area_sqm": ("environment", "computed"),
        "risk_sections[].status": ("environment", "computed"),
        "terrain.height_difference_m": ("terrain", "computed"),
        "infrastructure[].buffer_m": ("infrastructure", "approximation"),
        "utilities_preview.coverage_status": ("infrastructure", "source_fact"),
        "warnings[].message": ("quality", "metadata"),
        "sources[].artifact_sha256": ("sources", "source_fact"),
    }
    for path, (section, kind) in expectations.items():
        mapping = mapping_for(path)
        assert mapping is not None, path
        assert (mapping.section, mapping.kind) == (section, kind), path
    assert mapping_for("nieistniejace.pole") is None


def test_field_presence_counts_values_and_nulls_for_a_real_snapshot() -> None:
    response, _ = load_fixture("multizone")
    rows = {row["mapping"].pattern: row for row in field_presence(response)}
    assert len(rows) == len(FIELD_MAPPINGS)
    assert rows["parcel.metrics.*"]["present"] == 5
    # 3 strefy × 3 parametry „max_*”; wysokość strefy SU jest null (≠ 0).
    assert rows["pog.zones[].max_*"]["present"] == 8 and rows["pog.zones[].max_*"]["missing"] == 1
    assert rows["manual_zone_context.*"] == {**rows["manual_zone_context.*"], "present": 0, "missing": 0}
    assert rows["access_token"]["present"] == 0


def test_mapping_documentation_is_in_sync_with_code() -> None:
    """docs/report/field-mapping.md jest generowany z tej samej tabeli."""
    document = find_repo_root() / "docs" / "report" / "field-mapping.md"
    assert document.read_text("utf-8") == render_field_mapping_markdown()


def test_ten_sections_in_fixed_order() -> None:
    assert [section.number for section in REPORT_SECTIONS] == list(range(1, 11))
    assert [section.id for section in REPORT_SECTIONS] == [
        "parcel", "summary", "mpzp", "pog", "environment", "terrain",
        "infrastructure", "quality", "sources", "limitations",
    ]
