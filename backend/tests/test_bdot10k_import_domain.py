"""Import BDOT10k (Task 23.1): reguły normalizacji, naprawa geometrii i wybór zasięgu wydania — bez bazy i sieci."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon
from shapely.wkb import dumps as dump_wkb
from shapely.wkb import loads as load_wkb

from app.core.data_sources import get_catalog
from app.modules.imports.domain.bdot10k import (
    ALL_FIELD_KEYS,
    REJECT_DUPLICATE_ID,
    REJECT_ID_TOO_LONG,
    REJECT_MISSING_ID,
    REJECT_NO_GEOMETRY,
    Bdot10kValidationError,
    LayerStats,
    RawFeature,
    RoadRow,
    clean_text,
    data_as_of,
    keep_latest_per_id,
    normalize_building,
    normalize_road,
    parse_positive_int,
    parse_version,
    parse_width,
    scope_key,
    select_extent,
    validate_mapping,
    validate_powiat_teryt,
)
from app.modules.imports.infrastructure.bdot10k.geometry import ShapelyGeometryTools

MAPPING = get_catalog().get("bdot10k").field_mapping
LINE = dump_wkb(LineString([(0, 0), (10, 0)]))
SQUARE = dump_wkb(Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]))


def _road(**attributes: object) -> RawFeature:
    base = {
        "lokalnyId": "ROAD-1",
        "kategoriaIstnienia": "eksploatowany",
        "kategoriaZarzadzania": "gminna",
        "klasaDrogi": "droga lokalna",
        "wersja": "2026-01-22T12:00:00",
    }
    return RawFeature({**base, **attributes}, LINE)


def test_catalog_mapping_covers_every_field_the_importer_reads() -> None:
    assert ALL_FIELD_KEYS <= set(MAPPING)
    assert validate_mapping(MAPPING) is MAPPING
    with pytest.raises(Bdot10kValidationError, match="local_id"):
        validate_mapping({key: value for key, value in MAPPING.items() if key != "local_id"})


@pytest.mark.parametrize("value", ["126", "12611", "12a1", "", " "])
def test_powiat_teryt_must_be_four_digits(value: str) -> None:
    with pytest.raises(Bdot10kValidationError):
        validate_powiat_teryt(value)


def test_scope_key_is_one_active_release_per_powiat() -> None:
    assert validate_powiat_teryt(" 0262 ") == "0262"
    assert scope_key("0262") == "powiat:0262" and len(scope_key("1465")) <= 40


def test_road_attributes_are_mapped_and_empty_text_is_none_not_a_value() -> None:
    stats = LayerStats()

    row = normalize_road(_road(klasaDrogi="  ", numerDrogi=None, szerokoscNawierzchni="7,5", ulicaNazwa1=" ul.  Długa "), MAPPING, stats)

    assert isinstance(row, RoadRow)
    assert (row.local_id, row.management_category, row.road_class) == ("ROAD-1", "gminna", None)
    assert row.road_number is None and row.street_name == "ul. Długa"
    assert row.surface_width_m == 7.5
    assert row.feature_version_at == datetime(2026, 1, 22, 12, 0, tzinfo=timezone.utc)
    assert stats.read == 1 and stats.imported == 0 and not stats.rejected


def test_missing_and_too_long_identifiers_are_rejected_with_a_reason() -> None:
    stats = LayerStats()

    assert normalize_road(_road(lokalnyId=None), MAPPING, stats) is None
    assert normalize_road(_road(lokalnyId="x" * 65), MAPPING, stats) is None
    assert normalize_road(RawFeature({"lokalnyId": "A"}, None), MAPPING, stats) is None

    assert stats.read == 3
    assert dict(stats.rejected) == {REJECT_MISSING_ID: 1, REJECT_ID_TOO_LONG: 1, REJECT_NO_GEOMETRY: 1}


def test_building_keeps_functions_and_floors_but_no_name_remarks_or_egib_identifier() -> None:
    stats = LayerStats()
    raw = RawFeature(
        {
            "lokalnyId": "B-1",
            "kategoriaIstnienia": "eksploatowany",
            "funkcjaOgolnaBudynku": "budynki mieszkalne",
            "funkcjaSzczegolowaBudynku": "budynek jednorodzinny",
            "przewazajacaFunkcjaBudynku": "budynek jednorodzinny",
            "liczbaKondygnacji": "2",
            "zrodloDanychGeometrycznych": "EGiB",
            "wersja": "2026-01-22T00:00:00",
            # Pola, które importer nie czyta (a nawet gdyby je dostał, nie ma dla nich miejsca w wierszu).
            "nazwa": "Jan Kowalski",
            "uwagi": "dane osobowe",
            "identyfikatorEGiB": "PL.PZGIK.nr_ewid.123",
        },
        SQUARE,
    )

    row = normalize_building(raw, MAPPING, stats)

    assert row is not None and row.floors == 2 and row.general_function == "budynki mieszkalne"
    serialized = repr(row)
    assert "Jan Kowalski" not in serialized and "dane osobowe" not in serialized and "PL.PZGIK" not in serialized
    assert not any(hasattr(row, name) for name in ("name", "remarks", "egib_id", "nazwa", "uwagi"))


def test_null_is_not_zero_for_floors_and_width() -> None:
    assert parse_positive_int(None) is None and parse_positive_int("brak") is None and parse_positive_int("-1") is None
    assert parse_positive_int("0") == 0  # zero kondygnacji to wartość źródła (np. wiata), a nie brak danych
    assert parse_width(None) is None and parse_width("0") is None and parse_width("abc") is None
    assert parse_width("6.0") == 6.0
    assert clean_text(None) is None and clean_text("   ") is None


def test_version_without_time_zone_is_read_as_utc_and_garbage_is_none() -> None:
    assert parse_version("2025-05-12T00:00:00.000") == datetime(2025, 5, 12, tzinfo=timezone.utc)
    assert parse_version("2025-05-12T00:00:00+02:00") == datetime(2025, 5, 11, 22, 0, tzinfo=timezone.utc)
    assert parse_version("nie-data") is None and parse_version(None) is None


def test_duplicate_ids_keep_the_latest_version_and_every_extra_row_is_counted() -> None:
    stats = LayerStats()
    older = normalize_road(_road(wersja="2025-01-01T00:00:00", klasaDrogi="stara"), MAPPING, LayerStats())
    newer = normalize_road(_road(wersja="2026-01-01T00:00:00", klasaDrogi="nowa"), MAPPING, LayerStats())
    other = normalize_road(_road(lokalnyId="ROAD-2"), MAPPING, LayerStats())
    assert older and newer and other

    kept = keep_latest_per_id([older, other, newer], stats)

    assert [(row.local_id, row.road_class) for row in kept] == [("ROAD-1", "nowa"), ("ROAD-2", "droga lokalna")]
    assert stats.rejected[REJECT_DUPLICATE_ID] == 1


def test_data_as_of_is_the_newest_object_version_not_the_download_date() -> None:
    first, second = LayerStats(), LayerStats()
    normalize_road(_road(wersja="2024-09-11T12:00:00"), MAPPING, first)
    normalize_road(_road(lokalnyId="R2", wersja="2026-05-18T00:00:00"), MAPPING, second)

    assert data_as_of(first, second) == date(2026, 5, 18)
    assert data_as_of(LayerStats()) is None


def _unit(kind: str, teryt: str, wkb: bytes | None = SQUARE, name: str = "powiat Kraków") -> RawFeature:
    return RawFeature(
        {"rodzaj": kind, "identyfikatorTERYTjednostki": teryt, "nazwa": name}, wkb
    )


def test_extent_is_exactly_one_powiat_object_with_the_requested_teryt() -> None:
    features = [_unit("województwo", "12"), _unit("powiat", "1261"), _unit("gmina", "126101"), _unit("powiat", "1262")]

    extent = select_extent(features, MAPPING, "1261")

    assert extent.teryt_powiat == "1261" and extent.name == "powiat Kraków" and extent.geometry_wkb == SQUARE


@pytest.mark.parametrize(
    "features",
    [
        [],
        [_unit("powiat", "1262")],
        [_unit("powiat", "1261"), _unit("powiat", "1261")],
        [_unit("powiat", "1261", wkb=None)],
    ],
)
def test_package_without_an_unambiguous_extent_is_rejected(features: list[RawFeature]) -> None:
    with pytest.raises(Bdot10kValidationError, match="zasięg wydania"):
        select_extent(features, MAPPING, "1261")


# --- naprawa geometrii --------------------------------------------------------------------------


def test_valid_line_becomes_a_multilinestring_and_is_not_reported_as_repaired() -> None:
    result = ShapelyGeometryTools().repair_line(LINE)

    assert result.repaired is False and result.reject_reason is None
    geometry = load_wkb(result.wkb)
    assert isinstance(geometry, MultiLineString) and geometry.length == 10.0


def test_self_intersecting_polygon_is_repaired_and_keeps_a_positive_area() -> None:
    bow_tie = dump_wkb(Polygon([(0, 0), (10, 10), (10, 0), (0, 10)]))

    result = ShapelyGeometryTools().repair_polygon(bow_tie)

    assert result.repaired is True and result.wkb is not None and result.area_sqm == pytest.approx(50.0)
    geometry = load_wkb(result.wkb)
    assert isinstance(geometry, MultiPolygon) and geometry.is_valid


@pytest.mark.parametrize(
    ("method", "wkb", "reason"),
    [
        ("repair_line", dump_wkb(Point(1, 1)), "wrong_geometry_type"),
        ("repair_line", dump_wkb(LineString([(1, 1), (1, 1)])), "empty_after_repair"),
        ("repair_polygon", dump_wkb(LineString([(0, 0), (1, 1)])), "wrong_geometry_type"),
        ("repair_polygon", dump_wkb(Polygon([(0, 0), (5, 0), (10, 0), (0, 0)])), "empty_after_repair"),
        ("repair_polygon", b"not-a-wkb", "no_geometry"),
    ],
)
def test_unusable_geometry_is_rejected_with_a_specific_reason_never_silently_fixed(
    method: str, wkb: bytes, reason: str
) -> None:
    result = getattr(ShapelyGeometryTools(), method)(wkb)

    assert result.wkb is None and result.reject_reason == reason
