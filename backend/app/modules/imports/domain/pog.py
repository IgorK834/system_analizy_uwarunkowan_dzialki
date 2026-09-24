"""Czysty model domenowy obiektów POG zgodnych z APP 3.0.

Adapter RU tłumaczy XML, namespace i słowniki na te typy. Moduł nie wykonuje
IO i nie zna ORM. Surowy status i odwołania są zachowywane, dzięki czemu brak
rozpoznanego kodu nigdy nie staje się lokalnie statusem wiążącym.
"""

from __future__ import annotations

import unicodedata
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime
from typing import Any, Mapping

from app.shared.geometry import GeometryPayload
from app.shared.provenance import CatalogRecordProvenance, is_verified_https_url
from app.shared.planning_status import (
    BINDING,
    LEGAL_STATUS_VALUES,
    is_official_status_code,
    normalize_official_legal_status,
)


POG_FEATURE_TYPES: tuple[str, ...] = (
    "planning_zone", "ouz", "downtown_area", "social_infrastructure_standard",
)
POG_OBJECT_TYPES: tuple[str, ...] = (
    "planning_act", *POG_FEATURE_TYPES, "formal_document",
)
POG_LEGAL_STATUS_VALUES: tuple[str, ...] = LEGAL_STATUS_VALUES
BINDING_LEGAL_STATUS = BINDING
APP_OBJECT_URI_PREFIX = "https://www.gov.pl/zagospodarowanieprzestrzenne/app/"
DOCUMENT_RESOLUTION_STATUSES: tuple[str, ...] = ("resolved", "unresolved", "unavailable")
DOCUMENT_RELATIONS: tuple[str, ...] = (
    "przystapienie", "uchwala", "zmienia", "uchyla", "uniewaznia",
)


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


def normalize_feature_type(raw_layer: str | None, attributes: Mapping[str, Any] | None = None) -> str | None:
    hints = [raw_layer] if raw_layer else []
    if attributes:
        hints.extend(str(attributes[k]) for k in ("feature_type", "typ", "typ_obiektu", "layer", "warstwa") if attributes.get(k) is not None)
    for hint in hints:
        if normalized := _FEATURE_TYPE_ALIASES.get(_fold(hint)):
            return normalized
    return None


def normalize_legal_status(raw_status: str | None) -> str:
    """Normalizuje wyłącznie urzędowy kod statusu, nigdy datę uchwały.

    Deleguje do wspólnego mappera BK-106; nierozpoznany lub brakujący kod daje
    ``unknown``, a nie status wiążący.
    """
    return normalize_official_legal_status(raw_status)


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
class PogObjectRef:
    """Rozłożone odwołanie xlink/idIIP: typ, przestrzeń nazw, lokalnyId, wersja."""

    object_type: str
    namespace: str
    local_id: str
    version_id: str | None = None

    def matches(self, object_id: PogObjectId) -> bool:
        """Zgodność po identyfikatorze; wersja musi się zgadzać, gdy ją podano."""
        if self.namespace != object_id.namespace.rstrip("/") or self.local_id != object_id.local_id:
            return False
        return self.version_id is None or self.version_id == object_id.version_id


