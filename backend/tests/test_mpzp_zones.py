from datetime import datetime, timezone

import pytest
from shapely.geometry import Polygon

from app.schemas.mpzp import MpzpParameter, MpzpZoneResult as ParserMpzpZoneResult
from app.schemas.source import SourceMetadata
from app.services.mpzp_zones import (
    InvalidZoneSymbolError,
    MANUAL_ZONE_SYMBOL_CONFIDENCE,
    ZoneGeometryCandidate,
    calculate_mpzp_zone_intersections,
    calculate_single_symbol_fallback,
    map_parser_zone_to_analyze_response,
    validate_zone_symbol_format,
)


def _source(confidence: float = 0.6, manual_review: bool = True) -> SourceMetadata:
    return SourceMetadata(
        source_name="KIMPZP",
        source_url="https://example.local/mpzp",
        fetched_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
        confidence=confidence,
        manual_review_required=manual_review,
    )


# --- validate_zone_symbol_format ---------------------------------------


@pytest.mark.parametrize(
    "symbol",
    ["230_U", "230_UMW", "MN.1", "MN.11", "1UZ", "6.8.MW/U", "2.UP"],
)
def test_validate_zone_symbol_accepts_real_world_symbols(symbol: str) -> None:
    assert validate_zone_symbol_format(symbol) == symbol


def test_validate_zone_symbol_strips_whitespace() -> None:
    assert validate_zone_symbol_format("  230_U  ") == "230_U"


def test_validate_zone_symbol_rejects_empty_string() -> None:
    with pytest.raises(InvalidZoneSymbolError):
        validate_zone_symbol_format("")


def test_validate_zone_symbol_rejects_whitespace_only() -> None:
    with pytest.raises(InvalidZoneSymbolError):
        validate_zone_symbol_format("   ")


def test_validate_zone_symbol_rejects_too_long() -> None:
    with pytest.raises(InvalidZoneSymbolError):
        validate_zone_symbol_format("A" * 21)


def test_validate_zone_symbol_accepts_max_length() -> None:
    symbol = "A" * 20
    assert validate_zone_symbol_format(symbol) == symbol


def test_validate_zone_symbol_rejects_internal_whitespace() -> None:
    with pytest.raises(InvalidZoneSymbolError):
        validate_zone_symbol_format("230 U")


def test_validate_zone_symbol_rejects_internal_newline() -> None:
    # strip() usuwa tylko whitespace na KRAWĘDZIACH, więc trailing '\n' nie
    # jest wystarczającym testem odrzucenia znaków kontrolnych — sprawdzamy
    # nowa linię wewnątrz symbolu, którą strip() nie usunie.
    with pytest.raises(InvalidZoneSymbolError):
        validate_zone_symbol_format("230\n_U")


def test_validate_zone_symbol_rejects_control_characters() -> None:
    with pytest.raises(InvalidZoneSymbolError):
        validate_zone_symbol_format("230_U\x00")


def test_validate_zone_symbol_rejects_disallowed_punctuation() -> None:
    with pytest.raises(InvalidZoneSymbolError):
        validate_zone_symbol_format("230;U")


# --- map_parser_zone_to_analyze_response --------------------------------


def test_map_parser_zone_maps_known_parameters() -> None:
    parser_zone = ParserMpzpZoneResult(
        zone_symbol="230_U",
        parameters=[
            MpzpParameter(
                name="max_building_height_m",
                normalized_value=9.0,
                confidence=0.8,
                manual_review_required=False,
            ),
            MpzpParameter(
                name="max_storeys",
                normalized_value=2,
                confidence=0.8,
                manual_review_required=False,
            ),
            MpzpParameter(
                name="primary_use",
                normalized_value="zabudowa mieszkaniowa jednorodzinna",
                confidence=0.7,
                manual_review_required=False,
            ),
        ],
    )
    source = _source(confidence=MANUAL_ZONE_SYMBOL_CONFIDENCE)

    result, skipped = map_parser_zone_to_analyze_response(
        parser_zone, parcel_area_sqm=1000.0, source=source
    )

    assert result.zone_symbol == "230_U"
    assert result.max_building_height_m == 9.0
    assert result.max_floors == 2
    assert result.primary_use == "zabudowa mieszkaniowa jednorodzinna"
    assert skipped == []


