from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
import respx
from shapely.geometry import Polygon

from app.core.settings import settings
from app.services.gml import GmlFeature
from app.services.isok import (
    ISOK_SRS_NAME,
    ISOK_TYPE_NAME,
    IsokServiceUnavailableError,
    RiskFeature,
    _build_risk_feature,
    _classify_flood_probability,
    _parse_zone_response,
    fetch_flood_risks,
)

SQUARE_PARCEL = Polygon.from_bounds(500000, 200000, 500100, 200100)

# Strefa p=1% w całości wewnątrz działki (500020,200020)-(500080,200080).
MOCK_GML_ZONE_HIGH_OVERLAP = """<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:isok="http://isok.example/">
  <wfs:member>
    <isok:StrefaZagrozenia gml:id="f1">
      <isok:prawdopodobienstwo>1%</isok:prawdopodobienstwo>
      <isok:geometria>
        <gml:Polygon srsName="EPSG:2180">
          <gml:exterior>
            <gml:LinearRing>
              <gml:posList>500020 200020 500080 200020 500080 200080 500020 200080 500020 200020</gml:posList>
            </gml:LinearRing>
          </gml:exterior>
        </gml:Polygon>
      </isok:geometria>
    </isok:StrefaZagrozenia>
  </wfs:member>
</wfs:FeatureCollection>"""

# Strefa p=1% stykająca się dokładnie z krawędzią działki x=500100 — zero pola wspólnego.
MOCK_GML_ZONE_BOUNDARY_TOUCH = """<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:isok="http://isok.example/">
  <wfs:member>
    <isok:StrefaZagrozenia gml:id="f1">
      <isok:prawdopodobienstwo>1%</isok:prawdopodobienstwo>
      <isok:geometria>
        <gml:Polygon srsName="EPSG:2180">
          <gml:exterior>
            <gml:LinearRing>
              <gml:posList>500100 200000 500150 200000 500150 200100 500100 200100 500100 200000</gml:posList>
            </gml:LinearRing>
          </gml:exterior>
        </gml:Polygon>
      </isok:geometria>
    </isok:StrefaZagrozenia>
  </wfs:member>
</wfs:FeatureCollection>"""

# Strefa całkowicie rozłączna z działką.
MOCK_GML_ZONE_OUTSIDE = """<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:isok="http://isok.example/">
  <wfs:member>
    <isok:StrefaZagrozenia gml:id="f1">
      <isok:prawdopodobienstwo>1%</isok:prawdopodobienstwo>
      <isok:geometria>
        <gml:Polygon srsName="EPSG:2180">
          <gml:exterior>
            <gml:LinearRing>
              <gml:posList>500200 200000 500250 200000 500250 200050 500200 200050 500200 200000</gml:posList>
            </gml:LinearRing>
          </gml:exterior>
        </gml:Polygon>
      </isok:geometria>
    </isok:StrefaZagrozenia>
  </wfs:member>
</wfs:FeatureCollection>"""

# Strefa nakładająca się na działkę, ale bez rozpoznawalnego atrybutu prawdopodobieństwa.
MOCK_GML_ZONE_UNKNOWN_PROBABILITY = """<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:isok="http://isok.example/">
  <wfs:member>
    <isok:StrefaZagrozenia gml:id="f1">
      <isok:prawdopodobienstwo>nieznana</isok:prawdopodobienstwo>
      <isok:geometria>
        <gml:Polygon srsName="EPSG:2180">
          <gml:exterior>
            <gml:LinearRing>
              <gml:posList>500020 200020 500080 200020 500080 200080 500020 200080 500020 200020</gml:posList>
            </gml:LinearRing>
          </gml:exterior>
        </gml:Polygon>
      </isok:geometria>
    </isok:StrefaZagrozenia>
  </wfs:member>
</wfs:FeatureCollection>"""

# Strefa 50x50 pokrywająca dokładnie 25% powierzchni działki 100x100.
MOCK_GEOJSON_ZONE_MEDIUM = """{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "properties": {"prawdopodobienstwo": "10%"},
      "geometry": {
        "type": "Polygon",
        "coordinates": [[[500000, 200000], [500050, 200000], [500050, 200050], [500000, 200050], [500000, 200000]]]
      }
    }
  ]
}"""

