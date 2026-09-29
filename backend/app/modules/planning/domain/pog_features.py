"""Kanoniczne atrybuty obiektów POG wspólne dla analizy i mapy (BK-401).

Analiza działki (``app.services.pog_analyzer``) i kafle wektorowe POG czytają
typ strefy, symbol, etykietę i cztery parametry wyłącznie przez funkcje tego
modułu. Dzięki temu kliknięta cecha mapy ma z konstrukcji te same wartości co
wynik analizy tej samej geometrii z tego samego wydania — nie istnieje druga,
równoległa implementacja w SQL ani w TypeScript.

Zasady:
- ``None`` pozostaje ``None`` (brak wartości), nigdy nie jest zamieniany na 0;
- nierozpoznany typ strefy daje jawny kod ``unknown``, a nie „najbliższą” strefę;
- moduł jest czysty (bez IO, ORM i frameworka webowego).
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

# Dokładne nazwy czterech parametrów kontraktu POG v2 (BK-105). Te same klucze
# trafiają do ``PogZoneResult``, atrybutów kafla MVT i wyrażeń stylu mapy.
POG_PARAMETER_NAMES: Final[tuple[str, ...]] = (
    "max_overground_floor_area_ratio",
    "max_building_height_m",
    "max_building_coverage_pct",
    "min_biologically_active_pct",
)

# Ustawowe kody stref (art. 13c ust. 2 upzp; słownik RodzajStrefyPlanistycznejKod).
POG_ZONE_CODES: Final[tuple[str, ...]] = (
    "SW", "SJ", "SZ", "SU", "SH", "SP", "SR", "SI", "SN", "SC", "SG", "SO", "SK",
)
UNKNOWN_ZONE_CODE: Final[str] = "unknown"

ZONE_ATTRIBUTE_KEYS: Final[tuple[str, ...]] = (
    "zone_type",
    "typ_strefy",
    "kod_strefy",
    "symbol",
    "oznaczenie",
)
FEATURE_ID_KEYS: Final[tuple[str, ...]] = (
    "feature_id", "id_iip", "idIIP", "identifier", "oznaczenie",
)
SYMBOL_KEYS: Final[tuple[str, ...]] = ("symbol", "oznaczenie")
LABEL_KEYS: Final[tuple[str, ...]] = ("label", "nazwa")
PARAMETER_SOURCE_KEYS: Final[tuple[str, ...]] = (
    "parameter_source",
    "parameters_source",
    "zrodlo_parametrow",
    "źródło_parametrów",
)
PLANNING_PARAMETER_KEYS: Final[dict[str, tuple[str, ...]]] = {
    "max_overground_floor_area_ratio": (
        "max_overground_floor_area_ratio",
        "maksymalna_nadziemna_intensywnosc_zabudowy",
        "maksymalna_nadziemna_intensywność_zabudowy",
    ),
    "max_building_height_m": (
        "max_building_height_m",
        "maksymalna_wysokosc_zabudowy",
        "maksymalna_wysokość_zabudowy",
    ),
    "max_building_coverage_pct": (
        "max_building_coverage_pct",
        "maksymalny_udzial_powierzchni_zabudowy",
        "maksymalny_udział_powierzchni_zabudowy",
    ),
    "min_biologically_active_pct": (
        "min_biologically_active_pct",
        "minimalny_udzial_powierzchni_biologicznie_czynnej",
        "minimalny_udział_powierzchni_biologicznie_czynnej",
    ),
}
NESTED_PARAMETERS_KEY: Final[str] = "parameters"

# Pełne polskie nazwy są akceptowane wyłącznie jako jawne aliasy ustawowych
# kodów. Nieznany opis nie jest dopasowywany heurystycznie do "najbliższej"
# strefy, lecz pozostaje ``unknown``.
ZONE_ALIASES: Final[dict[str, str]] = {
    "sw": "SW",
    "strefawielofunkcyjnazzabudowamieszkaniowawielorodzinna": "SW",
    "sj": "SJ",
    "strefawielofunkcyjnazzabudowamieszkaniowajednorodzinna": "SJ",
    "sz": "SZ",
    "strefawielofunkcyjnazzabudowazagrodowa": "SZ",
    "su": "SU",
    "strefauslugowa": "SU",
    "sh": "SH",
    "strefahandluwielkopowierzchniowego": "SH",
    "sp": "SP",
    "strefagospodarcza": "SP",
    "sr": "SR",
    "strefaprodukcjirolniczej": "SR",
    "si": "SI",
    "strefainfrastrukturalna": "SI",
    "sn": "SN",
    "strefazieleniirekreacji": "SN",
    "sc": "SC",
    "strefacmentarzy": "SC",
    "sg": "SG",
    "strefagornictwa": "SG",
    "so": "SO",
    "strefaotwarta": "SO",
    "sk": "SK",
    "strefakomunikacyjna": "SK",
}

# Klucze surowych atrybutów (lower-case), które wpływają na kanoniczne atrybuty.
# Adapter kafli pobiera z bazy wyłącznie ten podzbiór — nigdy pełny rekord APP
# (np. tekst ``posList`` geometrii) — a test kontraktu pilnuje, że wynik jest
# identyczny jak dla pełnych atrybutów.
RAW_ATTRIBUTE_KEYS: Final[frozenset[str]] = frozenset(
    key.lower()
    for key in (
        *ZONE_ATTRIBUTE_KEYS,
        *FEATURE_ID_KEYS,
        *SYMBOL_KEYS,
        *LABEL_KEYS,
        *PARAMETER_SOURCE_KEYS,
        *(candidate for keys in PLANNING_PARAMETER_KEYS.values() for candidate in keys),
        NESTED_PARAMETERS_KEY,
        "feature_version",
        "gml_url",
        "primary_profiles",
        "additional_profiles",
    )
)


def normalize_name(value: str) -> str:
    """Składa nazwę do porównań: małe litery, bez diakrytyków i separatorów.

    ``ł`` nie ma rozkładu NFKD, dlatego jest zamieniane jawnie — inaczej
    urzędowa etykieta „strefa usługowa” nie trafiałaby w alias ``SU``.
    """
    decomposed = unicodedata.normalize("NFKD", value.lower().replace("ł", "l"))
    return "".join(
        char
        for char in decomposed
        if not unicodedata.combining(char) and char.isalnum()
    )


def first_string_attribute(
    attributes: Mapping[str, object],
    candidates: tuple[str, ...],
) -> str | None:
    """Pierwsza niepusta wartość tekstowa wg kolejności kandydatów (bez wielkości liter)."""
    for candidate in candidates:
        for key, value in attributes.items():
            if key.lower() == candidate.lower() and value is not None:
                text = str(value).strip()
                if text:
                    return text
    return None


def normalize_zone_code(raw_value: str | None) -> str:
    """Kod ustawowy strefy (``SW``…``SK``) albo jawne ``unknown``."""
    if not raw_value:
        return UNKNOWN_ZONE_CODE
    return ZONE_ALIASES.get(normalize_name(raw_value), UNKNOWN_ZONE_CODE)


def extract_planning_parameters(attributes: Mapping[str, object]) -> dict[str, object]:
    """Surowe wartości czterech parametrów pod kanonicznymi kluczami.

    Klucz obecny z wartością ``None`` jest zachowany — oznacza jawny brak
    wartości w źródle i nie jest nadpisywany aliasem.
    """
    normalized_attributes = {key.lower(): value for key, value in attributes.items()}
    parameters: dict[str, object] = {}
    for canonical_name, candidates in PLANNING_PARAMETER_KEYS.items():
        for candidate in candidates:
            if candidate.lower() in normalized_attributes:
                parameters[canonical_name] = normalized_attributes[candidate.lower()]
                break
    nested = attributes.get(NESTED_PARAMETERS_KEY)
    if isinstance(nested, dict):
        parameters.update(nested)
    return parameters


def float_parameter(parameters: Mapping[str, object], name: str) -> float | None:
    """Liczba parametru albo ``None``; wartość nieliczbowa nie staje się zerem."""
    value = parameters.get(name)
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("value")
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def parameters_are_informational(attributes: Mapping[str, object]) -> bool:
    """Parametry z PDF/uzasadnienia są informacyjne i wymagają weryfikacji."""
    source = first_string_attribute(attributes, PARAMETER_SOURCE_KEYS)
    if not source:
        return False
    normalized = normalize_name(source)
    return "pdf" in normalized or "uzasadnienie" in normalized


def profiles_from_attributes(
    attributes: Mapping[str, object], key: str
) -> tuple[dict[str, str | None], ...]:
    raw = attributes.get(key)
    if not isinstance(raw, (list, tuple)):
        return ()
    result: list[dict[str, str | None]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        result.append({
            "code": str(item.get("code") or ""),
            "label": str(item["label"]) if item.get("label") is not None else None,
            "dictionary_source": str(item.get("dictionary_source") or ""),
        })
    return tuple(result)


def release_feature_attributes(
    raw_attributes: Mapping[str, object] | None,
    *,
    feature_identifier: str | None,
    feature_version: str | None,
    symbol: str | None,
    label: str | None,
    primary_profiles: object,
    additional_profiles: object,
    parameters: Mapping[str, object] | None,
    gml_url: str | None = None,
) -> dict[str, object]:
    """Składa atrybuty obiektu z lokalnego wydania PostGIS (kolejność ma znaczenie).

    Surowe atrybuty są bazą, kolumny kanoniczne importu je nadpisują, a
    parametry BK-105 (cztery klucze, także ``None``) mają najwyższy priorytet.
    """
    attributes: dict[str, object] = dict(raw_attributes or {})
    attributes.update({
        "feature_id": feature_identifier,
        "feature_version": feature_version,
        "gml_url": gml_url,
        "symbol": symbol,
        "label": label,
        "primary_profiles": primary_profiles or [],
        "additional_profiles": additional_profiles or [],
    })
    attributes.update(dict(parameters or {}))
    return attributes


@dataclass(frozen=True)
class PogFeaturePresentation:
    """Kanoniczne atrybuty jednego obiektu POG używane przez analizę i mapę."""

    feature_id: str | None
    zone_code: str
    source_zone_type: str | None
    symbol: str | None
    label: str | None
    max_overground_floor_area_ratio: float | None
    max_building_height_m: float | None
    max_building_coverage_pct: float | None
    min_biologically_active_pct: float | None
    parameters_informational: bool
    primary_profiles: tuple[dict[str, str | None], ...]
    additional_profiles: tuple[dict[str, str | None], ...]
    feature_version: str | None

    def parameter(self, name: str) -> float | None:
        if name not in POG_PARAMETER_NAMES:
            raise KeyError(name)
        value: float | None = getattr(self, name)
        return value


def pog_feature_presentation(attributes: Mapping[str, object]) -> PogFeaturePresentation:
    """Wylicza kanoniczne atrybuty obiektu z jego słownika atrybutów."""
    source_zone_type = first_string_attribute(attributes, ZONE_ATTRIBUTE_KEYS)
    parameters = extract_planning_parameters(attributes)
    return PogFeaturePresentation(
        feature_id=first_string_attribute(attributes, FEATURE_ID_KEYS),
        zone_code=normalize_zone_code(source_zone_type),
        source_zone_type=source_zone_type,
        symbol=first_string_attribute(attributes, SYMBOL_KEYS),
        label=first_string_attribute(attributes, LABEL_KEYS),
        max_overground_floor_area_ratio=float_parameter(
            parameters, "max_overground_floor_area_ratio"
        ),
        max_building_height_m=float_parameter(parameters, "max_building_height_m"),
        max_building_coverage_pct=float_parameter(parameters, "max_building_coverage_pct"),
        min_biologically_active_pct=float_parameter(
            parameters, "min_biologically_active_pct"
        ),
        parameters_informational=parameters_are_informational(attributes),
        primary_profiles=profiles_from_attributes(attributes, "primary_profiles"),
        additional_profiles=profiles_from_attributes(attributes, "additional_profiles"),
        feature_version=first_string_attribute(attributes, ("feature_version",)),
    )
