"""Czytniki jawnie skonfigurowanych warstw POG (APP/GML/GeoJSON/ZIP).

Każda warstwa POG jest jawnie przypisana do jednego z czterech typów
(``planning_zone``, ``ouz``, ``downtown_area``, ``social_infrastructure_standard``)
— importer nie zgaduje typu ustawowego. Schematy APP różnią się między
producentami GIS, dlatego mapowanie atrybutów jest konfigurowalne per warstwa, a
surowe atrybuty źródłowe zawsze są zachowywane jako dowód.

Pobieranie i rozpakowywanie ZIP przechodzi przez wspólny, bezpieczny mechanizm
``app.shared.safe_archive`` (Zip Slip, zip bomb, limity), a nie przez
niekontrolowane ``extractall``.
"""

from __future__ import annotations

import io
import re
import tempfile
import zipfile
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from shapely import from_wkt
from shapely.ops import unary_union

from app.modules.imports.application.pog_import import PogSourceBatch
from app.core.ru_contracts import RuContractError, parse_xml_root
from app.modules.imports.domain.pog import (
    PogActRecord,
    PogFeatureRecord,
    PogFormalDocumentRecord,
    PogFunctionalProfile,
    PogNumericValue,
    PogObjectId,
    PogPlanningParameters,
    PogReference,
    PogValidationError,
    normalize_feature_type,
    normalize_legal_status,
)
from app.modules.imports.infrastructure.vector import VectorFeature, read_vector_features
from app.modules.imports.infrastructure.wfs import WfsFetcher, WfsResource
from app.shared.geometry import GeometryPayload
from app.shared.safe_archive import extract_zip
from app.services.gml import GmlResponseError, parse_feature_collection


APP3_NAMESPACE = "https://www.gov.pl/static/zagospodarowanieprzestrzenne/schemas/app/3.0"
XLINK_NAMESPACE = "http://www.w3.org/1999/xlink"
_XLINK_HREF = f"{{{XLINK_NAMESPACE}}}href"
_XLINK_TITLE = f"{{{XLINK_NAMESPACE}}}title"
_TERYT_RE = re.compile(r"/(\d{6,7})-POG(?:/|$)")


@dataclass(frozen=True)
class RuPogObjects:
    """Sześć typów APP odczytanych z jednej lub wielu odpowiedzi WFS."""

    acts: tuple[PogActRecord, ...] = ()
    features: tuple[PogFeatureRecord, ...] = ()
    documents: tuple[PogFormalDocumentRecord, ...] = ()


@dataclass(frozen=True)
class PogLayerResource:
    """Pojedyncza warstwa POG przypisana do typu obiektu."""

    feature_type: str
    path: Path
    source_crs: str
    field_mapping: dict[str, str] = field(default_factory=dict)
    layer: str | int | None = None


@dataclass(frozen=True)
class PogBoundaryResource:
    """Warstwa granicy aktu (aktPlanowaniaPrzestrzennego)."""

    path: Path
    source_crs: str
    layer: str | int | None = None


@dataclass(frozen=True)
class PogActMetadata:
    """Metadane aktu, gdy nie pochodzą z atrybutów warstwy."""

    act_identifier: str
    teryt: str
    legal_status: str
    resolution_number: str | None = None
    resolution_date: date | None = None
    name: str | None = None


def _boundary_payload(
    boundary: PogBoundaryResource | None,
) -> GeometryPayload | None:
    if boundary is None:
        return None
    features = read_vector_features(
        boundary.path, layer=boundary.layer, declared_crs=boundary.source_crs
    )
    if not features:
        return None
    merged = unary_union([from_wkt(feature.geometry.wkt) for feature in features])
    if merged.is_empty:
        return None
    return GeometryPayload(merged.wkt, crs=features[0].geometry.crs)


def _features_from_layers(
    layers: list[tuple[PogLayerResource, tuple[VectorFeature, ...]]],
) -> tuple[PogFeatureRecord, ...]:
    records: list[PogFeatureRecord] = []
    for resource, features in layers:
        for feature in features:
            records.append(
                PogFeatureRecord(
                    feature_type=resource.feature_type,
                    geometry=feature.geometry,
                    raw_attributes=feature.attributes,
                )
            )
    return tuple(records)


