from dataclasses import dataclass

from pyproj import Transformer
import shapely
from shapely import wkt as shapely_wkt
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.validation import explain_validity, make_valid

from app.core.network_rules import NetworkRule, load_network_rules
from app.core.settings import settings


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


@dataclass(frozen=True)
class TechnicalSetbackResult:
    """
    Wynik wstępnego technicznego odsunięcia geometrii działki od jej granicy.

    To odsunięcie nie jest linią zabudowy z MPZP, tylko technicznym
    przybliżeniem minimalnej odległości od granicy działki. Pole
    is_technical_approximation jest zawsze True, aby dalsze warstwy API i UI
    nie prezentowały wyniku jako ostatecznego ustalenia planistycznego.
    """

    buildable_area_sqm: float
    buildable_geometry: BaseGeometry
    setback_m: float
    is_technical_approximation: bool
    warning: str | None


def calculate_technical_setback(
    geometry: BaseGeometry, setback_m: float | None = None
) -> TechnicalSetbackResult:
    """
    Liczy wstępny obszar po technicznym odsunięciu od granicy działki.

    Obliczenia zakładają geometrię w EPSG:2180, więc bufor i powierzchnia są
    wyrażone w metrach oraz m². Wynik jest wyłącznie technicznym przybliżeniem
    minimalnej odległości od granicy i nie zastępuje linii zabudowy ani innych
    ustaleń MPZP. Jeżeli bufor ujemny usuwa całą geometrię, funkcja zwraca pole
    0 m² oraz ostrzeżenie zamiast rzucać wyjątek.
    """
    if setback_m is None:
        setback_m = settings.default_technical_setback_m

    buffered = geometry.buffer(-setback_m)
    if buffered.is_empty or buffered.area <= 0.0:
        return TechnicalSetbackResult(
            buildable_area_sqm=0.0,
            buildable_geometry=buffered,
            setback_m=setback_m,
            is_technical_approximation=True,
            warning=(
                "Działka jest zbyt wąska dla technicznego odsunięcia "
                f"{setback_m:.1f} m od granicy i po odsunięciu nie pozostaje "
                "żaden obszar do zabudowy. To odsunięcie techniczne, nie "
                "ostateczna linia zabudowy z MPZP."
            ),
        )

    return TechnicalSetbackResult(
        buildable_area_sqm=buffered.area,
        buildable_geometry=buffered,
        setback_m=setback_m,
        is_technical_approximation=True,
        warning=None,
    )


@dataclass(frozen=True)
class NetworkGeometryInput:
    """
    Lekki, lokalny typ wejściowy dla calculate_network_protection_zones.

    geometry.py nie importuje niczego z app.services.kiut, żeby uniknąć
    zależności w niewłaściwym kierunku (kiut.py może w przyszłości importować
    geometry.py, nie odwrotnie — sekcja 4 context.md: serwisy jednoodpowiedzialne).
    Wywołujący kod (przyszła fasada np. initiation.py) mapuje NetworkFeature
    z kiut.py na ten typ przed wywołaniem tej funkcji.
    """

    network_type: str
    geometry: BaseGeometry
    input_index: int | None = None


@dataclass(frozen=True)
class NetworkProtectionZone:
    """Pojedyncza strefa ochronna wynikająca z jednej sieci uzbrojenia, przycięta do granic działki."""

    network_type: str
    buffer_m: float
    zone_area_sqm: float
    source: str
    confidence: float
    note: str
    geometry: BaseGeometry
    input_index: int | None
    # BK-306: strefa reguły bez zweryfikowanej podstawy jest wyłącznie
    # przybliżeniem prezentacyjnym i nie pomniejsza powierzchni netto.
    simulation_only: bool = True
    affects_buildable_area: bool = False


@dataclass(frozen=True)
class NetworkProtectionZonesResult:
    """
    Wynik odjęcia stref ochronnych sieci uzbrojenia od technicznego obszaru zabudowy.

    Nie modyfikuje samego obszaru zabudowy z calculate_technical_setback — zwraca
    nową, pomniejszoną wartość netto.
    """

    net_buildable_area_sqm: float
    zones: list[NetworkProtectionZone]
    warnings: list[str]


