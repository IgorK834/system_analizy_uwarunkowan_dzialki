"""Kompletność analizy od końca do końca (AU-104): orkiestrator → PostGIS → API → cache → PDF.

Scenariusz bez lokalnego wektora MPZP: strefa pochodzi z aktu i dokumentu (``document_candidate``), reszta źródeł
jest dostępna. Przed AU-104 taki wynik był zawsze ``partial``; teraz ``complete`` wynika z kompletności sekcji.
Usługi zewnętrzne są zamrożonymi fixture'ami — bez internetu.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.access_control import make_analysis_token
from app.modules.imports.infrastructure.ogc_client import OgcTransportError
from app.shared.provenance import Provenance
from tests.test_analysis_orchestrator import _found_discovery
from tests.test_terrain_e2e import (  # noqa: F401 - fixtures pytest
    CONTROL,
    _PREFIX,
    _nmt,
    _pdf_text,
    _post_analyze,
    _reread,
    cleanup_rows,
    client,
    relief_enabled,
    bdot_warsaw_release,
)
from tests.terrain_fixtures import FakeWcsClient, requires_gdal

pytestmark = pytest.mark.integration


def _analyze(identifier: str):  # noqa: ANN202
    return _post_analyze(identifier, CONTROL.wkt, _nmt("nmt_getminmaxbypolygon.txt"), discovery=_found_discovery())


@requires_gdal
def test_mpzp_from_act_and_document_gives_complete_without_a_local_vector(
    relief_enabled: FakeWcsClient,  # noqa: F811 - fixture pytest
    bdot_warsaw_release,  # noqa: F811, ANN001 - fixture pytest
) -> None:
    identifier = f"{_PREFIX}CMP_{uuid4().hex[:6]}"

    response, _calls = _analyze(identifier)

    assert response.status_code == 200
    body = response.json()
    assert body["mpzp_zones"] and {zone["assignment_method"] for zone in body["mpzp_zones"]} == {"document_candidate"}
    completeness = body["completeness"]
    mpzp = next(item for item in completeness["sections"] if item["section"] == "mpzp")
    assert mpzp["state"] == "checked" and mpzp["basis"] == "document_candidate"
    # Wektor lokalny to wyższa jakość, nie warunek: status wynika z kompletności sekcji w zakresie.
    states = {item["section"]: item["state"] for item in completeness["sections"]}
    assert completeness["complete"] is True and body["status"] == "complete", states
    assert body["headline"] == completeness["headline"] and completeness["headline"].startswith("Sprawdzono wszystkie")
    assert any("document_candidate" in note for note in completeness["notes"])  # jakość MPZP jest nazwana wprost
    assert states["utilities"] == "out_of_scope"
    assert states["transport"] == states["buildings"] == "checked"  # BDOT10k: zasięg wydania obejmuje działkę kontrolną


@requires_gdal
def test_completeness_survives_save_reread_cache_and_pdf(
    relief_enabled: FakeWcsClient,  # noqa: F811 - fixture pytest
    bdot_warsaw_release,  # noqa: F811, ANN001 - fixture pytest
) -> None:
    identifier = f"{_PREFIX}CMP_{uuid4().hex[:6]}"
    first, _ = _analyze(identifier)
    body = first.json()

    # Odczyt historyczny: ta sama, zapisana ocena (nie przeliczona).
    reread = _reread(body["analysis_id"])
    assert reread["completeness"] == {**body["completeness"], "origin": "stored"} and reread["headline"] == body["headline"]

    # Cache: drugie żądanie zwraca ten sam status i komunikat.
    cached, nmt_calls = _analyze(identifier)
    assert cached.status_code == 200 and nmt_calls == 0
    assert cached.json()["analysis_id"] == body["analysis_id"]
    assert cached.json()["status"] == "complete" and cached.json()["headline"] == body["headline"]

    # PDF, strona 1: ten sam komunikat i tabela sekcji.
    report = client.get(f"/report/{body['analysis_id']}?access_token={make_analysis_token(body['analysis_id'])}")
    assert report.status_code == 200
    text = _pdf_text(report.content)
    assert "Kompletność analizy" in text and body["headline"].split(". ")[0] in text
    assert "akt i dokument, bez lokalnego wektora" in text
    assert "poza zakresem" in text


@requires_gdal
def test_unavailable_terrain_relief_lowers_the_status_to_partial_and_names_the_missing_section(
    relief_enabled: FakeWcsClient,  # noqa: F811 - fixture pytest
    bdot_warsaw_release,  # noqa: F811, ANN001 - fixture pytest
) -> None:
    identifier = f"{_PREFIX}CMP_{uuid4().hex[:6]}"
    # WCS NMT niedostępny (jak w live smoke 2026-10-12: „Server disconnected”) — pochodne rastra nie powstają.
    relief_enabled.coverage = OgcTransportError(
        "Server disconnected without sending a response",
        source=Provenance(source_id="nmt_wcs", fetched_at=None, request_url="https://wcs.example.test", operation="WCS:GetCoverage", complete=False),
    )

    response, _ = _analyze(identifier)

    body = response.json()
    assert response.status_code == 200
    terrain = next(item for item in body["completeness"]["sections"] if item["section"] == "terrain")
    assert terrain["state"] == "missing" and terrain["quality_status"] == "partial"
    assert "TERRAIN_RELIEF_NOT_AVAILABLE" in terrain["reason_codes"]
    assert body["status"] == "partial" and "brak: teren (NMT)" in body["headline"]
