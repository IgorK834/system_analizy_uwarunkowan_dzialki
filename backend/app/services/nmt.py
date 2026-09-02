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
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Final

import httpx
from shapely.geometry.base import BaseGeometry

from app.core.settings import settings
from app.schemas.analyze import SourceMetadata

logger = logging.getLogger(__name__)

NMT_TIMEOUT_S: Final[float] = 15.0

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
    wywołującego są nieodróżnialne od awarii transportu.
    """


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


async def fetch_terrain_extremes(
    parcel_geometry: BaseGeometry,
    client: httpx.AsyncClient | None = None,
) -> TerrainExtremes | None:
    """Pobiera minimalną i maksymalną wysokość terenu w obrysie działki.

    Geometria wejściowa musi być w EPSG:2180 w kolejności ``(easting, northing)``,
    czyli w tej samej konwencji, w której geometrię zwraca ULDK — WKT jest
    przekazywany do usługi bez transformacji.

    Zwraca ``None``, gdy usługa odpowiedziała poprawnie, ale nie ma danych
    wysokościowych dla tego obszaru. To nie jest awaria, tylko fakt o pokryciu
    danych, dlatego nie podnosi wyjątku. Timeout, błąd HTTP, błąd zgłoszony przez
    usługę w polu ``error`` oraz odpowiedź bez wysokości podnoszą
    ``NmtServiceUnavailableError``.
    """
    params = {
        "request": "GetMinMaxByPolygon",
        "polygon": parcel_geometry.wkt,
    }

    if client is not None:
        response_text, source_url = await _fetch_nmt_response_text(client, params)
    else:
        async with httpx.AsyncClient() as owned_client:
            response_text, source_url = await _fetch_nmt_response_text(
                owned_client, params
            )

    fetched_at = datetime.now(timezone.utc)
    return _build_terrain_extremes(
        response_text, parcel_geometry, source_url, fetched_at
    )


async def _fetch_nmt_response_text(
    client: httpx.AsyncClient,
    params: dict[str, str],
) -> tuple[str, str]:
    try:
        response = await client.get(
            settings.nmt_base_url,
            params=params,
            timeout=NMT_TIMEOUT_S,
        )
        response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise NmtServiceUnavailableError(
            "Usługa NMT nie odpowiedziała w wymaganym czasie."
        ) from exc
    except httpx.HTTPError as exc:
        raise NmtServiceUnavailableError(f"Usługa NMT zwróciła błąd: {exc}") from exc

    return response.text, str(response.url)


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
) -> TerrainExtremes | None:
    values = _parse_nmt_response(text)

    if not values:
        raise NmtServiceUnavailableError(
            "Odpowiedź usługi NMT nie zawiera żadnych wartości."
        )

    error_message = values.get(_ERROR_KEY)
    if error_message:
        raise NmtServiceUnavailableError(
            f"Usługa NMT zgłosiła błąd: {error_message}"
        )

    min_height = _parse_float(values.get(_MIN_KEY))
    max_height = _parse_float(values.get(_MAX_KEY))
    if min_height is None or max_height is None:
        raise NmtServiceUnavailableError(
            "Odpowiedź usługi NMT nie zawiera wysokości Hmin/Hmax."
        )

    if min_height > max_height:
        # Sentinel braku pokrycia danymi: usługa zwraca Hmin=2500, Hmax=0 oraz
        # punkty POINT(0 0 ...). Odwrócona relacja min>max jest niemożliwa dla
        # realnych danych, więc rozpoznajemy ją zamiast zaszywać liczbę 2500.
        logger.info(
            "NMT nie ma danych wysokościowych dla obszaru (Hmin=%s, Hmax=%s).",
            min_height,
            max_height,
        )
        return None

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
        grid_size_m=_parse_float(values.get(_GRID_KEY)),
        sampled_points=_parse_int(values.get(_POINTS_KEY)),
        source_metadata=SourceMetadata(
            source_name="NMT",
            source_url=source_url,
            fetched_at=fetched_at,
            confidence=0.9 if not warnings else 0.5,
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
