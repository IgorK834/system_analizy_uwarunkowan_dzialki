"""Import oficjalnych słowników adresowych off-line GUGiK do PostGIS.

Usługa SLNOFF publikuje pełne i przyrostowe paczki ZIP. Importer:

1. pobiera manifest SOAP dla jawnego zakresu TERYT,
2. akceptuje paczki wyłącznie z oficjalnego hosta GUGiK i z limitem rozmiaru,
3. zachowuje artefakt oraz SHA-256,
4. buduje nowe ``data_release`` poza aktywnym zbiorem,
5. publikuje wydanie atomowo dopiero po kontroli jakości.

Wyszukiwanie HTTP nigdy nie uruchamia synchronizacji. Narzędzie działa jako
osobny proces utrzymaniowy: ``python -m ...dictionary_import sync``.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import sys
import zipfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import IO
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.core.data_sources import DataSourceEntry, ensure_source_runnable
from app.core.settings import settings
from app.db.session import SessionLocal
from app.models.versioned import DataRelease, DataSource, ImportRun, SourceArtifact
from app.modules.location.domain.normalization import fold_text

SOURCE_ID = "prg_address_dictionary"
IMPORTER_VERSION = "address-index/2"
_SOAP_NAMESPACE = "http://gugik.gov.pl/schemas/slowniki-offline-service/1.0"
_SOAP_ENVELOPE = "http://schemas.xmlsoap.org/soap/envelope/"
_ALLOWED_PACKAGE_HOSTS = frozenset({"mapy.geoportal.gov.pl"})
_RESULT_TYPES = ("city", "street", "house_number")


class AddressIndexImportError(RuntimeError):
    """Kontrolowany błąd manifestu, paczki lub kontroli jakości importu."""


@dataclass(frozen=True)
class UpdatePackage:
    url: str
    incremental: bool
    dictionary_type: str
    published_at: datetime | None


@dataclass(frozen=True)
class UpdateManifest:
    version_id: str
    packages: tuple[UpdatePackage, ...]


@dataclass(frozen=True)
class AddressRecord:
    source_object_id: str
    source_version: str | None
    city: str
    street: str | None
    house_number: str
    postal_code: str | None
    municipality: str | None
    county: str | None
    voivodeship: str | None
    teryt: str | None
    simc: str | None
    ulic: str | None
    x: float | None
    y: float | None
    valid_from: datetime | None
    valid_to: datetime | None
    active: bool

    @property
    def label(self) -> str:
        location = f"{self.city}, "
        street = f"{self.street} " if self.street else ""
        return f"{location}{street}{self.house_number}".strip()

    @property
    def normalized_label(self) -> str:
        searchable = " ".join(
            value
            for value in (
                self.label,
                self.postal_code,
                self.municipality,
                self.county,
                self.voivodeship,
            )
            if value
        )
        return " ".join(fold_text(searchable).split())


def _local_name(tag: str) -> str:
    return tag.rsplit("}", maxsplit=1)[-1]


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


def parse_update_manifest(xml: bytes) -> UpdateManifest:
    """Parsuje odpowiedź SOAP bez zależności od konkretnego prefiksu namespace."""
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise AddressIndexImportError("Manifest SOAP nie jest poprawnym XML.") from exc

    fault = next(
        (element for element in root.iter() if _local_name(element.tag) == "Fault"),
        None,
    )
    if fault is not None:
        raise AddressIndexImportError("Usługa słowników zwróciła SOAP Fault.")

    update_list = next(
        (
            element
            for element in root.iter()
            if _local_name(element.tag) == "updateList"
        ),
        None,
    )
    if update_list is None:
        raise AddressIndexImportError("Manifest nie zawiera updateList.")
    version_id = _clean(update_list.attrib.get("verId"))
    if not version_id:
        raise AddressIndexImportError("Manifest nie zawiera identyfikatora verId.")

    packages: list[UpdatePackage] = []
    for element in update_list:
        if _local_name(element.tag) != "update":
            continue
        url = _clean(element.attrib.get("url"))
        dictionary_type = _clean(element.attrib.get("sln"))
        if not url or not dictionary_type:
            raise AddressIndexImportError("Niekompletny wpis update w manifeście.")
        packages.append(
            UpdatePackage(
                url=url,
                incremental=element.attrib.get("incr", "false").lower() == "true",
                dictionary_type=dictionary_type,
                published_at=_parse_datetime(element.attrib.get("dt")),
            )
        )
    return UpdateManifest(version_id=version_id, packages=tuple(packages))


def parse_address_xml(stream: IO[bytes]) -> Iterator[AddressRecord]:
    """Strumieniowo odczytuje pełne/przyrostowe rekordy ``sln:adres``."""
    try:
        iterator = ElementTree.iterparse(stream, events=("end",))
        for _, element in iterator:
            if _local_name(element.tag) != "adres":
                continue
            values = {
                _local_name(child.tag): _clean(child.text)
                for child in element
                if child.text is not None
            }
            record = _record_from_values(values)
            if record is not None:
                yield record
            element.clear()
    except ElementTree.ParseError as exc:
        raise AddressIndexImportError("Paczka zawiera niepoprawny XML.") from exc


def _record_from_values(values: dict[str, str | None]) -> AddressRecord | None:
    source_id = values.get("pktPrgIIPId") or values.get("pktEmuiaIIPId")
    city = values.get("miejscNazwa")
    number = values.get("pktNumer")
    if not source_id or not city or not number:
        return None

    namespace = values.get("pktPrgIIPPn") or values.get("pktEmuiaIIPPn") or "GUGiK"
    valid_to = _parse_datetime(values.get("cyklZyciaDo") or values.get("waznyDo"))
    status = fold_text(values.get("pktStatus") or "").strip()
    inactive_status = status in {
        "nieistniejacy",
        "zniesiony",
        "usuniety",
        "historical",
        "deleted",
    }
    x = _finite_float(values.get("pktX"))
    y = _finite_float(values.get("pktY"))
    active = valid_to is None and not inactive_status

    return AddressRecord(
        source_object_id=f"{namespace}:{source_id}",
        source_version=values.get("pktPrgIIPWersja")
        or values.get("pktEmuiaIIPWersja"),
        city=city,
        street=values.get("ulNazwaGlowna") or values.get("ulNazwaCzesc"),
        house_number=number,
        postal_code=values.get("pktKodPocztowy"),
        municipality=values.get("gmNazwa"),
        county=values.get("powNazwa"),
        voivodeship=values.get("wojNazwa"),
        teryt=values.get("gmIdTeryt"),
        simc=values.get("miejscIdTeryt"),
        ulic=values.get("ulIdTeryt"),
        x=x,
        y=y,
        valid_from=_parse_datetime(values.get("cyklZyciaOd") or values.get("waznyOd")),
        valid_to=valid_to,
        active=active and x is not None and y is not None,
    )


def _finite_float(value: str | None) -> float | None:
    if not value:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    if not math.isfinite(parsed):
        return None
    # Luźna osłona dla EPSG:2180. Dokładny zasięg Polski sprawdza QA PostGIS.
    if not 0.0 <= parsed <= 1_000_000.0:
        return None
    return parsed


def iter_address_records_from_zip(
    payload: bytes, *, max_uncompressed_bytes: int
) -> Iterator[AddressRecord]:
    """Waliduje ZIP i zwraca rekordy ze wszystkich plików XML."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise AddressIndexImportError("Pobrana paczka nie jest poprawnym ZIP.") from exc

    with archive:
        xml_entries = []
        total_size = 0
        for info in archive.infolist():
            path = PurePosixPath(info.filename)
            if path.is_absolute() or ".." in path.parts:
                raise AddressIndexImportError("Paczka zawiera niedozwoloną ścieżkę.")
            total_size += info.file_size
            if total_size > max_uncompressed_bytes:
                raise AddressIndexImportError(
                    "Rozpakowana paczka przekracza limit bezpieczeństwa."
                )
            if not info.is_dir() and path.suffix.lower() == ".xml":
                xml_entries.append(info)
        if not xml_entries:
            raise AddressIndexImportError("Paczka nie zawiera pliku XML.")

        for info in xml_entries:
            with archive.open(info) as stream:
                yield from parse_address_xml(stream)


