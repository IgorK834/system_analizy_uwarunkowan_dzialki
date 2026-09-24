#!/usr/bin/env python3
"""Recznie odswieza offline fixtures kontraktow uslug RU.

Skrypt wykonuje prawdziwe zapytania HTTP, dlatego nie jest importowany przez
pytest ani uruchamiany w CI. Najpierw pobiera i waliduje komplet odpowiedzi w
pamieci, a dopiero potem atomowo podmienia pojedyncze pliki i manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from xml.etree import ElementTree

import httpx

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.modules.imports.infrastructure.pog.csw_metadata import (  # noqa: E402
    parse_iso_records,
)
from app.core.ru_contracts import (  # noqa: E402
    RuContractError,
    assert_csw_contract,
    assert_wfs_contract,
    assert_wfs_getfeature_contract,
    assert_wms_contract,
    parse_csw_capabilities,
    parse_wfs_capabilities,
    parse_wfs_getfeature,
    parse_wms_capabilities,
    trim_wfs_getfeature,
)

BASE_URL = "https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe"
# Próbka łańcucha provenance BK-107: Sopot ma komplet akt–dokument–strefa w
# zamrożonych GetFeature oraz rekordy POG i MPZP w CSW (test rozróżniania).
CSW_TERYT = "226401"
OFFICIAL_SERVICES_URL = "https://rejestr-urbanistyczny.gov.pl/uslugi-sieciowe"
APP_POG_NAMESPACE = (
    "https://www.gov.pl/static/zagospodarowanieprzestrzenne/schemas/app/3.0"
)
JPT_FILTER = (
    '<fes:Filter xmlns:fes="http://www.opengis.net/fes/2.0" '
    f'xmlns:app-pog="{APP_POG_NAMESPACE}">'
    '<fes:PropertyIsLike wildCard="%" singleChar="_" escapeChar="\\">'
    "<fes:ValueReference>"
    "app-pog:idIIP/app-pog:Identyfikator/app-pog:przestrzenNazw"
    "</fes:ValueReference>"
    "<fes:Literal>%246101%</fes:Literal>"
    "</fes:PropertyIsLike>"
    "</fes:Filter>"
)


@dataclass(frozen=True)
class RequestSpec:
    filename: str
    endpoint: str
    params: dict[str, str]
    service: str
    version: str
    validate: Callable[[bytes], None]
    transform: Callable[[bytes], bytes] | None = None


def _validate_wms(payload: bytes) -> None:
    assert_wms_contract(parse_wms_capabilities(payload))


def _validate_wfs(payload: bytes) -> None:
    assert_wfs_contract(parse_wfs_capabilities(payload))


def _validate_csw(payload: bytes) -> None:
    assert_csw_contract(parse_csw_capabilities(payload))


def _validate_csw_records(payload: bytes) -> None:
    records = parse_iso_records(payload)
    if not any(
        (record.resource_identifier or "").endswith(f"/{CSW_TERYT}-POG/") for record in records
    ):
        raise RuContractError("GetRecords CSW nie zawiera rekordu zbioru POG próbki.")


def _validate_getfeature(payload: bytes) -> None:
    assert_wfs_getfeature_contract(parse_wfs_getfeature(payload))


def _validate_xsd(payload: bytes) -> None:
    root = ElementTree.fromstring(payload)
    if root.tag != "{http://www.w3.org/2001/XMLSchema}schema":
        raise RuContractError("Odpowiedź nie jest schematem XML Schema.")


def _getfeature_spec(filename: str, type_name: str) -> RequestSpec:
    return RequestSpec(
        filename=filename,
        endpoint=f"{BASE_URL}/app-pog/wfs",
        params={
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": f"app-pog:{type_name}",
            "count": "1",
            "srsName": "EPSG:2180",
        },
        service="wfs",
        version="2.0.0",
        validate=_validate_getfeature,
    )


REQUESTS = (
    RequestSpec(
        filename="wms_pog_capabilities_1_3_0.xml",
        endpoint=f"{BASE_URL}/wms-pog/wms",
        params={"service": "WMS", "version": "1.3.0", "request": "GetCapabilities"},
        service="wms",
        version="1.3.0",
        validate=_validate_wms,
    ),
    RequestSpec(
        filename="wfs_pog_capabilities_2_0_0.xml",
        endpoint=f"{BASE_URL}/app-pog/wfs",
        params={"service": "WFS", "version": "2.0.0", "request": "GetCapabilities"},
        service="wfs",
        version="2.0.0",
        validate=_validate_wfs,
    ),
    RequestSpec(
        filename="csw_capabilities_2_0_2.xml",
        endpoint=f"{BASE_URL}/csw",
        params={
            "service": "CSW",
            "version": "2.0.2",
            "request": "GetCapabilities",
        },
        service="csw",
        version="2.0.2",
        validate=_validate_csw,
    ),
    RequestSpec(
        filename="wfs_pog_getfeature_246101.xml",
        endpoint=f"{BASE_URL}/app-pog/wfs",
        params={
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": "app-pog:AktPlanowaniaPrzestrzennego",
            "count": "2",
            "srsName": "urn:ogc:def:crs:EPSG::2180",
            "namespaces": f"xmlns(app-pog,{APP_POG_NAMESPACE})",
            "FILTER": JPT_FILTER,
        },
        service="wfs",
        version="2.0.0",
        validate=_validate_getfeature,
        transform=trim_wfs_getfeature,
    ),
    RequestSpec(
        filename="wfs_pog_describe_feature_type_3_0.xsd",
        endpoint=f"{BASE_URL}/app-pog/wfs",
        params={
            "service": "WFS",
            "version": "2.0.0",
            "request": "DescribeFeatureType",
        },
        service="wfs",
        version="2.0.0",
        validate=_validate_xsd,
    ),
    RequestSpec(
        filename="planowaniePrzestrzenne_3_0.xsd",
        endpoint=(
            "https://www.gov.pl/static/zagospodarowanieprzestrzenne/"
            "schemas/app/3.0/planowaniePrzestrzenne_3_0.xsd"
        ),
        params={},
        service="wfs",
        version="2.0.0",
        validate=_validate_xsd,
    ),
    _getfeature_spec(
        "wfs_pog_getfeature_act.xml", "AktPlanowaniaPrzestrzennego"
    ),
    _getfeature_spec("wfs_pog_getfeature_document.xml", "DokumentFormalny"),
    _getfeature_spec(
        "wfs_pog_getfeature_osdis.xml",
        "ObszarStandardowDostepnosciInfrastrukturySpolecznej",
    ),
    _getfeature_spec(
        "wfs_pog_getfeature_ouz.xml", "ObszarUzupelnieniaZabudowy"
    ),
    _getfeature_spec(
        "wfs_pog_getfeature_ozs.xml", "ObszarZabudowySrodmiejskiej"
    ),
    _getfeature_spec("wfs_pog_getfeature_zone.xml", "StrefaPlanistyczna"),
    RequestSpec(
        filename="csw_getrecords_iso_226401.xml",
        endpoint=f"{BASE_URL}/csw",
        params={
            "service": "CSW",
            "version": "2.0.2",
            "request": "GetRecords",
            "resultType": "results",
            "elementSetName": "full",
            "typeNames": "csw:Record",
            "outputSchema": "http://www.isotc211.org/2005/gmd",
            "outputFormat": "application/xml",
            "maxRecords": "10",
            "startPosition": "1",
            "constraintLanguage": "CQL_TEXT",
            "constraint_language_version": "1.1.0",
            "constraint": f"dc:title like '%({CSW_TERYT})%'",
        },
        service="csw",
        version="2.0.2",
        validate=_validate_csw_records,
    ),
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pobiera i atomowo aktualizuje fixtures kontraktow WMS/WFS/CSW RU."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=BACKEND_DIR / "tests" / "fixtures" / "ru",
        help="Katalog docelowy (domyslnie backend/tests/fixtures/ru).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Timeout kazdego zapytania HTTP w sekundach (domyslnie 60).",
    )
    return parser.parse_args()


def _is_xml_content_type(value: str) -> bool:
    media_type = value.split(";", 1)[0].strip().lower()
    return media_type in {"application/xml", "text/xml"} or media_type.endswith("+xml")


def _fetch_one(client: httpx.Client, spec: RequestSpec) -> tuple[bytes, dict[str, str]]:
    response = client.get(spec.endpoint, params=spec.params)
    if response.status_code != 200:
        detail = response.text.strip().replace("\n", " ")[:500]
        raise RuContractError(
            f"{spec.filename}: oczekiwano HTTP 200, otrzymano "
            f"{response.status_code}: {detail}"
        )
    content_type = response.headers.get("content-type", "")
    if not _is_xml_content_type(content_type):
        raise RuContractError(
            f"{spec.filename}: oczekiwano Content-Type XML, otrzymano {content_type!r}."
        )
    if not response.content.strip():
        raise RuContractError(f"{spec.filename}: serwer zwrocil pusta odpowiedz.")

    payload = (
        spec.transform(response.content)
        if spec.transform is not None
        else response.content
    )
    spec.validate(payload)
    if spec.service == "wms":
        capabilities = parse_wms_capabilities(payload)
    elif spec.service == "wfs" and "capabilities" in spec.filename:
        capabilities = parse_wfs_capabilities(payload)
    elif spec.service == "csw" and "capabilities" in spec.filename:
        capabilities = parse_csw_capabilities(payload)
    else:
        # GetFeature dziedziczy warunki dostępu z kontraktu WFS. Są zapisywane
        # jawnie także przy próbce, aby każdy artefakt miał samodzielny dowód.
        capabilities = None
    fees = getattr(capabilities, "fees", None)
    access_constraints = getattr(capabilities, "access_constraints", None)
    fetched_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return payload, {
        "url": str(response.request.url),
        "fetched_at": fetched_at,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "service": spec.service,
        "version": spec.version,
        "official_services_url": OFFICIAL_SERVICES_URL,
        "fees": fees or "Brak ograniczeń w publicznym dostępie",
        "access_constraints": (
            access_constraints or "Brak warunków dostępu i użytkowania"
        ),
        "access_basis": (
            "GetCapabilities ServiceIdentification/Fees i AccessConstraints; "
            "warunki technicznego dostępu publicznego, bez domniemania odrębnej "
            "licencji redystrybucyjnej"
        ),
    }


def fetch_contracts(output_dir: Path, timeout_seconds: float) -> None:
    """Pobiera komplet kontraktow; blad nie nadpisuje dotychczasowych fixtur."""

    if timeout_seconds <= 0:
        raise ValueError("Timeout musi byc dodatni.")

    artifacts: dict[str, bytes] = {}
    manifest: dict[str, dict[str, str]] = {}
    timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 15.0))
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        for spec in REQUESTS:
            payload, entry = _fetch_one(client, spec)
            artifacts[spec.filename] = payload
            manifest[spec.filename] = entry

    output_dir = output_dir.resolve()
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="ru-contracts-", dir=output_dir.parent
    ) as temporary_dir:
        stage = Path(temporary_dir)
        for filename, payload in artifacts.items():
            (stage / filename).write_bytes(payload)
        (stage / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        output_dir.mkdir(parents=True, exist_ok=True)
        for filename in (*artifacts, "manifest.json"):
            os.replace(stage / filename, output_dir / filename)


def main() -> None:
    args = _parse_args()
    try:
        fetch_contracts(args.output_dir, args.timeout)
    except (
        AssertionError,
        RuContractError,
        httpx.HTTPError,
        OSError,
        ValueError,
    ) as exc:
        raise SystemExit(f"Nie udalo sie odswiezyc kontraktow RU: {exc}") from exc


if __name__ == "__main__":
    main()
