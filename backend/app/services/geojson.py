from __future__ import annotations

from typing import Any

from pyproj import Transformer
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform

# Osobny Transformer od tego w geometry.py, bo tutaj pyproj pracuje jako funkcja
# przekazywana do shapely.ops.transform i transformuje całe geometrie Shapely.
_TO_WGS84_GEOM = Transformer.from_crs("EPSG:2180", "EPSG:4326", always_xy=True)


def transform_geometry_to_wgs84(geometry: BaseGeometry) -> BaseGeometry:
    """
    Transformuje całą geometrię Shapely z EPSG:2180 do WGS84 (EPSG:4326).

    Użycie always_xy=True jest obowiązkowe, spójnie z geometry.py, ponieważ
    projekt konsekwentnie traktuje współrzędne jako pary x/y oraz lon/lat.
    Funkcja nie przelicza pól powierzchni ani długości; zmienia wyłącznie
    współrzędne geometrii do wizualizacji na mapie. Wynikowa geometria ma
    współrzędne w kolejności (lon, lat).
    """

    def _project(
        x: Any, y: Any, z: Any = None
    ) -> tuple[Any, Any] | tuple[Any, Any, Any]:
        lon, lat = _TO_WGS84_GEOM.transform(x, y)
        return (lon, lat) if z is None else (lon, lat, z)

    return shapely_transform(_project, geometry)


def geometry_to_geojson_feature(
    geometry: BaseGeometry, properties: dict[str, Any] | None = None
) -> dict[str, Any]:
    """
    Konwertuje geometrię Shapely w WGS84 do słownika GeoJSON Feature.

    Geometria wejściowa musi być już przetransformowana do WGS84; ta funkcja
    nie robi transformacji, tylko serializuje obiekt Shapely. Parametr
    properties pozwala dołączyć metadane warstwy, np. parcel albo
    buildable_area, bez zmiany geometrii.
    """
    return {
        "type": "Feature",
        "geometry": _json_compatible(mapping(geometry)),
        "properties": properties or {},
    }


def geometries_to_geojson_feature_collection(
    features: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Opakowuje listę gotowych obiektów GeoJSON Feature w FeatureCollection.

    Używane, gdy odpowiedź API musi zwrócić więcej niż jedną warstwę geometryczną
    naraz, np. działkę i osobno obszar zabudowy.
    """
    return {"type": "FeatureCollection", "features": features}


def parcel_geometry_to_geojson(
    geometry: BaseGeometry, parcel_identifier: str
) -> dict[str, Any]:
    """
    Transformuje geometrię działki z EPSG:2180 i zwraca GeoJSON Feature.

    Funkcja pomocnicza jest przeznaczona dla geometrii z ULDK, którą frontend
    ma wyświetlić jako warstwę działki w MapLibre.
    """
    wgs84_geometry = transform_geometry_to_wgs84(geometry)
    return geometry_to_geojson_feature(
        wgs84_geometry,
        properties={"parcel_identifier": parcel_identifier, "layer": "parcel"},
    )


def buildable_area_geometry_to_geojson(
    geometry: BaseGeometry, setback_m: float
) -> dict[str, Any]:
    """
    Transformuje geometrię obszaru po technicznym odsunięciu do GeoJSON Feature.

    Wejściem powinna być geometria po buforze ujemnym, a nie sam wynik
    TechnicalSetbackResult, ponieważ ten wynik przechowuje pole powierzchni,
    ale nie przechowuje geometrii obszaru.
    """
    wgs84_geometry = transform_geometry_to_wgs84(geometry)
    return geometry_to_geojson_feature(
        wgs84_geometry,
        properties={
            "layer": "buildable_area",
            "setback_m": setback_m,
            "is_technical_approximation": True,
        },
    )


def analysis_layer_geometry_to_geojson(
    geometry: BaseGeometry,
    layer: str,
    properties: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Buduje GeoJSON warstwy analizy z geometrii w EPSG:2180.

    Pusta geometria nie jest wysyłana jako pozornie dostępna warstwa. Nazwa
    domenowa ``layer`` i przekazane atrybuty trafiają do ``properties``, aby
    frontend mógł renderować kolekcje sieci, stref ochronnych i ryzyk bez
    odgadywania znaczenia obiektów po kolejności.
    """
    if geometry.is_empty:
        return None
    return geometry_to_geojson_feature(
        transform_geometry_to_wgs84(geometry),
        properties={"layer": layer, **(properties or {})},
    )


def _json_compatible(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_compatible(item) for item in value]
    if isinstance(value, list):
        return [_json_compatible(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_compatible(item) for key, item in value.items()}
    return value
