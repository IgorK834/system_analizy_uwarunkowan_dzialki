from pyproj import Transformer


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