def _build_act(
    metadata: PogActMetadata,
    layers: list[tuple[PogLayerResource, tuple[VectorFeature, ...]]],
    boundary: GeometryPayload | None,
) -> PogActRecord:
    return PogActRecord(
        act_identifier=metadata.act_identifier,
        resolution_number=metadata.resolution_number,
        resolution_date=metadata.resolution_date,
        teryt=metadata.teryt,
        name=metadata.name,
        legal_status=metadata.legal_status,
        boundary=boundary,
        features=_features_from_layers(layers),
    )


class PyogrioPogReader:
    """Czyta lokalne pliki warstw POG jawnie przypisane do typów obiektów."""

    def __init__(
        self,
        resources: tuple[PogLayerResource, ...],
        *,
        metadata: PogActMetadata,
        boundary: PogBoundaryResource | None = None,
    ) -> None:
        self._resources = resources
        self._metadata = metadata
        self._boundary = boundary

    def read(self) -> PogSourceBatch:
        layers = [
            (
                resource,
                read_vector_features(
                    resource.path,
                    layer=resource.layer,
                    declared_crs=resource.source_crs,
                ),
            )
            for resource in self._resources
        ]
        boundary = _boundary_payload(self._boundary)
        act = _build_act(self._metadata, layers, boundary)
        return PogSourceBatch(
            content=_pack_local_resources(self._resources, self._boundary),
            filename="pog-vector.zip",
            media_type="application/zip",
            acts=(act,),
        )


class WfsPogReader:
    """Pobiera warstwy POG przez WFS i zachowuje bezpieczny artefakt ZIP."""

    def __init__(
        self,
        resources: tuple[tuple[str, WfsResource], ...],
        *,
        metadata: PogActMetadata,
        fetcher: WfsFetcher | None = None,
    ) -> None:
        # Każdy element to (feature_type, zasób WFS). Kolejność == kolejność
        # plików GML w rozpakowanym artefakcie.
        self._resources = resources
        self._metadata = metadata
        self._fetcher = fetcher or WfsFetcher()

    def read(self) -> PogSourceBatch:
        wfs_resources = tuple(resource for _feature_type, resource in self._resources)
        content = self._fetcher.fetch(wfs_resources)
        layers: list[tuple[PogLayerResource, tuple[VectorFeature, ...]]] = []
        ru_parts: list[RuPogObjects] = []
        with tempfile.TemporaryDirectory(prefix="pog-wfs-") as temporary:
            # Bezpieczne rozpakowanie zamiast extractall (Zip Slip, zip bomb).
            extract_zip(content, temporary, allowed_suffixes=(".gml",))
            paths = sorted(Path(temporary).glob("*.gml"))
            for (feature_type, resource), path in zip(
                self._resources, paths, strict=True
            ):
                raw = path.read_bytes()
                if APP3_NAMESPACE.encode() in raw:
                    expected = resource.type_name.rsplit(":", 1)[-1]
                    ru_parts.append(
                        parse_ru_app_feature_collection(
                            raw,
                            expected_type=expected,
                            source_reference=resource.url,
                        )
                    )
                    continue
                local = PogLayerResource(
                    feature_type=feature_type,
                    path=path,
                    source_crs=resource.source_crs,
                    field_mapping=resource.field_mapping,
                )
                layers.append(
                    (local, read_vector_features(path, declared_crs=resource.source_crs))
                )
            if ru_parts:
                objects = merge_ru_pog_objects(tuple(ru_parts))
                acts = assemble_ru_pog_acts(objects)
                if not acts:
                    raise PogValidationError(
                        "Odpowiedź RU zawiera obiekty zależne bez AktPlanowaniaPrzestrzennego."
                    )
            else:
                acts = (_build_act(self._metadata, layers, None),)
        return PogSourceBatch(
            content=content,
            filename="pog-wfs.zip",
            media_type="application/zip",
            acts=acts,
            complete=True,
            strict_identifiers=bool(ru_parts),
            parser_config_id="ru-app-3.0-v2",
        )


