"""Czysty model domenowy obiektów POG zgodnych z APP 3.0.

Adapter RU tłumaczy XML, namespace i słowniki na te typy. Moduł nie wykonuje
IO i nie zna ORM. Surowy status i odwołania są zachowywane, dzięki czemu brak
rozpoznanego kodu nigdy nie staje się lokalnie statusem wiążącym.
"""

from __future__ import annotations

import unicodedata
from dataclasses import asdict, dataclass, field, replace
from datetime import date
from typing import Any, Mapping

from app.shared.geometry import GeometryPayload


POG_FEATURE_TYPES: tuple[str, ...] = (
    "planning_zone", "ouz", "downtown_area", "social_infrastructure_standard",
)
POG_OBJECT_TYPES: tuple[str, ...] = (
    "planning_act", *POG_FEATURE_TYPES, "formal_document",
)
POG_LEGAL_STATUS_VALUES: tuple[str, ...] = (
    "project", "in_progress", "adopted", "not_available",
)
BINDING_LEGAL_STATUS = "adopted"


class PogValidationError(ValueError):
    """Obiekt POG narusza kontrakt wymagany do publikacji."""


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.strip().lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c) and c.isalnum())


_FEATURE_TYPE_ALIASES: dict[str, str] = {
    "strefaplanistyczna": "planning_zone", "planningzone": "planning_zone", "strefa": "planning_zone",
    "obszaruzupelnieniazabudowy": "ouz", "ouz": "ouz", "obszaruzupelnienia": "ouz",
    "obszarzabsrodmiejskiej": "downtown_area", "obszarzabudowysrodmiejskiej": "downtown_area",
    "downtownarea": "downtown_area", "srodmiescie": "downtown_area",
    "obszarstandardowdostepnosciinfrastrukturyspolecznej": "social_infrastructure_standard",
    "standarddostepnosci": "social_infrastructure_standard", "standardydostepnosci": "social_infrastructure_standard",
    "standarddostepnosciinfrastrukturyspolecznej": "social_infrastructure_standard",
    "standardydostepnosciinfrastrukturyspolecznej": "social_infrastructure_standard",
    "socialinfrastructurestandard": "social_infrastructure_standard", "infrastrukturaspoleczna": "social_infrastructure_standard",
}

_LEGAL_STATUS_ALIASES: dict[str, str] = {
    "uchwalony": "adopted", "obowiazujacy": "adopted", "adopted": "adopted",
    "przyjety": "adopted", "legalforce": "adopted", "prawniewiazacylubrealizowany": "adopted",
    "projekt": "project", "project": "project", "wtrakcie": "in_progress",
    "wtrakciesporzadzania": "in_progress", "wopracowaniu": "in_progress", "inprogress": "in_progress",
    "brak": "not_available", "niedostepny": "not_available", "notavailable": "not_available",
}


def normalize_feature_type(raw_layer: str | None, attributes: Mapping[str, Any] | None = None) -> str | None:
    hints = [raw_layer] if raw_layer else []
    if attributes:
        hints.extend(str(attributes[k]) for k in ("feature_type", "typ", "typ_obiektu", "layer", "warstwa") if attributes.get(k) is not None)
    for hint in hints:
        if normalized := _FEATURE_TYPE_ALIASES.get(_fold(hint)):
            return normalized
    return None


def normalize_legal_status(raw_status: str | None) -> str:
    """Normalizuje wyłącznie jawny kod/etykietę statusu, nigdy datę uchwały."""
    if not raw_status:
        return "not_available"
    return _LEGAL_STATUS_ALIASES.get(
        _fold(raw_status.rsplit("/", 1)[-1]),
        _LEGAL_STATUS_ALIASES.get(_fold(raw_status), "not_available"),
    )


@dataclass(frozen=True)
class PogObjectId:
    namespace: str
    local_id: str
    version_id: str | None = None

    def validate(self) -> None:
        if not self.namespace.strip() or not self.local_id.strip():
            raise PogValidationError("idIIP wymaga przestrzenNazw i lokalnyId.")

    @property
    def stable_id(self) -> str:
        return f"{self.namespace.rstrip('/')}/{self.local_id}"

    @property
    def versioned_id(self) -> str:
        return f"{self.stable_id}/{self.version_id}" if self.version_id else self.stable_id