def calculate_network_protection_zones(
    parcel: BaseGeometry,
    buildable_area: BaseGeometry,
    networks: list[NetworkGeometryInput],
    rules: dict[str, NetworkRule] | None = None,
) -> NetworkProtectionZonesResult:
    """
    Liczy techniczne bufory sieci uzbrojenia terenu i ich wpływ na obszar zabudowy.

    Dla każdej sieci przecinającej działkę tworzymy bufor wokół linii i
    przycinamy go do obszaru zabudowy. Od obszaru netto odejmowane są wyłącznie
    strefy reguł ze zweryfikowaną podstawą (``affects_buildable_area``). Strefy
    reguł symulacyjnych (BK-306) są zwracane z ``simulation_only=True`` jako
    przybliżenie prezentacyjne — ich pole opisuje hipotetyczną redukcję, której
    NIE zastosowano.
    Promień bufora pochodzi wyłącznie z konfiguracji (network_rules.json) przez
    load_network_rules() — nigdy z zahardkodowanej liczby w kodzie. Sieci
    leżące poza działką nie zmniejszają obszaru netto, chyba że reguła ma
    apply_even_outside_parcel=True (np. strefa kontrolowana gazociągu, która
    może obejmować teren poza samą siecią). Sieci o nieznanym typie (brak
    reguły w konfiguracji) są pomijane z ostrzeżeniem, nie z domyślnym buforem.
    Wszystkie obliczenia zakładają, że parcel, buildable_area i geometrie sieci
    są już w EPSG:2180.
    """
    if rules is None:
        rules = load_network_rules()

    warnings: list[str] = []
    # Osobne akumulatory: strefy wpływające na wynik i strefy symulacyjne nie
    # mogą się wzajemnie „zjadać” przy rozliczaniu nakładania.
    covered_by_kind: dict[bool, list[BaseGeometry]] = {True: [], False: []}
    zones: list[NetworkProtectionZone] = []

    for network in networks:
        rule = rules.get(network.network_type)
        if rule is None:
            warnings.append(
                f"Brak reguły strefy ochronnej dla typu sieci "
                f"{network.network_type!r} — sieć pominięta w obliczeniach."
            )
            continue

        line = network.geometry
        # cap_style='flat' — linie sieci są zazwyczaj fragmentami dłuższej
        # infrastruktury obciętymi do BBOX zapytania WFS, a nie rzeczywistymi
        # zakończeniami, więc płaskie zakończenia bufora unikają sztucznych
        # zaokrągleń na granicy zapytania.
        buffered = line.buffer(rule.default_buffer_m, cap_style="flat")

        # Filtr "sieć poza działką nie zmniejsza obszaru" sprawdzamy na
        # oryginalnej linii, nie na buforze — chyba że reguła jawnie mówi,
        # że strefa ochronna ma zastosowanie nawet dla sieci leżącej poza
        # działką (np. strefa kontrolowana gazociągu).
        applies = (
            line.intersects(parcel)
            if not rule.apply_even_outside_parcel
            else buffered.intersects(parcel)
        )
        if not applies:
            continue

        # Raportowana powierzchnia strefy odpowiada faktycznej redukcji
        # technicznego obszaru zabudowy. Część bufora leżąca wyłącznie w pasie
        # odsunięcia od granicy działki nie może zawyżać audytowanej dedukcji.
        clipped = buffered.intersection(buildable_area)
        if clipped.is_empty:
            continue

        # Przy nakładających się buforach przypisujemy kolejnej sieci tylko
        # jeszcze nieodjętą część. Dzięki temu suma ``zone_area_sqm`` jest
        # równa faktycznej redukcji netto i pozwala odtworzyć rachunek bez
        # podwójnego liczenia wspólnego fragmentu stref.
        affects = rule.affects_buildable_area and not rule.simulation_only
        clipped_zone_geometries = covered_by_kind[affects]
        previously_covered = (
            unary_union(clipped_zone_geometries)
            if clipped_zone_geometries
            else None
        )
        effective_zone = (
            clipped.difference(previously_covered)
            if previously_covered is not None
            else clipped
        )
        clipped_zone_geometries.append(clipped)
        zones.append(
            NetworkProtectionZone(
                network_type=network.network_type,
                buffer_m=rule.default_buffer_m,
                zone_area_sqm=effective_zone.area,
                source=rule.source,
                confidence=rule.confidence,
                note=rule.note,
                geometry=effective_zone,
                input_index=network.input_index,
                simulation_only=rule.simulation_only,
                affects_buildable_area=affects,
            )
        )

    affecting = covered_by_kind[True]
    if not affecting:
        return NetworkProtectionZonesResult(
            net_buildable_area_sqm=buildable_area.area,
            zones=zones,
            warnings=warnings,
        )

    union_zone = unary_union(affecting)
    net_geometry = buildable_area.difference(union_zone)
    return NetworkProtectionZonesResult(
        net_buildable_area_sqm=net_geometry.area,
        zones=zones,
        warnings=warnings,
    )