EMPTY_GML = '<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0"></wfs:FeatureCollection>'
MALFORMED_GML = "<not><valid"

FETCHED_AT = datetime(2026, 7, 2, 12, 0, 0, tzinfo=timezone.utc)
_CONTRACTS = Path(__file__).resolve().parent / "fixtures" / "source_contracts"


def _risks_from_zones(zones: list[GmlFeature]) -> list[RiskFeature]:
    risks = []
    for zone in zones:
        risk = _build_risk_feature(
            SQUARE_PARCEL, zone.geometry, zone.properties, "url", FETCHED_AT
        )
        if risk is not None:
            risks.append(risk)
    return risks


# --- Główne kryteria akceptacji ----------------------------------------------


def test_zone_fully_overlapping_parcel_with_p1_gets_severity_high() -> None:
    zones = _parse_zone_response(MOCK_GML_ZONE_HIGH_OVERLAP)
    result = _risks_from_zones(zones)

    assert len(result) == 1
    assert result[0].severity == "high"
    assert result[0].intersection_area_sqm > 0
    assert result[0].probability_class == "1%"


def test_zone_boundary_touch_gets_boundary_touch_warning_not_full_risk() -> None:
    zones = _parse_zone_response(MOCK_GML_ZONE_BOUNDARY_TOUCH)
    result = _risks_from_zones(zones)

    assert len(result) == 1
    assert result[0].severity == "low"
    assert result[0].intersection_area_sqm == pytest.approx(0.0, abs=1e-6)
    assert any("boundary_touch" in w for w in result[0].warnings)


def test_zone_outside_parcel_is_not_included_in_results() -> None:
    zones = _parse_zone_response(MOCK_GML_ZONE_OUTSIDE)
    result = _risks_from_zones(zones)

    assert result == []


def test_zone_medium_probability_10_percent() -> None:
    zones = _parse_zone_response(MOCK_GEOJSON_ZONE_MEDIUM)
    result = _risks_from_zones(zones)

    assert result[0].severity == "medium"


def test_zone_unknown_probability_gets_conservative_severity_and_warning() -> None:
    zones = _parse_zone_response(MOCK_GML_ZONE_UNKNOWN_PROBABILITY)
    result = _risks_from_zones(zones)

    assert result[0].severity == "medium"
    assert len(result[0].warnings) >= 1


def test_empty_response_returns_empty_list_not_exception() -> None:
    assert _parse_zone_response(EMPTY_GML) == []


# --- Rozróżnienie wyjątek-vs-pusta-lista (critical_design_deviation) ---------


@pytest.mark.asyncio
@respx.mock
async def test_fetch_flood_risks_timeout_raises_isok_service_unavailable() -> None:
    respx.get(settings.isok_wfs_base_url).mock(
        side_effect=httpx.TimeoutException("timeout")
    )

    with pytest.raises(IsokServiceUnavailableError):
        await fetch_flood_risks(SQUARE_PARCEL)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_flood_risks_http_500_raises_isok_service_unavailable() -> None:
    respx.get(settings.isok_wfs_base_url).mock(return_value=httpx.Response(500))

    with pytest.raises(IsokServiceUnavailableError):
        await fetch_flood_risks(SQUARE_PARCEL)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_flood_risks_malformed_response_raises_isok_service_unavailable_not_empty_list() -> None:
    respx.get(settings.isok_wfs_base_url).mock(
        return_value=httpx.Response(200, text=MALFORMED_GML)
    )

    with pytest.raises(IsokServiceUnavailableError):
        await fetch_flood_risks(SQUARE_PARCEL)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_flood_risks_success_returns_risk_features_via_respx() -> None:
    respx.get(settings.isok_wfs_base_url).mock(
        return_value=httpx.Response(200, text=MOCK_GML_ZONE_HIGH_OVERLAP)
    )

    result = await fetch_flood_risks(SQUARE_PARCEL)

    assert len(result) == 1


