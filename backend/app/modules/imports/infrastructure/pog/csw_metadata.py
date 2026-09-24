"""Metadane CSW RU i oficjalne URL-e obiektów APP (BK-107).

Rekordy ISO 19139 z katalogu CSW Rejestru Urbanistycznego są pobierane
wspólnym klientem OGC (BK-102) i zamrażane w snapshotcie importu. Rekord jest
łączony z aktem wyłącznie po ``MD_Identifier/code`` równym URI przestrzeni
nazw aktu (``…/AktPlanowaniaPrzestrzennego/{przestrzenNazw}/``) — nigdy po
podobnym tytule. Zapytanie CSW zawęża wyniki po TERYT w tytule (pycsw RU nie
obsługuje filtra po identyfikatorze zasobu), ale sam tytuł niczego nie wiąże.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from urllib.parse import urlencode
from xml.etree import ElementTree

from app.core.ru_contracts import RuContractError, parse_xml_root
from app.modules.imports.domain.pog import PogActRecord, PogObjectId, PogValidationError
from app.modules.imports.infrastructure.ogc_client import OgcClient, OgcError
from app.shared.provenance import CatalogRecordProvenance, Provenance, is_verified_https_url

GMD = "http://www.isotc211.org/2005/gmd"
GCO = "http://www.isotc211.org/2005/gco"
ISO_OUTPUT_SCHEMA = GMD
APP3_NAMESPACE = "https://www.gov.pl/static/zagospodarowanieprzestrzenne/schemas/app/3.0"
FEATURE_TYPE_NAMES: dict[str, str] = {
    "planning_zone": "StrefaPlanistyczna",
    "ouz": "ObszarUzupelnieniaZabudowy",
    "downtown_area": "ObszarZabudowySrodmiejskiej",
    "social_infrastructure_standard": "ObszarStandardowDostepnosciInfrastrukturySpolecznej",
    "planning_act": "AktPlanowaniaPrzestrzennego",
    "formal_document": "DokumentFormalny",
}


@dataclass(frozen=True)
class CswMetadataResult:
    """Wynik pobrania metadanych: rekordy, artefakt i provenance zapytania."""

    records: tuple[CatalogRecordProvenance, ...]
    response: bytes
    source: Provenance | None
    complete: bool
    error: str | None = None


def csw_record_url(csw_url: str, record_id: str) -> str:
    """Oficjalny URL karty metadanych (GetRecordById, ISO 19139)."""
    query = urlencode(
        {
            "service": "CSW",
            "version": "2.0.2",
            "request": "GetRecordById",
            "id": record_id,
            "elementSetName": "full",
            "outputSchema": ISO_OUTPUT_SCHEMA,
        }
    )
    return f"{csw_url}?{query}"


def ru_object_gml_url(wfs_url: str | None, type_name: str, object_id: PogObjectId | None) -> str | None:
    """Oficjalny URL GML dokładnej wersji obiektu APP (WFS GetFeature + FES idIIP).

    URL wskazuje usługę, z której obiekt faktycznie zaimportowano
    (``source_reference``), więc nie zależy od bieżącego katalogu. Filtr obejmuje
    przestrzeń nazw, lokalnyId i — jeśli jest — wersjaId.
    """
    if not wfs_url or object_id is None or not is_verified_https_url(wfs_url):
        return None
    conditions = [
        ("przestrzenNazw", object_id.namespace.rstrip("/")),
        ("lokalnyId", object_id.local_id),
    ]
    if object_id.version_id:
        conditions.append(("wersjaId", object_id.version_id))
    predicates = "".join(
        "<fes:PropertyIsEqualTo><fes:ValueReference>"
        f"app-pog:idIIP/app-pog:Identyfikator/app-pog:{name}"
        f"</fes:ValueReference><fes:Literal>{_xml_escape(value)}</fes:Literal>"
        "</fes:PropertyIsEqualTo>"
        for name, value in conditions
    )
    fes = (
        '<fes:Filter xmlns:fes="http://www.opengis.net/fes/2.0" '
        f'xmlns:app-pog="{APP3_NAMESPACE}"><fes:And>{predicates}</fes:And></fes:Filter>'
    )
    query = urlencode(
        {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": f"app-pog:{type_name}",
            "namespaces": f"xmlns(app-pog,{APP3_NAMESPACE})",
            "FILTER": fes,
        }
    )
    return f"{wfs_url}?{query}"


def parse_iso_records(
    content: bytes,
    *,
    csw_url: str | None = None,
    fetched_at: datetime | None = None,
    response_sha256: str | None = None,
) -> tuple[CatalogRecordProvenance, ...]:
    """Parsuje rekordy ``gmd:MD_Metadata`` z odpowiedzi GetRecords/GetRecordById.

    ``response_sha256`` wskazuje zapisany artefakt odpowiedzi; domyślnie jest to
    SHA przekazanej treści.
    """
    try:
        root = parse_xml_root(content, max_nodes=200_000)
    except RuContractError as exc:
        raise PogValidationError(f"Niepoprawna odpowiedź CSW: {exc}") from exc
    response_sha = response_sha256 or hashlib.sha256(content).hexdigest()
    records: list[CatalogRecordProvenance] = []
    for metadata in root.iter(f"{{{GMD}}}MD_Metadata"):
        record_id = _string(metadata.find(f"{{{GMD}}}fileIdentifier"))
        if not record_id:
            raise PogValidationError("Rekord CSW nie ma gmd:fileIdentifier.")
        citation = metadata.find(f".//{{{GMD}}}identificationInfo//{{{GMD}}}citation/{{{GMD}}}CI_Citation")
        dates = _citation_dates(citation)
        references = tuple(
            dict.fromkeys(
                url.strip()
                for node in metadata.iter(f"{{{GMD}}}URL")
                if (url := node.text) and url.strip()
            )
        )
        records.append(
            CatalogRecordProvenance(
                record_id=record_id,
                resource_identifier=_string(
                    citation.find(f"{{{GMD}}}identifier//{{{GMD}}}code") if citation is not None else None
                ),
                title=_string(citation.find(f"{{{GMD}}}title") if citation is not None else None),
                publication_date=dates.get("publication"),
                revision_date=dates.get("revision"),
                creation_date=dates.get("creation"),
                date_stamp=_date(_string(metadata.find(f"{{{GMD}}}dateStamp"))),
                metadata_url=csw_record_url(csw_url, record_id) if csw_url else None,
                references=references,
                record_sha256=_canonical_sha(metadata),
                response_sha256=response_sha,
                fetched_at=fetched_at,
            )
        )
    return tuple(records)


def fetch_act_metadata(
    client: OgcClient,
    csw_url: str,
    teryt: str,
) -> CswMetadataResult:
    """Pobiera rekordy ISO dla TERYT; błąd CSW nie przerywa importu.

    Awaria jest zwracana jawnie (``error`` + provenance), aby snapshot zapisał
    niepełny wynik zamiast udawać brak metadanych.
    """
    if not teryt.isdigit():
        return CswMetadataResult((), b"", None, False, "csw_invalid_teryt")
    try:
        result = client.fetch_csw(
            csw_url,
            output_schema=ISO_OUTPUT_SCHEMA,
            max_records=20,
            extra_params={
                "constraintLanguage": "CQL_TEXT",
                "constraint_language_version": "1.1.0",
                "constraint": f"dc:title like '%({teryt})%'",
            },
        )
    except OgcError as exc:
        return CswMetadataResult((), b"", exc.source, False, f"csw_unavailable:{type(exc).__name__}")
    fetched_at = result.source.fetched_at or datetime.now(timezone.utc)
    # Klient zwraca rekordy SearchResults osobno; artefakt to ich deterministyczne
    # złączenie i to jego SHA jest zapisywany jako ``response_sha256``.
    response_sha = hashlib.sha256(result.artifact).hexdigest()
    try:
        records = tuple(
            record
            for item in result.features
            for record in parse_iso_records(
                item, csw_url=csw_url, fetched_at=fetched_at, response_sha256=response_sha
            )
        )
    except PogValidationError as exc:
        return CswMetadataResult((), result.artifact, result.source, False, f"csw_invalid:{exc}")
    return CswMetadataResult(records, result.artifact, result.source, result.complete)


def attach_metadata(
    acts: tuple[PogActRecord, ...],
    records: tuple[CatalogRecordProvenance, ...],
) -> tuple[tuple[PogActRecord, ...], tuple[str, ...]]:
    """Przypina rekordy CSW do aktów po identyfikatorze zasobu (nie po tytule)."""
    warnings: list[str] = []
    linked: list[PogActRecord] = []
    for act in acts:
        identifier = act.catalog_resource_identifier
        matches = tuple(
            record
            for record in records
            if identifier
            and record.resource_identifier
            and record.resource_identifier.rstrip("/") == identifier.rstrip("/")
        )
        if not matches:
            warnings.append(f"csw_metadata_unresolved:{act.act_identifier}")
        linked.append(replace(act, metadata=matches))
    return tuple(linked), tuple(warnings)


def _citation_dates(citation: ElementTree.Element | None) -> dict[str, date | None]:
    result: dict[str, date | None] = {}
    if citation is None:
        return result
    for ci_date in citation.iter(f"{{{GMD}}}CI_Date"):
        code = ci_date.find(f".//{{{GMD}}}CI_DateTypeCode")
        kind = code.get("codeListValue") if code is not None else None
        date_node = ci_date.find(f"{{{GMD}}}date")
        value = next(
            (node.text for node in (date_node if date_node is not None else ()) if node.text),
            None,
        )
        if kind and value:
            result[kind] = _date(value)
    return result


def _string(element: ElementTree.Element | None) -> str | None:
    if element is None:
        return None
    text = " ".join("".join(element.itertext()).split())
    return text or None


def _date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _canonical_sha(element: ElementTree.Element) -> str:
    canonical = ElementTree.canonicalize(
        ElementTree.tostring(element, encoding="unicode"),
        strip_text=True,
        rewrite_prefixes=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
