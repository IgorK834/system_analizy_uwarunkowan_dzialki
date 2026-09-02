"""Czyste typy Planu Ogólnego Gminy (POG/APP) i reguły ich walidacji.

POG jest odrębnym aktem prawa miejscowego (reforma planowania przestrzennego).
Import POG jest wersjonowany niezależnie od MPZP i przechowuje cztery logiczne
warstwy: strefy planistyczne, obszary uzupełnienia zabudowy (OUZ), obszary
zabudowy śródmiejskiej oraz standardy dostępności infrastruktury społecznej.

Kluczowa reguła domenowa (context.md pkt 9): akt o statusie ``project`` lub
``in_progress`` NIGDY nie może być prezentowany jako plan uchwalony i
obowiązujący. Wiążący jest wyłącznie status ``adopted`` — sprawdza to
``PogActRecord.is_binding`` i to na nim opierają się zapytania przecięć.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, Mapping

from app.shared.geometry import GeometryPayload


# Cztery logiczne warstwy POG. Zgodne z Objective zadania 12.3 i z warstwami
# WMS PlanyOgolneGmin (catalog.yaml: pog_app). ``social_infrastructure_standard``
# to standardy dostępności infrastruktury społecznej z ustawy — odrębny typ, nie
# to samo co metadane aktu (patrz known_gaps gap_2).
POG_FEATURE_TYPES: tuple[str, ...] = (
    "planning_zone",
    "ouz",
    "downtown_area",
    "social_infrastructure_standard",
)

# Status prawny aktu POG. Kolejność od najmniej do najbardziej wiążącego nie ma
# znaczenia; istotne jest, że wiążący jest wyłącznie ``adopted``.
POG_LEGAL_STATUS_VALUES: tuple[str, ...] = (
    "project",
    "in_progress",
    "adopted",
    "not_available",
)

# Jedyny status, w którym POG jest uchwalony i obowiązujący.
BINDING_LEGAL_STATUS: str = "adopted"


class PogValidationError(ValueError):
    """Rekord POG nie ma metadanych wymaganych do jednoznacznej publikacji."""


def _fold(value: str) -> str:
    """Sprowadza etykietę źródłową do postaci bez diakrytyków i spacji."""
    decomposed = unicodedata.normalize("NFKD", value.strip().lower())
    return "".join(
        char
        for char in decomposed
        if not unicodedata.combining(char) and char.isalnum()
    )


# Mapowanie znormalizowanych nazw warstw/atrybutów źródłowych na typ warstwy.
# Zachowujemy elastyczność, bo schematy APP różnią się między producentami GIS
# (gap: minimum dwa różne schematy). Surowe atrybuty i tak są zachowywane.
_FEATURE_TYPE_ALIASES: dict[str, str] = {
    "strefaplanistyczna": "planning_zone",
    "planningzone": "planning_zone",
    "strefa": "planning_zone",
    "obszaruzupelnieniazabudowy": "ouz",
    "ouz": "ouz",
    "obszaruzupelnienia": "ouz",
    "obszarzabsrodmiejskiej": "downtown_area",
    "obszarzabudowysrodmiejskiej": "downtown_area",
    "downtownarea": "downtown_area",
    "srodmiescie": "downtown_area",
    "standarddostepnosci": "social_infrastructure_standard",
    "standardydostepnosci": "social_infrastructure_standard",
    "standarddostepnosciinfrastrukturyspolecznej": "social_infrastructure_standard",
    "standardydostepnosciinfrastrukturyspolecznej": "social_infrastructure_standard",
    "socialinfrastructurestandard": "social_infrastructure_standard",
    "infrastrukturaspoleczna": "social_infrastructure_standard",
}

# Mapowanie znormalizowanych statusów źródłowych na enum. Status nieznany NIGDY
# nie staje się ``adopted`` — domyślnie zwracamy ``not_available``, aby nie
# przedstawić niepotwierdzonego aktu jako obowiązującego.
_LEGAL_STATUS_ALIASES: dict[str, str] = {
    "uchwalony": "adopted",
    "obowiazujacy": "adopted",
    "adopted": "adopted",
    "przyjety": "adopted",
    "projekt": "project",
    "project": "project",
    "wtrakcie": "in_progress",
    "wtrakciesporzadzania": "in_progress",
    "wopracowaniu": "in_progress",
    "inprogress": "in_progress",
    "brak": "not_available",
    "niedostepny": "not_available",
    "notavailable": "not_available",
}


def normalize_feature_type(
    raw_layer: str | None, attributes: Mapping[str, Any] | None = None
) -> str | None:
    """Rozpoznaje typ warstwy POG po nazwie warstwy lub atrybucie typu.

    Zwraca ``None``, gdy warstwy nie da się jednoznacznie sklasyfikować — wtedy
    obiekt jest pomijany (nie zgadujemy typu ustawowego).
    """
    hints: list[str] = []
    if raw_layer:
        hints.append(raw_layer)
    if attributes:
        for key in ("feature_type", "typ", "typ_obiektu", "layer", "warstwa"):
            value = attributes.get(key)
            if value is not None:
                hints.append(str(value))
    for hint in hints:
        folded = _fold(hint)
        if folded in _FEATURE_TYPE_ALIASES:
            return _FEATURE_TYPE_ALIASES[folded]
    return None


def normalize_legal_status(raw_status: str | None) -> str:
    """Mapuje status źródłowy na enum; status nieznany nie jest wiążący."""
    if not raw_status:
        return "not_available"
    folded = _fold(raw_status)
    return _LEGAL_STATUS_ALIASES.get(folded, "not_available")


@dataclass(frozen=True)
class PogFeatureRecord:
    """Pojedynczy obiekt POG jednej z czterech warstw, z surowymi atrybutami."""

    feature_type: str
    geometry: GeometryPayload
    raw_attributes: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if self.feature_type not in POG_FEATURE_TYPES:
            raise PogValidationError(
                f"Nieznany typ warstwy POG: {self.feature_type!r}. "
                f"Dozwolone: {POG_FEATURE_TYPES}."
            )

    def with_geometry(self, geometry: GeometryPayload) -> PogFeatureRecord:
        return replace(self, geometry=geometry)


@dataclass(frozen=True)
class PogActRecord:
    """Akt planowania przestrzennego (POG) wraz z warstwami i statusem prawnym."""

    act_identifier: str
    resolution_number: str | None
    resolution_date: date | None
    teryt: str
    name: str | None
    legal_status: str = "not_available"
    boundary: GeometryPayload | None = None
    features: tuple[PogFeatureRecord, ...] = ()

    def validate(self) -> None:
        if not self.act_identifier.strip():
            raise PogValidationError("Brak identyfikatora aktu POG.")
        if not self.teryt.strip():
            raise PogValidationError("Brak kodu TERYT aktu POG.")
        if self.legal_status not in POG_LEGAL_STATUS_VALUES:
            raise PogValidationError(
                f"Nieznany status prawny POG: {self.legal_status!r}."
            )
        # Akt uchwalony musi mieć numer i datę uchwały — inaczej nie wolno
        # prezentować go jako obowiązującego.
        if self.legal_status == BINDING_LEGAL_STATUS:
            if not (self.resolution_number and self.resolution_number.strip()):
                raise PogValidationError(
                    "Uchwalony POG wymaga numeru uchwały."
                )
            if self.resolution_date is None:
                raise PogValidationError("Uchwalony POG wymaga daty uchwały.")
        for feature in self.features:
            feature.validate()

    @property
    def is_binding(self) -> bool:
        """Czy akt jest uchwalony i obowiązujący (jedyny status wiążący)."""
        return self.legal_status == BINDING_LEGAL_STATUS

    def feature_counts(self) -> dict[str, int]:
        """Liczba obiektów w każdej z czterech warstw (także zerowa)."""
        counts = {feature_type: 0 for feature_type in POG_FEATURE_TYPES}
        for feature in self.features:
            counts[feature.feature_type] = counts.get(feature.feature_type, 0) + 1
        return counts