@dataclass(frozen=True)
class PogReference:
    href: str
    label: str | None = None

    def validate(self) -> None:
        if not self.href.strip():
            raise PogValidationError("Odwołanie POG wymaga xlink:href.")


@dataclass(frozen=True)
class PogFunctionalProfile:
    code: str
    label: str | None
    dictionary_source: str

    def validate(self) -> None:
        if not self.code.strip() or not self.dictionary_source.strip():
            raise PogValidationError("Profil wymaga kodu i źródła słownika.")


@dataclass(frozen=True)
class PogNumericValue:
    value: float
    unit: str

    def validate(self, *, percentage: bool = False) -> None:
        if self.value < 0:
            raise PogValidationError("Parametr POG nie może być ujemny.")
        if percentage and self.value > 100:
            raise PogValidationError("Parametr procentowy POG musi należeć do 0–100.")
        if not self.unit.strip():
            raise PogValidationError("Parametr POG wymaga jednostki.")


@dataclass(frozen=True)
class PogPlanningParameters:
    max_overground_floor_area_ratio: PogNumericValue | None = None
    max_building_height: PogNumericValue | None = None
    max_building_coverage: PogNumericValue | None = None
    min_biologically_active: PogNumericValue | None = None

    def validate(self) -> None:
        if self.max_overground_floor_area_ratio:
            self.max_overground_floor_area_ratio.validate()
            if self.max_overground_floor_area_ratio.unit != "1":
                raise PogValidationError("Intensywność zabudowy wymaga jednostki '1'.")
        if self.max_building_height:
            self.max_building_height.validate()
            if self.max_building_height.unit != "m":
                raise PogValidationError("Wysokość zabudowy wymaga jednostki 'm'.")
        for value in (self.max_building_coverage, self.min_biologically_active):
            if value:
                value.validate(percentage=True)
                if value.unit != "%":
                    raise PogValidationError("Udział powierzchni wymaga jednostki '%'.")

    def values(self) -> dict[str, float | None]:
        return {
            "max_overground_floor_area_ratio": self.max_overground_floor_area_ratio.value if self.max_overground_floor_area_ratio else None,
            "max_building_height_m": self.max_building_height.value if self.max_building_height else None,
            "max_building_coverage_pct": self.max_building_coverage.value if self.max_building_coverage else None,
            "min_biologically_active_pct": self.min_biologically_active.value if self.min_biologically_active else None,
        }


@dataclass(frozen=True)
class PogFormalDocumentRecord:
    object_id: PogObjectId
    title: str | None = None
    link: str | None = None
    act_reference: PogReference | None = None
    source_reference: str | None = None
    raw_attributes: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        self.object_id.validate()
        if self.act_reference:
            self.act_reference.validate()


@dataclass(frozen=True)
class PogFeatureRecord:
    """Strefa, OUZ, OZS albo OSDIS wraz z pełnym idIIP i relacją do aktu."""

    feature_type: str
    geometry: GeometryPayload
    raw_attributes: Mapping[str, Any] = field(default_factory=dict)
    object_id: PogObjectId | None = None
    act_reference: PogReference | None = None
    source_reference: str | None = None
    raw_legal_status: str | None = None
    symbol: str | None = None
    label: str | None = None
    parameters: PogPlanningParameters | None = None
    primary_profiles: tuple[PogFunctionalProfile, ...] = ()
    additional_profiles: tuple[PogFunctionalProfile, ...] = ()

    def validate(self) -> None:
        if self.feature_type not in POG_FEATURE_TYPES:
            raise PogValidationError(f"Nieznany typ warstwy POG: {self.feature_type!r}. Dozwolone: {POG_FEATURE_TYPES}.")
        if self.object_id:
            self.object_id.validate()
        if self.act_reference:
            self.act_reference.validate()
        if self.parameters:
            self.parameters.validate()
        for profile in (*self.primary_profiles, *self.additional_profiles):
            profile.validate()

    def with_geometry(self, geometry: GeometryPayload) -> PogFeatureRecord:
        return replace(self, geometry=geometry)

    @property
    def stable_id(self) -> str | None:
        return self.object_id.stable_id if self.object_id else None


