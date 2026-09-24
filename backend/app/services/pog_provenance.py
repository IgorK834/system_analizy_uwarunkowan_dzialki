"""Łańcuch provenance POG dla wyniku analizy (BK-107).

Buduje ``PogActResult`` z wersji aktu zapisanej w przypiętym wydaniu:
cecha → akt/wersja → metadane CSW → dokumenty formalne z SHA-256. Wszystkie
wartości pochodzą ze snapshotu importu (PostGIS), a oficjalne URL-e GML są
składane z usługi, z której obiekt faktycznie pobrano — bez odpytywania
bieżącego katalogu czy usług RU.
"""

from __future__ import annotations

from typing import Any, Final

from app.modules.imports.domain.pog import PogObjectId
from app.modules.imports.infrastructure.pog.csw_metadata import (
    FEATURE_TYPE_NAMES,
    ru_object_gml_url,
)
from app.schemas.analyze import PogActResult
from app.schemas.source import CatalogMetadataSource, FormalDocumentSource

DOCUMENT_WARNINGS: Final[dict[str, str]] = {
    "superseded": "Dokument jest nieaktualny — został uchylony.",
    "unavailable": (
        "Dokument niedostępny: akt go wskazuje, ale rekordu dokumentu nie ma w "
        "pobranych danych Rejestru Urbanistycznego."
    ),
    "unresolved": (
        "Nierozstrzygnięte powiązanie z tą wersją aktu — dokumentu nie przypisano "
        "do wyniku."
    ),
}
UNVERIFIED_LINK_WARNING: Final[str] = (
    "Link do dokumentu nie jest zweryfikowanym adresem HTTPS i nie jest klikalny."
)
RELATION_LABELS_PL: Final[dict[str, str]] = {
    "przystapienie": "przystąpienie do sporządzenia",
    "uchwala": "uchwalenie",
    "zmienia": "zmiana",
    "uchyla": "uchylenie",
    "uniewaznia": "unieważnienie",
}


def object_id_from_identifier(identifier: str | None, version: str | None) -> PogObjectId | None:
    """Odtwarza idIIP z ``przestrzenNazw/lokalnyId``; inne formaty dają ``None``."""
    if not identifier or identifier.count("/") < 2:
        return None
    namespace, local_id = identifier.rsplit("/", 1)
    return PogObjectId(namespace=namespace, local_id=local_id, version_id=version)


def feature_gml_url(
    source_reference: str | None,
    feature_type: str,
    identifier: str | None,
    version: str | None,
) -> str | None:
    type_name = FEATURE_TYPE_NAMES.get(feature_type)
    if type_name is None:
        return None
    return ru_object_gml_url(
        source_reference, type_name, object_id_from_identifier(identifier, version)
    )


def document_source(document: dict[str, Any]) -> FormalDocumentSource:
    resolution = document.get("resolution_status") or "resolved"
    if resolution in {"unavailable", "unresolved"}:
        status = resolution
    elif document.get("repeal_date") is not None:
        status = "superseded"
    else:
        status = "current"
    warning = DOCUMENT_WARNINGS.get(status)
    if status == "unresolved" and document.get("resolution_note"):
        warning = f"{warning} {document['resolution_note']}"
    source = FormalDocumentSource(
        document_identifier=str(document["document_identifier"]),
        document_version=document.get("document_version"),
        publication_id=document.get("publication_id"),
        title=document.get("title"),
        short_name=document.get("short_name"),
        identification_number=document.get("identification_number"),
        relation=document.get("relation"),
        document_date=document.get("document_date"),
        effective_date=document.get("effective_date"),
        repeal_date=document.get("repeal_date"),
        link=document.get("link"),
        link_verified=bool(document.get("link_verified")),
        record_sha256=document.get("record_sha256"),
        status=status,  # type: ignore[arg-type]
        warning=warning,
    )
    if source.link and not source.link_verified:
        source = source.model_copy(
            update={
                "warning": " ".join(
                    part for part in (source.warning, UNVERIFIED_LINK_WARNING) if part
                )
            }
        )
    return source


def act_result_from_provenance(provenance: dict[str, Any]) -> PogActResult:
    """Mapuje zamrożony łańcuch z PostGIS na kontrakt ``PogActResult``."""
    identifier = str(provenance["act_identifier"])
    version = provenance.get("object_version_id")
    records = [CatalogMetadataSource(**record) for record in provenance.get("metadata", [])]
    metadata = records[0] if records else None
    return PogActResult(
        id=identifier,
        version=version,
        title=provenance.get("title"),
        resolution_number=provenance.get("resolution_number"),
        resolution_date=provenance.get("resolution_date"),
        act_identifier=identifier,
        act_version=version,
        publication_id=provenance.get("publication_id"),
        version_started_at=provenance.get("version_started_at"),
        publication_date=metadata.publication_date if metadata else None,
        valid_from=provenance.get("legal_valid_from"),
        valid_to=provenance.get("legal_valid_to"),
        gml_url=ru_object_gml_url(
            provenance.get("source_reference"),
            FEATURE_TYPE_NAMES["planning_act"],
            object_id_from_identifier(identifier, version),
        ),
        card_url=metadata.metadata_url if metadata else None,
        data_release_id=provenance.get("data_release_id"),
        release_label=provenance.get("release_label"),
        artifact_sha256=provenance.get("artifact_sha256"),
        fetched_at=provenance.get("artifact_fetched_at"),
        metadata=metadata,
        formal_documents=[document_source(doc) for doc in provenance.get("documents", [])],
    )