class AddressDictionaryClient:
    """Minimalny klient SOAP/HTTP oficjalnej usługi SLNOFF."""

    def __init__(
        self,
        endpoint: str = settings.address_dictionary_soap_url,
        *,
        connect_timeout: float = settings.address_index_connect_timeout_seconds,
        read_timeout: float = settings.address_index_read_timeout_seconds,
        max_package_bytes: int = settings.address_index_max_package_bytes,
    ) -> None:
        self._endpoint = endpoint
        self._timeout = httpx.Timeout(
            connect=connect_timeout,
            read=read_timeout,
            write=read_timeout,
            pool=connect_timeout,
        )
        self._max_package_bytes = max_package_bytes

    def full_manifest(self, teryt: str) -> UpdateManifest:
        return self._manifest("pobierzPelne", "teryt", teryt)

    def incremental_manifest(self, version_id: str) -> UpdateManifest:
        return self._manifest("pobierzPrzyrost", "verId", version_id)

    def _manifest(self, operation: str, parameter: str, value: str) -> UpdateManifest:
        body = (
            f'<soapenv:Envelope xmlns:soapenv="{_SOAP_ENVELOPE}" '
            f'xmlns:ns="{_SOAP_NAMESPACE}"><soapenv:Header/><soapenv:Body>'
            f"<ns:{operation}><ns:{parameter}>{_xml_escape(value)}</ns:{parameter}>"
            f"</ns:{operation}></soapenv:Body></soapenv:Envelope>"
        ).encode("utf-8")
        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.post(
                    self._endpoint,
                    content=body,
                    headers={
                        "Content-Type": "text/xml; charset=utf-8",
                        "SOAPAction": "",
                    },
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AddressIndexImportError(
                "Nie udało się pobrać manifestu słowników GUGiK."
            ) from exc
        return parse_update_manifest(response.content)

    def download(self, url: str) -> bytes:
        current = url
        try:
            with httpx.Client(timeout=self._timeout, follow_redirects=False) as client:
                for _ in range(4):
                    _validate_package_url(current)
                    with client.stream("GET", current) as response:
                        if response.is_redirect:
                            location = response.headers.get("location")
                            if not location:
                                raise AddressIndexImportError(
                                    "Przekierowanie paczki nie ma adresu docelowego."
                                )
                            current = urljoin(current, location)
                            continue
                        response.raise_for_status()
                        declared = response.headers.get("content-length")
                        if declared and int(declared) > self._max_package_bytes:
                            raise AddressIndexImportError(
                                "Paczka przekracza limit rozmiaru."
                            )
                        chunks: list[bytes] = []
                        size = 0
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            if size > self._max_package_bytes:
                                raise AddressIndexImportError(
                                    "Paczka przekracza limit rozmiaru."
                                )
                            chunks.append(chunk)
                        return b"".join(chunks)
                raise AddressIndexImportError("Zbyt wiele przekierowań paczki.")
        except (httpx.HTTPError, ValueError) as exc:
            if isinstance(exc, AddressIndexImportError):
                raise
            raise AddressIndexImportError(
                "Nie udało się pobrać paczki słowników GUGiK."
            ) from exc


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _validate_package_url(url: str) -> None:
    parsed = urlparse(url)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in _ALLOWED_PACKAGE_HOSTS
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 80, 443)
    ):
        raise AddressIndexImportError("Manifest wskazuje niedozwolony adres paczki.")