@dataclass(frozen=True)
class PogActRecord:
    act_identifier: str
    resolution_number: str | None
    resolution_date: date | None
    teryt: str
    name: str | None
    legal_status: str = "not_available"
    boundary: GeometryPayload | None = None
    features: tuple[PogFeatureRecord, ...] = ()
    object_id: PogObjectId | None = None
    raw_legal_status: str | None = None
    source_reference: str | None = None
    feature_references: tuple[PogReference, ...] = ()
    document_references: tuple[PogReference, ...] = ()
    documents: tuple[PogFormalDocumentRecord, ...] = ()

    def validate(self) -> None:
        if not self.act_identifier.strip():
            raise PogValidationError("Brak identyfikatora aktu POG.")
        if not self.teryt.strip():
            raise PogValidationError("Brak kodu TERYT aktu POG.")
        if self.legal_status not in POG_LEGAL_STATUS_VALUES:
            raise PogValidationError(f"Nieznany status prawny POG: {self.legal_status!r}.")
        if self.object_id:
            self.object_id.validate()
        for reference in (*self.feature_references, *self.document_references):
            reference.validate()
        for document in self.documents:
            document.validate()
        for feature in self.features:
            feature.validate()

    @property
    def is_binding(self) -> bool:
        return self.legal_status == BINDING_LEGAL_STATUS

    def feature_counts(self) -> dict[str, int]:
        counts = {feature_type: 0 for feature_type in POG_FEATURE_TYPES}
        for feature in self.features:
            counts[feature.feature_type] += 1
        return counts


def pog_record_to_dict(record: PogActRecord) -> dict[str, Any]:
    """Deterministyczny zapis relacji domenowych do JSON/audytu."""
    payload = asdict(record)
    if record.resolution_date:
        payload["resolution_date"] = record.resolution_date.isoformat()
    return payload


def pog_record_from_dict(payload: Mapping[str, Any]) -> PogActRecord:
    """Odtwarza zapis :func:`pog_record_to_dict` bez utraty null/0 i xlinków."""
    def oid(value: Mapping[str, Any] | None) -> PogObjectId | None:
        return PogObjectId(**value) if value else None
    def ref(value: Mapping[str, Any] | None) -> PogReference | None:
        return PogReference(**value) if value else None
    def num(value: Mapping[str, Any] | None) -> PogNumericValue | None:
        return PogNumericValue(**value) if value else None

    features: list[PogFeatureRecord] = []
    for item in payload.get("features", ()):
        pp = item.get("parameters")
        parameters = PogPlanningParameters(
            max_overground_floor_area_ratio=num(pp.get("max_overground_floor_area_ratio")),
            max_building_height=num(pp.get("max_building_height")),
            max_building_coverage=num(pp.get("max_building_coverage")),
            min_biologically_active=num(pp.get("min_biologically_active")),
        ) if pp else None
        features.append(PogFeatureRecord(
            feature_type=item["feature_type"], geometry=GeometryPayload(**item["geometry"]),
            raw_attributes=item.get("raw_attributes", {}), object_id=oid(item.get("object_id")),
            act_reference=ref(item.get("act_reference")), source_reference=item.get("source_reference"),
            raw_legal_status=item.get("raw_legal_status"), symbol=item.get("symbol"), label=item.get("label"),
            parameters=parameters,
            primary_profiles=tuple(PogFunctionalProfile(**p) for p in item.get("primary_profiles", ())),
            additional_profiles=tuple(PogFunctionalProfile(**p) for p in item.get("additional_profiles", ())),
        ))
    documents = tuple(PogFormalDocumentRecord(
        object_id=oid(item["object_id"]), title=item.get("title"), link=item.get("link"),
        act_reference=ref(item.get("act_reference")), source_reference=item.get("source_reference"),
        raw_attributes=item.get("raw_attributes", {}),
    ) for item in payload.get("documents", ()))
    return PogActRecord(
        act_identifier=str(payload["act_identifier"]), resolution_number=payload.get("resolution_number"),
        resolution_date=date.fromisoformat(payload["resolution_date"]) if payload.get("resolution_date") else None,
        teryt=str(payload["teryt"]), name=payload.get("name"), legal_status=str(payload.get("legal_status", "not_available")),
        boundary=GeometryPayload(**payload["boundary"]) if payload.get("boundary") else None,
        features=tuple(features), object_id=oid(payload.get("object_id")), raw_legal_status=payload.get("raw_legal_status"),
        source_reference=payload.get("source_reference"),
        feature_references=tuple(PogReference(**r) for r in payload.get("feature_references", ())),
        document_references=tuple(PogReference(**r) for r in payload.get("document_references", ())), documents=documents,
    )
