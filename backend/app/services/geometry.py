from dataclasses import dataclass

from pyproj import Transformer
import shapely
from shapely import wkt as shapely_wkt
from shapely.geometry.base import BaseGeometry
from shapely.validation import explain_validity, make_valid


class CoordinatesOutsidePolandError(ValueError):
    """Współrzędne WGS84 leżą poza przybliżonymi granicami Polski."""


# Przybliżone granice Polski są celowo nieco szersze niż rzeczywisty zasięg kraju,
# żeby nie odrzucać punktów na krańcach ani danych z drobnymi błędami wejściowymi.
_POLAND_LON_MIN = 14.0
_POLAND_LON_MAX = 25.0
_POLAND_LAT_MIN = 48.9
_POLAND_LAT_MAX = 55.0

# Utworzenie Transformer ładuje dane PROJ, więc robimy to raz na poziomie modułu.
_TO_PUWG = Transformer.from_crs("EPSG:4326", "EPSG:2180", always_xy=True)
_TO_WGS84 = Transformer.from_crs("EPSG:2180", "EPSG:4326", always_xy=True)


def to_puwg1992(lon: float, lat: float) -> tuple[float, float]:
    """
    Transformuje punkt z WGS84 (EPSG:4326) do PUWG 1992 (EPSG:2180).

    Wejściem są jawnie rozdzielone współrzędne lon/lat z mapy lub frontendu.
    Wynikiem jest para (x, y) w metrach, gotowa do zapytań ULDK i dalszych
    obliczeń metrycznych w polskim układzie EPSG:2180. Użycie always_xy=True
    jest obowiązkowe, ponieważ formalna kolejność osi EPSG:4326 w pyproj może
    być lat/lon, a API projektu przekazuje wartości jako lon, lat.

    Raises:
        CoordinatesOutsidePolandError: gdy punkt wejściowy leży poza Polską.
    """
    _validate_poland_bounds(lon, lat)
    x, y = _TO_PUWG.transform(lon, lat)
    return x, y


def to_wgs84(x: float, y: float) -> tuple[float, float]:
    """
    Transformuje punkt z PUWG 1992 (EPSG:2180) do WGS84 (EPSG:4326).

    Funkcja służy do przygotowania współrzędnych dla frontendu i GeoJSON.
    Wynik po transformacji jest walidowany względem przybliżonych granic Polski,
    aby odsiać błędne punkty metryczne zanim trafią do warstwy API.

    Raises:
        CoordinatesOutsidePolandError: gdy wynik transformacji leży poza Polską.
    """
    lon, lat = _TO_WGS84.transform(x, y)
    _validate_poland_bounds(lon, lat)
    return lon, lat


def _validate_poland_bounds(lon: float, lat: float) -> None:
    if not _POLAND_LON_MIN <= lon <= _POLAND_LON_MAX:
        raise CoordinatesOutsidePolandError(
            f"Długość geograficzna {lon:.4f}°E leży poza granicami Polski "
            f"({_POLAND_LON_MIN:.1f}°E-{_POLAND_LON_MAX:.1f}°E)."
        )

    if not _POLAND_LAT_MIN <= lat <= _POLAND_LAT_MAX:
        raise CoordinatesOutsidePolandError(
            f"Szerokość geograficzna {lat:.4f}°N leży poza granicami Polski "
            f"({_POLAND_LAT_MIN:.1f}°N-{_POLAND_LAT_MAX:.1f}°N)."
        )


class InvalidParcelGeometryError(ValueError):
    """
    Geometria działki jest nieprawidłowa i nie udało się jej naprawić przez make_valid,
    albo naprawiona geometria zmieniła typ na nieobsługiwany.
    """


@dataclass(frozen=True)
class ParcelGeometryMetrics:
    """Wewnętrzny wynik obliczeń metrycznych dla geometrii działki."""

    area_sqm: float
    area_are: float
    area_ha: float
    perimeter_m: float
    centroid_x: float
    centroid_y: float
    is_valid: bool
    geometry_repaired: bool
    repair_warning: str | None


def parse_parcel_geometry(wkt: str) -> BaseGeometry:
    """
    Parsuje WKT działki z ULDK do obiektu Shapely.

    Wejściowy WKT jest już w EPSG:2180, więc funkcja nie wykonuje żadnej
    transformacji współrzędnych. Obsługiwane są tylko geometrie Polygon i
    MultiPolygon; otwory w poligonach są zachowane, bo dalsze obliczenia muszą
    uwzględniać realną powierzchnię netto działki. Funkcja rzuca
    InvalidParcelGeometryError dla błędnego WKT lub nieobsługiwanego typu geometrii.
    """
    try:
        geometry = shapely_wkt.loads(wkt)
    except (shapely.errors.ShapelyError, ValueError, TypeError) as exc:
        raise InvalidParcelGeometryError(
            f"Nie udało się sparsować WKT geometrii działki: {exc}"
        ) from exc

    _ensure_supported_parcel_geometry_type(geometry)
    return geometry


def calculate_geometry_metrics(geometry: BaseGeometry) -> ParcelGeometryMetrics:
    """
    Liczy pole, obwód i centroid geometrii działki w EPSG:2180.

    Obliczenia metryczne wykonujemy w EPSG:2180, ponieważ Shapely liczy w
    jednostkach układu geometrii, a dla działek potrzebujemy metrów, arów i
    hektarów, nie stopni geograficznych. Jeżeli oryginalna geometria jest
    niepoprawna, stosujemy make_valid jawnie i zwracamy ostrzeżenie, bo taki
    wynik wymaga ręcznej weryfikacji. Shapely automatycznie uwzględnia otwory
    w poligonach przy liczeniu area, więc nie zakładamy ich braku.
    """
    original_is_valid = geometry.is_valid
    geometry_repaired = False
    repair_warning = None
    working_geometry = geometry

    if not original_is_valid:
        reason = explain_validity(geometry)
        working_geometry = make_valid(geometry)
        geometry_repaired = True
        repair_warning = (
            f"Geometria działki była nieprawidłowa ({reason}) i została "
            "automatycznie naprawiona. Wynik wymaga ręcznej weryfikacji."
        )
        if working_geometry.geom_type not in ("Polygon", "MultiPolygon"):
            raise InvalidParcelGeometryError(
                f"Naprawiona geometria ma nieobsługiwany typ {working_geometry.geom_type}."
            )

    area_sqm = working_geometry.area
    perimeter_m = working_geometry.length
    centroid = working_geometry.centroid
    return ParcelGeometryMetrics(
        area_sqm=area_sqm,
        area_are=area_sqm / 100.0,
        area_ha=area_sqm / 10000.0,
        perimeter_m=perimeter_m,
        centroid_x=centroid.x,
        centroid_y=centroid.y,
        is_valid=original_is_valid,
        geometry_repaired=geometry_repaired,
        repair_warning=repair_warning,
    )


def _ensure_supported_parcel_geometry_type(geometry: BaseGeometry) -> None:
    if geometry.geom_type not in ("Polygon", "MultiPolygon"):
        raise InvalidParcelGeometryError(
            f"Nieobsługiwany typ geometrii: {geometry.geom_type}. "
            "Oczekiwano Polygon lub MultiPolygon."
        )