_UPSERT_HOUSE = text(
    """
    INSERT INTO address_search_entries (
        data_release_id, source_artifact_id, source_object_id, source_version,
        result_type, label, normalized_label, country, voivodeship, county,
        municipality, city, street, house_number, postal_code, teryt, simc,
        ulic, geometry, valid_from, valid_to
    ) VALUES (
        :release_id, :artifact_id, :source_object_id, :source_version,
        'house_number', :label, :normalized_label, 'Polska', :voivodeship,
        :county, :municipality, :city, :street, :house_number, :postal_code,
        -- W słownikach GUGiK pktX jest współrzędną północną, a pktY wschodnią.
        -- PostGIS POINT używa kolejności easting,northing, więc osie są jawnie
        -- odwrócone przy zapisie do kanonicznego EPSG:2180.
        :teryt, :simc, :ulic, ST_SetSRID(ST_MakePoint(:y, :x), 2180),
        :valid_from, NULL
    )
    ON CONFLICT (data_release_id, source_object_id, result_type) DO UPDATE SET
        source_artifact_id = EXCLUDED.source_artifact_id,
        source_version = EXCLUDED.source_version,
        label = EXCLUDED.label,
        normalized_label = EXCLUDED.normalized_label,
        voivodeship = EXCLUDED.voivodeship,
        county = EXCLUDED.county,
        municipality = EXCLUDED.municipality,
        city = EXCLUDED.city,
        street = EXCLUDED.street,
        house_number = EXCLUDED.house_number,
        postal_code = EXCLUDED.postal_code,
        teryt = EXCLUDED.teryt,
        simc = EXCLUDED.simc,
        ulic = EXCLUDED.ulic,
        geometry = EXCLUDED.geometry,
        valid_from = EXCLUDED.valid_from,
        valid_to = NULL
    """
)

