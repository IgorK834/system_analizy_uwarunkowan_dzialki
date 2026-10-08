"""Serwis NMT (GUGiK) — rzeźba terenu działki na podstawie Numerycznego Modelu Terenu.

Różnica wysokości na działce jest wejściem do oceny warunków zabudowy: wpływa na
koszt niwelacji, odwodnienie, potrzebę zabezpieczenia skarp i posadowienie
budynku. Sekcja jest informacyjna, nie prawna — brak danych o rzeźbie terenu nie
unieważnia analizy, ale nie może też udawać, że teren jest płaski.

Kontrakt usługi potwierdzono realnymi zapytaniami 2026-07-30 (fixtures
``tests/fixtures/source_contracts/nmt_getminmaxbypolygon*.txt``). Ustalenia:

1. **Protokół to zwykły GET, bez autoryzacji, a odpowiedź to ``text/plain``** w
   formacie ``Klucz<TAB>wartość`` — nie JSON.
2. **Współrzędne w WKT są w kolejności ``(easting, northing)`` EPSG:2180**, czyli
   dokładnie w konwencji geometrii kanonicznej systemu — WKT działki przekazujemy
   bez żadnej transformacji. Uwaga: to NIE jest ta sama konwencja, co w
   ``GetHByXY``, gdzie parametr ``x`` oznacza northing (nazewnictwo PUWG92).
   Potwierdzone eksperymentalnie: ``GetHByXY&x=486000&y=637000`` zwraca 114,7 m
   (Warszawa), a ``GetMinMaxByPolygon`` dla ``POLYGON((637000 486000, ...))``
   zwraca Hmin 112,3 / Hmax 115,7 — czyli tę samą lokalizację przy odwróconych
   liczbach.
3. **Błędy i brak pokrycia mają status HTTP 200.** Błąd wejścia to wiersz
   ``error<TAB>Niepoprawny poligon``, a brak danych wysokościowych to sentinel
   ``Hmin 2500`` / ``Hmax 0`` (wraz z ``POINT(0 0 2500)``). Bez jawnego
   rozpoznania obu przypadków adapter zwróciłby różnicę wysokości -2500 m.

Zapytanie ``GetMinMaxByPolygon`` przyjmuje pełny, wielowierzchołkowy WKT działki
(sprawdzone na realnej działce o 1,5 kB WKT) oraz ``MULTIPOLYGON``, więc
geometrii nie trzeba upraszczać.

Adres rzeczywistego zapytania zawiera cały wielokąt (dla 76 wierzchołków ok. 2,9 kB, dla
134 wierzchołków ok. 4,9 kB), więc nie nadaje się na ``SourceMetadata.source_url``: trafia
do bazy, raportu i pakietu audytowego. Provenance zapisuje adres bazowy usługi i skróconą
informację o zapytaniu (``polygon_sha256``, ``vertex_count``) — wystarcza do ustalenia, o jaką
geometrię pytano, bez przenoszenia jej. Pełny adres jest tylko w logu na poziomie DEBUG.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Final

import httpx
import shapely
from shapely.geometry.base import BaseGeometry

from app.core.settings import settings
from app.schemas.analyze import SourceMetadata

logger = logging.getLogger(__name__)

NMT_TIMEOUT_S: Final[float] = 15.0
NMT_SOURCE_NAME: Final[str] = "NMT"
NMT_SOURCE_ID: Final[str] = "nmt"
_AVAILABLE_CONFIDENCE: Final[float] = 0.9
_AREA_MISMATCH_CONFIDENCE: Final[float] = 0.5

# Kody przyczyn niedostępności przenoszone do ``TerrainResult.reason_code``.
REASON_TIMEOUT: Final[str] = "SERVICE_TIMEOUT"
REASON_HTTP_ERROR: Final[str] = "SERVICE_HTTP_ERROR"
REASON_REPORTED_ERROR: Final[str] = "SERVICE_REPORTED_ERROR"
REASON_INVALID_RESPONSE: Final[str] = "INVALID_RESPONSE"
REASON_NO_COVERAGE: Final[str] = "NO_COVERAGE_SENTINEL"

# Klucze odpowiedzi tekstowej usługi.
_ERROR_KEY: Final[str] = "error"
_MIN_KEY: Final[str] = "hmin"
_MAX_KEY: Final[str] = "hmax"
_GRID_KEY: Final[str] = "grid size [m]"
_POINTS_KEY: Final[str] = "points count"
_AREA_KEY: Final[str] = "polygon area"

# Dopuszczalna względna rozbieżność pola powierzchni raportowanego przez usługę
# wobec pola geometrii wysłanej w zapytaniu. Zgodność pola jest tanim testem
# integralności: gdyby usługa zinterpretowała współrzędne inaczej (np. odwrotna
# kolejność osi) albo obcięła geometrię, pole natychmiast by się rozjechało.
_AREA_MISMATCH_TOLERANCE_RATIO: Final[float] = 0.01


class NmtServiceUnavailableError(Exception):
    """Usługa NMT nie odpowiedziała, zwróciła błąd albo odpowiedź bez wysokości.

    Obejmuje też błędy raportowane ze statusem HTTP 200 w polu ``error``, bo dla
    wywołującego są nieodróżnialne od awarii transportu. Wyjątek niesie kod
    przyczyny i provenance nieudanej próby, aby niepełny wynik nadal wskazywał,
    kiedy i o co pytano usługę.
    """

    def __init__(
        self,
        message: str,
        *,
        reason_code: str = REASON_INVALID_RESPONSE,
        source_metadata: SourceMetadata | None = None,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.source_metadata = source_metadata


@dataclass(frozen=True)
class TerrainExtremes:
    """Skrajne wysokości terenu w obrysie działki i wynikające z nich deniwelacje."""

    min_height_m: float
    max_height_m: float
    height_difference_m: float
    grid_size_m: float | None
    sampled_points: int | None
    source_metadata: SourceMetadata
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TerrainNoCoverage:
    """Usługa odpowiedziała poprawnie, ale nie ma danych wysokościowych obszaru.

    To fakt o pokryciu danych, a nie pomiar: wysokości pozostają nieznane i nie
    wolno ich utożsamiać z płaskim terenem. Metryki próbkowania i provenance są
    zachowane, bo dokumentują, o co i kiedy pytano usługę.
    """

    grid_size_m: float | None
    sampled_points: int | None
    source_metadata: SourceMetadata
    warnings: list[str] = field(default_factory=list)


TerrainMeasurement = TerrainExtremes | TerrainNoCoverage


async def fetch_terrain_extremes(
    parcel_geometry: BaseGeometry,
    client: httpx.AsyncClient | None = None,
) -> TerrainMeasurement:
    """Pobiera minimalną i maksymalną wysokość terenu w obrysie działki.

    Geometria wejściowa musi być w EPSG:2180 w kolejności ``(easting, northing)``,
    czyli w tej samej konwencji, w której geometrię zwraca ULDK — WKT jest
    przekazywany do usługi bez transformacji.

    Zwraca ``TerrainNoCoverage``, gdy usługa odpowiedziała poprawnie, ale nie ma
    danych wysokościowych dla tego obszaru (sentinel ``Hmin=2500``/``Hmax=0``).
    To nie jest awaria, tylko fakt o pokryciu danych, dlatego nie podnosi
    wyjątku. Timeout, błąd HTTP, błąd zgłoszony przez usługę w polu ``error``
    oraz odpowiedź bez wysokości podnoszą ``NmtServiceUnavailableError``.
    """
    polygon_wkt = parcel_geometry.wkt
    params = {
        "request": "GetMinMaxByPolygon",
        "polygon": polygon_wkt,
    }
    source_url = _provenance_url(parcel_geometry, polygon_wkt)
    logger.debug(
        "NMT GetMinMaxByPolygon pełny adres zapytania: %s",
        httpx.URL(settings.nmt_base_url, params=params),
    )

    if client is not None:
        response_text = await _fetch_nmt_response_text(client, params, source_url)
    else:
        async with httpx.AsyncClient() as owned_client:
            response_text = await _fetch_nmt_response_text(
                owned_client, params, source_url
            )

    fetched_at = datetime.now(timezone.utc)
    return _build_terrain_extremes(
        response_text, parcel_geometry, source_url, fetched_at
    )


def _provenance_url(parcel_geometry: BaseGeometry, polygon_wkt: str) -> str:
    """Adres bazowy usługi ze skróconą informacją o zapytaniu zamiast pełnego wielokąta.

    ``polygon_sha256`` to skrót WKT wysłanego do usługi, a ``vertex_count`` liczba jego
    współrzędnych (z powtórzonym punktem zamykającym pierścień) — długość adresu nie zależy
    od złożoności geometrii.
    """
    return str(
        httpx.URL(
            settings.nmt_base_url,
            params={
                "request": "GetMinMaxByPolygon",
                "polygon_sha256": hashlib.sha256(polygon_wkt.encode("utf-8")).hexdigest(),
                "vertex_count": str(shapely.get_num_coordinates(parcel_geometry)),
            },
        )
    )


async def _fetch_nmt_response_text(
    client: httpx.AsyncClient,
    params: dict[str, str],
    request_url: str,
) -> str:
    attempted_at = datetime.now(timezone.utc)
    try:
        response = await client.get(
            settings.nmt_base_url,
            params=params,
            timeout=NMT_TIMEOUT_S,
        )
        response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise NmtServiceUnavailableError(
            "Usługa NMT nie odpowiedziała w wymaganym czasie.",
            reason_code=REASON_TIMEOUT,
            source_metadata=_failure_source(request_url, attempted_at, None),
        ) from exc
    except httpx.HTTPStatusError as exc:
        # Komunikat wyjątku httpx zawiera pełny adres z wielokątem działki — nie przenosimy go dalej.
        raise NmtServiceUnavailableError(
            f"Usługa NMT zwróciła błąd HTTP {exc.response.status_code}.",
            reason_code=REASON_HTTP_ERROR,
            source_metadata=_failure_source(
                request_url, attempted_at, exc.response.status_code
            ),
        ) from exc
    except httpx.HTTPError as exc:
        raise NmtServiceUnavailableError(
            f"Usługa NMT zwróciła błąd transportu ({type(exc).__name__}).",
            reason_code=REASON_HTTP_ERROR,
            source_metadata=_failure_source(request_url, attempted_at, None),
        ) from exc

    return response.text


def _failure_source(
    source_url: str,
    fetched_at: datetime,
    response_status: int | None,
    artifact_sha256: str | None = None,
) -> SourceMetadata:
    """Provenance nieudanej próby: zerowa pewność i wymagana weryfikacja."""
    return SourceMetadata(
        source_id=NMT_SOURCE_ID,
        source_name=NMT_SOURCE_NAME,
        source_url=source_url,
        fetched_at=fetched_at,
        response_status=response_status,
        artifact_sha256=artifact_sha256,
        confidence=0.0,
        manual_review_required=True,
    )


def _parse_nmt_response(text: str) -> dict[str, str]:
    """Rozkłada odpowiedź ``Klucz<TAB>wartość`` na słownik kluczy pisanych małymi literami.

    Wiersze bez tabulatora (np. nagłówek ``Hmin geom:``) oraz wiersze listy
    punktów (``<TAB>0:<TAB>POINT(...)``) są pomijane, bo adapter potrzebuje
    wyłącznie wartości skrajnych i metryk próbkowania.
    """
    values: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition("\t")
        if not separator:
            continue
        normalized_key = key.strip().rstrip(":").strip().lower()
        if not normalized_key:
            continue
        values.setdefault(normalized_key, value.strip())
    return values


def _build_terrain_extremes(
    text: str,
    parcel_geometry: BaseGeometry,
    source_url: str,
    fetched_at: datetime,
) -> TerrainMeasurement:
    values = _parse_nmt_response(text)
    artifact_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()

    if not values:
        raise NmtServiceUnavailableError(
            "Odpowiedź usługi NMT nie zawiera żadnych wartości.",
            reason_code=REASON_INVALID_RESPONSE,
            source_metadata=_failure_source(source_url, fetched_at, 200, artifact_sha256),
        )

    error_message = values.get(_ERROR_KEY)
    if error_message:
        # Błąd wejścia przychodzi ze statusem HTTP 200 — nie wolno go
        # potraktować jak poprawnej odpowiedzi ani „naprawić” do zera.
        raise NmtServiceUnavailableError(
            f"Usługa NMT zgłosiła błąd: {error_message}",
            reason_code=REASON_REPORTED_ERROR,
            source_metadata=_failure_source(source_url, fetched_at, 200, artifact_sha256),
        )

    min_height = _parse_float(values.get(_MIN_KEY))
    max_height = _parse_float(values.get(_MAX_KEY))
    if min_height is None or max_height is None:
        raise NmtServiceUnavailableError(
            "Odpowiedź usługi NMT nie zawiera wysokości Hmin/Hmax.",
            reason_code=REASON_INVALID_RESPONSE,
            source_metadata=_failure_source(source_url, fetched_at, 200, artifact_sha256),
        )

    grid_size = _parse_float(values.get(_GRID_KEY))
    sampled_points = _parse_int(values.get(_POINTS_KEY))

    if min_height > max_height:
        # Sentinel braku pokrycia danymi: usługa zwraca Hmin=2500, Hmax=0 oraz
        # punkty POINT(0 0 ...). Odwrócona relacja min>max jest niemożliwa dla
        # realnych danych, więc rozpoznajemy ją zamiast zaszywać liczbę 2500.
        logger.info(
            "NMT nie ma danych wysokościowych dla obszaru (Hmin=%s, Hmax=%s).",
            min_height,
            max_height,
        )
        return TerrainNoCoverage(
            grid_size_m=grid_size,
            sampled_points=sampled_points,
            source_metadata=SourceMetadata(
                source_id=NMT_SOURCE_ID,
                source_name=NMT_SOURCE_NAME,
                source_url=source_url,
                fetched_at=fetched_at,
                response_status=200,
                artifact_sha256=artifact_sha256,
                confidence=_AVAILABLE_CONFIDENCE,
                manual_review_required=False,
            ),
            warnings=[
                "NMT nie ma danych wysokościowych dla obszaru działki — "
                "deniwelacja jest nieznana; brak pokrycia nie oznacza płaskiego "
                "terenu."
            ],
        )

    warnings: list[str] = []
    reported_area = _parse_float(values.get(_AREA_KEY))
    if _is_area_mismatched(reported_area, parcel_geometry.area):
        warnings.append(
            "Powierzchnia poligonu zwrócona przez NMT "
            f"({reported_area:.2f} m2) różni się od powierzchni geometrii "
            f"działki ({parcel_geometry.area:.2f} m2) — wynik deniwelacji może "
            "dotyczyć innego obszaru i wymaga ręcznej weryfikacji."
        )

    return TerrainExtremes(
        min_height_m=min_height,
        max_height_m=max_height,
        height_difference_m=round(max_height - min_height, 3),
        grid_size_m=grid_size,
        sampled_points=sampled_points,
        source_metadata=SourceMetadata(
            source_id=NMT_SOURCE_ID,
            source_name=NMT_SOURCE_NAME,
            source_url=source_url,
            fetched_at=fetched_at,
            response_status=200,
            artifact_sha256=artifact_sha256,
            confidence=(
                _AVAILABLE_CONFIDENCE if not warnings else _AREA_MISMATCH_CONFIDENCE
            ),
            manual_review_required=bool(warnings),
        ),
        warnings=warnings,
    )


def _is_area_mismatched(reported_area: float | None, geometry_area: float) -> bool:
    if reported_area is None or geometry_area <= 0:
        return False
    relative_difference = abs(reported_area - geometry_area) / geometry_area
    return relative_difference > _AREA_MISMATCH_TOLERANCE_RATIO


def _parse_float(raw_value: str | None) -> float | None:
    if raw_value is None:
        return None
    try:
        return float(raw_value.replace(",", "."))
    except ValueError:
        return None


def _parse_int(raw_value: str | None) -> int | None:
    value = _parse_float(raw_value)
    return int(value) if value is not None else None