@pytest.mark.asyncio
@respx.mock
async def test_fetch_flood_risks_sends_bbox_with_epsg2180() -> None:
    route = respx.get(settings.isok_wfs_base_url).mock(
        return_value=httpx.Response(200, text=MOCK_GML_ZONE_HIGH_OVERLAP)
    )

    await fetch_flood_risks(SQUARE_PARCEL)

    assert "EPSG:2180" in route.calls.last.request.url.params["bbox"]


@pytest.mark.asyncio
@respx.mock
async def test_fetch_flood_risks_empty_bbox_response_returns_empty_list_no_exception() -> None:
    respx.get(settings.isok_wfs_base_url).mock(
        return_value=httpx.Response(200, text=EMPTY_GML)
    )

    result = await fetch_flood_risks(SQUARE_PARCEL)

    assert result == []


# --- Klasyfikacja prawdopodobieństwa -----------------------------------------


def test_classify_flood_probability_1_percent_is_high() -> None:
    assert _classify_flood_probability({"prawdopodobienstwo": "1%"}) == (
        "1%",
        "high",
        None,
    )


def test_classify_flood_probability_10_percent_is_medium_not_high() -> None:
    # Regresja przeciw fałszywemu dopasowaniu substring '1%' wewnątrz '10%'.
    assert _classify_flood_probability({"prawdopodobienstwo": "10%"})[1] == "medium"


def test_classify_flood_probability_0_2_percent_is_low() -> None:
    assert _classify_flood_probability({"prawdopodobienstwo": "0,2%"})[1] == "low"


def test_classify_flood_probability_missing_attribute_returns_none_and_conservative_severity() -> None:
    result = _classify_flood_probability({})

    assert result[0] is None
    assert result[1] == "medium"
    assert result[2] is not None


# --- _build_risk_feature -------------------------------------------------------


def test_build_risk_feature_disjoint_returns_none() -> None:
    disjoint_zone = Polygon.from_bounds(500200, 200000, 500250, 200050)

    result = _build_risk_feature(SQUARE_PARCEL, disjoint_zone, {}, "url", FETCHED_AT)

    assert result is None


def test_risk_feature_area_ratio_calculated_correctly() -> None:
    # Strefa pokrywająca dokładnie 25% powierzchni działki (50x50 z 100x100).
    zone = Polygon.from_bounds(500000, 200000, 500050, 200050)

    result = _build_risk_feature(
        SQUARE_PARCEL, zone, {"prawdopodobienstwo": "1%"}, "url", FETCHED_AT
    )

    assert result is not None
    assert result.area_ratio == pytest.approx(0.25, abs=0.01)


def test_risk_feature_has_source_metadata_isok() -> None:
    zone = Polygon.from_bounds(500000, 200000, 500050, 200050)

    result = _build_risk_feature(
        SQUARE_PARCEL, zone, {"prawdopodobienstwo": "1%"}, "url", FETCHED_AT
    )

    assert result is not None
    assert result.source_metadata.source_name == "ISOK"


def test_risk_feature_is_dataclass_instance() -> None:
    zone = Polygon.from_bounds(500000, 200000, 500050, 200050)

    result = _build_risk_feature(
        SQUARE_PARCEL, zone, {"prawdopodobienstwo": "1%"}, "url", FETCHED_AT
    )

    assert isinstance(result, RiskFeature)


# --- Kontrakt realnej usługi INSPIRE (potwierdzony 2026-07-30) ----------------


@pytest.mark.asyncio
@respx.mock
async def test_fetch_requests_hazard_area_layer_and_projected_srs() -> None:
    """Bez jawnego srsName usługa zwróciłaby geometrię w stopniach (EPSG:4258).

    Serwer akceptuje wyłącznie formę URN — skrót ``EPSG:2180`` powoduje HTTP 500,
    dlatego kształt tego parametru jest częścią kontraktu, nie kosmetyką.
    """
    route = respx.get(settings.isok_wfs_base_url).mock(
        return_value=httpx.Response(200, text=EMPTY_GML)
    )

    await fetch_flood_risks(SQUARE_PARCEL)

    params = route.calls.last.request.url.params
    assert params["typeNames"] == ISOK_TYPE_NAME == "nz-core:HazardArea"
    assert params["srsName"] == ISOK_SRS_NAME
    assert params["srsName"].startswith("urn:ogc:def:crs:EPSG:")