def parse_app_reference(href: str | None) -> PogObjectRef | None:
    """Parsuje URI obiektu APP: ``…/app/{Typ}/{ns1}/{ns2}/{lokalnyId}[/{wersja}]``.

    Przestrzeń nazw idIIP ma dwa segmenty (np. ``PL.ZIPPZP.10011/226401-POG``).
    Nieznany format zwraca ``None`` — odwołanie nie jest wtedy dopasowywane
    heurystycznie (np. po tytule albo sufiksie).
    """
    if not href or not href.startswith(APP_OBJECT_URI_PREFIX):
        return None
    parts = href[len(APP_OBJECT_URI_PREFIX):].strip("/").split("/")
    if len(parts) not in (4, 5) or not all(parts):
        return None
    return PogObjectRef(
        object_type=parts[0],
        namespace=f"{parts[1]}/{parts[2]}",
        local_id=parts[3],
        version_id=parts[4] if len(parts) == 5 else None,
    )


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
    """Dokument formalny APP wraz z hashem rekordu i stanem powiązania z aktem.

    ``resolution_status``: ``resolved`` — powiązany z wersją aktu po
    identyfikatorze i wersji; ``unresolved`` — odwołanie do innej wersji albo
    niejednoznaczne (nigdy nie jest podpinany do innej wersji);
    ``unavailable`` — akt wskazuje dokument, którego rekordu brak w danych.
    """

    object_id: PogObjectId
    title: str | None = None
    link: str | None = None
    act_reference: PogReference | None = None
    source_reference: str | None = None
    raw_attributes: Mapping[str, Any] = field(default_factory=dict)
    publication_id: str | None = None
    short_name: str | None = None
    identification_number: str | None = None
    relation: str | None = None
    document_date: date | None = None
    effective_date: date | None = None
    repeal_date: date | None = None
    record_sha256: str | None = None
    resolution_status: str = "resolved"
    resolution_note: str | None = None

    def validate(self) -> None:
        self.object_id.validate()
        if self.act_reference:
            self.act_reference.validate()
        if self.resolution_status not in DOCUMENT_RESOLUTION_STATUSES:
            raise PogValidationError(
                f"Nieznany stan powiązania dokumentu: {self.resolution_status!r}."
            )
        if self.record_sha256 is not None and len(self.record_sha256) != 64:
            raise PogValidationError("SHA-256 rekordu dokumentu musi mieć 64 znaki.")

    @property
    def link_verified(self) -> bool:
        return is_verified_https_url(self.link)


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
    legal_status: str = "unknown"
    boundary: GeometryPayload | None = None
    features: tuple[PogFeatureRecord, ...] = ()
    object_id: PogObjectId | None = None
    raw_legal_status: str | None = None
    source_reference: str | None = None
    feature_references: tuple[PogReference, ...] = ()
    document_references: tuple[PogReference, ...] = ()
    documents: tuple[PogFormalDocumentRecord, ...] = ()
    publication_id: str | None = None
    version_started_at: datetime | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    metadata: tuple[CatalogRecordProvenance, ...] = ()

    def validate(self) -> None:
        if not self.act_identifier.strip():
            raise PogValidationError("Brak identyfikatora aktu POG.")
        if not self.teryt.strip():
            raise PogValidationError("Brak kodu TERYT aktu POG.")
        if self.legal_status not in POG_LEGAL_STATUS_VALUES:
            raise PogValidationError(f"Nieznany status prawny POG: {self.legal_status!r}.")
        if self.legal_status == BINDING_LEGAL_STATUS and not is_official_status_code(
            self.raw_legal_status
        ):
            raise PogValidationError(
                "Status binding wymaga urzędowego kodu statusu w raw_legal_status "
                "(np. INSPIRE legalForce); status nie jest ustalany lokalnie."
            )
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

    @property
    def catalog_resource_identifier(self) -> str | None:
        """Identyfikator zbioru danych aktu w CSW RU (MD_Identifier/code)."""
        if not self.object_id:
            return None
        namespace = self.object_id.namespace.rstrip("/")
        return f"{APP_OBJECT_URI_PREFIX}AktPlanowaniaPrzestrzennego/{namespace}/"

    def feature_counts(self) -> dict[str, int]:
        counts = {feature_type: 0 for feature_type in POG_FEATURE_TYPES}
        for feature in self.features:
            counts[feature.feature_type] += 1
        return counts


def references_act(href: str | None, act: PogActRecord) -> bool:
    """Czy xlink wskazuje dokładnie ten akt (identyfikator i zgodna wersja)."""
    ref = parse_app_reference(href)
    return bool(
        ref
        and ref.object_type == "AktPlanowaniaPrzestrzennego"
        and act.object_id
        and ref.matches(act.object_id)
    )


