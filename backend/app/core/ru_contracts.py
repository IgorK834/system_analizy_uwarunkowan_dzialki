"""Offline parsery kontraktow uslug Rejestru Urbanistycznego.

Modul celowo korzysta wylacznie z biblioteki standardowej. Odczytuje zapisane
fixtury XML, nie wykonuje polaczen sieciowych i moze byc bezpiecznie uzywany
przez zwykle testy jednostkowe oraz przyszle adaptery OGC.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from xml.etree import ElementTree

_MANIFEST_FIELDS: Final = {
    "url",
    "fetched_at",
    "sha256",
    "service",
    "version",
    "official_services_url",
    "fees",
    "access_constraints",
    "access_basis",
}
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_APP_POG_NAMESPACE: Final = (
    "https://www.gov.pl/static/zagospodarowanieprzestrzenne/schemas/app/3.0"
)

REQUIRED_WFS_FEATURE_TYPES: Final = frozenset(
    {
        "AktPlanowaniaPrzestrzennego",
        "StrefaPlanistyczna",
        "ObszarUzupelnieniaZabudowy",
        "ObszarZabudowySrodmiejskiej",
        "ObszarStandardowDostepnosciInfrastrukturySpolecznej",
        "DokumentFormalny",
    }
)
EXPECTED_WMS_CRS: Final = frozenset({"EPSG:2180", "EPSG:4326", "CRS:84"})


class RuContractError(ValueError):
    """Fixture albo manifest RU nie spelnia podstawowego kontraktu pliku."""


@dataclass(frozen=True)
class RuManifestEntry:
    """Metadane pochodzenia jednego pliku fixture RU."""

    url: str
    fetched_at: str
    sha256: str
    service: str
    version: str
    official_services_url: str
    fees: str
    access_constraints: str
    access_basis: str


@dataclass(frozen=True)
class RuFixtureSet:
    """Manifest i surowe bajty zweryfikowanych lokalnych fixtur."""

    manifest: dict[str, RuManifestEntry]
    xml: dict[str, bytes]


@dataclass(frozen=True)
class WmsCapabilities:
    """Czesc kontraktu WMS wykorzystywana przez aplikacje."""

    version: str
    layers: frozenset[str]
    crs: frozenset[str]
    get_map_formats: frozenset[str]
    fees: str | None
    access_constraints: str | None


@dataclass(frozen=True)
class WfsCapabilities:
    """Czesc kontraktu WFS potrzebna przyszlemu klientowi RU."""

    version: str
    feature_types: frozenset[str]
    default_crs: frozenset[str]
    supported_crs: frozenset[str]
    get_feature_formats: frozenset[str]
    count_default: int | None
    fees: str | None
    access_constraints: str | None


@dataclass(frozen=True)
class CswCapabilities:
    """Czesc kontraktu CSW potrzebna do discovery metadanych."""

    version: str
    operations: frozenset[str]
    get_records_output_schemas: frozenset[str]
    fees: str | None
    access_constraints: str | None


@dataclass(frozen=True)
class WfsGetFeature:
    """Minimalne fakty z odpowiedzi WFS GetFeature."""

    feature_types: frozenset[str]
    feature_count: int
    number_matched: str | None
    number_returned: int | None
    identifiers: frozenset[str]


XmlSource = bytes | bytearray | Path


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _read_xml(source: XmlSource) -> bytes:
    if isinstance(source, Path):
        try:
            return source.read_bytes()
        except OSError as exc:
            raise RuContractError(
                f"Nie mozna odczytac fixture XML {source}: {exc}"
            ) from exc
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    raise TypeError("Zrodlem XML musza byc bajty albo pathlib.Path.")


def parse_xml_root(
    source: XmlSource, *, max_depth: int = 64, max_nodes: int = 100_000
) -> ElementTree.Element:
    """Bezpiecznie parsuje XML bez DTD/encji i z limitami struktury.

    ``xml.etree`` nie pobiera zasobów sieciowych, ale jawne odrzucenie DTD i
    deklaracji encji zapobiega także lokalnym rozwinięciom encji. Limity są
    sprawdzane podczas ``iterparse``, zanim dokument zostanie przekazany dalej.
    """

    if max_depth < 1 or max_nodes < 1:
        raise ValueError("Limity XML muszą być dodatnie.")
    payload = _read_xml(source)
    upper = payload.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise RuContractError("XML zawiera zabronioną deklarację DTD lub encji.")

    depth = 0
    nodes = 0
    try:
        parser = ElementTree.iterparse(io.BytesIO(payload), events=("start", "end"))
        for event, _element in parser:
            if event == "start":
                depth += 1
                nodes += 1
                if depth > max_depth:
                    raise RuContractError(
                        f"XML przekracza limit głębokości {max_depth}."
                    )
                if nodes > max_nodes:
                    raise RuContractError(f"XML przekracza limit {max_nodes} węzłów.")
            else:
                depth -= 1
        # ``iterparse`` w CPython udostępnia ``root``, ale stuby typeshed go nie znają.
        root: ElementTree.Element | None = getattr(parser, "root", None)
        if root is None:
            raise RuContractError("XML nie zawiera elementu głównego.")
        return root
    except ElementTree.ParseError as exc:
        raise RuContractError(f"Niepoprawny XML fixture RU: {exc}") from exc


def exception_report_message(root: ElementTree.Element) -> str | None:
    """Zwraca opis OGC ExceptionReport/ServiceExceptionReport, jeśli istnieje."""

    if _local_name(root.tag) not in {"ExceptionReport", "ServiceExceptionReport"}:
        return None
    messages = [
        text.strip()
        for element in root.iter()
        if _local_name(element.tag) in {"ExceptionText", "ServiceException"}
        for text in [element.text or ""]
        if text.strip()
    ]
    return "; ".join(messages) or "Usługa OGC zwróciła raport wyjątku."


# Zachowana nazwa prywatna ogranicza zmianę istniejących parserów.
_parse_root = parse_xml_root


def _first_text(root: ElementTree.Element, local_name: str) -> str | None:
    for element in root.iter():
        if _local_name(element.tag) == local_name and element.text:
            value = element.text.strip()
            if value:
                return value
    return None


def _canonical_crs(value: str) -> str:
    normalized = value.strip()
    epsg_match = re.search(r"EPSG(?::|::|/0/)(\d+)$", normalized, re.IGNORECASE)
    if epsg_match:
        return f"EPSG:{epsg_match.group(1)}"
    return normalized.upper() if normalized.upper() == "CRS:84" else normalized


def _operation(
    root: ElementTree.Element, operation_name: str
) -> ElementTree.Element | None:
    for element in root.iter():
        if (
            _local_name(element.tag) == "Operation"
            and element.attrib.get("name") == operation_name
        ):
            return element
    return None


def _parameter_values(
    operation: ElementTree.Element | None, parameter_name: str
) -> frozenset[str]:
    if operation is None:
        return frozenset()
    for parameter in operation:
        if (
            _local_name(parameter.tag) == "Parameter"
            and parameter.attrib.get("name", "").lower() == parameter_name.lower()
        ):
            return frozenset(
                value.text.strip()
                for value in parameter.iter()
                if _local_name(value.tag) == "Value" and value.text
            )
    return frozenset()


def load_ru_fixture_dir(path: Path) -> RuFixtureSet:
    """Wczytuje lokalny manifest i sprawdza XML oraz SHA-256 kazdej fixtury."""

    fixture_dir = Path(path)
    manifest_path = fixture_dir / "manifest.json"
    try:
        raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuContractError(f"Nie mozna odczytac manifestu RU: {exc}") from exc
    if not isinstance(raw_manifest, dict) or not raw_manifest:
        raise RuContractError(
            "Manifest RU musi byc niepustym obiektem keyed-by-filename."
        )

    manifest: dict[str, RuManifestEntry] = {}
    xml: dict[str, bytes] = {}
    for filename, raw_entry in raw_manifest.items():
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise RuContractError(
                f"Niebezpieczna nazwa pliku w manifeście RU: {filename!r}."
            )
        if not isinstance(raw_entry, dict) or set(raw_entry) != _MANIFEST_FIELDS:
            raise RuContractError(
                f"Wpis {filename!r} musi miec dokladnie pola {sorted(_MANIFEST_FIELDS)}."
            )
        if raw_entry.get("service") not in {"wms", "wfs", "csw"}:
            raise RuContractError(f"Nieznana usluga w manifeście RU: {filename!r}.")
        if not all(isinstance(raw_entry.get(field), str) for field in _MANIFEST_FIELDS):
            raise RuContractError(f"Pola wpisu {filename!r} musza byc tekstowe.")
        if not _SHA256_RE.fullmatch(raw_entry["sha256"]):
            raise RuContractError(f"Niepoprawny SHA-256 wpisu {filename!r}.")

        fixture_path = fixture_dir / filename
        payload = _read_xml(fixture_path)
        _parse_root(payload)
        actual_sha256 = hashlib.sha256(payload).hexdigest()
        if actual_sha256 != raw_entry["sha256"]:
            raise RuContractError(
                f"SHA-256 fixture {filename!r} nie zgadza sie z manifestem."
            )
        manifest[filename] = RuManifestEntry(**raw_entry)
        xml[filename] = payload
    return RuFixtureSet(manifest=manifest, xml=xml)


def parse_wms_capabilities(source: XmlSource) -> WmsCapabilities:
    """Parsuje WMS 1.3.0 bez zalozen o prefiksach przestrzeni nazw."""

    root = _parse_root(source)
    layers: set[str] = set()
    crs: set[str] = set()
    for layer in root.iter():
        if _local_name(layer.tag) != "Layer":
            continue
        for child in layer:
            local_name = _local_name(child.tag)
            if local_name == "Name" and child.text:
                layers.add(child.text.strip())
            elif local_name in {"CRS", "SRS"} and child.text:
                crs.add(_canonical_crs(child.text))

    get_map_formats: set[str] = set()
    for element in root.iter():
        if _local_name(element.tag) == "GetMap":
            get_map_formats.update(
                child.text.strip()
                for child in element
                if _local_name(child.tag) == "Format" and child.text
            )

    return WmsCapabilities(
        version=root.attrib.get("version", ""),
        layers=frozenset(layers),
        crs=frozenset(crs),
        get_map_formats=frozenset(get_map_formats),
        fees=_first_text(root, "Fees"),
        access_constraints=_first_text(root, "AccessConstraints"),
    )


def parse_wfs_capabilities(source: XmlSource) -> WfsCapabilities:
    """Parsuje typy, CRS, formaty i limit stronicowania WFS 2.0."""

    root = _parse_root(source)
    feature_types: set[str] = set()
    default_crs: set[str] = set()
    supported_crs: set[str] = set()
    for feature_type in root.iter():
        if _local_name(feature_type.tag) != "FeatureType":
            continue
        for child in feature_type:
            local_name = _local_name(child.tag)
            if local_name == "Name" and child.text:
                feature_types.add(child.text.strip().split(":")[-1])
            elif local_name in {"DefaultCRS", "DefaultSRS"} and child.text:
                canonical = _canonical_crs(child.text)
                default_crs.add(canonical)
                supported_crs.add(canonical)
            elif local_name in {"OtherCRS", "OtherSRS"} and child.text:
                supported_crs.add(_canonical_crs(child.text))

    get_feature = _operation(root, "GetFeature")
    count_default: int | None = None
    if get_feature is not None:
        for element in get_feature.iter():
            if (
                _local_name(element.tag) == "Constraint"
                and element.attrib.get("name") == "CountDefault"
            ):
                raw_default = _first_text(element, "DefaultValue") or _first_text(
                    element, "Value"
                )
                if raw_default is not None:
                    try:
                        count_default = int(raw_default)
                    except ValueError as exc:
                        raise RuContractError(
                            f"CountDefault WFS nie jest liczba: {raw_default!r}."
                        ) from exc

    return WfsCapabilities(
        version=root.attrib.get("version", ""),
        feature_types=frozenset(feature_types),
        default_crs=frozenset(default_crs),
        supported_crs=frozenset(supported_crs),
        get_feature_formats=_parameter_values(get_feature, "outputFormat"),
        count_default=count_default,
        fees=_first_text(root, "Fees"),
        access_constraints=_first_text(root, "AccessConstraints"),
    )


def parse_csw_capabilities(source: XmlSource) -> CswCapabilities:
    """Parsuje operacje i outputSchema operacji GetRecords CSW 2.0.2."""

    root = _parse_root(source)
    operations = frozenset(
        element.attrib["name"]
        for element in root.iter()
        if _local_name(element.tag) == "Operation" and "name" in element.attrib
    )
    get_records = _operation(root, "GetRecords")
    return CswCapabilities(
        version=root.attrib.get("version", ""),
        operations=operations,
        get_records_output_schemas=_parameter_values(get_records, "outputSchema"),
        fees=_first_text(root, "Fees"),
        access_constraints=_first_text(root, "AccessConstraints"),
    )


def parse_wfs_getfeature(source: XmlSource) -> WfsGetFeature:
    """Parsuje licznik, rzeczywiste typy cech i identyfikatory GetFeature."""

    root = _parse_root(source)
    feature_types: set[str] = set()
    identifiers: set[str] = set()
    feature_count = 0
    for member in root.iter():
        if _local_name(member.tag) not in {"member", "featureMember"}:
            continue
        feature = next(iter(member), None)
        if feature is None:
            continue
        feature_count += 1
        feature_types.add(_local_name(feature.tag))
        for element in feature.iter():
            if (
                _local_name(element.tag)
                in {"identifier", "przestrzenNazw", "lokalnyId", "wersjaId"}
                and element.text
            ):
                identifiers.add(element.text.strip())

    raw_returned = root.attrib.get("numberReturned")
    try:
        number_returned = int(raw_returned) if raw_returned is not None else None
    except ValueError as exc:
        raise RuContractError(
            f"numberReturned WFS nie jest liczba: {raw_returned!r}."
        ) from exc
    return WfsGetFeature(
        feature_types=frozenset(feature_types),
        feature_count=feature_count,
        number_matched=root.attrib.get("numberMatched"),
        number_returned=number_returned,
        identifiers=frozenset(identifiers),
    )


def trim_wfs_getfeature(source: XmlSource) -> bytes:
    """Przycina GetFeature do metadanych aktu bez geometrii i list wydzielen."""

    root = _parse_root(source)
    keep = {
        "identifier",
        "idIIP",
        "poczatekWersjiObiektu",
        "tytul",
        "typPlanu",
        "status",
        "modyfikacja",
    }
    for member in root.iter():
        if _local_name(member.tag) not in {"member", "featureMember"}:
            continue
        feature = next(iter(member), None)
        if feature is None:
            continue
        for child in list(feature):
            if _local_name(child.tag) not in keep:
                feature.remove(child)

    ElementTree.register_namespace("wfs", "http://www.opengis.net/wfs/2.0")
    ElementTree.register_namespace("gml", "http://www.opengis.net/gml/3.2")
    ElementTree.register_namespace("app-pog", _APP_POG_NAMESPACE)
    ElementTree.register_namespace("xlink", "http://www.w3.org/1999/xlink")
    ElementTree.register_namespace("xsi", "http://www.w3.org/2001/XMLSchema-instance")
    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)


def assert_wms_contract(capabilities: WmsCapabilities) -> None:
    """Fail loud, gdy fixture przestaje dowodzic zmierzonego kontraktu WMS."""

    assert capabilities.version == "1.3.0"
    assert capabilities.layers
    assert all(layer.startswith("APP.POG.") for layer in capabilities.layers)
    assert "image/png" in capabilities.get_map_formats
    assert capabilities.crs == EXPECTED_WMS_CRS


def assert_wfs_contract(
    capabilities: WfsCapabilities, *, expected_count_default: int | None = None
) -> None:
    """Fail loud dla wersji, typow, EPSG:2180 albo limitu WFS RU."""

    assert capabilities.version == "2.0.0"
    assert REQUIRED_WFS_FEATURE_TYPES <= capabilities.feature_types
    assert "EPSG:2180" in capabilities.default_crs
    assert capabilities.count_default is not None
    assert capabilities.count_default > 0
    if expected_count_default is not None:
        assert capabilities.count_default == expected_count_default
    assert "application/gml+xml; version=3.2" in capabilities.get_feature_formats


def assert_csw_contract(capabilities: CswCapabilities) -> None:
    """Fail loud dla wersji lub kontraktu GetRecords CSW RU."""

    assert capabilities.version == "2.0.2"
    assert "GetRecords" in capabilities.operations
    assert capabilities.get_records_output_schemas


def assert_wfs_getfeature_contract(
    response: WfsGetFeature, *, expected_teryt: str = "246101"
) -> None:
    """Fail loud, gdy mala odpowiedz nie zawiera aktu oczekiwanego JPT."""

    assert response.number_returned is not None
    assert response.number_returned == response.feature_count
    assert response.feature_count > 0
    assert "AktPlanowaniaPrzestrzennego" in response.feature_types
    assert any(expected_teryt in identifier for identifier in response.identifiers)
