"""Leksykon ilości z ustaleń planów miejscowych (PV3-07).

Moduł zawiera wyłącznie DANE języka ustaleń: rzeczowniki parametrów, operatory
(przymiotnik przed wartością, rzeczownik po wartości, porównawcze z przeczeniem, ``do N``,
zakresy), jednostki, wykluczenia i znaczniki list. Logika dopasowania jest w
``quantity_engine``, a zamiana zapisu na liczbę w ``quantity_normalization``. Jeden leksykon
obsługuje oba dotychczasowe parsery (``mpzp_parser_numeric``, ``mpzp_parser_descriptive``)
i serwis reguł (``rules``), więc poprawka sformułowania jest robiona raz.

Zasada ostrożności: leksykon wymienia, co WOLNO przypisać do parametru, a wszystko, co nie
jest wymienione (np. wysokość obiektów małej architektury), nie trafia do wyniku. Brak
dopasowania jest zawsze lepszy od zgadywania — ``null`` to nie ``0``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Mapping

from app.shared.numbers import NUMBER_WORD_PATTERN

# Zmiana jakiejkolwiek stałej w tym module zmienia wynik parsera; wchodzi do wersji
# parsera i do sygnatury kalibracji pewności (PV3-09).
LEXICON_VERSION: Final[str] = "quantity-lexicon/1.0"

# --- rodzaje jednostek ----------------------------------------------------------------

UNIT_NONE: Final[str] = "none"
UNIT_PERCENT: Final[str] = "percent"
UNIT_METER: Final[str] = "m"
UNIT_AREA: Final[str] = "m2"
UNIT_HECTARE: Final[str] = "ha"
UNIT_DEGREE: Final[str] = "deg"
UNIT_STOREY: Final[str] = "storey"
UNIT_PLACE: Final[str] = "place"

# Jednostka po liczbie (po opcjonalnej spacji). Kolejność ma znaczenie: ``m2`` przed ``m``.
# ``stopni`` i ``st.`` to zapis słowny stopni; litera ``o`` i ``º`` są artefaktami zapisu
# symbolu stopnia (``30o``, ``30º``) i dostają flagę oraz karę w normalizacji.
_UNIT_ALTERNATIVES: Final[tuple[tuple[str, str], ...]] = (
    (UNIT_PERCENT, r"%|procent\w*|proc\."),
    (UNIT_AREA, r"m(?:2(?!\d)|\s?²)|m\.?\s?kw\b\.?|metr\w*\s+kwadrat\w*"),
    (UNIT_HECTARE, r"ha\b"),
    (UNIT_METER, r"m(?![\wąćęłńóśźż²])|metr\w*"),
    (UNIT_DEGREE, r"[°º˚]|stopni\w*|st\."),
    (UNIT_STOREY, r"kondygnac\w*"),
    (UNIT_PLACE, r"miejsc\w*|stanowisk\w*"),
)
UNIT_REGEXES: Final[tuple[tuple[str, re.Pattern[str]], ...]] = tuple(
    (kind, re.compile(pattern, re.IGNORECASE)) for kind, pattern in _UNIT_ALTERNATIVES
)
# Litera ``o`` DOKLEJONA do liczby (``od 30o do 45o``) to artefakt symbolu stopnia; ze
# spacją byłaby przyimkiem (``o wysokości``).
DEGREE_LETTER_PATTERN: Final[re.Pattern[str]] = re.compile(r"o(?![\wąćęłńóśźż])")
# Symbol stopnia zniekształcony przez OCR (``30” do 45*``, ``25*- 40*``): znak doklejony do liczby.
OCR_DEGREE_GARBLE: Final[re.Pattern[str]] = re.compile(r"[”“\"*'’‘˝″](?![\wąćęłńóśźż])")

# Po jednostce ``m`` te słowa oznaczają, że to NIE wysokość/odsunięcie parametru
# (``12 m szerokości``, ``100 m n.p.m.``); przed liczbą — że opisuje inną miarę.
METER_TRAILING_STOP: Final[re.Pattern[str]] = re.compile(
    r"\s*(?:n\.?\s?p\.?\s?m|npm|szeroko|długo|głęboko|średnic|rzędn|mb\b)", re.IGNORECASE
)
OTHER_MEASURE_BEFORE: Final[re.Pattern[str]] = re.compile(
    r"(?:szeroko\w*|długo\w*|głęboko\w*|średnic\w*|promie\w*|rzędn\w*|odległo\w*)\s*(?:wynosi\s*)?"
    r"(?:co\s+najmniej\s+|nie\s+mniej\w*\s+ni[żz]\s+|do\s+|około\s+|ok\.\s*)?[:\-–—]?\s*$",
    re.IGNORECASE,
)

# --- operatory -----------------------------------------------------------------------

OP_MAX: Final[str] = "max"
OP_MIN: Final[str] = "min"
OP_EXACT: Final[str] = "exact"
OP_RANGE: Final[str] = "range"
# Operatory, które NIE dają wartości parametru: ``większej niż 11 m`` to próg (nie maksimum),
# ``o 0,6`` to przyrost względem wartości podstawowej.
OP_GT: Final[str] = "gt"
OP_LT: Final[str] = "lt"
OP_DELTA: Final[str] = "delta"

# Operator tuż PRZED wartością (adiektyw/rzeczownik/porównawcze/``do N``). Dopasowanie jest
# zakotwiczone na końcu okna poprzedzającego liczbę, więc ``do`` jako przyimek w środku
# zdania nie jest operatorem.
_MAX_WORDS: Final[str] = (
    r"maksymalnie|maksimum|maksymaln\w*|max\.?|"
    r"nie\s+więcej\s+niż|nie\s+większ\w*\s+niż|nie\s+wyżej(?:\s+jednak)?\s+niż|"
    r"nie\s+wyższ\w*\s+niż|nie\s+może\s+być\s+(?:większ\w*|wyższ\w*)\s+niż|"
    r"nie\s+może\s+przekraczać|nie\s+przekracza|nieprzekraczając\w*|nie\s+przekraczając\w*|"
    r"najwyżej|do"
)
_MIN_WORDS: Final[str] = (
    r"minimalnie|minimum|minimaln\w*|min\.?|"
    r"nie\s+mniej\s+niż|nie\s+mniejsz\w*\s+niż|co\s+najmniej|przynajmniej|"
    r"nie\s+może\s+być\s+(?:mniejsz\w*|niższ\w*)\s+niż|"
    r"nie\s+niżej\s+niż|nie\s+niższ\w*\s+niż"
)
_EXACT_WORDS: Final[str] = r"wynosi|wynoszą|wynoszące|równ\w*|w\s+wysokości"
_GT_WORDS: Final[str] = r"większ\w*\s+niż|wyższ\w*\s+niż|powyżej|ponad"
_LT_WORDS: Final[str] = r"mniejsz\w*\s+niż|niższ\w*\s+niż|poniżej"
_DELTA_WORDS: Final[str] = r"(?<![\wąćęłńóśźż])o"
OPERATOR_BEFORE: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    (OP_MIN, re.compile(rf"(?:{_MIN_WORDS})\s*[:\-–—,]?\s*$", re.IGNORECASE)),
    (OP_MAX, re.compile(rf"(?:{_MAX_WORDS})\s*[:\-–—,]?\s*$", re.IGNORECASE)),
    (OP_EXACT, re.compile(rf"(?:{_EXACT_WORDS})\s*[:\-–—,]?\s*$", re.IGNORECASE)),
    (OP_GT, re.compile(rf"(?:{_GT_WORDS})\s*[:\-–—,]?\s*$", re.IGNORECASE)),
    (OP_LT, re.compile(rf"(?:{_LT_WORDS})\s*[:\-–—,]?\s*$", re.IGNORECASE)),
    (OP_DELTA, re.compile(rf"(?:{_DELTA_WORDS})\s*$", re.IGNORECASE)),
)
# Kontekst przyrostu/odstępstwa w nagłówku albo przed wartością: liczby z takiej klauzuli
# opisują zmianę wskaźnika, nie jego wartość (``dopuszcza się zwiększenie maksymalnych
# wskaźników: powierzchni zabudowy – do 100%``).
DELTA_CONTEXT: Final[re.Pattern[str]] = re.compile(
    r"zwiększeni\w*|zmniejszeni\w*|zwiększyć|zmniejszyć|zwiększa|zmniejsza|odstępstw\w*",
    re.IGNORECASE,
)
# Przymiotnik/rzeczownik przy RZECZOWNIKU PARAMETRU (``maksymalny wskaźnik``, ``wskaźnik
# minimalnej intensywności``): operator wynika z okna przed rzeczownikiem.
NOUN_PREFIX_MAX: Final[re.Pattern[str]] = re.compile(r"maksymaln\w*|maksimum|maksymalnie", re.IGNORECASE)
NOUN_PREFIX_MIN: Final[re.Pattern[str]] = re.compile(r"minimaln\w*|minimum|minimalnie", re.IGNORECASE)
NOUN_PREFIX_WINDOW: Final[int] = 36

# --- wykluczenia i rzeczowniki neutralizujące -----------------------------------------

# ``wysokość`` przy obiekcie spoza białej listy (parter, elewacja, mała architektura,
# infrastruktura, urządzenia, ogrodzenia, grunt…) NIE jest wysokością zabudowy. Takie trafienie
# zjada liczbę (``IGNORE``), zamiast oddać ją poprzedniemu rzeczownikowi.
FAMILY_IGNORE: Final[str] = "ignore"

# --- rodziny parametrów ---------------------------------------------------------------

FAMILY_HEIGHT: Final[str] = "height"
FAMILY_STOREYS: Final[str] = "storeys"
FAMILY_COVERAGE: Final[str] = "coverage"
FAMILY_BIO: Final[str] = "bio_active"
FAMILY_INTENSITY: Final[str] = "intensity"
FAMILY_ROOF: Final[str] = "roof_angle"
FAMILY_SETBACK: Final[str] = "setback"
FAMILY_PARKING: Final[str] = "parking"
FAMILY_PLOT: Final[str] = "min_plot_area"
FAMILY_RETAIL: Final[str] = "retail_area"


@dataclass(frozen=True)
class ParameterFamily:
    """Rodzina parametru: co przyjmuje, jaki ma domyślny operator i jak nazywa wynik.

    ``names`` mapuje operator na nazwę parametru parsera; ``None`` oznacza „rozpoznany, ale
    nie wydobywany” (np. minimalna wysokość — katalog parsera jej nie zna).
    """

    family: str
    unit: str | None
    names: Mapping[str, str | None]
    default_operator: str
    accepted_units: frozenset[str]
    nouns: tuple[str, ...] = ()
    statutory: bool = False
    # Czy brak jawnego operatora oznacza zgadywanie (flaga ``operator_implied`` i kara). Dla
    # wysokości czy udziałów domyślny kierunek jest standardem języka planów, dla intensywności
    # i kąta dachu — nie (pojedyncza liczba bywa dolną granicą, górną albo wartością stałą).
    implied_operator_flag: bool = False


def _family(
    family: str,
    unit: str | None,
    names: Mapping[str, str | None],
    default_operator: str,
    accepted: tuple[str, ...],
    nouns: tuple[str, ...] = (),
    *,
    statutory: bool = False,
    implied_operator_flag: bool = False,
) -> ParameterFamily:
    return ParameterFamily(
        family=family,
        unit=unit,
        names=MappingProxyType(dict(names)),
        default_operator=default_operator,
        accepted_units=frozenset(accepted),
        nouns=nouns,
        statutory=statutory,
        implied_operator_flag=implied_operator_flag,
    )


# Wysokość: biała lista przedmiotów (zabudowa, budynek). Wszystko inne po słowie ``wysokość``
# jest neutralizowane przez ``HEIGHT_OTHER_NOUN``.
_HEIGHT_MODIFIER: Final[str] = (
    r"(?:nowych|nowej|nowego|nowe|projektowan\w+|istniejąc\w+|poszczególn\w+|główn\w+|"
    r"pozostał\w+|wszystki\w+)"
)
HEIGHT_OTHER_NOUN: Final[str] = r"wysoko\w*"

# Cztery parametry spoza katalogu BK-603, nazwane w ustawie o planowaniu i zagospodarowaniu
# przestrzennym (art. 15 ust. 2 pkt 6 i 8, art. 10 ust. 3a): minimalna liczba miejsc do
# parkowania, minimalny udział powierzchni zabudowy, minimalna wielkość nowo wydzielanych
# działek i maksymalna powierzchnia sprzedaży obiektów handlowych. Pozostałe dziewięć to
# parametry katalogu ewaluacji BK-603.
PARAMETER_FAMILIES: Final[Mapping[str, ParameterFamily]] = MappingProxyType(
    {
        FAMILY_HEIGHT: _family(
            FAMILY_HEIGHT,
            "m",
            {OP_MAX: "max_building_height_m", OP_EXACT: "max_building_height_m", OP_MIN: None},
            OP_MAX,
            (UNIT_METER,),
            (
                rf"wysoko\w*\s+(?:{_HEIGHT_MODIFIER}\s+){{0,2}}(?:budynk\w+|zabudow\w+)",
                r"wysoko\w*(?=\s*[:–—-]\s*(?:\w+\s+){0,2}\d)",
            ),
        ),
        FAMILY_STOREYS: _family(
            FAMILY_STOREYS,
            None,
            {OP_MAX: "max_storeys", OP_EXACT: "max_storeys", OP_MIN: None},
            OP_MAX,
            (UNIT_STOREY, UNIT_NONE),
            (r"liczb\w*\s+kondygnacj\w*",),
        ),
        FAMILY_COVERAGE: _family(
            FAMILY_COVERAGE,
            "percent",
            {
                OP_MAX: "max_building_coverage_percent",
                OP_EXACT: "max_building_coverage_percent",
                OP_MIN: "min_building_coverage_percent",
            },
            OP_MAX,
            (UNIT_PERCENT, UNIT_NONE),
            (r"powierzchni\w*\s+zabudowy", r"(?:wskaźnik\w*|udzia\w+)\s+(?:procentow\w*\s+)?zabudowy"),
        ),
        FAMILY_BIO: _family(
            FAMILY_BIO,
            "percent",
            {OP_MIN: "min_biologically_active_percent", OP_EXACT: "min_biologically_active_percent", OP_MAX: None},
            OP_MIN,
            (UNIT_PERCENT, UNIT_NONE),
            (r"biologicznie\s+czynn\w*",),
        ),
        FAMILY_INTENSITY: _family(
            FAMILY_INTENSITY,
            None,
            {OP_MIN: "min_intensity", OP_MAX: "max_intensity", OP_EXACT: "max_intensity"},
            OP_MAX,
            (UNIT_NONE,),
            (r"intensywno\w*\s+zabudowy",),
            implied_operator_flag=True,
        ),
        FAMILY_ROOF: _family(
            FAMILY_ROOF,
            "deg",
            {OP_MIN: "roof_angle_min_deg", OP_MAX: "roof_angle_max_deg", OP_EXACT: None},
            OP_EXACT,
            (UNIT_DEGREE, UNIT_NONE),
            (
                r"k[ąa]t\w*\s+nachylenia",
                r"nachylen\w*",
                r"pochylen\w*",
                r"spadk\w*\s+połaci",
                r"pod\s+k[ąa]tem",
            ),
            implied_operator_flag=True,
        ),
        FAMILY_SETBACK: _family(
            FAMILY_SETBACK,
            "m",
            {OP_EXACT: "setback_m", OP_MIN: "setback_m", OP_MAX: None},
            OP_EXACT,
            (UNIT_METER,),
        ),
        FAMILY_PARKING: _family(
            FAMILY_PARKING,
            "miejsca/lokal",
            {OP_MIN: "parking_minimum", OP_EXACT: "parking_minimum", OP_MAX: None},
            OP_MIN,
            (UNIT_PLACE,),
            statutory=True,
        ),
        FAMILY_PLOT: _family(
            FAMILY_PLOT,
            "m2",
            {OP_MIN: "min_plot_area_m2", OP_EXACT: "min_plot_area_m2", OP_MAX: None},
            OP_MIN,
            (UNIT_AREA, UNIT_HECTARE),
            (r"minimaln\w*\s+powierzchni\w*\s+(?:nowo\s+wydziel\w+\s+)?(?:działk\w*|działek)",),
            statutory=True,
        ),
        FAMILY_RETAIL: _family(
            FAMILY_RETAIL,
            "m2",
            {OP_MAX: "max_retail_sales_area_m2", OP_EXACT: "max_retail_sales_area_m2", OP_MIN: None},
            OP_MAX,
            (UNIT_AREA,),
            (r"obiekt\w*\s+handlow\w*",),
            statutory=True,
        ),
    }
)

# Parametry parsera, które silnik zwraca, w stałej kolejności (kolejność wyniku jest częścią
# kontraktu: testy porównują listy, a evidence w snapshotach ma stabilną kolejność).
PARAMETER_ORDER: Final[tuple[str, ...]] = (
    "max_building_height_m",
    "min_intensity",
    "max_intensity",
    "max_building_coverage_percent",
    "min_biologically_active_percent",
    "roof_angle_min_deg",
    "roof_angle_max_deg",
    "max_storeys",
    "setback_m",
    "parking_minimum",
    "min_building_coverage_percent",
    "min_plot_area_m2",
    "max_retail_sales_area_m2",
)

# Rzeczowniki, które NIE są parametrem, ale zjadają liczbę (szerokość, długość, poziom
# posadowienia parteru itd.), żeby nie przypisać jej poprzedniemu rzeczownikowi.
NEUTRALIZING_NOUNS: Final[tuple[str, ...]] = (
    r"szeroko\w*",
    r"długo\w*",
    r"głęboko\w*",
    r"średnic\w*",
    r"promie[nń]\w*",
    r"rzędn\w*",
    r"poziom\w*\s+(?:posadowieni\w*|parteru|podłog\w*)",
    r"liczb\w*\s+(?:lokali|mieszka\w+|osób|stanowisk\s+pracy)",
)

# --- zasięg rzeczownika: ile znaków może dzielić rzeczownik od wartości --------------

MAX_GAP_FIRST: Final[int] = 130
MAX_GAP_NEXT: Final[int] = 100

# --- znaczniki list i klauzule --------------------------------------------------------

# Ranga znacznika: im niższa, tym wyższy poziom redakcyjny (§ > ust. > pkt > lit. > tiret).
RANK_SECTION: Final[int] = 0
RANK_PARAGRAPH: Final[int] = 1
RANK_POINT: Final[int] = 2
RANK_LETTER: Final[int] = 3
RANK_BULLET: Final[int] = 4

MARKER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[ \t]*(?:"
    r"(?P<section>§\s*\d+[a-z]?\.?)"
    r"|(?P<paragraph>\d{1,2}\.)(?=\s)"
    r"|(?P<point>\d{1,2}\))"
    r"|(?P<letter>[a-ząćęłńóśźż]\))"
    r"|(?P<bullet>[-–—−•·]{1,3})(?=\s|$)"
    r")[ \t]*",
    re.MULTILINE,
)

# --- szum stron i odwołania ------------------------------------------------------------

# Nagłówki i stopki stron (Dziennik Urzędowy, podpis elektroniczny, numery stron, kreski)
# oraz odwołania do przepisów nie niosą wartości parametrów; są maskowane spacjami o tej
# samej długości, więc zakresy znaków w tekście źródłowym pozostają prawdziwe.
NOISE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    # Nagłówek strony Dziennika Urzędowego: ``… Dolnośląskiego – 5 – Poz. 1124`` albo, z OCR,
    # ``… Wielkopolskiego Nr 91 — 9891 — Poz. 2276.`` (bez zjadania reszty linii: tekst z OCR
    # bywa jedną linią).
    re.compile(
        r"Dziennik\s+Urzędow\w+\s+[^\n–—]{0,90}?[–—]\s*\d+\s*[–—]\s*Poz\.\s*\d+\.?", re.IGNORECASE
    ),
    re.compile(r"\bId:\s*[0-9A-Fa-f-]{16,}\.?(?:\s+Podpisany)?(?:\s+Strona\s+\d+(?:\s+z\s+\d+)?)?"),
    re.compile(r"^[ \t]*Strona\s+\d+(?:\s+z\s+\d+)?[ \t]*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^[ \t–—\-_=.]{8,}$", re.MULTILINE),
    re.compile(
        r"(?:§|art\.|ust\.|pkt|lit\.|poz\.|rozdz\.|rozdziału|załącznik\w*)\s*\d+[a-z]?"
        r"(?:\s*(?:ust\.|pkt|lit\.)\s*\w+)*",
        re.IGNORECASE,
    ),
    # Identyfikator: token z cyfrą i wielką literą (symbole stref, numery, ``E2``, ``230_UMW``).
    re.compile(
        r"(?<![\wąćęłńóśźż])(?=[^\s]*\d)(?=[^\s]*[A-ZĄĆĘŁŃÓŚŹŻ])[0-9A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż][^\s,;:()]*"
    ),
)

# --- kary za cechy wyniku (strategia/flaga) --------------------------------------------

# Mnożnik pewności silnika dla każdej flagi; flagi wynikają z normalizacji i z dopasowania.
# To są kary SILNIKA (przed kalibracją): PV3-09 zastępuje je prawdopodobieństwem
# skalibrowanym na danych, a flagi stają się jego cechami.
FLAG_PENALTIES: Final[Mapping[str, float]] = MappingProxyType(
    {
        "ratio_to_percent": 0.95,
        "implicit_percent": 0.85,
        "percent_to_ratio": 0.9,
        "degree_artifact": 0.7,
        "degree_letter": 0.9,
        "unit_implied": 0.9,
        "number_word": 0.95,
        "inherited_noun": 1.0,
        "operator_implied": 0.88,
        "noun_implied": 0.9,
        "degree_ocr": 0.85,
        "approximate": 0.8,
        "double_notation": 1.0,
        "double_notation_mismatch": 0.6,
        "range_dash": 0.97,
        "hectare_to_m2": 0.97,
    }
)
BASE_CONFIDENCE: Final[float] = 0.85

# --- słownik geometrii dachu (współdzielony z parserem opisowym i warunkami) ----------

# ``(rdzeń przymiotnika, forma kanoniczna)``; rdzeń dopasowuje się jako początek słowa.
# Formy złożone (``dwuspadowe lub wielospadowe``) składa parser opisowy z tych rdzeni.
ROOF_TYPES: Final[tuple[tuple[str, str], ...]] = (
    ("dwuspadow", "dwuspadowy"),
    ("wielospadow", "wielospadowy"),
    ("jednospadow", "jednospadowy"),
    ("płask", "płaski"),
    ("strom", "stromy"),
    ("symetryczn", "symetryczny"),
    ("mansardow", "mansardowy"),
    ("kopułow", "kopułowy"),
)
ROOF_TYPE_LABELS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "dwuspadowy_lub_wielospadowy": "dach dwuspadowy lub wielospadowy",
        "dwuspadowy": "dach dwuspadowy",
        "wielospadowy": "dach wielospadowy",
        "jednospadowy": "dach jednospadowy",
        "płaski": "dach płaski",
        "stromy": "dach stromy",
        "symetryczny": "dach symetryczny",
        "mansardowy": "dach mansardowy",
        "kopułowy": "dach kopułowy",
    }
)

_ROOF_STEMS: Final[str] = "|".join(re.escape(stem) + r"\w*" for stem, _ in ROOF_TYPES)
ROOF_GEOMETRY_PATTERN: Final[re.Pattern[str]] = re.compile(
    rf"dach\w*\s+(?P<first>{_ROOF_STEMS})(?:\s+lub\s+(?P<second>{_ROOF_STEMS}))?", re.IGNORECASE
)


def roof_type_for(word: str) -> str:
    """Forma kanoniczna rodzaju dachu dla słowa (``płaskie`` → ``płaski``); słowo spoza słownika bez zmian."""
    lowered = word.lower()
    for stem, canonical in ROOF_TYPES:
        if lowered.startswith(stem):
            return canonical
    return lowered


@dataclass(frozen=True)
class RoofGeometryMatch:
    """Rodzaj dachu wskazany w tekście: zakres, dosłowny zapis i forma kanoniczna."""

    start: int
    end: int
    raw: str
    canonical: str


def find_roof_geometry(text: str) -> list[RoofGeometryMatch]:
    """Rodzaje dachu z tekstu (``dachy dwuspadowe lub wielospadowe``) wg słownika leksykonu."""
    matches: list[RoofGeometryMatch] = []
    for match in ROOF_GEOMETRY_PATTERN.finditer(text):
        first = roof_type_for(match.group("first"))
        second = match.group("second")
        if second is None:
            canonical = first
        elif {first, roof_type_for(second)} == {"dwuspadowy", "wielospadowy"}:
            canonical = "dwuspadowy_lub_wielospadowy"
        else:
            canonical = f"{first}_lub_{roof_type_for(second)}"
        matches.append(RoofGeometryMatch(match.start(), match.end(), match.group(0), canonical))
    return matches


# --- wzorce techniczne słowa „liczba słowna + kondygnacja” ----------------------------

NUMBER_WORD_STOREY_PATTERN: Final[re.Pattern[str]] = re.compile(
    rf"(?P<word>{NUMBER_WORD_PATTERN})\s+(?P<unit>kondygnac\w*)", re.IGNORECASE
)
COMPOUND_STOREY_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(?P<word>(?:jedno|dwu|trzy|cztero|pięcio|sześcio|siedmio|ośmio)kondygnacyjn\w*)", re.IGNORECASE
)
UNDERGROUND_FOLLOWING: Final[re.Pattern[str]] = re.compile(r"\s*(?:\([^)]*\)\s*)?podziemn", re.IGNORECASE)
