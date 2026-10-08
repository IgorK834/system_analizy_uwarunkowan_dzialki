"""Typy domenowe punktowego rozpoznania aktów MPZP z KIMPZP (AU-004).

KIMPZP (``plany_granice``, WMS GetFeatureInfo) zwraca sklejone odpowiedzi usług
gminnych w kilku formatach. Adapter w ``infrastructure`` sprowadza je do
``KimpzpPointResult``: listy aktów obecnych w punkcie, ich zmian oraz jednego
z rozłącznych statusów źródła. Moduł jest czystą logiką — bez HTML i sieci.

Zasady:

* numer uchwały pochodzi wyłącznie z pola rekordu planu i musi przejść walidację
  formatu (``normalize_resolution_number``) — nazwy plików, UUID i numery
  porządkowe planu nie są numerem uchwały;
* zmiany planu („Zmiany tekstowe”, „Zmiany”) są osobną listą ``amendments`` aktu,
  nigdy samodzielnym aktem;
* „brak serwisu dla wskazanego obszaru” to ``no_coverage``, błąd usługi to
  ``unavailable`` — żaden z nich nie jest „nie znaleziono planu” (``no_match``);
* gdy w punkcie jest więcej niż jeden obowiązujący akt, wynik niesie flagę
  ``MPZP_MULTIPLE_ACTS_AT_POINT`` i nie wybiera aktu (rozstrzygnięcie: AU-101).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Final, Literal

KimpzpSourceStatus = Literal["available", "no_match", "no_coverage", "unavailable", "unknown"]
KimpzpActLegalStatus = Literal["binding", "not_binding", "unknown"]
KimpzpInformatization = Literal["vector", "raster", "unknown"]

KIMPZP_NO_SERVICE_FOR_AREA: Final[str] = "KIMPZP_NO_SERVICE_FOR_AREA"
KIMPZP_NO_RESULT_FOR_AREA: Final[str] = "KIMPZP_NO_RESULT_FOR_AREA"
KIMPZP_SERVICE_ERROR: Final[str] = "KIMPZP_SERVICE_ERROR"
KIMPZP_UNRECOGNIZED_RESPONSE: Final[str] = "KIMPZP_UNRECOGNIZED_RESPONSE"
KIMPZP_EMPTY_RESPONSE: Final[str] = "KIMPZP_EMPTY_RESPONSE"
KIMPZP_PARTIAL_SERVICE_ERROR: Final[str] = "KIMPZP_PARTIAL_SERVICE_ERROR"
MPZP_MULTIPLE_ACTS_AT_POINT: Final[str] = "MPZP_MULTIPLE_ACTS_AT_POINT"

# Kolejność rozstrzygania statusu punktu złożonego z wielu odpowiedzi gminnych:
# znaleziony akt wygrywa, a błąd usługi jest ważniejszy niż „brak wyniku”, bo
# „brak wyniku” jednej usługi nie unieważnia błędu drugiej.
_STATUS_PRECEDENCE: Final[tuple[KimpzpSourceStatus, ...]] = (
    "available",
    "unavailable",
    "unknown",
    "no_match",
    "no_coverage",
)

_NULL_TOKENS: Final[frozenset[str]] = frozenset({"", "null", "none", "brak", "-", "n/a"})
_RESOLUTION_PREFIX = re.compile(
    r"^(?:uchwa[lł]a\s+)?(?:nr\.?|numer)\s*[:.]?\s*", re.IGNORECASE
)
# Numer uchwały: co najmniej dwa człony rozdzielone ukośnikiem, np. IV/30/2024,
# 576/XLVII/2010, XII/131/11, LIII/1464/21. Człon to cyfry lub litery z
# opcjonalnymi kropkami/łącznikami („XXXIV/123.2/2005”, „Nr 12-A/2001”).
_RESOLUTION_NUMBER = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.\-]*(?:/[0-9A-Za-z][0-9A-Za-z.\-]*)+$")
_RESOLUTION_IN_TEXT = re.compile(
    r"(?:uchwa[lł]\w*\s+)?(?:nr\.?|numer)\s+([0-9A-Za-z][0-9A-Za-z.\-]*(?:/[0-9A-Za-z][0-9A-Za-z.\-]*)+)",
    re.IGNORECASE,
)
_MAX_RESOLUTION_LENGTH: Final[int] = 60
_ISO_DATE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})")
_DOTTED_DATE = re.compile(r"^(\d{1,2})[.](\d{1,2})[.](\d{4})")
_DOTTED_DATE_YEAR_FIRST = re.compile(r"^(\d{4})[.](\d{1,2})[.](\d{1,2})")
# GeoServer z domyślną lokalizacją Javy: „8/11/12, 12:00 AM”.
_US_SHORT_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2,4}),?\s")


def fold_text(value: str) -> str:
    """Klucz porównań: bez diakrytyków, małe litery, pojedyncze spacje.

    ``ł`` nie rozkłada się w NFKD, dlatego jest zamieniane jawnie.
    """
    decomposed = unicodedata.normalize("NFKD", value.replace("ł", "l").replace("Ł", "L"))
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(stripped.casefold().split())


def clean_value(value: str | None) -> str | None:
    """Usuwa białe znaki i zamienia znaczniki pustej wartości (``NULL``) na ``None``."""
    if value is None:
        return None
    text = " ".join(value.split())
    if fold_text(text) in _NULL_TOKENS:
        return None
    return text


def normalize_resolution_number(value: str | None) -> str | None:
    """Zwraca numer uchwały w postaci kanonicznej albo ``None``.

    Odrzuca wartości, które nie są numerem uchwały: nazwy plików
    (``XXI_232_20_rys``), numery porządkowe planu (``001``), UUID i opisy.
    Usuwa prefiks „Uchwała nr”/„Nr” i spacje wokół ukośników.
    """
    text = clean_value(value)
    if text is None:
        return None
    text = _RESOLUTION_PREFIX.sub("", text)
    text = re.sub(r"\s*/\s*", "/", text).strip().rstrip(".,;")
    if not text or len(text) > _MAX_RESOLUTION_LENGTH or " " in text:
        return None
    if not _RESOLUTION_NUMBER.match(text):
        return None
    if not any(char.isdigit() for char in text) and not _looks_roman(text):
        return None
    return text


def resolution_number_from_text(value: str | None) -> str | None:
    """Wyłuskuje numer uchwały z opisu („Uchwała nr XXIII/322/2012 Rady …”)."""
    text = clean_value(value)
    if text is None:
        return None
    match = _RESOLUTION_IN_TEXT.search(text)
    return normalize_resolution_number(match.group(1)) if match else None


def resolution_key(number: str) -> str:
    """Klucz deduplikacji numeru uchwały (wielkość liter i kropki bez znaczenia)."""
    return number.casefold().rstrip(".")


def _looks_roman(text: str) -> bool:
    return any(re.fullmatch(r"[IVXLCDM]+", part) for part in text.upper().split("/"))


def parse_source_date(value: str | None) -> date | None:
    """Data z formatów spotykanych w odpowiedziach gminnych; nieznany format → ``None``.

    Obsługiwane: ISO (``2024-06-26``, także z czasem), ``17.06.2011``,
    ``2011.06.17`` i amerykański skrót GeoServera ``8/11/12, 12:00 AM``.
    """
    text = clean_value(value)
    if text is None:
        return None
    try:
        if match := _ISO_DATE.match(text):
            return date(int(match[1]), int(match[2]), int(match[3]))
        if match := _DOTTED_DATE.match(text):
            return date(int(match[3]), int(match[2]), int(match[1]))
        if match := _DOTTED_DATE_YEAR_FIRST.match(text):
            return date(int(match[1]), int(match[2]), int(match[3]))
        if match := _US_SHORT_DATE.match(text):
            year = int(match[3])
            if year < 100:
                year += 2000 if year < 70 else 1900
            return date(year, int(match[1]), int(match[2]))
        return datetime.strptime(text, "%b %d, %Y, %I:%M:%S %p").date()
    except ValueError:
        return None


def legal_status_from_text(value: str | None) -> KimpzpActLegalStatus:
    """Status obowiązywania z nagłówka bloku albo pola ``status``."""
    text = fold_text(value or "")
    if not text:
        return "unknown"
    if any(token in text for token in ("nieobow", "uchylon", "utracil", "archiwaln", "niewazn")):
        return "not_binding"
    if any(token in text for token in ("obowiaz", "obowazuj", "prawnie wiazacy", "wiazacy")):
        return "binding"
    return "unknown"


def informatization_from_text(value: str | None) -> KimpzpInformatization:
    text = fold_text(value or "")
    if "rastr" in text or "raster" in text:
        return "raster"
    if "wektor" in text:
        return "vector"
    return "unknown"


@dataclass(frozen=True)
class KimpzpAmendment:
    """Zmiana planu wymieniona przy akcie — nigdy samodzielny akt w punkcie."""

    kind: Literal["text_change", "change", "note"]
    resolution_number: str | None = None
    name: str | None = None
    adopted_on: date | None = None
    valid_from: date | None = None
    document_url: str | None = None
    bip_url: str | None = None
    raw_text: str | None = None


@dataclass(frozen=True)
class KimpzpAct:
    """Akt MPZP obecny w punkcie według jednej lub kilku odpowiedzi gminnych."""

    resolution_number: str | None
    resolution_date: date | None = None
    name: str | None = None
    valid_from: date | None = None
    repealed_on: date | None = None
    legal_status: KimpzpActLegalStatus = "unknown"
    text_url: str | None = None
    legend_url: str | None = None
    drawing_url: str | None = None
    bip_url: str | None = None
    www_url: str | None = None
    journal: str | None = None
    informatization: KimpzpInformatization = "unknown"
    zone_symbols: tuple[str, ...] = ()
    amendments: tuple[KimpzpAmendment, ...] = ()
    source_format: str = "unknown"

    @property
    def document_url(self) -> str | None:
        """Dokument do parsera: tekst uchwały, a dopiero gdy go brak — ogólny link WWW.

        Strona BIP i legenda nie są tekstem uchwały, więc nie są tu używane.
        """
        return self.text_url or self.www_url

    @property
    def in_force_or_unknown(self) -> bool:
        return self.legal_status != "not_binding"

    def identity(self) -> str | None:
        if self.resolution_number:
            return "nr:" + resolution_key(self.resolution_number)
        if self.text_url:
            return "url:" + self.text_url
        return None


@dataclass(frozen=True)
class KimpzpPointResult:
    """Wynik jednego punktu próbki (jedno zapytanie GetFeatureInfo)."""

    status: KimpzpSourceStatus
    acts: tuple[KimpzpAct, ...] = ()
    reason_codes: tuple[str, ...] = ()
    unassigned_zone_symbols: tuple[str, ...] = ()
    vector_available: bool = True
    messages: tuple[str, ...] = ()

    @property
    def found(self) -> bool:
        return self.status == "available"

    @property
    def zone_symbols(self) -> tuple[str, ...]:
        symbols: list[str] = []
        for act in self.acts:
            symbols.extend(act.zone_symbols)
        symbols.extend(self.unassigned_zone_symbols)
        return tuple(dict.fromkeys(symbols))

    @property
    def multiple_acts(self) -> bool:
        return len([act for act in self.acts if act.in_force_or_unknown]) > 1


@dataclass
class _ActDraft:
    act: KimpzpAct
    symbols: list[str] = field(default_factory=list)
    amendments: list[KimpzpAmendment] = field(default_factory=list)


def merge_acts(acts: list[KimpzpAct]) -> tuple[KimpzpAct, ...]:
    """Scala opisy tego samego aktu (ten sam numer uchwały) i sortuje wynik.

    Pierwsza niepusta wartość pola wygrywa — kolejność wejścia to kolejność
    dokumentu, więc blok planu ma pierwszeństwo przed rekordem strefy. Status
    ``not_binding`` z jednego źródła nie jest nadpisywany przez ``unknown``.
    Akty bez numeru i bez linku do tekstu nie są scalane (brak tożsamości).
    """
    drafts: dict[str, _ActDraft] = {}
    anonymous: list[_ActDraft] = []
    for act in acts:
        key = act.identity()
        if key is None:
            anonymous.append(_ActDraft(act, list(act.zone_symbols), list(act.amendments)))
            continue
        draft = drafts.get(key)
        if draft is None:
            drafts[key] = _ActDraft(act, list(act.zone_symbols), list(act.amendments))
            continue
        draft.act = _merge_pair(draft.act, act)
        draft.symbols.extend(act.zone_symbols)
        draft.amendments.extend(act.amendments)
    merged = [
        replace(
            draft.act,
            zone_symbols=tuple(dict.fromkeys(draft.symbols)),
            amendments=_unique_amendments(draft.amendments),
        )
        for draft in [*drafts.values(), *anonymous]
    ]
    return tuple(sort_acts(merged))


def _merge_pair(first: KimpzpAct, second: KimpzpAct) -> KimpzpAct:
    values = {}
    for name in (
        "resolution_date",
        "name",
        "valid_from",
        "repealed_on",
        "text_url",
        "legend_url",
        "drawing_url",
        "bip_url",
        "www_url",
        "journal",
    ):
        values[name] = getattr(first, name) or getattr(second, name)
    legal_status = first.legal_status if first.legal_status != "unknown" else second.legal_status
    informatization = (
        first.informatization if first.informatization != "unknown" else second.informatization
    )
    return replace(
        first,
        legal_status=legal_status,
        informatization=informatization,
        **values,
    )


def _unique_amendments(amendments: list[KimpzpAmendment]) -> tuple[KimpzpAmendment, ...]:
    seen: set[tuple[str, str | None, str | None]] = set()
    unique: list[KimpzpAmendment] = []
    for amendment in amendments:
        key = (
            amendment.kind,
            resolution_key(amendment.resolution_number) if amendment.resolution_number else None,
            amendment.raw_text if amendment.resolution_number is None else None,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(amendment)
    return tuple(unique)


def sort_acts(acts: list[KimpzpAct]) -> list[KimpzpAct]:
    """Kolejność: „obowiązuje od” malejąco, potem data uchwały malejąco, brak dat na końcu."""

    def key(act: KimpzpAct) -> tuple[int, int, int, int, str]:
        valid_from = act.valid_from.toordinal() if act.valid_from else 0
        adopted = act.resolution_date.toordinal() if act.resolution_date else 0
        return (
            0 if act.valid_from else 1,
            -valid_from,
            0 if act.resolution_date else 1,
            -adopted,
            act.resolution_number or "",
        )

    return sorted(acts, key=key)


def combine_statuses(statuses: list[KimpzpSourceStatus]) -> KimpzpSourceStatus:
    """Status wielu odpowiedzi gminnych; pusta lista oznacza ``unknown``."""
    for status in _STATUS_PRECEDENCE:
        if status in statuses:
            return status
    return "unknown"


@dataclass(frozen=True)
class KimpzpDiscoverySummary:
    """Agregat wszystkich punktów próbki działki."""

    status: KimpzpSourceStatus
    acts: tuple[KimpzpAct, ...]
    reason_codes: tuple[str, ...]
    zone_symbols: tuple[str, ...]
    multiple_acts_at_point: bool
    multiple_acts_on_parcel: bool
    failed_points: int
    queried_points: int
    saw_vector_unavailable: bool

    @property
    def single_act(self) -> KimpzpAct | None:
        """Jedyny obowiązujący (lub o nieznanym statusie) akt; przy kilku — ``None``."""
        candidates = [act for act in self.acts if act.in_force_or_unknown]
        return candidates[0] if len(candidates) == 1 else None


def summarize_points(
    results: list[KimpzpPointResult | None],
) -> KimpzpDiscoverySummary:
    """Agreguje punkty bez uprzywilejowania żadnego z nich.

    ``None`` oznacza punkt, którego zapytanie się nie powiodło (sieć, HTTP) —
    liczy się jak ``unavailable``.
    """
    statuses: list[KimpzpSourceStatus] = []
    acts: list[KimpzpAct] = []
    reasons_by_status: list[tuple[KimpzpSourceStatus, tuple[str, ...]]] = []
    symbols: list[str] = []
    multiple_at_point = False
    failed = 0
    saw_vector_unavailable = False
    for result in results:
        if result is None:
            failed += 1
            statuses.append("unavailable")
            reasons_by_status.append(("unavailable", (KIMPZP_SERVICE_ERROR,)))
            continue
        statuses.append(result.status)
        acts.extend(result.acts)
        reasons_by_status.append((result.status, result.reason_codes))
        symbols.extend(result.zone_symbols)
        multiple_at_point = multiple_at_point or result.multiple_acts
        if not result.vector_available:
            saw_vector_unavailable = True
    merged = merge_acts(acts)
    status = combine_statuses(statuses)
    in_force = [act for act in merged if act.in_force_or_unknown]
    # Powody tylko punktów o statusie końcowym (np. „brak wyniku” jednego punktu
    # nie jest powodem, gdy inny punkt wskazał akt); błąd części punktów przy
    # znalezionym akcie jest jawnie oznaczony jako wynik częściowy.
    reasons = [
        reason
        for point_status, point_reasons in reasons_by_status
        if point_status == status
        for reason in point_reasons
    ]
    if multiple_at_point:
        reasons.append(MPZP_MULTIPLE_ACTS_AT_POINT)
    if status == "available" and "unavailable" in statuses:
        reasons.append(KIMPZP_PARTIAL_SERVICE_ERROR)
    return KimpzpDiscoverySummary(
        status=status,
        acts=merged,
        reason_codes=tuple(dict.fromkeys(reasons)),
        zone_symbols=tuple(dict.fromkeys(symbols)),
        multiple_acts_at_point=multiple_at_point,
        multiple_acts_on_parcel=len(in_force) > 1,
        failed_points=failed,
        queried_points=len(results),
        saw_vector_unavailable=saw_vector_unavailable,
    )
