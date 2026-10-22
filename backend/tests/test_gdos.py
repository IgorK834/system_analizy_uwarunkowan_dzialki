"""Testy adaptera GDOŚ (formy ochrony przyrody).

Kontrakt usługi potwierdzono realnymi zapytaniami 2026-07-30 — fixtures w
``tests/fixtures/source_contracts/gdos_*``. Testy pilnują trzech rzeczy, które
w realnej usłudze zachowują się inaczej niż w naiwnej implementacji:
brak ``typeNames`` daje błąd ze statusem HTTP 200, rodzaj ochrony wynika z
odpytanej warstwy (nie z atrybutu cechy), a każda warstwa wymaga osobnego
zapytania.
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx
from shapely.geometry import Point, box, shape

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
    fetch_nature_protection_section,
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
        "GDOS:PomnikiPrzyrodyPunktowe",
        "GDOS:ramsar",
        "GDOS:korytarzeEkologiczne",
        "GDOS:ElektrownieWiatrowe",
    }
    # Oba warianty Natura 2000 muszą dawać ten sam znormalizowany typ, bo
    # analysis_orchestrator odwzorowuje 'natura2000' na risk_type 'natura_2000'.
    assert GDOS_PROTECTION_LAYERS["GDOS:ObszarySpecjalnejOchrony"] == "natura2000"
    assert GDOS_PROTECTION_LAYERS["GDOS:SpecjalneObszaryOchrony"] == "natura2000"


# --- Parsowanie odpowiedzi ----------------------------------------------------


def test_valid_empty_collection_returns_empty_list_but_blank_response_is_unavailable() -> None:
    assert _parse_layer_response(EMPTY_GML) == []
    with pytest.raises(GdosServiceUnavailableError):
        _parse_layer_response("   ")


def test_geojson_without_geometry_is_ignored() -> None:
    payload = json.dumps(
        {"type": "FeatureCollection", "features": [{"properties": {}}]}
    )

    assert _parse_layer_response(payload) == []


def test_geojson_point_geometry_is_parsed() -> None:
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

    parsed = _parse_layer_response(payload)
    assert len(parsed) == 1
    assert parsed[0].geometry.equals(Point(1, 2))


def test_point_monument_distance_uses_parcel_polygon_and_has_no_fake_area() -> None:
    feature = _build_nature_protection_feature(
        SQUARE_PARCEL,
        Point(500250, 200050),
        {"nazwa": "Dąb testowy"},
        "https://example.test/gdos",
        datetime.now(UTC),
        protection_type="pomnik_przyrody",
        include_near=True,
        search_radius_m=200.0,
        layer_name="GDOS:PomnikiPrzyrodyPunktowe",
    )

    assert feature is not None
    assert feature.proximity_status == "near"
    assert feature.distance_m == pytest.approx(150.0)
    assert feature.intersection_area_sqm == 0.0


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


@pytest.mark.parametrize(
    ("filename", "layer", "expected_name", "expected_xy"),
    [
        (
            "monument-point.xml",
            "PomnikiPrzyrodyPunktowe",
            "Wojtek",
            (295613.17, 575509.9),
        ),
        (
            "wind-turbine.xml",
            "ElektrownieWiatrowe",
            "Budowa elektrowni wiatrowej o mocy 1,5 Mwe w miejscowści Bargły",
            (508490.717309904, 315524.8191028603),
        ),
    ],
)
def test_real_point_contract_fixtures_preserve_axis_order_and_name(
    filename: str,
    layer: str,
    expected_name: str,
    expected_xy: tuple[float, float],
) -> None:
    payload = (_CONTRACTS / "gdos_proximity" / filename).read_text(encoding="utf-8")

    parsed = _parse_layer_response(payload)

    assert len(parsed) == 1
    assert parsed[0].layer == layer
    assert parsed[0].geometry.x == pytest.approx(expected_xy[0])
    assert parsed[0].geometry.y == pytest.approx(expected_xy[1])
    assert (
        parsed[0].properties.get("nazwa")
        or parsed[0].properties.get("nazwa_przedsiewziecia")
    ) == expected_name


def test_real_audit_parcel_has_no_monuments_within_200m_and_keeps_response_hashes() -> None:
    root = _CONTRACTS / "gdos_proximity"
    evidence = json.loads((root / "gora-kalwaria-verification.json").read_text(encoding="utf-8"))

    for kind in ("point", "surface"):
        path = root / f"gora-kalwaria-monuments-{kind}-empty.xml"
        payload = path.read_text(encoding="utf-8")
        assert _parse_layer_response(payload) == []
        assert hashlib.sha256(path.read_bytes()).hexdigest() == evidence["monuments"][f"{kind}_fixture_sha256"]

    assert evidence["parcel_identifier"] == "141801_4.0701.23/8"
    assert evidence["protected_landscape"]["name"] == "Warszawski"
    assert evidence["protected_landscape"]["intersection_pct"] == 100.0
    assert evidence["monuments"]["search_radius_m"] == 200.0


@pytest.mark.parametrize(
    (
        "filename",
        "layer",
        "expected_sha256",
        "expected_name",
        "expected_area_sqm",
        "expected_area_ratio",
        "expected_severity",
    ),
    [
        (
            "ruciane-nida-birds.xml",
            "GDOS:ObszarySpecjalnejOchrony",
            "f0bc9dd6c9fc74379e40b339a45f1a65934d07dad1bbc67de6422c8334e03911",
            "Puszcza Piska",
            265703.139,
            1.0,
            "high",
        ),
        (
            "ruciane-nida-habitats.xml",
            "GDOS:SpecjalneObszaryOchrony",
            "6314e89cd5e938000e37bdacdca13307ba5a257887dcd4f7e03437fcd2189a33",
            "Ostoja Piska",
            2766.727,
            0.01041285,
            "low",
        ),
    ],
)
def test_real_ruciane_nida_parcel_intersects_natura_2000_contract_fixtures(
    filename: str,
    layer: str,
    expected_sha256: str,
    expected_name: str,
    expected_area_sqm: float,
    expected_area_ratio: float,
    expected_severity: str,
) -> None:
    """Regresja odbiorowa dla rzeczywistej działki z Rucianego-Nidy."""
    fixture_path = _CONTRACTS / "gdos_proximity" / filename
    payload = fixture_path.read_text(encoding="utf-8")
    parcel_path = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "reference_corpus"
        / "artifacts"
        / "parcels"
        / "real-026-281604-5-0011-107.geojson"
    )
    parcel = shape(json.loads(parcel_path.read_text(encoding="utf-8"))["geometry"])

    parsed = _parse_layer_response(payload, layer)

    assert hashlib.sha256(fixture_path.read_bytes()).hexdigest() == expected_sha256
    assert len(parsed) == 1
    feature = _build_nature_protection_feature(
        parcel,
        parsed[0].geometry,
        parsed[0].properties,
        "https://sdi.gdos.gov.pl/wfs",
        datetime(2026, 10, 21, tzinfo=UTC),
        protection_type="natura2000",
        feature_id=parsed[0].feature_id,
        layer_name=layer,
    )

    assert feature is not None
    assert feature.name == expected_name
    assert feature.protection_type == "natura2000"
    assert feature.proximity_status == "intersection"
    assert feature.intersection_area_sqm == pytest.approx(expected_area_sqm, abs=1.0)
    assert feature.area_ratio == pytest.approx(expected_area_ratio, abs=0.0001)
    assert feature.severity == expected_severity


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
        "499000.0,199000.0,501100.0,201100.0"
    )
    monument_calls = [
        call
        for call in route.calls
        if call.request.url.params["typeNames"]
        in {"GDOS:PomnikiPrzyrodyPowierzchniowe", "GDOS:PomnikiPrzyrodyPunktowe"}
    ]
    assert all(
        call.request.url.params["bbox"].startswith(
            "499800.0,199800.0,500300.0,200300.0"
        )
        for call in monument_calls
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

    assert result[0].source_metadata.source_name == "GDOŚ"
    assert result[0].source_metadata.manual_review_required is False


@respx.mock
@pytest.mark.asyncio
async def test_empty_layers_return_explicit_none_within_radius_per_layer() -> None:
    respx.get(settings.gdos_wfs_base_url).mock(
        return_value=httpx.Response(200, text=EMPTY_GML)
    )

    section = await fetch_nature_protection_section(SQUARE_PARCEL)

    assert section.features == []
    assert len(section.proximities) == len(GDOS_PROTECTION_LAYERS) == 14
    assert all(item.status == "none_within_radius" for item in section.proximities)
    monuments = [item for item in section.proximities if "Pomniki" in item.label]
    assert len(monuments) == 2
    assert all(item.search_radius_m == 200.0 for item in monuments)
