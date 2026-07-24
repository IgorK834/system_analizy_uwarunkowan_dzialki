"""Czyste typy i reguły normalizacji importowanych działek."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any, Mapping

from app.shared.geometry import GeometryPayload


_WHITESPACE = re.compile(r"\s+")
_PARCEL_NUMBER = re.compile(r"^[0-9]+(?:/[0-9]+)?(?:\.[0-9]+)?$")


class ParcelNormalizationError(ValueError):
    """Rekord działki nie ma minimalnego, jednoznacznego kontraktu."""


@dataclass(frozen=True)
class ParcelRecord:
    """Działka odczytana ze źródła przed publikacją."""

    parcel_identifier: str
    number: str | None
    sheet: str | None
    precinct: str | None
    cadastral_unit: str | None
    teryt: str | None
    geometry: GeometryPayload
    reported_area_sqm: float | None = None
    raw_attributes: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedParcel:
    """Znormalizowany rekord gotowy do naprawy geometrii i QA."""

    parcel_identifier: str
    number: str | None
    sheet: str | None
    precinct: str | None
    cadastral_unit: str | None
    teryt: str | None
    geometry: GeometryPayload
    reported_area_sqm: float | None
    raw_attributes: Mapping[str, Any]

    def with_geometry(self, geometry: GeometryPayload) -> NormalizedParcel:
        return replace(self, geometry=geometry)


@dataclass(frozen=True)
class ParcelQaIssue:
    code: str
    message: str
    parcel_identifier: str | None = None
    severity: str = "error"


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = _WHITESPACE.sub(" ", value.strip())
    return cleaned or None


def normalize_parcel(record: ParcelRecord) -> NormalizedParcel:
    """Normalizuje identyfikatory bez zmieniania treści atrybutów źródłowych."""
    identifier = _clean(record.parcel_identifier)
    if not identifier:
        raise ParcelNormalizationError("Brak identyfikatora działki.")
    if any(char.isspace() for char in identifier):
        raise ParcelNormalizationError(
            f"Identyfikator działki {identifier!r} zawiera białe znaki."
        )

    number = _clean(record.number)
    if number and not _PARCEL_NUMBER.fullmatch(number):
        raise ParcelNormalizationError(
            f"Nieprawidłowy numer działki {number!r}."
        )

    teryt = _clean(record.teryt)
    if teryt and (not teryt.isdigit() or len(teryt) not in (4, 6, 7, 8)):
        raise ParcelNormalizationError(f"Nieprawidłowy kod TERYT {teryt!r}.")
    if record.reported_area_sqm is not None and record.reported_area_sqm <= 0:
        raise ParcelNormalizationError("Pole ewidencyjne musi być dodatnie.")

    return NormalizedParcel(
        parcel_identifier=identifier,
        number=number,
        sheet=_clean(record.sheet),
        precinct=_clean(record.precinct),
        cadastral_unit=_clean(record.cadastral_unit),
        teryt=teryt,
        geometry=record.geometry,
        reported_area_sqm=record.reported_area_sqm,
        raw_attributes=dict(record.raw_attributes),
    )


def area_is_within_tolerance(
    reported_area_sqm: float | None,
    calculated_area_sqm: float,
    tolerance_ratio: float,
) -> bool:
    """Porównuje pole źródłowe z metrycznym polem geometrii w EPSG:2180."""
    if reported_area_sqm is None:
        return True
    if tolerance_ratio < 0:
        raise ValueError("Tolerancja pola nie może być ujemna.")
    return (
        abs(reported_area_sqm - calculated_area_sqm) / reported_area_sqm
        <= tolerance_ratio
    )


def duplicate_identifiers(records: list[NormalizedParcel]) -> set[str]:
    """Zwraca identyfikatory występujące więcej niż raz w jednym imporcie."""
    seen: set[str] = set()
    duplicates: set[str] = set()
    for record in records:
        if record.parcel_identifier in seen:
            duplicates.add(record.parcel_identifier)
        seen.add(record.parcel_identifier)
    return duplicates


def teryt_is_in_scope(teryt: str | None, scopes: tuple[str, ...]) -> bool | None:
    """Sprawdza deklarowany zasięg; None oznacza brak danych referencyjnych."""
    if not teryt or not scopes or "*" in scopes:
        return None
    return any(teryt.startswith(scope) or scope.startswith(teryt) for scope in scopes)