def test_real_getfeature_fixture_parsed_with_swapped_axes_and_holes() -> None:
    """Realna cecha ma srsName w formie HTTP, czyli kolejność (northing, easting).

    Współrzędne w fixture to 374871..374899 (northing) i 698899..698907 (easting),
    więc po sprowadzeniu do konwencji systemu easting musi być pierwszą osią.
    Cecha ma też pierścienie wewnętrzne, które muszą trafić do geometrii.
    """
    payload = (_CONTRACTS / "isok_getfeature.xml").read_text(encoding="utf-8")

    zones = _parse_zone_response(payload)

    assert len(zones) == 1
    minx, miny, maxx, maxy = zones[0].geometry.bounds
    assert 698890 <= minx <= 698910
    assert 374860 <= miny <= 374910
    assert 698890 <= maxx <= 698910
    assert 374860 <= maxy <= 374910
    assert len(zones[0].geometry.interiors) == 2


def test_real_getfeature_fixture_classifies_probability_from_inspire_field() -> None:
    payload = (_CONTRACTS / "isok_getfeature.xml").read_text(encoding="utf-8")

    zones = _parse_zone_response(payload)
    probability_class, severity, warning = _classify_flood_probability(
        zones[0].properties
    )

    assert probability_class == "scenariusz Q 0,2% (raz na 500 lat)"
    assert severity == "low"
    assert warning is None


def test_probability_ignores_inconsistent_numeric_field() -> None:
    """probabilityOfOccurrence bywa 0.02 dla scenariusza 0,2% — nie używamy go.

    Klasyfikacja opiera się na tekście qualitativeLikelihood, więc niespójna
    wartość liczbowa nie może zmienić wyniku.
    """
    properties = {
        "qualitativeLikelihood": "scenariusz Q 1% (raz na 100 lat)",
        "probabilityOfOccurrence": "0.02",
        "returnPeriod": "100.0",
    }

    assert _classify_flood_probability(properties)[1] == "high"


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("scenariusz Q 10% (raz na 10 lat)", "medium"),
        ("scenariusz Q 1% (raz na 100 lat)", "high"),
        ("scenariusz Q 0,2% (raz na 500 lat)", "low"),
    ],
)
def test_real_inspire_scenarios_map_to_expected_severity(
    scenario: str, expected: str
) -> None:
    assert _classify_flood_probability({"qualitativeLikelihood": scenario})[1] == (
        expected
    )


def test_return_period_is_used_when_scenario_text_is_unrecognized() -> None:
    properties = {"qualitativeLikelihood": "brak opisu", "returnPeriod": "100.0"}

    _, severity, warning = _classify_flood_probability(properties)

    assert severity == "high"
    assert warning is None


@pytest.mark.asyncio
@respx.mock
async def test_fetch_exception_report_http_200_raises_not_empty_list() -> None:
    """Usługi WFS zgłaszają błędy statusem 200, więc raise_for_status milczy."""
    payload = (_CONTRACTS / "gdos_exception_report.xml").read_text(encoding="utf-8")
    respx.get(settings.isok_wfs_base_url).mock(
        return_value=httpx.Response(200, text=payload)
    )

    with pytest.raises(IsokServiceUnavailableError):
        await fetch_flood_risks(SQUARE_PARCEL)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_unexpected_crs_raises_instead_of_silent_miss() -> None:
    """Geometria w innym układzie nie może cicho dać "brak zagrożenia"."""
    payload = """<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:nz="http://inspire.example/">
  <wfs:member>
    <nz:HazardArea>
      <nz:geometry>
        <gml:Polygon srsName="urn:ogc:def:crs:EPSG::4258">
          <gml:exterior><gml:LinearRing><gml:posList>52.2 21.0 52.3 21.0 52.3 21.1 52.2 21.1 52.2 21.0</gml:posList></gml:LinearRing></gml:exterior>
        </gml:Polygon>
      </nz:geometry>
    </nz:HazardArea>
  </wfs:member>
</wfs:FeatureCollection>"""
    respx.get(settings.isok_wfs_base_url).mock(
        return_value=httpx.Response(200, text=payload)
    )

    with pytest.raises(IsokServiceUnavailableError):
        await fetch_flood_risks(SQUARE_PARCEL)