def read_pog_archive(
    content: bytes,
    resources: tuple[PogLayerResource, ...],
    *,
    metadata: PogActMetadata,
) -> PogSourceBatch:
    """Rozpakowuje bezpiecznie ZIP APP/GML i mapuje pliki na warstwy po kolejności.

    Kolejność ``resources`` odpowiada posortowanej alfabetycznie liście plików w
    archiwum. Ekstrakcja używa wspólnego mechanizmu bezpieczeństwa, więc Zip
    Slip i zip bomb powodują kontrolowane odrzucenie całego importu.
    """
    with tempfile.TemporaryDirectory(prefix="pog-archive-") as temporary:
        extracted = extract_zip(
            content,
            temporary,
            allowed_suffixes=(".gml", ".xml", ".geojson", ".json"),
        )
        paths = sorted(extracted)
        layers = [
            (
                PogLayerResource(
                    feature_type=resource.feature_type,
                    path=path,
                    source_crs=resource.source_crs,
                    field_mapping=resource.field_mapping,
                    layer=resource.layer,
                ),
                read_vector_features(
                    path, layer=resource.layer, declared_crs=resource.source_crs
                ),
            )
            for resource, path in zip(resources, paths, strict=True)
        ]
        act = _build_act(metadata, layers, None)
    return PogSourceBatch(
        content=content,
        filename="pog-archive.zip",
        media_type="application/zip",
        acts=(act,),
    )