def resolve_act_documents(
    act: PogActRecord,
    documents: tuple[PogFormalDocumentRecord, ...],
) -> tuple[PogFormalDocumentRecord, ...]:
    """Wiąże dokumenty z wersją aktu po identyfikatorze i wersji, nigdy po tytule.

    - Dokument wskazany przez akt (``dokument*``) albo wskazujący akt (relacja
      ``przystapienie``/``uchwala``/…) jest ``resolved``, jeśli dokładnie jeden
      rekord pasuje do identyfikatora i — gdy podana — wersji.
    - Odwołanie bez wersji pasujące do wielu wersji dokumentu jest
      niejednoznaczne: wszystkie takie rekordy są ``unresolved``.
    - Dokument wskazujący inną wersję aktu jest ``unresolved`` z notą; nie jest
      podpinany do tej wersji jako rozstrzygnięty.
    - Odwołanie aktu bez rekordu dokumentu tworzy wpis ``unavailable``.

    Rekordy o tym samym tytule, lecz innych identyfikatorach lub wersjach, nigdy
    nie są scalane.
    """
    if act.object_id is None:
        return tuple(
            doc for doc in documents
            if doc.act_reference and doc.act_reference.href.rstrip("/").endswith(act.act_identifier)
        )
    act_ns = act.object_id.namespace.rstrip("/")
    act_local = act.object_id.local_id
    result: dict[tuple[str, str | None], PogFormalDocumentRecord] = {}

    for reference in act.document_references:
        ref = parse_app_reference(reference.href)
        if ref is None:
            continue
        candidates = [doc for doc in documents if ref.matches(doc.object_id)]
        if len(candidates) == 1:
            doc = candidates[0]
            result[(doc.object_id.stable_id, doc.object_id.version_id)] = replace(
                doc, resolution_status="resolved", resolution_note=None
            )
        elif len(candidates) > 1:
            for doc in candidates:
                result[(doc.object_id.stable_id, doc.object_id.version_id)] = replace(
                    doc,
                    resolution_status="unresolved",
                    resolution_note=(
                        "Odwołanie aktu bez wersji pasuje do wielu wersji dokumentu."
                    ),
                )
        else:
            placeholder = PogObjectId(ref.namespace, ref.local_id, ref.version_id)
            result[(placeholder.stable_id, placeholder.version_id)] = PogFormalDocumentRecord(
                object_id=placeholder,
                act_reference=PogReference(act.object_id.versioned_id),
                source_reference=act.source_reference,
                resolution_status="unavailable",
                resolution_note=(
                    "Akt wskazuje dokument, którego rekordu nie ma w pobranych danych."
                ),
            )

    referenced_versions: dict[str, set[str | None]] = {}
    for reference in act.document_references:
        ref = parse_app_reference(reference.href)
        if ref is not None:
            stable = f"{ref.namespace}/{ref.local_id}"
            referenced_versions.setdefault(stable, set()).add(ref.version_id)

    for doc in documents:
        key = (doc.object_id.stable_id, doc.object_id.version_id)
        if key in result or doc.act_reference is None:
            continue
        ref = parse_app_reference(doc.act_reference.href)
        if ref is None or ref.namespace != act_ns or ref.local_id != act_local:
            continue
        pinned_versions = referenced_versions.get(doc.object_id.stable_id)
        if pinned_versions and doc.object_id.version_id not in pinned_versions:
            result[key] = replace(
                doc,
                resolution_status="unresolved",
                resolution_note=(
                    "Akt wskazuje inną wersję tego dokumentu; ta wersja nie jest "
                    "podpinana do wersji aktu."
                ),
            )
        elif ref.matches(act.object_id):
            result[key] = replace(doc, resolution_status="resolved", resolution_note=None)
        else:
            result[key] = replace(
                doc,
                resolution_status="unresolved",
                resolution_note=(
                    f"Dokument wskazuje wersję aktu {ref.version_id}, a nie "
                    f"{act.object_id.version_id}."
                ),
            )
    return tuple(result[key] for key in sorted(result, key=lambda item: (item[0], item[1] or "")))


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def pog_record_to_dict(record: PogActRecord) -> dict[str, Any]:
    """Deterministyczny zapis relacji domenowych do JSON/audytu."""
    return _json_ready(asdict(record))


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
    def day(value: Any) -> date | None:
        return date.fromisoformat(value) if value else None

    documents = tuple(PogFormalDocumentRecord(
        object_id=oid(item["object_id"]), title=item.get("title"), link=item.get("link"),
        act_reference=ref(item.get("act_reference")), source_reference=item.get("source_reference"),
        raw_attributes=item.get("raw_attributes", {}),
        publication_id=item.get("publication_id"), short_name=item.get("short_name"),
        identification_number=item.get("identification_number"), relation=item.get("relation"),
        document_date=day(item.get("document_date")), effective_date=day(item.get("effective_date")),
        repeal_date=day(item.get("repeal_date")), record_sha256=item.get("record_sha256"),
        resolution_status=item.get("resolution_status", "resolved"),
        resolution_note=item.get("resolution_note"),
    ) for item in payload.get("documents", ()))
    metadata = tuple(CatalogRecordProvenance(
        record_id=item["record_id"], resource_identifier=item.get("resource_identifier"),
        title=item.get("title"), publication_date=day(item.get("publication_date")),
        revision_date=day(item.get("revision_date")), creation_date=day(item.get("creation_date")),
        date_stamp=day(item.get("date_stamp")), metadata_url=item.get("metadata_url"),
        references=tuple(item.get("references", ())), record_sha256=item.get("record_sha256"),
        response_sha256=item.get("response_sha256"),
        fetched_at=datetime.fromisoformat(item["fetched_at"]) if item.get("fetched_at") else None,
    ) for item in payload.get("metadata", ()))
    return PogActRecord(
        act_identifier=str(payload["act_identifier"]), resolution_number=payload.get("resolution_number"),
        resolution_date=date.fromisoformat(payload["resolution_date"]) if payload.get("resolution_date") else None,
        teryt=str(payload["teryt"]), name=payload.get("name"), legal_status=str(payload.get("legal_status", "unknown")),
        boundary=GeometryPayload(**payload["boundary"]) if payload.get("boundary") else None,
        features=tuple(features), object_id=oid(payload.get("object_id")), raw_legal_status=payload.get("raw_legal_status"),
        source_reference=payload.get("source_reference"),
        feature_references=tuple(PogReference(**r) for r in payload.get("feature_references", ())),
        document_references=tuple(PogReference(**r) for r in payload.get("document_references", ())), documents=documents,
        publication_id=payload.get("publication_id"),
        version_started_at=(
            datetime.fromisoformat(payload["version_started_at"])
            if payload.get("version_started_at") else None
        ),
        valid_from=day(payload.get("valid_from")), valid_to=day(payload.get("valid_to")),
        metadata=metadata,
    )