_DELETE_OBJECTS = text(
    """
    DELETE FROM address_search_entries
    WHERE data_release_id = :release_id
      AND result_type = 'house_number'
      AND source_object_id = ANY(:source_object_ids)
    """
)

_CLONE_RELEASE = text(
    """
    INSERT INTO address_search_entries (
        data_release_id, source_artifact_id, source_object_id, source_version,
        result_type, label, normalized_label, country, voivodeship, county,
        municipality, city, street, house_number, postal_code, teryt, simc,
        ulic, geometry, valid_from, valid_to
    )
    SELECT
        :new_release_id, source_artifact_id, source_object_id, source_version,
        result_type, label, normalized_label, country, voivodeship, county,
        municipality, city, street, house_number, postal_code, teryt, simc,
        ulic, geometry, valid_from, valid_to
    FROM address_search_entries
    WHERE data_release_id = :old_release_id
    """
)

_FOLD_SQL = (
    "translate(lower({value}), "
    "'ąćęłńóśźżĄĆĘŁŃÓŚŹŻ', 'acelnoszzacelnoszz')"
)


class AddressIndexImporter:
    """Publikuje pełne lub przyrostowe wydanie lokalnego indeksu."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session] = SessionLocal,
        client: AddressDictionaryClient | None = None,
        batch_size: int = settings.address_index_import_batch_size,
        max_uncompressed_bytes: int = settings.address_index_max_uncompressed_bytes,
    ) -> None:
        self._session_factory = session_factory
        self._client = client or AddressDictionaryClient()
        self._batch_size = batch_size
        self._max_uncompressed_bytes = max_uncompressed_bytes

    def sync(self, scopes: Sequence[str], mode: str = "full") -> dict[str, object]:
        normalized_scopes = _validate_scopes(scopes)
        if mode not in {"full", "incremental"}:
            raise ValueError("Tryb musi mieć wartość full albo incremental.")
        catalog_entry = ensure_source_runnable(SOURCE_ID)
        source_id = self._ensure_data_source(catalog_entry)
        previous = self._active_state(source_id)
        if mode == "incremental" and previous is None:
            raise AddressIndexImportError(
                "Brak aktywnego wydania. Najpierw wykonaj import pełny."
            )

        manifests = self._manifests(normalized_scopes, mode, previous)
        version_label = _release_label(normalized_scopes, manifests)
        existing = self._existing_release(source_id, version_label)
        if existing is not None:
            return {
                "status": "unchanged",
                "release_id": existing,
                "version_label": version_label,
            }

        run_id = self._start_run(source_id, mode, normalized_scopes)
        try:
            result = self._build_release(
                source_id=source_id,
                run_id=run_id,
                scopes=normalized_scopes,
                manifests=manifests,
                version_label=version_label,
                previous=previous,
                incremental=mode == "incremental",
            )
        except Exception as exc:
            self._fail_run(run_id, exc)
            raise
        return result

    def _ensure_data_source(self, catalog_entry: DataSourceEntry) -> int:
        with self._session_factory.begin() as db:
            source = db.query(DataSource).filter_by(source_id=SOURCE_ID).one_or_none()
            if source is None:
                source = DataSource(
                    source_id=SOURCE_ID,
                    owner=catalog_entry.owner,
                    status=catalog_entry.status.value,
                    access_type=catalog_entry.access_type.value,
                    license=catalog_entry.license,
                    attribution=catalog_entry.attribution,
                )
                db.add(source)
                db.flush()
            else:
                source.owner = catalog_entry.owner
                source.status = catalog_entry.status.value
                source.access_type = catalog_entry.access_type.value
                source.license = catalog_entry.license
                source.attribution = catalog_entry.attribution
            return source.id

    def _active_state(self, source_id: int) -> tuple[int, dict[str, str]] | None:
        with self._session_factory() as db:
            row = db.execute(
                text(
                    """
                    SELECT dr.id, ir.checkpoint
                    FROM data_releases dr
                    LEFT JOIN LATERAL (
                        SELECT checkpoint
                        FROM import_runs
                        WHERE data_release_id = dr.id AND status = 'succeeded'
                        ORDER BY id DESC LIMIT 1
                    ) ir ON true
                    WHERE dr.data_source_id = :source_id AND dr.is_active
                    """
                ),
                {"source_id": source_id},
            ).mappings().one_or_none()
            if row is None:
                return None
            checkpoint = row["checkpoint"] or {}
            return int(row["id"]), dict(checkpoint.get("scope_versions", {}))

    def _manifests(
        self,
        scopes: tuple[str, ...],
        mode: str,
        previous: tuple[int, dict[str, str]] | None,
    ) -> dict[str, UpdateManifest]:
        manifests: dict[str, UpdateManifest] = {}
        for scope in scopes:
            if mode == "full":
                manifests[scope] = self._client.full_manifest(scope)
                continue
            assert previous is not None
            version_id = previous[1].get(scope)
            if not version_id:
                raise AddressIndexImportError(
                    f"Aktywne wydanie nie ma checkpointu dla TERYT {scope}."
                )
            manifests[scope] = self._client.incremental_manifest(version_id)
        return manifests

    def _existing_release(self, source_id: int, version_label: str) -> int | None:
        with self._session_factory() as db:
            return db.execute(
                text(
                    "SELECT id FROM data_releases "
                    "WHERE data_source_id=:source_id AND version_label=:version"
                ),
                {"source_id": source_id, "version": version_label},
            ).scalar_one_or_none()

    def _start_run(self, source_id: int, mode: str, scopes: Sequence[str]) -> int:
        with self._session_factory.begin() as db:
            run = ImportRun(
                data_source_id=source_id,
                status="running",
                importer_version=IMPORTER_VERSION,
                stats={"mode": mode, "scopes": list(scopes)},
                started_at=datetime.now(timezone.utc),
            )
            db.add(run)
            db.flush()
            return run.id

    def _build_release(
        self,
        *,
        source_id: int,
        run_id: int,
        scopes: tuple[str, ...],
        manifests: dict[str, UpdateManifest],
        version_label: str,
        previous: tuple[int, dict[str, str]] | None,
        incremental: bool,
    ) -> dict[str, object]:
        counters = {"packages": 0, "upserted": 0, "deleted": 0}
        with self._session_factory.begin() as db:
            published_at = max(
                (
                    package.published_at
                    for manifest in manifests.values()
                    for package in manifest.packages
                    if package.published_at is not None
                ),
                default=datetime.now(timezone.utc),
            )
            release = DataRelease(
                data_source_id=source_id,
                version_label=version_label,
                published_at=published_at,
                is_active=False,
                importer_version=IMPORTER_VERSION,
            )
            db.add(release)
            db.flush()
            db.query(ImportRun).filter_by(id=run_id).update(
                {"data_release_id": release.id}
            )

            if incremental and previous is not None:
                db.execute(
                    _CLONE_RELEASE,
                    {
                        "old_release_id": previous[0],
                        "new_release_id": release.id,
                    },
                )

            for scope in scopes:
                manifest = manifests[scope]
                address_packages = sorted(
                    (
                        package
                        for package in manifest.packages
                        if package.dictionary_type == "adr"
                    ),
                    key=lambda package: package.published_at
                    or datetime.min.replace(tzinfo=timezone.utc),
                )
                if not address_packages:
                    if incremental:
                        # Brak paczek w odpowiedzi pobierzPrzyrost oznacza, że
                        # źródło nie opublikowało zmian od checkpointu.
                        continue
                    raise AddressIndexImportError(
                        f"Manifest TERYT {scope} nie zawiera paczki adresów."
                    )
                if any(not package.incremental for package in address_packages):
                    db.execute(
                        text(
                            "DELETE FROM address_search_entries "
                            "WHERE data_release_id=:release_id "
                            "AND (teryt LIKE :scope_prefix OR teryt IS NULL)"
                        ),
                        {"release_id": release.id, "scope_prefix": f"{scope}%"},
                    )

                for package in address_packages:
                    payload = self._client.download(package.url)
                    artifact_id = self._artifact(db, source_id, package.url, payload)
                    counters["packages"] += 1
                    self._apply_package(
                        db, release.id, artifact_id, payload, counters
                    )

            self._rebuild_parent_entries(db, release.id)
            counts = self._quality_check(db, release.id)
            counters.update(counts)

            db.execute(
                text(
                    "UPDATE data_releases SET is_active=false "
                    "WHERE data_source_id=:source_id AND is_active"
                ),
                {"source_id": source_id},
            )
            release.is_active = True
            run = db.query(ImportRun).filter_by(id=run_id).one()
            run.status = "succeeded"
            run.finished_at = datetime.now(timezone.utc)
            run.stats = counters
            run.checkpoint = {
                "scopes": list(scopes),
                "scope_versions": {
                    scope: manifest.version_id for scope, manifest in manifests.items()
                },
            }
            return {
                "status": "published",
                "release_id": release.id,
                "version_label": version_label,
                **counters,
            }

    def _artifact(
        self, db: Session, source_id: int, url: str, payload: bytes
    ) -> int:
        digest = hashlib.sha256(payload).hexdigest()
        existing = db.query(SourceArtifact).filter_by(
            data_source_id=source_id, content_hash=digest
        ).one_or_none()
        if existing is not None:
            return existing.id
        artifact = SourceArtifact(
            data_source_id=source_id,
            uri=url,
            media_type="application/zip",
            content_hash=digest,
            size_bytes=len(payload),
            fetched_at=datetime.now(timezone.utc),
        )
        db.add(artifact)
        db.flush()
        return artifact.id

    def _apply_package(
        self,
        db: Session,
        release_id: int,
        artifact_id: int | None,
        payload: bytes,
        counters: dict[str, int],
    ) -> None:
        upserts: list[dict[str, object]] = []
        deletes: list[str] = []
        records = iter_address_records_from_zip(
            payload, max_uncompressed_bytes=self._max_uncompressed_bytes
        )
        for record in records:
            if not record.active:
                deletes.append(record.source_object_id)
                if len(deletes) >= self._batch_size:
                    self._delete_batch(db, release_id, deletes)
                    counters["deleted"] += len(deletes)
                    deletes.clear()
                continue
            assert record.x is not None and record.y is not None
            upserts.append(
                {
                    "release_id": release_id,
                    "artifact_id": artifact_id,
                    "source_object_id": record.source_object_id,
                    "source_version": record.source_version,
                    "label": record.label,
                    "normalized_label": record.normalized_label,
                    "voivodeship": record.voivodeship,
                    "county": record.county,
                    "municipality": record.municipality,
                    "city": record.city,
                    "street": record.street,
                    "house_number": record.house_number,
                    "postal_code": record.postal_code,
                    "teryt": record.teryt,
                    "simc": record.simc,
                    "ulic": record.ulic,
                    "x": record.x,
                    "y": record.y,
                    "valid_from": record.valid_from,
                }
            )
            if len(upserts) >= self._batch_size:
                db.execute(_UPSERT_HOUSE, upserts)
                counters["upserted"] += len(upserts)
                upserts.clear()
        if upserts:
            db.execute(_UPSERT_HOUSE, upserts)
            counters["upserted"] += len(upserts)
        if deletes:
            self._delete_batch(db, release_id, deletes)
            counters["deleted"] += len(deletes)

    @staticmethod
    def _delete_batch(db: Session, release_id: int, source_ids: list[str]) -> None:
        db.execute(
            _DELETE_OBJECTS,
            {"release_id": release_id, "source_object_ids": source_ids},
        )

    @staticmethod
    def _rebuild_parent_entries(db: Session, release_id: int) -> None:
        db.execute(
            text(
                "DELETE FROM address_search_entries WHERE data_release_id=:release_id "
                "AND result_type IN ('city', 'street')"
            ),
            {"release_id": release_id},
        )
        folded_city = _FOLD_SQL.format(
            value=(
                "concat_ws(' ', city, max(municipality), max(county), "
                "max(voivodeship))"
            )
        )
        db.execute(
            text(
                f"""
                INSERT INTO address_search_entries (
                    data_release_id, source_object_id, result_type, label,
                    normalized_label, country, voivodeship, county, municipality,
                    city, teryt, simc, geometry
                )
                SELECT
                    :release_id,
                    'city:' || md5(concat_ws('|', max(teryt), simc, city)),
                    'city', city, {folded_city}, 'Polska',
                    max(voivodeship), max(county), max(municipality), city,
                    max(teryt), simc, ST_Centroid(ST_Collect(geometry))
                FROM address_search_entries
                WHERE data_release_id=:release_id
                  AND result_type='house_number' AND city IS NOT NULL
                GROUP BY city, simc
                """
            ),
            {"release_id": release_id},
        )
        folded_street = _FOLD_SQL.format(
            value=(
                "concat_ws(' ', city, street, max(municipality), max(county), "
                "max(voivodeship))"
            )
        )
        db.execute(
            text(
                f"""
                INSERT INTO address_search_entries (
                    data_release_id, source_object_id, result_type, label,
                    normalized_label, country, voivodeship, county, municipality,
                    city, street, teryt, simc, ulic, geometry
                )
                SELECT
                    :release_id,
                    'street:' || md5(concat_ws('|', max(teryt), simc, ulic, street)),
                    'street', city || ', ' || street, {folded_street}, 'Polska',
                    max(voivodeship), max(county), max(municipality), city, street,
                    max(teryt), simc, ulic, ST_Centroid(ST_Collect(geometry))
                FROM address_search_entries
                WHERE data_release_id=:release_id
                  AND result_type='house_number' AND street IS NOT NULL
                GROUP BY city, street, simc, ulic
                """
            ),
            {"release_id": release_id},
        )

    @staticmethod
    def _quality_check(db: Session, release_id: int) -> dict[str, int]:
        counts = {
            result_type: int(total)
            for result_type, total in db.execute(
                text(
                    "SELECT result_type, count(*) AS count "
                    "FROM address_search_entries WHERE data_release_id=:release_id "
                    "GROUP BY result_type"
                ),
                {"release_id": release_id},
            )
        }
        if counts.get("house_number", 0) == 0:
            raise AddressIndexImportError(
                "Kontrola jakości odrzuciła wydanie bez aktywnych adresów."
            )
        invalid = db.execute(
            text(
                "SELECT count(*) FROM address_search_entries "
                "WHERE data_release_id=:release_id "
                "AND (ST_IsEmpty(geometry) OR ST_SRID(geometry) <> 2180)"
            ),
            {"release_id": release_id},
        ).scalar_one()
        if invalid:
            raise AddressIndexImportError(
                "Kontrola jakości wykryła niepoprawne geometrie indeksu."
            )
        return {f"{kind}_count": counts.get(kind, 0) for kind in _RESULT_TYPES}

    def _fail_run(self, run_id: int, exc: Exception) -> None:
        with self._session_factory.begin() as db:
            run = db.query(ImportRun).filter_by(id=run_id).one_or_none()
            if run is None:
                return
            run.status = "failed"
            run.finished_at = datetime.now(timezone.utc)
            current = dict(run.stats or {})
            current["error_type"] = type(exc).__name__
            run.stats = current


def _validate_scopes(scopes: Sequence[str]) -> tuple[str, ...]:
    values = tuple(dict.fromkeys(scope.strip() for scope in scopes if scope.strip()))
    if not values:
        raise ValueError("Podaj co najmniej jeden zakres TERYT.")
    for value in values:
        if not value.isdigit() or len(value) not in {2, 4, 7}:
            raise ValueError(
                f"TERYT {value!r} musi mieć 2, 4 albo 7 cyfr."
            )
    return values


def _release_label(
    scopes: Sequence[str], manifests: dict[str, UpdateManifest]
) -> str:
    canonical = json.dumps(
        {
            "importer_version": IMPORTER_VERSION,
            "scope_versions": {
                scope: manifests[scope].version_id for scope in sorted(scopes)
            },
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]
    return f"sln-{digest}"


def _default_scopes() -> list[str]:
    return [
        item.strip()
        for item in settings.address_index_teryt_scopes.split(",")
        if item.strip()
    ]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Lokalny indeks adresowy GUGiK")
    subparsers = parser.add_subparsers(dest="command", required=True)
    sync_parser = subparsers.add_parser("sync", help="Pobierz i opublikuj wydanie")
    sync_parser.add_argument(
        "--mode", choices=("full", "incremental"), default="full"
    )
    sync_parser.add_argument(
        "--scope",
        action="append",
        dest="scopes",
        help="TERYT województwa/powiatu/gminy; można podać wielokrotnie.",
    )
    subparsers.add_parser("status", help="Pokaż status aktywnego wydania")
    return parser


def _status() -> dict[str, object]:
    with SessionLocal() as db:
        row = db.execute(
            text(
                """
                SELECT dr.id, dr.version_label, dr.published_at,
                       count(ase.id) AS entry_count
                FROM data_sources ds
                JOIN data_releases dr ON dr.data_source_id=ds.id AND dr.is_active
                LEFT JOIN address_search_entries ase ON ase.data_release_id=dr.id
                WHERE ds.source_id=:source_id
                GROUP BY dr.id
                """
            ),
            {"source_id": SOURCE_ID},
        ).mappings().one_or_none()
        return {"ready": row is not None, **(dict(row) if row else {})}


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "status":
            result = _status()
        else:
            result = AddressIndexImporter().sync(
                args.scopes or _default_scopes(), mode=args.mode
            )
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0
    except (AddressIndexImportError, ValueError) as exc:
        print(f"Błąd indeksu adresowego: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