def _pack_local_resources(
    resources: tuple[PogLayerResource, ...],
    boundary: PogBoundaryResource | None,
) -> bytes:
    """Pakuje wejściowe pliki w deterministyczny ZIP dla artefaktu źródłowego."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for index, resource in enumerate(resources):
            info = zipfile.ZipInfo(
                f"{index:02d}-{resource.feature_type}{resource.path.suffix}"
            )
            info.date_time = (1980, 1, 1, 0, 0, 0)
            archive.writestr(info, resource.path.read_bytes())
        if boundary is not None:
            info = zipfile.ZipInfo(f"99-boundary{boundary.path.suffix}")
            info.date_time = (1980, 1, 1, 0, 0, 0)
            archive.writestr(info, boundary.path.read_bytes())
    return buffer.getvalue()


# --- Oficjalny kontrakt RU APP 3.0 ------------------------------------------


def parse_ru_app_feature_collection(
    content: bytes,
    *,
    expected_type: str | None = None,
    source_reference: str | None = None,
) -> RuPogObjects:
    """Mapuje oficjalny WFS APP 3.0 bez heurystyk zależnych od gminy.

    Namespace i typ cechy są częścią kontraktu. Geometria bez jawnego CRS jest
    odrzucana: adapter nie może zgadywać EPSG:2180. Wartości procentowe mają
    jednostkę ``%`` zdefiniowaną w oficjalnym XSD, intensywność jest
    bezwymiarowa (``1``), a wysokość musi jawnie zawierać ``uom=\"m\"``.
    """
    try:
        root = parse_xml_root(content, max_nodes=300_000)
    except RuContractError as exc:
        raise PogValidationError(str(exc)) from exc

    acts: list[PogActRecord] = []
    features: list[PogFeatureRecord] = []
    documents: list[PogFormalDocumentRecord] = []
    geometry_elements: list[ElementTree.Element] = []
    feature_elements: list[ElementTree.Element] = []
    for member in root.iter():
        if _local_name(member.tag) not in {"member", "featureMember"}:
            continue
        for element in list(member):
            namespace, local = _split_tag(element.tag)
            if namespace != APP3_NAMESPACE:
                raise PogValidationError(
                    f"Nieobsługiwany namespace APP: {namespace or '<brak>'}."
                )
            if expected_type and local != expected_type:
                raise PogValidationError(
                    f"Oczekiwano {expected_type}, otrzymano {local}."
                )
            feature_elements.append(element)
            if _child(element, "geometria") is not None or _child(element, "zasiegPrzestrzenny") is not None:
                geometry_elements.append(element)

    geometries = []
    if geometry_elements:
        for element in geometry_elements:
            container = _child(element, "geometria")
            if container is None:
                container = _child(element, "zasiegPrzestrzenny")
            assert container is not None
            if not any(node.get("srsName") for node in container.iter()):
                raise PogValidationError("Geometria APP nie ma jawnego srsName; CRS nie będzie zgadywany.")
        try:
            geometries = parse_feature_collection(content.decode("utf-8"))
        except (UnicodeDecodeError, GmlResponseError) as exc:
            raise PogValidationError(f"Niepoprawna geometria APP: {exc}") from exc

    geometry_index = 0
    for element in feature_elements:
        local = _local_name(element.tag)
        object_id = _parse_object_id(element)
        raw = _raw_attributes(element)
        if local == "AktPlanowaniaPrzestrzennego":
            geometry = None
            if _child(element, "zasiegPrzestrzenny") is not None:
                if geometry_index >= len(geometries):
                    raise PogValidationError("Akt APP deklaruje geometrię, której nie odczytano.")
                geometry = GeometryPayload(geometries[geometry_index].geometry.wkt)
                geometry_index += 1
            raw_status = _reference_value(_child(element, "status"))
            teryt_match = _TERYT_RE.search(object_id.namespace + "/")
            if not teryt_match:
                raise PogValidationError("Nie można odczytać TERYT z przestrzenNazw idIIP.")
            feature_refs = tuple(
                _parse_reference(child) for child in element
                if _local_name(child.tag) in {"wydzielenie", "regulacja"} and child.get(_XLINK_HREF)
            )
            document_refs = tuple(
                _parse_reference(child) for child in element
                if _local_name(child.tag) in {"dokumentPrzystepujacy", "dokumentUchwalajacy"} and child.get(_XLINK_HREF)
            )
            acts.append(PogActRecord(
                act_identifier=object_id.stable_id,
                resolution_number=None,
                resolution_date=None,
                teryt=teryt_match.group(1),
                name=_text(_child(element, "tytul")),
                legal_status=normalize_legal_status(raw_status),
                boundary=geometry,
                object_id=object_id,
                raw_legal_status=raw_status,
                source_reference=source_reference,
                feature_references=feature_refs,
                document_references=document_refs,
            ))
            continue

        if local == "DokumentFormalny":
            act_ref_element = next(
                (child for child in element if _local_name(child.tag) in {"przystapienie", "uchwalenie", "zmiana"} and child.get(_XLINK_HREF)),
                None,
            )
            documents.append(PogFormalDocumentRecord(
                object_id=object_id,
                title=_text(_child(element, "tytul")),
                link=_text(_child(element, "lacze")),
                act_reference=_parse_reference(act_ref_element) if act_ref_element is not None else None,
                source_reference=source_reference,
                raw_attributes=raw,
            ))
            continue

        feature_type = normalize_feature_type(local)
        if feature_type is None:
            raise PogValidationError(f"Nieobsługiwany typ obiektu APP: {local}.")
        if geometry_index >= len(geometries):
            raise PogValidationError(f"Obiekt {local} nie zawiera geometrii powierzchniowej.")
        geometry = GeometryPayload(geometries[geometry_index].geometry.wkt)
        geometry_index += 1
        status = _child(element, "status")
        raw_status = _reference_value(status)
        plan = _child(element, "plan")
        parameters = _parse_parameters(element) if feature_type == "planning_zone" else None
        features.append(PogFeatureRecord(
            feature_type=feature_type,
            geometry=geometry,
            raw_attributes=raw,
            object_id=object_id,
            act_reference=_parse_reference(plan) if plan is not None else None,
            source_reference=source_reference,
            raw_legal_status=raw_status,
            symbol=_text(_child(element, "symbol")),
            label=_reference_label(_child(element, "nazwa")) or _text(_child(element, "nazwa")),
            parameters=parameters,
            primary_profiles=_profiles(element, "profilPodstawowy"),
            additional_profiles=_profiles(element, "profilDodatkowy"),
        ))

    return RuPogObjects(tuple(acts), tuple(features), tuple(documents))


def assemble_ru_pog_acts(objects: RuPogObjects) -> tuple[PogActRecord, ...]:
    """Łączy akty, cechy i dokumenty po stabilnym xlinku, zachowując sieroty."""
    assembled: list[PogActRecord] = []
    for act in objects.acts:
        stable = act.object_id.stable_id if act.object_id else act.act_identifier
        features = tuple(f for f in objects.features if f.act_reference and _same_reference(f.act_reference.href, stable))
        documents = tuple(d for d in objects.documents if d.act_reference and _same_reference(d.act_reference.href, stable))
        assembled.append(replace(act, features=features, documents=documents))
    return tuple(assembled)


def merge_ru_pog_objects(parts: tuple[RuPogObjects, ...]) -> RuPogObjects:
    return RuPogObjects(
        acts=tuple(item for part in parts for item in part.acts),
        features=tuple(item for part in parts for item in part.features),
        documents=tuple(item for part in parts for item in part.documents),
    )


def _split_tag(tag: str) -> tuple[str | None, str]:
    if tag.startswith("{"):
        namespace, local = tag[1:].split("}", 1)
        return namespace, local
    return None, tag


def _local_name(tag: str) -> str:
    return _split_tag(tag)[1]


def _child(element: ElementTree.Element, name: str) -> ElementTree.Element | None:
    return next((child for child in element if _local_name(child.tag) == name), None)


def _text(element: ElementTree.Element | None) -> str | None:
    if element is None:
        return None
    value = " ".join("".join(element.itertext()).split())
    return value or None


def _parse_object_id(element: ElementTree.Element) -> PogObjectId:
    id_iip = _child(element, "idIIP")
    if id_iip is None:
        raise PogValidationError(f"{_local_name(element.tag)} nie zawiera idIIP.")
    values = {_local_name(node.tag): _text(node) for node in id_iip.iter()}
    object_id = PogObjectId(
        namespace=values.get("przestrzenNazw") or "",
        local_id=values.get("lokalnyId") or "",
        version_id=values.get("wersjaId"),
    )
    object_id.validate()
    return object_id


def _parse_reference(element: ElementTree.Element) -> PogReference:
    reference = PogReference(element.get(_XLINK_HREF, ""), element.get(_XLINK_TITLE))
    reference.validate()
    return reference


def _reference_label(element: ElementTree.Element | None) -> str | None:
    return element.get(_XLINK_TITLE) if element is not None else None


def _reference_value(element: ElementTree.Element | None) -> str | None:
    if element is None:
        return None
    return element.get(_XLINK_HREF) or element.get(_XLINK_TITLE) or _text(element)


def _decimal(element: ElementTree.Element | None, *, unit: str, require_uom: bool = False) -> PogNumericValue | None:
    raw = _text(element)
    if raw is None:
        return None
    if require_uom and (element is None or element.get("uom") != unit):
        raise PogValidationError(f"Parametr {getattr(element, 'tag', '')} wymaga uom={unit!r}.")
    try:
        value = float(raw.replace(" ", "").replace(",", "."))
    except ValueError as exc:
        raise PogValidationError(f"Niepoprawna wartość liczbowa APP: {raw!r}.") from exc
    return PogNumericValue(value, unit)


def _parse_parameters(element: ElementTree.Element) -> PogPlanningParameters:
    parameters = PogPlanningParameters(
        max_overground_floor_area_ratio=_decimal(_child(element, "maksNadziemnaIntensywnoscZabudowy"), unit="1"),
        max_building_height=_decimal(_child(element, "maksWysokoscZabudowy"), unit="m", require_uom=True),
        max_building_coverage=_decimal(_child(element, "maksUdzialPowierzchniZabudowy"), unit="%"),
        min_biologically_active=_decimal(_child(element, "minUdzialPowierzchniBiologicznieCzynnej"), unit="%"),
    )
    parameters.validate()
    return parameters


def _profiles(element: ElementTree.Element, name: str) -> tuple[PogFunctionalProfile, ...]:
    profiles: list[PogFunctionalProfile] = []
    for child in element:
        if _local_name(child.tag) != name:
            continue
        href = child.get(_XLINK_HREF, "")
        source, _, code = href.partition("#")
        if not code:
            code = href.rstrip("/").rsplit("/", 1)[-1]
            source = href.rsplit("/", 1)[0]
        profile = PogFunctionalProfile(code, child.get(_XLINK_TITLE), source)
        profile.validate()
        profiles.append(profile)
    return tuple(profiles)


def _raw_attributes(element: ElementTree.Element) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for child in element:
        name = _local_name(child.tag)
        value: Any = _text(child)
        if href := child.get(_XLINK_HREF):
            value = {"href": href, "label": child.get(_XLINK_TITLE)}
        if name in result:
            current = result[name]
            result[name] = [*current, value] if isinstance(current, list) else [current, value]
        else:
            result[name] = value
    return result


def _same_reference(href: str, stable_id: str) -> bool:
    return href.rstrip("/").endswith(stable_id.rstrip("/"))
