"""Testy adaptera GDOŚ (formy ochrony przyrody).

Kontrakt usługi potwierdzono realnymi zapytaniami 2026-07-30 — fixtures w
``tests/fixtures/source_contracts/gdos_*``. Testy pilnują trzech rzeczy, które
w realnej usłudze zachowują się inaczej niż w naiwnej implementacji:
brak ``typeNames`` daje błąd ze statusem HTTP 200, rodzaj ochrony wynika z
odpytanej warstwy (nie z atrybutu cechy), a każda warstwa wymaga osobnego
zapytania.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx
from shapely.geometry import box

from app.core.settings import settings
from app.services.gdos import (
    GDOS_PROTECTION_LAYERS,
    GdosServiceUnavailableError,
    NatureProtectionFeature,
    _build_nature_protection_feature,
    _normalize_protection_type_value,
    _parse_layer_response,
    _ratio_based_severity,
    fetch_nature_protection_areas,
)

SQUARE_PARCEL = box(500000, 200000, 500100, 200100)
_CONTRACTS = Path(__file__).resolve().parent / "fixtures" / "source_contracts"


def _gml_polygon(
    bounds: tuple[float, float, float, float],
    protection_type: str,
    name: str = "Obszar testowy",
    layer: str = "forma",
) -> str:
    """Buduje uproszczoną odpowiedź z ``srsName`` w formie skróconej.

    Forma skrócona ``EPSG:2180`` oznacza kolejność osi (easting, northing), więc
    współrzędne w tym helperze są zapisane wprost jak w geometrii kanonicznej.
    """
    minx, miny, maxx, maxy = bounds
    pos_list = (
        f"{minx} {miny} {maxx} {miny} {maxx} {maxy} "
        f"{minx} {maxy} {minx} {miny}"
    )
    return f"""
    <wfs:FeatureCollection
        xmlns:wfs="http://www.opengis.net/wfs/2.0"
        xmlns:gml="http://www.opengis.net/gml/3.2"
        xmlns:gdos="https://sdi.gdos.gov.pl">
      <wfs:member>
        <gdos:{layer}>
          <gdos:forma_ochrony>{protection_type}</gdos:forma_ochrony>
          <gdos:nazwa>{name}</gdos:nazwa>
          <gdos:geometry>
            <gml:Polygon srsName="EPSG:2180">
              <gml:exterior><gml:LinearRing><gml:posList>{pos_list}</gml:posList></gml:LinearRing></gml:exterior>
            </gml:Polygon>
          </gdos:geometry>
        </gdos:{layer}>
      </wfs:member>
    </wfs:FeatureCollection>
    """


MOCK_GML_FULL_COVERAGE = _gml_polygon(
    (499990, 199990, 500110, 200110), "Natura 2000"
)
MOCK_GML_PARTIAL_25_PERCENT = _gml_polygon(
    (500000, 200000, 500050, 200050), "park krajobrazowy"
)
MOCK_GML_DISJOINT = _gml_polygon(
    (500200, 200000, 500250, 200050), "Natura 2000"
)
MOCK_GML_UNKNOWN_TYPE = _gml_polygon(
    (500000, 200000, 500050, 200050), "XYZ_NIEZNANY"
)
MOCK_GML_RESERVE_SMALL_OVERLAP = _gml_polygon(
    (500000, 200000, 500005, 200100), "rezerwat przyrody"
)
MOCK_GML_BOUNDARY_TOUCH_RESERVE = _gml_polygon(
    (500100, 200000, 500110, 200100), "rezerwat przyrody"
)
MOCK_GEOJSON_NATURA2000_LOW = json.dumps(
    {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "forma_ochrony": "Natura 2000",
                    "nazwa": "Dolina X",
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [500000, 200000],
                            [500005, 200000],
                            [500005, 200100],
                            [500000, 200100],
                            [500000, 200000],
                        ]
                    ],
                },
            }
        ],
    }
)
EMPTY_GML = (
    '<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0">'
    "</wfs:FeatureCollection>"
)
MALFORMED_GML = "<not><valid"


def _features_from_payload(
    payload: str, protection_type: str | None = None
) -> list[NatureProtectionFeature]:
    features: list[NatureProtectionFeature] = []
    for zone in _parse_layer_response(payload):
        feature = _build_nature_protection_feature(
            SQUARE_PARCEL,
            zone.geometry,
            zone.properties,
            "https://example.test/gdos",
            datetime.now(UTC),
            protection_type=protection_type,
        )
        if feature is not None:
            features.append(feature)
    return features


# --- Klasyfikacja severity ----------------------------------------------------


def test_full_coverage_gets_severity_high() -> None:
    result = _features_from_payload(MOCK_GML_FULL_COVERAGE)

    assert result[0].severity == "high"
    assert result[0].area_ratio >= 0.99


def test_partial_intersection_contains_area_ratio_and_geometry() -> None:
    result = _features_from_payload(MOCK_GML_PARTIAL_25_PERCENT)

    assert result[0].area_ratio == pytest.approx(0.25, abs=0.01)
    assert result[0].geometry.area == pytest.approx(2500.0, abs=1.0)
    assert result[0].intersection_area_sqm == pytest.approx(2500.0, abs=1.0)
    assert result[0].severity == "medium"


def test_no_collision_returns_empty_list() -> None:
    assert _features_from_payload(MOCK_GML_DISJOINT) == []


def test_unknown_protection_type_handled_with_warning_not_exception() -> None:
    result = _features_from_payload(MOCK_GML_UNKNOWN_TYPE)

    assert result[0].protection_type == "unknown"
    assert len(result[0].warnings) >= 1
    assert result[0].source_metadata.manual_review_required is True
    assert result[0].source_metadata.confidence == 0.4


def test_reserve_small_overlap_still_high() -> None:
    result = _features_from_payload(MOCK_GML_RESERVE_SMALL_OVERLAP)

    assert result[0].area_ratio == pytest.approx(0.05)
    assert result[0].severity == "high"


def test_reserve_boundary_touch_overrides_to_low() -> None:
    result = _features_from_payload(MOCK_GML_BOUNDARY_TOUCH_RESERVE)

    assert result[0].severity == "low"
    assert any("boundary_touch" in warning for warning in result[0].warnings)


def test_natura2000_low_ratio_is_low() -> None:
    result = _features_from_payload(MOCK_GEOJSON_NATURA2000_LOW)

    assert result[0].severity == "low"
    assert result[0].name == "Dolina X"
    assert result[0].protection_type == "natura2000"


@pytest.mark.parametrize(
    ("ratio", "expected"),
    [(0.3, "medium"), (0.6, "high"), (0.05, "low")],
)
def test_ratio_based_severity_thresholds(ratio: float, expected: str) -> None:
    assert _ratio_based_severity(ratio) == expected


def test_normalize_protection_type_recognizes_multiple_keywords() -> None:
    assert (
        _normalize_protection_type_value("Rezerwat przyrody Bór")
        == "rezerwat_przyrody"
    )
    assert (
        _normalize_protection_type_value("Park  Krajobrazowy Dolina")
        == "park_krajobrazowy"
    )
    assert _normalize_protection_type_value("coś zupełnie innego") == "unknown"


# --- Rodzaj ochrony pochodzi z warstwy, nie z atrybutu cechy ------------------


def test_protection_type_from_layer_wins_over_attributes() -> None:
    # Realne cechy GDOŚ nie mają atrybutu rodzaju ochrony, więc typ przekazany
    # z warstwy musi mieć pierwszeństwo — także wtedy, gdy atrybut istnieje.
    payload = _gml_polygon((499990, 199990, 500110, 200110), "park krajobrazowy")

    result = _features_from_payload(payload, protection_type="park_narodowy")

    assert result[0].protection_type == "park_narodowy"
    assert result[0].source_metadata.manual_review_required is False


def test_layer_protection_type_applies_always_high_rule() -> None:
    # Mały udział powierzchni, ale rezerwat rozpoznany z warstwy → high.
    payload = _gml_polygon((500000, 200000, 500005, 200100), "brak atrybutu")

    result = _features_from_payload(payload, protection_type="rezerwat_przyrody")

    assert result[0].area_ratio == pytest.approx(0.05)
    assert result[0].severity == "high"


def test_all_declared_layers_map_to_known_protection_types() -> None:
    assert set(GDOS_PROTECTION_LAYERS) == {
        "GDOS:ParkiNarodowe",
        "GDOS:Rezerwaty",
        "GDOS:ObszarySpecjalnejOchrony",
        "GDOS:SpecjalneObszaryOchrony",
        "GDOS:ParkiKrajobrazowe",
        "GDOS:ObszaryChronionegoKrajobrazu",
        "GDOS:UzytkiEkologiczne",
        "GDOS:ZespolyPrzyrodniczoKrajobrazowe",
        "GDOS:StanowiskaDokumentacyjne",
        "GDOS:PomnikiPrzyrodyPowierzchniowe",
    }
    # Oba warianty Natura 2000 muszą dawać ten sam znormalizowany typ, bo
    # analysis_orchestrator odwzorowuje 'natura2000' na risk_type 'natura_2000'.
    assert GDOS_PROTECTION_LAYERS["GDOS:ObszarySpecjalnejOchrony"] == "natura2000"
    assert GDOS_PROTECTION_LAYERS["GDOS:SpecjalneObszaryOchrony"] == "natura2000"


# --- Parsowanie odpowiedzi ----------------------------------------------------


def test_empty_response_returns_empty_list() -> None:
    assert _parse_layer_response(EMPTY_GML) == []
    assert _parse_layer_response("   ") == []


def test_geojson_without_geometry_is_ignored() -> None:
    payload = json.dumps(
        {"type": "FeatureCollection", "features": [{"properties": {}}]}
    )

    assert _parse_layer_response(payload) == []


def test_geojson_non_polygon_geometry_is_ignored() -> None:
    payload = json.dumps(
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "properties": {},
                    "geometry": {"type": "Point", "coordinates": [1, 2]},
                }
            ],
        }
    )

    assert _parse_layer_response(payload) == []


def test_real_getfeature_fixture_is_parsed_with_swapped_axes() -> None:
    """Realna cecha ma srsName w formie URN, czyli kolejność (northing, easting).

    Koperta cechy podana przez usługę to 389350..389404 / 706474..706508, więc po
    sprowadzeniu do konwencji systemu easting musi być w zakresie 706474..706508.
    """
    payload = (_CONTRACTS / "gdos_getfeature.xml").read_text(encoding="utf-8")

    features = _parse_layer_response(payload)

    assert len(features) == 1
    assert features[0].layer == "PomnikiPrzyrodyPowierzchniowe"
    minx, miny, maxx, maxy = features[0].geometry.bounds
    assert 706474 <= minx <= 706509
    assert 389350 <= miny <= 389405
    assert 706474 <= maxx <= 706509
    assert 389350 <= maxy <= 389405


def test_real_getfeature_fixture_tolerates_missing_name() -> None:
    payload = (_CONTRACTS / "gdos_getfeature.xml").read_text(encoding="utf-8")

    features = _parse_layer_response(payload)
    feature = _build_nature_protection_feature(
        features[0].geometry,
        features[0].geometry,
        features[0].properties,
        "https://example.test/gdos",
        datetime.now(UTC),
        protection_type="pomnik_przyrody",
    )

    assert feature is not None
    assert feature.name is None
    assert feature.protection_type == "pomnik_przyrody"


def test_exception_report_with_http_200_raises_not_empty_list() -> None:
    payload = (_CONTRACTS / "gdos_exception_report.xml").read_text(encoding="utf-8")

    with pytest.raises(GdosServiceUnavailableError):
        _parse_layer_response(payload)


# --- Zachowanie sieciowe ------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_fetch_timeout_raises_gdos_service_unavailable() -> None:
    respx.get(settings.gdos_wfs_base_url).mock(
        side_effect=httpx.TimeoutException("timeout")
    )

    with pytest.raises(GdosServiceUnavailableError):
        await fetch_nature_protection_areas(SQUARE_PARCEL)


@respx.mock
@pytest.mark.asyncio
async def test_fetch_http_500_raises_gdos_service_unavailable() -> None:
    respx.get(settings.gdos_wfs_base_url).mock(return_value=httpx.Response(500))

    with pytest.raises(GdosServiceUnavailableError):
        await fetch_nature_protection_areas(SQUARE_PARCEL)


@respx.mock
@pytest.mark.asyncio
async def test_fetch_malformed_response_raises_not_empty_list() -> None:
    respx.get(settings.gdos_wfs_base_url).mock(
        return_value=httpx.Response(200, text=MALFORMED_GML)
    )

    with pytest.raises(GdosServiceUnavailableError):
        await fetch_nature_protection_areas(SQUARE_PARCEL)


@respx.mock
@pytest.mark.asyncio
async def test_fetch_exception_report_http_200_raises_not_empty_list() -> None:
    """Regresja: usługa zgłasza błąd statusem 200, więc raise_for_status milczy."""
    payload = (_CONTRACTS / "gdos_exception_report.xml").read_text(encoding="utf-8")
    respx.get(settings.gdos_wfs_base_url).mock(
        return_value=httpx.Response(200, text=payload)
    )

    with pytest.raises(GdosServiceUnavailableError):
        await fetch_nature_protection_areas(SQUARE_PARCEL)


@respx.mock
@pytest.mark.asyncio
async def test_fetch_queries_every_layer_separately_with_type_names() -> None:
    route = respx.get(settings.gdos_wfs_base_url).mock(
        return_value=httpx.Response(200, text=EMPTY_GML)
    )

    await fetch_nature_protection_areas(SQUARE_PARCEL)

    requested = [call.request.url.params["typeNames"] for call in route.calls]
    assert sorted(requested) == sorted(GDOS_PROTECTION_LAYERS)
    # Każde zapytanie musi mieć dokładnie jedną warstwę — usługa odrzuca listy.
    assert all("," not in type_name for type_name in requested)


@respx.mock
@pytest.mark.asyncio
async def test_fetch_assigns_protection_type_per_queried_layer() -> None:
    def _respond(request: httpx.Request) -> httpx.Response:
        type_name = request.url.params["typeNames"]
        if type_name == "GDOS:Rezerwaty":
            return httpx.Response(
                200,
                text=_gml_polygon(
                    (500000, 200000, 500005, 200100),
                    "brak atrybutu rodzaju",
                    name="Rezerwat Testowy",
                    layer="Rezerwaty",
                ),
            )
        return httpx.Response(200, text=EMPTY_GML)

    respx.get(settings.gdos_wfs_base_url).mock(side_effect=_respond)

    result = await fetch_nature_protection_areas(SQUARE_PARCEL)

    assert len(result) == 1
    assert result[0].protection_type == "rezerwat_przyrody"
    assert result[0].name == "Rezerwat Testowy"
    # Rezerwat daje high nawet przy 5% udziału powierzchni.
    assert result[0].severity == "high"


@respx.mock
@pytest.mark.asyncio
async def test_fetch_sends_bbox_with_epsg2180() -> None:
    route = respx.get(settings.gdos_wfs_base_url).mock(
        return_value=httpx.Response(200, text=EMPTY_GML)
    )

    await fetch_nature_protection_areas(SQUARE_PARCEL)

    assert "EPSG:2180" in route.calls.last.request.url.params["bbox"]
    # Kolejność bbox to (minE,minN,maxE,maxN) — odwrotny bbox usługa odrzuca.
    assert route.calls.last.request.url.params["bbox"].startswith(
        "500000.0,200000.0,500100.0,200100.0"
    )


@respx.mock
@pytest.mark.asyncio
async def test_single_failing_layer_fails_whole_section() -> None:
    """Nie wolno raportować braku kolizji, gdy jednej kategorii nie sprawdzono."""

    def _respond(request: httpx.Request) -> httpx.Response:
        if request.url.params["typeNames"] == "GDOS:ParkiNarodowe":
            return httpx.Response(503)
        return httpx.Response(200, text=EMPTY_GML)

    respx.get(settings.gdos_wfs_base_url).mock(side_effect=_respond)

    with pytest.raises(GdosServiceUnavailableError):
        await fetch_nature_protection_areas(SQUARE_PARCEL)


@respx.mock
@pytest.mark.asyncio
async def test_risk_feature_has_source_metadata_gdos() -> None:
    def _respond(request: httpx.Request) -> httpx.Response:
        if request.url.params["typeNames"] == "GDOS:ParkiNarodowe":
            return httpx.Response(200, text=MOCK_GML_FULL_COVERAGE)
        return httpx.Response(200, text=EMPTY_GML)

    respx.get(settings.gdos_wfs_base_url).mock(side_effect=_respond)

    result = await fetch_nature_protection_areas(SQUARE_PARCEL)

    assert result[0].source_metadata.source_name == "GDOS"
    assert result[0].source_metadata.manual_review_required is False