def test_map_parser_zone_flags_unmapped_parameters_not_silently_dropped() -> None:
    parser_zone = ParserMpzpZoneResult(
        zone_symbol="230_U",
        parameters=[
            MpzpParameter(
                name="prohibition",
                normalized_value="zakaz lokalizacji obiektów handlowych >2000m2",
                confidence=0.6,
                manual_review_required=True,
            ),
            MpzpParameter(
                name="roof_geometry",
                normalized_value="dwuspadowy",
                confidence=0.5,
                manual_review_required=True,
            ),
        ],
    )
    source = _source()

    result, skipped = map_parser_zone_to_analyze_response(
        parser_zone, parcel_area_sqm=500.0, source=source
    )

    assert set(skipped) == {"prohibition", "roof_geometry"}
    # Parametry pominięte nie mogą trafić do żadnego pola płaskiego wyniku.
    assert result.max_building_height_m is None


def test_map_parser_zone_assumes_whole_parcel_area_without_vector_geometry() -> None:
    parser_zone = ParserMpzpZoneResult(zone_symbol="230_U", parameters=[])
    source = _source()

    result, _ = map_parser_zone_to_analyze_response(
        parser_zone, parcel_area_sqm=1234.5, source=source
    )

    assert result.intersection_area_sqm == 1234.5
    assert result.intersection_pct == 100.0
    assert result.is_dominant is True
    assert result.source is source


# --- calculate_mpzp_zone_intersections ----------------------------------


def test_two_intersecting_zones_return_both_with_exact_area_ratio() -> None:
    # Działka kwadratowa 10x10 = 100 m². Strefa A pokrywa lewą połowę (50 m²),
    # strefa B prawą połowę (50 m²) — udziały powinny być dokładnie po 50%.
    parcel = Polygon.from_bounds(0, 0, 10, 10)
    zone_a = ZoneGeometryCandidate(
        zone_symbol="A",
        geometry=Polygon.from_bounds(0, 0, 5, 10),
        source_metadata=_source(),
    )
    zone_b = ZoneGeometryCandidate(
        zone_symbol="B",
        geometry=Polygon.from_bounds(5, 0, 10, 10),
        source_metadata=_source(),
    )

    result = calculate_mpzp_zone_intersections(parcel, [zone_a, zone_b])

    by_symbol = {zone.zone_symbol: zone for zone in result.zones}
    assert by_symbol["A"].intersection_area_sqm == pytest.approx(50.0)
    assert by_symbol["A"].area_ratio == pytest.approx(50.0)
    assert by_symbol["B"].intersection_area_sqm == pytest.approx(50.0)
    assert by_symbol["B"].area_ratio == pytest.approx(50.0)
    assert result.multi_zone is True


def test_dominant_zone_chosen_by_largest_intersection_area_not_list_order() -> None:
    parcel = Polygon.from_bounds(0, 0, 10, 10)
    # Strefa "first" jest pierwsza na liście, ale ma mniejsze pole przecięcia
    # niż "second" — dominująca musi być wybrana po polu, nie po pozycji.
    zone_first = ZoneGeometryCandidate(
        zone_symbol="first",
        geometry=Polygon.from_bounds(0, 0, 3, 10),
        source_metadata=_source(),
    )
    zone_second = ZoneGeometryCandidate(
        zone_symbol="second",
        geometry=Polygon.from_bounds(3, 0, 10, 10),
        source_metadata=_source(),
    )
    zone_third = ZoneGeometryCandidate(
        zone_symbol="third",
        geometry=Polygon.from_bounds(0, 0, 1, 1),
        source_metadata=_source(),
    )

    result = calculate_mpzp_zone_intersections(
        parcel, [zone_first, zone_second, zone_third]
    )

    assert result.dominant_zone is not None
    assert result.dominant_zone.zone_symbol == "second"


