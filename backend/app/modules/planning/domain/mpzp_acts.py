"""Akty MPZP z Rejestru Urbanistycznego i KIMPZP: przecięcie z działką, scalanie kanałów, pierwszeństwo (AU-101).

Moduł jest czystą logiką (bez sieci i XML). Adapter RU (``infrastructure/ru_mpzp_acts``) dostarcza
``RuActRecord`` z geometrią zasięgu aktu w EPSG:2180 (x = wschód, y = północ), a KIMPZP — ``KimpzpAct``
bez geometrii. Zasady:

* przecięcie działki z granicą aktu liczone jest wyłącznie w EPSG:2180; akt, który tylko dotyka bbox albo
  samej granicy działki (pole przecięcia poniżej ``MIN_INTERSECTION_AREA_SQM``), jest odrzucany z diagnostyką
  (``RejectedAct``), a nie raportowany jako akt działki;
* akt RU nigdy nie jest strefą: RU nie zawiera wydzieleń przeznaczenia (guard ``vector_zones`` w katalogu);
* numer uchwały pochodzi z dokumentu uchwalającego (``numerIdentyfikacyjny`` albo tytuł), a dla aktu bez
  dokumentu uchwalającego (np. projekt z samą uchwałą o przystąpieniu) pozostaje ``None`` — numer uchwały
  o przystąpieniu nie jest numerem planu;
* pierwszeństwo przy aktach nakładających się jest informacyjne, jawne i wersjonowane
  (``PRECEDENCE_RULE_VERSION``). Rozstrzygają wyłącznie sygnały jawne w danych (status, data utraty mocy,
  rozłączność geometrii). Późniejsza data „obowiązuje od” sama nie rozstrzyga — uchwała może obejmować tylko
  część obszaru wcześniejszego planu, a zakres zmiany zna dopiero jej tekst. Przy braku rozstrzygnięcia
  obie uchwały zostają na liście z ``manual_review_required``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Final, Literal, Sequence
from urllib.parse import urlsplit

import shapely
from shapely.geometry.base import BaseGeometry

from app.modules.planning.domain.kimpzp_discovery import (
    KimpzpAct,
    normalize_resolution_number,
    resolution_key,
    resolution_number_from_text,
)

KIMPZP_SOURCE_ID: Final[str] = "kimpzp"
RU_SOURCE_ID: Final[str] = "mpzp_ru"

# Wersja reguły pierwszeństwa informacyjnego (analogicznie do ``planning_compatibility``): zmiana treści
# reguły zmienia numer wersji, a wynik zapisany w snapshocie niesie wersję, według której powstał.
PRECEDENCE_RULE_VERSION: Final[str] = "mpzp-act-precedence/1.0"
MPZP_ACTS_SCHEMA_VERSION: Final[str] = "1.0"

# Przecięcie mniejsze niż ta powierzchnia to szum digitalizacji granicy (styk planów), a nie pokrycie działki.
MIN_INTERSECTION_AREA_SQM: Final[float] = 0.5

# --- kody powodów --------------------------------------------------------------------------------------
MPZP_CHANNEL_MISMATCH: Final[str] = "MPZP_CHANNEL_MISMATCH"
MPZP_ACT_OVERLAP_UNRESOLVED: Final[str] = "MPZP_ACT_OVERLAP_UNRESOLVED"
MPZP_ACT_NO_GEOMETRY: Final[str] = "MPZP_ACT_NO_GEOMETRY"
MPZP_ACT_STATUS_UNKNOWN: Final[str] = "MPZP_ACT_STATUS_UNKNOWN"
MPZP_ACT_DRAFT: Final[str] = "MPZP_ACT_DRAFT"
MPZP_ACT_REPEALED: Final[str] = "MPZP_ACT_REPEALED"
MPZP_ACT_NOT_YET_IN_FORCE: Final[str] = "MPZP_ACT_NOT_YET_IN_FORCE"
MPZP_ACT_GEOMETRY_REPAIRED: Final[str] = "MPZP_ACT_GEOMETRY_REPAIRED"
MPZP_ACT_DOCUMENT_UNRESOLVED: Final[str] = "MPZP_ACT_DOCUMENT_UNRESOLVED"
MPZP_ACT_COMMENCEMENT_DOCUMENT_ONLY: Final[str] = "MPZP_ACT_COMMENCEMENT_DOCUMENT_ONLY"
MPZP_ACT_NUMBER_FROM_IDENTIFIER: Final[str] = "MPZP_ACT_NUMBER_FROM_IDENTIFIER"
MPZP_ACT_EFFECTIVE_DATE_MISMATCH: Final[str] = "MPZP_ACT_EFFECTIVE_DATE_MISMATCH"
MPZP_ACT_HAS_AMENDMENTS: Final[str] = "MPZP_ACT_HAS_AMENDMENTS"
MPZP_ACT_OLDER_VERSION_DROPPED: Final[str] = "MPZP_ACT_OLDER_VERSION_DROPPED"

ActLegalStatus = Literal["binding", "not_binding", "not_yet_binding", "unknown"]
ActChannel = Literal["kimpzp", "mpzp_ru"]
ActPrecedence = Literal["applies", "complementary", "superseded", "unresolved", "not_in_force"]
RejectionReason = Literal["no_overlap", "boundary_touch_only", "below_min_area", "geometry_unusable"]
ResolutionNumberBasis = Literal["document_number", "document_title", "identifier", "kimpzp"]
DocumentKind = Literal["adopting", "commencement", "other"]
ChannelStatus = Literal["available", "no_match", "no_coverage", "unavailable", "unknown"]

# INSPIRE ``ProcessStepGeneralValue`` używane przez RU w polu ``status`` aktu.
_RU_STATUS_BINDING: Final[frozenset[str]] = frozenset({"legalforce"})
_RU_STATUS_NOT_BINDING: Final[frozenset[str]] = frozenset({"obsolete"})
_RU_STATUS_NOT_YET: Final[frozenset[str]] = frozenset({"elaboration", "adoption"})

# Identyfikator lokalny RU w postaci „IV_30_2024_P1”: numer uchwały zapisany z podkreśleniami.
_IDENTIFIER_NUMBER = re.compile(
    r"^(?P<a>[IVXLCDM]+|\d{1,4})_(?P<b>\d{1,5}|[IVXLCDM]+)_(?P<c>\d{2,4})(?:_[A-Za-z]\d*)?$"
)


# --- rekordy wejściowe z adaptera RU ------------------------------------------------------------------------


@dataclass(frozen=True)
class RuDocumentRecord:
    """``app-mpzp:DokumentFormalny`` powiązany z aktem."""

    identifier: str
    title: str | None = None
    number: str | None = None
    number_basis: Literal["document_number", "document_title"] | None = None
    document_date: date | None = None
    effective_from: date | None = None
    repealed_on: date | None = None
    links: tuple[str, ...] = ()
    journal: str | None = None
    relations: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class RuDrawingRecord:
    """``app-mpzp:RysunekAktuPlanowaniaPrzestrzennego`` — rysunek z georeferencją (GeoTIFF)."""

    identifier: str
    title: str | None = None
    link: str | None = None
    legend: str | None = None
    crs: str | None = None
    scale_denominator: int | None = None
    description: str | None = None


@dataclass(frozen=True)
class RuActRecord:
    """``app-mpzp:AktPlanowaniaPrzestrzennego`` z geometrią zasięgu w EPSG:2180 (x = wschód, y = północ)."""

    identifier: str
    gml_id: str
    version_id: str | None = None
    title: str | None = None
    plan_type: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    status_code: str | None = None
    status_title: str | None = None
    version_started: datetime | None = None
    version_ended: datetime | None = None
    amendment_count: int | None = None
    geometry: BaseGeometry | None = None
    geometry_issue: str | None = None
    document_refs: tuple[tuple[str, str], ...] = ()
    drawing_refs: tuple[str, ...] = ()

    def documents_of(self, *roles: str) -> tuple[str, ...]:
        return tuple(identifier for role, identifier in self.document_refs if role in roles)


# --- przecięcie z działką -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class ActIntersection:
    """Część aktu leżąca na działce (EPSG:2180)."""

    area_sqm: float
    pct: float
    geometry: BaseGeometry
    geometry_repaired: bool = False


@dataclass(frozen=True)
class IntersectionOutcome:
    """Wynik przecięcia: akceptacja albo odrzucenie z diagnostyką (odległość, pole)."""

    accepted: ActIntersection | None
    rejection: RejectionReason | None = None
    area_sqm: float = 0.0
    distance_m: float | None = None
    geometry_repaired: bool = False


def _valid(geometry: BaseGeometry) -> tuple[BaseGeometry, bool]:
    if geometry.is_valid:
        return geometry, False
    return shapely.make_valid(geometry), True


def _polygonal(geometry: BaseGeometry) -> BaseGeometry:
    """Zostawia z wyniku ``make_valid``/przecięcia tylko część powierzchniową."""
    if geometry.geom_type in {"Polygon", "MultiPolygon"}:
        return geometry
    parts = [
        part
        for part in getattr(geometry, "geoms", [])
        if part.geom_type in {"Polygon", "MultiPolygon"}
    ]
    if not parts:
        return shapely.Polygon()
    return shapely.union_all(parts)


def intersect_act_with_parcel(parcel: BaseGeometry, act_geometry: BaseGeometry | None) -> IntersectionOutcome:
    """Przecięcie działki z zasięgiem aktu (EPSG:2180) i decyzja o akceptacji.

    ``pct`` to udział powierzchni działki pokrytej aktem. Akt odrzucony nie jest błędem: ``distance_m`` mówi,
    jak daleko od działki leży granica aktu (``0`` = styk), co odróżnia akt sąsiedni od aktu, który tylko
    trafił do ``bbox`` zapytania.
    """
    if act_geometry is None or act_geometry.is_empty:
        return IntersectionOutcome(None, "geometry_unusable")
    if parcel.is_empty or parcel.area <= 0:
        return IntersectionOutcome(None, "geometry_unusable")
    parcel_valid, _ = _valid(parcel)
    act_valid, repaired = _valid(act_geometry)
    # Wstępne cięcie do prostokąta działki (z zapasem) ogranicza koszt przecięcia z zasięgiem całej gminy.
    min_x, min_y, max_x, max_y = parcel_valid.bounds
    pad = 1.0
    window = shapely.box(min_x - pad, min_y - pad, max_x + pad, max_y + pad)
    if not act_valid.intersects(window):
        return IntersectionOutcome(
            None,
            "no_overlap",
            0.0,
            round(float(act_valid.distance(parcel_valid)), 3),
            repaired,
        )
    local = _polygonal(shapely.intersection(act_valid, window))
    shared = _polygonal(shapely.intersection(parcel_valid, local))
    area = float(shared.area)
    if shared.is_empty or area <= 0.0:
        distance = float(act_valid.distance(parcel_valid))
        reason: RejectionReason = "boundary_touch_only" if distance <= 1e-6 else "no_overlap"
        return IntersectionOutcome(None, reason, 0.0, round(distance, 3), repaired)
    if area < MIN_INTERSECTION_AREA_SQM:
        return IntersectionOutcome(None, "below_min_area", round(area, 4), 0.0, repaired)
    pct = min(100.0, area / float(parcel_valid.area) * 100.0)
    return IntersectionOutcome(
        ActIntersection(round(area, 2), round(pct, 2), shared, repaired),
        None,
        round(area, 2),
        0.0,
        repaired,
    )


# --- status prawny RU ---------------------------------------------------------------------------------------


def ru_legal_status(
    act: RuActRecord, as_of: date
) -> tuple[ActLegalStatus, list[str]]:
    """Status prawny aktu RU z pola ``status`` (kodlista INSPIRE) oraz dat „obowiązuje od/do”.

    Sam kod ``legalForce`` nie wystarcza, gdy data „obowiązuje do” minęła (``MPZP_ACT_REPEALED``) albo
    „obowiązuje od” leży w przyszłości (``MPZP_ACT_NOT_YET_IN_FORCE``). Projekt (``elaboration``/``adoption``)
    nie obowiązuje — jego „obowiązuje od” to data wersji rekordu, nie data wejścia planu w życie.
    """
    code = (act.status_code or "").casefold()
    reasons: list[str] = []
    if code in _RU_STATUS_NOT_YET:
        return "not_yet_binding", [MPZP_ACT_DRAFT]
    if code in _RU_STATUS_NOT_BINDING:
        return "not_binding", [MPZP_ACT_REPEALED]
    if code not in _RU_STATUS_BINDING:
        return "unknown", [MPZP_ACT_STATUS_UNKNOWN]
    if act.effective_to is not None and act.effective_to <= as_of:
        return "not_binding", [MPZP_ACT_REPEALED]
    if act.effective_from is not None and act.effective_from > as_of:
        return "not_yet_binding", [MPZP_ACT_NOT_YET_IN_FORCE]
    return "binding", reasons


def kimpzp_legal_status(act: KimpzpAct, as_of: date) -> tuple[ActLegalStatus, list[str]]:
    """Status aktu z KIMPZP; data „utracił moc” z przeszłości lub dzisiejsza oznacza brak mocy."""
    if act.repealed_on is not None and act.repealed_on <= as_of:
        return "not_binding", [MPZP_ACT_REPEALED]
    if act.legal_status == "not_binding":
        return "not_binding", [MPZP_ACT_REPEALED]
    if act.valid_from is not None and act.valid_from > as_of:
        return "not_yet_binding", [MPZP_ACT_NOT_YET_IN_FORCE]
    if act.legal_status == "binding":
        return "binding", []
    return "unknown", [MPZP_ACT_STATUS_UNKNOWN]


# --- numer uchwały aktu RU ----------------------------------------------------------------------------------


def number_from_identifier(identifier: str) -> str | None:
    """Numer uchwały z lokalnego identyfikatora RU (``IV_30_2024_P1`` → ``IV/30/2024``) albo ``None``."""
    local = identifier.rsplit("/", 1)[-1]
    match = _IDENTIFIER_NUMBER.match(local)
    if not match:
        return None
    return normalize_resolution_number(f"{match['a']}/{match['b']}/{match['c']}")


def document_number(title: str | None, number: str | None) -> tuple[str | None, str | None]:
    """(numer, podstawa) z pól dokumentu: ``numerIdentyfikacyjny`` ma pierwszeństwo przed tytułem."""
    from_number = normalize_resolution_number(number)
    if from_number:
        return from_number, "document_number"
    from_title = resolution_number_from_text(title)
    if from_title:
        return from_title, "document_title"
    return None, None


# --- wynik scalony ------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ActAmendment:
    """Zmiana planu wymieniona przy akcie (nigdy samodzielny akt)."""

    resolution_number: str | None = None
    effective_from: date | None = None
    document_url: str | None = None
    name: str | None = None
    source: ActChannel = "kimpzp"


@dataclass(frozen=True)
class MpzpActRecord:
    """Akt MPZP wskazany dla działki przez jeden albo oba kanały."""

    act_identifier: str | None
    title: str | None
    resolution_number: str | None
    resolution_number_basis: ResolutionNumberBasis | None
    effective_from: date | None
    effective_to: date | None
    legal_status: ActLegalStatus
    raw_status: str | None
    document_url: str | None
    document_kind: DocumentKind | None
    legend_url: str | None
    drawing_url: str | None
    sources: tuple[ActChannel, ...]
    intersection: ActIntersection | None = None
    amendments: tuple[ActAmendment, ...] = ()
    precedence: ActPrecedence = "applies"
    manual_review_required: bool = False
    reason_codes: tuple[str, ...] = ()
    journal: str | None = None
    resolution_date: date | None = None
    # Wszystkie znane linki do tekstu aktu (z obu kanałów) — wyłącznie do dopasowania aktu bez numeru.
    match_links: tuple[str, ...] = ()
    ru: RuActRecord | None = field(default=None, compare=False)
    kimpzp: KimpzpAct | None = field(default=None, compare=False)

    @property
    def in_force_candidate(self) -> bool:
        """Akt, który może obowiązywać (``binding``) albo ma nieustalony status."""
        return self.legal_status in {"binding", "unknown"}

    def key(self) -> str | None:
        if self.resolution_number:
            return "nr:" + resolution_key(self.resolution_number)
        if self.act_identifier:
            return "id:" + self.act_identifier.casefold()
        return None


@dataclass(frozen=True)
class RejectedAct:
    """Akt zwrócony przez RU dla ``bbox`` działki, ale nieprzecinający jej (diagnostyka)."""

    act_identifier: str
    title: str | None
    legal_status: ActLegalStatus
    reason: RejectionReason
    distance_m: float | None
    area_sqm: float
    resolution_number: str | None = None
    raw_status: str | None = None


@dataclass(frozen=True)
class ChannelOutcome:
    """Stan jednego kanału (KIMPZP albo RU) dla działki."""

    channel: ActChannel
    status: ChannelStatus
    reason_codes: tuple[str, ...] = ()
    acts_found: int = 0


def latest_versions(acts: Sequence[RuActRecord]) -> tuple[list[RuActRecord], list[RuActRecord]]:
    """Zostawia najnowszą wersję każdego aktu (po ``version_started``, potem ``version_id``)."""
    best: dict[str, RuActRecord] = {}
    dropped: list[RuActRecord] = []
    for act in acts:
        current = best.get(act.identifier)
        if current is None:
            best[act.identifier] = act
            continue
        newer = _version_key(act) > _version_key(current)
        dropped.append(current if newer else act)
        if newer:
            best[act.identifier] = act
    kept = [best[act.identifier] for act in acts if best[act.identifier] is act]
    return kept, dropped


def _version_key(act: RuActRecord) -> tuple[datetime, str]:
    return (act.version_started or datetime.min, act.version_id or "")


# --- dokument aktu RU ---------------------------------------------------------------------------------------


def _best_document(
    act: RuActRecord, documents: dict[str, RuDocumentRecord]
) -> tuple[RuDocumentRecord | None, DocumentKind | None]:
    """Dokument aktu: uchwalający, potem ogólny (``dokument``), na końcu uchwała o przystąpieniu."""
    for roles, kind in ((("dokumentUchwalajacy",), "adopting"), (("dokument",), "other")):
        for identifier in act.documents_of(*roles):
            document = documents.get(identifier)
            if document is not None:
                return document, kind  # type: ignore[return-value]
    for identifier in act.documents_of("dokumentPrzystepujacy"):
        document = documents.get(identifier)
        if document is not None:
            return document, "commencement"
    return None, None


def _first_https(urls: Sequence[str | None]) -> str | None:
    cleaned = [url for url in urls if url]
    for url in cleaned:
        if urlsplit(url).scheme == "https":
            return url
    return cleaned[0] if cleaned else None


def normalize_link(url: str | None) -> str | None:
    """Klucz porównania linków: bez schematu, ``www.``, końcowego ukośnika, wielkość liter bez znaczenia."""
    if not url:
        return None
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").casefold().removeprefix("www.")
    if not host:
        return None
    path = parts.path.rstrip("/")
    query = f"?{parts.query}" if parts.query else ""
    return f"{host}{path}{query}".casefold()


def ru_candidates(
    acts: Sequence[RuActRecord],
    documents: dict[str, RuDocumentRecord],
    drawings: dict[str, RuDrawingRecord],
    intersections: dict[str, ActIntersection],
    as_of: date,
) -> list[MpzpActRecord]:
    """Akty RU przecinające działkę jako rekordy domenowe (przed scaleniem z KIMPZP)."""
    records: list[MpzpActRecord] = []
    for act in acts:
        intersection = intersections.get(act.gml_id)
        if intersection is None:
            continue
        status, reasons = ru_legal_status(act, as_of)
        document, kind = _best_document(act, documents)
        number: str | None = None
        basis: ResolutionNumberBasis | None = None
        if document is not None and kind in {"adopting", "other"}:
            number, basis = document.number, document.number_basis
        codes = list(reasons)
        if kind == "commencement":
            codes.append(MPZP_ACT_COMMENCEMENT_DOCUMENT_ONLY)
        if document is None and act.document_refs:
            codes.append(MPZP_ACT_DOCUMENT_UNRESOLVED)
        if number is None and status in {"binding", "not_binding"} and document is None:
            guessed = number_from_identifier(act.identifier)
            if guessed:
                number, basis = guessed, "identifier"
                codes.append(MPZP_ACT_NUMBER_FROM_IDENTIFIER)
        if intersection.geometry_repaired:
            codes.append(MPZP_ACT_GEOMETRY_REPAIRED)
        amendments = _ru_amendments(act, documents)
        if amendments:
            codes.append(MPZP_ACT_HAS_AMENDMENTS)
        effective_from = act.effective_from
        if document is not None and kind == "adopting" and document.effective_from:
            if effective_from and document.effective_from != effective_from:
                codes.append(MPZP_ACT_EFFECTIVE_DATE_MISMATCH)
            effective_from = document.effective_from
        drawing_links = [
            drawings[identifier]
            for identifier in act.drawing_refs
            if identifier in drawings
        ]
        records.append(
            MpzpActRecord(
                act_identifier=act.identifier,
                title=act.title,
                resolution_number=number,
                resolution_number_basis=basis,
                effective_from=effective_from,
                effective_to=act.effective_to,
                legal_status=status,
                raw_status=act.status_title or act.status_code,
                document_url=_first_https(document.links) if document else None,
                document_kind=kind,
                legend_url=_first_https([item.legend for item in drawing_links]),
                drawing_url=_first_https([item.link for item in drawing_links]),
                sources=("mpzp_ru",),
                intersection=intersection,
                amendments=amendments,
                reason_codes=tuple(dict.fromkeys(codes)),
                journal=document.journal if document else None,
                resolution_date=document.document_date if document else None,
                match_links=tuple(document.links) if document else (),
                ru=act,
            )
        )
    return records


def _ru_amendments(act: RuActRecord, documents: dict[str, RuDocumentRecord]) -> tuple[ActAmendment, ...]:
    found: list[ActAmendment] = []
    for identifier in act.documents_of("dokumentZmieniajacy"):
        document = documents.get(identifier)
        if document is None:
            found.append(ActAmendment(source="mpzp_ru"))
            continue
        found.append(
            ActAmendment(
                resolution_number=document.number,
                effective_from=document.effective_from,
                document_url=_first_https(document.links),
                name=document.title and " ".join(document.title.split()),
                source="mpzp_ru",
            )
        )
    return tuple(found)


def kimpzp_candidates(acts: Sequence[KimpzpAct], as_of: date) -> list[MpzpActRecord]:
    """Akty z KIMPZP (bez geometrii: udział w działce jest ``null``, nie ``0``)."""
    records: list[MpzpActRecord] = []
    for act in acts:
        status, reasons = kimpzp_legal_status(act, as_of)
        codes = list(reasons)
        if act.amendments:
            codes.append(MPZP_ACT_HAS_AMENDMENTS)
        records.append(
            MpzpActRecord(
                act_identifier=None,
                title=act.name,
                resolution_number=act.resolution_number,
                resolution_number_basis="kimpzp" if act.resolution_number else None,
                effective_from=act.valid_from,
                effective_to=act.repealed_on,
                legal_status=status,
                raw_status=act.legal_status,
                document_url=act.document_url,
                document_kind="adopting" if act.document_url else None,
                legend_url=act.legend_url,
                drawing_url=act.drawing_url,
                sources=("kimpzp",),
                intersection=None,
                amendments=tuple(
                    ActAmendment(
                        resolution_number=item.resolution_number,
                        effective_from=item.valid_from,
                        document_url=item.document_url,
                        name=item.name or item.raw_text,
                        source="kimpzp",
                    )
                    for item in act.amendments
                ),
                reason_codes=tuple(dict.fromkeys(codes)),
                journal=act.journal,
                resolution_date=act.resolution_date,
                match_links=tuple(url for url in (act.text_url, act.www_url) if url),
                kimpzp=act,
            )
        )
    return records


# --- scalanie kanałów ---------------------------------------------------------------------------------------


def _same_act(left: MpzpActRecord, right: MpzpActRecord) -> bool:
    if left.resolution_number and right.resolution_number:
        return resolution_key(left.resolution_number) == resolution_key(right.resolution_number)
    links_left = {normalize_link(url) for url in _record_links(left)} - {None}
    links_right = {normalize_link(url) for url in _record_links(right)} - {None}
    return bool(links_left & links_right)


def _record_links(record: MpzpActRecord) -> list[str | None]:
    return [record.document_url, *record.match_links]


def merge_channels(
    ru_records: Sequence[MpzpActRecord], kimpzp_records: Sequence[MpzpActRecord]
) -> list[MpzpActRecord]:
    """Łączy ten sam akt z obu kanałów (numer uchwały, a w razie braku — link do tekstu)."""
    merged: list[MpzpActRecord] = []
    unmatched_k = list(kimpzp_records)
    for ru in ru_records:
        match = next((item for item in unmatched_k if _same_act(ru, item)), None)
        if match is None:
            merged.append(ru)
            continue
        unmatched_k.remove(match)
        merged.append(_merge_pair(ru, match))
    merged.extend(unmatched_k)
    return merged


def _merge_pair(ru: MpzpActRecord, kimpzp: MpzpActRecord) -> MpzpActRecord:
    codes = list(ru.reason_codes)
    for code in kimpzp.reason_codes:
        if code not in codes and code != MPZP_ACT_HAS_AMENDMENTS:
            codes.append(code)
    effective = ru.effective_from or kimpzp.effective_from
    if ru.effective_from and kimpzp.effective_from and ru.effective_from != kimpzp.effective_from:
        if MPZP_ACT_EFFECTIVE_DATE_MISMATCH not in codes:
            codes.append(MPZP_ACT_EFFECTIVE_DATE_MISMATCH)
    # Status: akt, który którykolwiek kanał uznaje za pozbawiony mocy/nieobowiązujący, nie jest „obowiązujący”.
    statuses = {ru.legal_status, kimpzp.legal_status}
    if "not_binding" in statuses:
        status: ActLegalStatus = "not_binding"
    elif "not_yet_binding" in statuses:
        status = "not_yet_binding"
    elif ru.legal_status == "binding" or kimpzp.legal_status == "binding":
        status = "binding"
    else:
        status = "unknown"
    document = _first_https([ru.document_url, kimpzp.document_url]) or ru.document_url or kimpzp.document_url
    amendments = list(ru.amendments)
    known = {item.resolution_number and resolution_key(item.resolution_number) for item in amendments}
    amendments.extend(
        item
        for item in kimpzp.amendments
        if not item.resolution_number or resolution_key(item.resolution_number) not in known
    )
    if amendments and MPZP_ACT_HAS_AMENDMENTS not in codes:
        codes.append(MPZP_ACT_HAS_AMENDMENTS)
    return replace(
        ru,
        title=ru.title or kimpzp.title,
        resolution_number=ru.resolution_number or kimpzp.resolution_number,
        resolution_number_basis=ru.resolution_number_basis
        if ru.resolution_number
        else kimpzp.resolution_number_basis,
        effective_from=effective,
        effective_to=ru.effective_to or kimpzp.effective_to,
        legal_status=status,
        raw_status=ru.raw_status or kimpzp.raw_status,
        document_url=document,
        document_kind=ru.document_kind or kimpzp.document_kind,
        legend_url=ru.legend_url or kimpzp.legend_url,
        drawing_url=ru.drawing_url or kimpzp.drawing_url,
        sources=("kimpzp", "mpzp_ru"),
        amendments=tuple(amendments),
        reason_codes=tuple(dict.fromkeys(codes)),
        journal=ru.journal or kimpzp.journal,
        resolution_date=ru.resolution_date or kimpzp.resolution_date,
        match_links=tuple(dict.fromkeys([*ru.match_links, *kimpzp.match_links])),
        kimpzp=kimpzp.kimpzp,
    )


# --- pierwszeństwo informacyjne ------------------------------------------------------------------------------


def _overlap(left: MpzpActRecord, right: MpzpActRecord) -> bool | None:
    """Czy akty pokrywają wspólną część działki; ``None`` = nie wiadomo (brak geometrii któregoś z nich)."""
    if left.intersection is None or right.intersection is None:
        return None
    shared = shapely.intersection(left.intersection.geometry, right.intersection.geometry)
    return float(shared.area) >= MIN_INTERSECTION_AREA_SQM


def apply_precedence(records: Sequence[MpzpActRecord]) -> list[MpzpActRecord]:
    """Reguła ``mpzp-act-precedence/1.0`` — patrz opis modułu.

    R1  akt bez mocy albo jeszcze nieobowiązujący (utracił moc, projekt) nie uczestniczy → ``not_in_force``;
    R2  akty uczestniczące, których części działki są rozłączne (obie geometrie znane) → ``complementary``;
    R3  akty uczestniczące, które mogą się nakładać (geometria wspólna albo nieznana) → ``unresolved``
        i ``manual_review_required`` — późniejsza data „obowiązuje od” sama nie rozstrzyga;
    R4  jedyny akt uczestniczący → ``applies``.
    """
    participants = [r for r in records if r.in_force_candidate]
    parent = {index: index for index in range(len(participants))}

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for left in range(len(participants)):
        for right in range(left + 1, len(participants)):
            if _overlap(participants[left], participants[right]) is not False:
                parent[find(left)] = find(right)
    members: dict[int, list[int]] = {}
    for index in range(len(participants)):
        members.setdefault(find(index), []).append(index)
    group_size = {index: len(members[find(index)]) for index in range(len(participants))}

    outcome: dict[int, MpzpActRecord] = {}
    for index, record in enumerate(participants):
        codes = list(record.reason_codes)
        review = record.manual_review_required
        if group_size[index] > 1:
            precedence: ActPrecedence = "unresolved"
            review = True
            codes.append(MPZP_ACT_OVERLAP_UNRESOLVED)
            if record.intersection is None:
                codes.append(MPZP_ACT_NO_GEOMETRY)
        elif len(participants) > 1:
            precedence = "complementary"
        else:
            precedence = "applies"
        if record.legal_status == "unknown":
            review = True
        outcome[id(record)] = replace(
            record,
            precedence=precedence,
            manual_review_required=review,
            reason_codes=tuple(dict.fromkeys(codes)),
        )
    result: list[MpzpActRecord] = []
    for record in records:
        if record.in_force_candidate:
            result.append(outcome[id(record)])
        else:
            result.append(replace(record, precedence="not_in_force"))
    return sort_records(result)


def sort_records(records: Sequence[MpzpActRecord]) -> list[MpzpActRecord]:
    """Obowiązujące przed nieobowiązującymi, w grupie: „obowiązuje od” malejąco, potem numer uchwały."""

    def key(record: MpzpActRecord) -> tuple[int, int, int, str]:
        in_force = 0 if record.in_force_candidate else 1
        return (
            in_force,
            0 if record.effective_from else 1,
            -(record.effective_from.toordinal() if record.effective_from else 0),
            record.resolution_number or record.act_identifier or "",
        )

    return sorted(records, key=key)


def channel_agreement(
    ru_records: Sequence[MpzpActRecord],
    kimpzp_records: Sequence[MpzpActRecord],
    ru_status: ChannelStatus,
    kimpzp_status: ChannelStatus,
) -> tuple[Literal["agree", "mismatch", "single_channel", "none"], tuple[str, ...], tuple[str, ...]]:
    """Zgodność kanałów wyłącznie dla aktów, które mogą obowiązywać.

    ``mismatch`` tylko wtedy, gdy oba kanały wskazują akty obowiązujące, a ich zbiory się różnią. Kanał, który
    nie ma aktu (RU bywa niekompletny), nie jest niezgodnością — to ``single_channel``. Projekt RU bez mocy
    prawnej nie konkuruje z obowiązującym aktem KIMPZP.
    """
    left = [r for r in ru_records if r.in_force_candidate]
    right = [r for r in kimpzp_records if r.in_force_candidate]
    ru_labels = tuple(_label(r) for r in left)
    kimpzp_labels = tuple(_label(r) for r in right)
    if ru_status != "available" or kimpzp_status != "available" or not left or not right:
        has_any = bool(left or right)
        return ("single_channel" if has_any else "none"), ru_labels, kimpzp_labels
    # Akt bez numeru uchwały jest dopasowywany po linku do tekstu (``_same_act``).
    if all(_matched(r, right) for r in left) and all(_matched(r, left) for r in right):
        return "agree", ru_labels, kimpzp_labels
    return "mismatch", ru_labels, kimpzp_labels


def _matched(record: MpzpActRecord, others: Sequence[MpzpActRecord]) -> bool:
    return any(_same_act(record, other) for other in others)


def _label(record: MpzpActRecord) -> str:
    return record.resolution_number or record.act_identifier or record.title or "akt bez numeru"