def test_centroid_is_not_sole_criterion_l_shaped_parcel() -> None:
    # Działka w kształcie litery L (ramiona 5x3 i 3x6, z narożnikiem 3x3
    # wspólnym dla obu stref): centroid geometryczny leży wewnątrz strefy A
    # (poziome ramię), ale strefa B (pionowe ramię) pokrywa większą
    # powierzchnię całej działki. Wynik dominant_zone musi wybrać B, mimo że
    # "centroid-only" podejście wskazałoby A.
    l_shape = Polygon([(0, 0), (5, 0), (5, 3), (3, 3), (3, 6), (0, 6), (0, 0)])
    zone_a = ZoneGeometryCandidate(
        zone_symbol="A",
        geometry=Polygon.from_bounds(0, 0, 5, 3),
        source_metadata=_source(),
    )
    zone_b = ZoneGeometryCandidate(
        zone_symbol="B",
        geometry=Polygon.from_bounds(0, 0, 3, 6),
        source_metadata=_source(),
    )

    centroid = l_shape.centroid
    # Sanity check: centroid leży wewnątrz strefy A ("centroid-only" podejście
    # błędnie wskazałoby A jako dominującą).
    assert zone_a.geometry.contains(centroid)

    result = calculate_mpzp_zone_intersections(l_shape, [zone_a, zone_b])

    area_a = l_shape.intersection(zone_a.geometry).area
    area_b = l_shape.intersection(zone_b.geometry).area
    assert area_b > area_a
    assert result.dominant_zone is not None
    assert result.dominant_zone.zone_symbol == "B"


def test_no_zone_geometries_returns_fallback_with_manual_review() -> None:
    parcel = Polygon.from_bounds(0, 0, 10, 10)

    result = calculate_mpzp_zone_intersections(parcel, [])

    assert result.dominant_zone is None
    assert result.zones == []
    assert result.multi_zone is False
    assert result.manual_review_required is True
    assert any("NO_ZONE_GEOMETRIES" in warning for warning in result.warnings)


def test_boundary_touch_is_flagged_but_not_removed_from_list() -> None:
    parcel = Polygon.from_bounds(0, 0, 10, 10)
    # Strefa dotyka tylko skrawka działki (0.01 m² z 100 m² = 0.01%),
    # znacznie poniżej progu boundary_touch (0.1%).
    tiny_zone = ZoneGeometryCandidate(
        zone_symbol="tiny",
        geometry=Polygon.from_bounds(9.99, 9.99, 10.1, 10.1),
        source_metadata=_source(),
    )

    result = calculate_mpzp_zone_intersections(parcel, [tiny_zone])

    assert len(result.zones) == 1
    assert result.zones[0].zone_symbol == "tiny"
    assert result.dominant_zone is None
    assert any("boundary_touch" in warning for warning in result.warnings)


def test_multi_zone_false_when_second_zone_below_threshold() -> None:
    parcel = Polygon.from_bounds(0, 0, 10, 10)
    dominant_zone = ZoneGeometryCandidate(
        zone_symbol="dominant",
        geometry=Polygon.from_bounds(0, 0, 10, 10),
        source_metadata=_source(),
    )
    # 0.5 m² z 100 m² = 0.5%, powyżej boundary_touch (0.1%), ale poniżej
    # multi_zone (5%) — nie powinno ustawić multi_zone=True.
    minor_zone = ZoneGeometryCandidate(
        zone_symbol="minor",
        geometry=Polygon.from_bounds(0, 0, 0.5, 1),
        source_metadata=_source(),
    )

    result = calculate_mpzp_zone_intersections(parcel, [dominant_zone, minor_zone])

    assert result.multi_zone is False
    assert result.dominant_zone is not None
    assert result.dominant_zone.zone_symbol == "dominant"


# --- calculate_single_symbol_fallback ------------------------------------


def test_single_symbol_fallback_assigns_whole_parcel() -> None:
    parcel = Polygon.from_bounds(0, 0, 10, 10)
    source = _source(confidence=MANUAL_ZONE_SYMBOL_CONFIDENCE)

    result = calculate_single_symbol_fallback(parcel, "230_U", source)

    assert result.dominant_zone is not None
    assert result.dominant_zone.zone_symbol == "230_U"
    assert result.dominant_zone.intersection_area_sqm == pytest.approx(100.0)
    assert result.dominant_zone.area_ratio == 100.0
    assert result.dominant_zone.is_dominant is True
    assert result.multi_zone is False
    assert result.manual_review_required is True
    assert any("SINGLE_ZONE_FALLBACK" in warning for warning in result.warnings)


def test_single_symbol_fallback_propagates_source_metadata_unchanged() -> None:
    parcel = Polygon.from_bounds(0, 0, 5, 5)
    source = _source(confidence=0.3, manual_review=True)

    result = calculate_single_symbol_fallback(parcel, "MN", source)

    assert result.zones[0].source_metadata is source
    assert result.zones[0].source_metadata.confidence == 0.3
