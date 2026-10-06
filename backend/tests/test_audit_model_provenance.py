"""Pakiet audytowy: provenance odczytu modelu językowego i reguła redystrybucji cytatu (PV3-18).

Z odpowiedzi modelu pakiet niesie tylko skrót i zweryfikowany cytat; cytat — zgodnie z polem ``redistribution``
katalogu źródeł strefy. Treść odpowiedzi modelu nigdy nie trafia do pakietu. Provenance jest zapisane także dla
niepełnego wyniku (ostrzeżenia ścieżki modelu). Bez bazy i sieci (zamrożony fixture raportu).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.modules.reporting.application.audit_export import build_audit_package
from app.modules.reporting.domain.audit_package import AUDIT_EXPORTER_VERSION
from app.schemas.analyze import MpzpParameterEvidence, MpzpValueCondition, WarningMessage
from app.shared.model_reading import MODEL_READING_DISCLAIMER, MODEL_READING_MARK
from tests.test_audit_export import LIMITS, _catalog, _input, _load_script, _zip_bytes

MODEL_ID = "gemini-3.8-flash"
PROMPT = "mpzp-extraction/1"
RESPONSE_SHA = "c0ffee" + "d" * 58
QUOTE = "zabudowa nie wyższa aniżeli 3 kondygnacje nadziemne"
CONDITION_QUOTE = "dla budynków przekrytych dachem płaskim"
DOC_SHA = "a" * 64
DETERMINISTIC_QUOTE = "maksymalna wysokość zabudowy: 12 m"


def with_model_reading(response: Any, *, warnings: bool = False) -> Any:
    """Pierwsza strefa: wartość deterministyczna + dwie wartości z modelu (jedna z warunkiem)."""
    zone = response.mpzp_zones[0]
    parameters = [
        MpzpParameterEvidence(
            name="max_building_height_m", normalized_value=12.0, unit="m", raw_value="12 m",
            evidence_text=DETERMINISTIC_QUOTE, page_number=12, document_sha256=DOC_SHA,
            extraction_method="pdf_text", confidence=0.9,
        ),
        MpzpParameterEvidence(
            name="max_storeys", normalized_value=3, raw_value="3 kondygnacje", evidence_text=QUOTE, page_number=13,
            document_sha256=DOC_SHA, extraction_method="llm_verified", review_status="ai_candidate",
            model_id=MODEL_ID, prompt_version=PROMPT, response_sha256=RESPONSE_SHA, confidence=0.62,
            manual_review_required=True,
        ),
        MpzpParameterEvidence(
            name="roof_angle_min_deg", normalized_value=30, unit="deg", raw_value="30°",
            evidence_text="dachy strome o kącie nie mniejszym niż 30°", page_number=14, document_sha256=DOC_SHA,
            extraction_method="llm_verified", review_status="ai_candidate", model_id=MODEL_ID, prompt_version=PROMPT,
            response_sha256=RESPONSE_SHA, confidence=0.6, manual_review_required=True,
            conditions=[MpzpValueCondition(kind="roof_type", label="dach stromy", quote=CONDITION_QUOTE)],
        ),
    ]
    updated = response.model_copy(update={"mpzp_zones": [zone.model_copy(update={"parameters": parameters}), *response.mpzp_zones[1:]]})
    if warnings:
        updated = updated.model_copy(update={"warnings": [
            *updated.warnings,
            WarningMessage(code="MPZP_LLM_UNAVAILABLE", message="Odczyt automatyczny był niedostępny (timeout).", severity="warning", source_name="mpzp"),
        ]})
    return updated


def zone_source_id() -> str | None:
    return _input().zone_source_ids[0]


def package(policy: str | None = None, *, warnings: bool = False):
    source_id = zone_source_id()
    catalog = _catalog(**({source_id: policy} if policy and source_id else {}))
    return build_audit_package(
        _input(catalog=catalog, response_update=lambda response: with_model_reading(response, warnings=warnings)), LIMITS
    )


def analysis_of(pkg: Any) -> dict[str, Any]:
    return json.loads(pkg.by_name("analysis.json").data)


# --- źródło strefy musi być rozpoznawalne, żeby testy redystrybucji miały sens ----------------------------------


def test_the_fixture_zone_source_is_resolvable_in_the_catalog() -> None:
    assert zone_source_id() is not None


# --- zgodnie z polem redistribution --------------------------------------------------------------------------------


def test_an_allowed_source_keeps_the_verified_quote_the_hash_and_the_provenance() -> None:
    document = analysis_of(package("allowed"))["model_provenance"]
    assert document["schema"] == "audit-model-provenance/1" and document["present"] is True
    assert document["model_responses_included"] is False
    assert document["mark"] == MODEL_READING_MARK and document["disclaimer"] == MODEL_READING_DISCLAIMER
    storeys, roof = document["entries"]
    assert (storeys["parameter"], storeys["value"], storeys["page_number"]) == ("max_storeys", 3, 13)
    assert storeys["model_id"] == MODEL_ID and storeys["prompt_version"] == PROMPT
    assert storeys["response_sha256"] == RESPONSE_SHA and storeys["document_sha256"] == DOC_SHA
    assert storeys["review_status"] == "ai_candidate" and storeys["extraction_method"] == "llm_verified"
    assert storeys["manual_review_required"] is True
    assert storeys["quote"] == QUOTE and storeys["quote_included"] is True and storeys["quote_omitted_reason"] is None
    assert len(storeys["quote_sha256"]) == 64 and storeys["redistribution"] == "allowed"
    assert storeys["pointer"] == "/result/mpzp_zones/0/parameters/1"
    assert roof["pointer"] == "/result/mpzp_zones/0/parameters/2" and roof["quote_included"] is True


def test_the_result_keeps_the_quotes_and_conditions_for_an_allowed_source() -> None:
    parameters = analysis_of(package("allowed"))["result"]["mpzp_zones"][0]["parameters"]
    assert parameters[1]["evidence_text"] == QUOTE and parameters[1]["raw_value"] == "3 kondygnacje"
    assert parameters[2]["conditions"][0]["quote"] == CONDITION_QUOTE


@pytest.mark.parametrize(
    ("policy", "reason"),
    [
        ("derived_only", "raw_redistribution_not_allowed"),
        ("forbidden", "redistribution_forbidden"),
        ("unconfirmed", "redistribution_unconfirmed"),
    ],
)
def test_a_source_that_does_not_allow_raw_data_keeps_only_the_hash_and_the_provenance(policy: str, reason: str) -> None:
    pkg = package(policy)
    analysis = analysis_of(pkg)
    first, second = analysis["model_provenance"]["entries"]
    for entry in (first, second):
        assert entry["quote"] is None and entry["quote_included"] is False and entry["quote_omitted_reason"] == reason
        assert len(entry["quote_sha256"]) == 64  # zostaje skrót cytatu
        assert entry["model_id"] == MODEL_ID and entry["prompt_version"] == PROMPT and entry["response_sha256"] == RESPONSE_SHA
        assert entry["value"] in (3, 30) and entry["page_number"] in (13, 14)  # wartość, strona i skrót dokumentu zostają
        assert entry["redistribution"] == policy
    parameters = analysis["result"]["mpzp_zones"][0]["parameters"]
    assert parameters[1]["evidence_text"] is None and parameters[1]["raw_value"] is None
    assert parameters[2]["conditions"][0]["quote"] is None and parameters[2]["conditions"][0]["label"] == "dach stromy"
    assert parameters[1]["normalized_value"] == 3 and parameters[1]["model_id"] == MODEL_ID  # provenance zostaje w wyniku
    pointers = {item["pointer"] for item in analysis["redactions"]}
    assert "/result/mpzp_zones/0/parameters/1/evidence_text" in pointers
    assert "/result/mpzp_zones/0/parameters/2/conditions/0/quote" in pointers
    omitted = {item["name"] for item in json.loads(pkg.by_name("manifest.json").data)["omitted_artifacts"]}
    assert "analysis.json#/result/mpzp_zones/0/parameters/1/evidence_text" in omitted


def test_the_quote_hash_is_the_same_whether_the_quote_is_included_or_omitted() -> None:
    from app.modules.reporting.domain.audit_package import canonical_bytes, sha256_hex

    expected = sha256_hex(canonical_bytes(QUOTE))
    included = analysis_of(package("allowed"))["model_provenance"]["entries"][0]
    assert included["quote_sha256"] == expected
    omitted_analysis = analysis_of(package("forbidden"))
    entry = omitted_analysis["model_provenance"]["entries"][0]
    redaction = next(item for item in omitted_analysis["redactions"] if item["pointer"].endswith("/parameters/1/evidence_text"))
    assert entry["quote_sha256"] == expected == redaction["sha256"]  # skrót pozwala sprawdzić cytat bez jego kopiowania


def test_the_omitted_quote_never_reaches_the_archive_but_its_hash_does() -> None:
    pkg = package("forbidden")
    archive = _zip_bytes(pkg)
    for name in ("analysis.json", "README.md", "manifest.json", "sources.json"):
        text = pkg.by_name(name).data.decode("utf-8")
        assert QUOTE not in text and CONDITION_QUOTE not in text, name
    assert QUOTE.encode() not in archive and CONDITION_QUOTE.encode() not in archive
    assert RESPONSE_SHA.encode() in pkg.by_name("analysis.json").data  # skrót odpowiedzi jest w pakiecie


def test_a_deterministic_value_is_never_listed_and_keeps_its_quote_whatever_the_policy() -> None:
    for policy in ("allowed", "forbidden"):
        analysis = analysis_of(package(policy))
        names = [entry["parameter"] for entry in analysis["model_provenance"]["entries"]]
        assert names == ["max_storeys", "roof_angle_min_deg"]
        # Zachowanie wartości deterministycznych nie zmienia się (pole nie jest cytatem z modelu).
        assert analysis["result"]["mpzp_zones"][0]["parameters"][0]["evidence_text"] == DETERMINISTIC_QUOTE


def test_the_model_response_content_is_never_part_of_the_package() -> None:
    pkg = package("allowed")
    text = "".join(file.data.decode("utf-8", "ignore") for file in pkg.files if file.name.endswith((".json", ".md")))
    assert '"candidates"' not in text and "not_found" not in text and "=====BEGIN DOCUMENT TEXT=====" not in text


# --- brak odczytu modelu i niepełny wynik -----------------------------------------------------------------------


def test_a_package_without_model_values_has_an_empty_block_and_no_unusual_redactions() -> None:
    pkg = build_audit_package(_input(), LIMITS)
    document = json.loads(pkg.by_name("analysis.json").data)["model_provenance"]
    assert document["present"] is False and document["entries"] == [] and document["warnings"] == []
    assert document["model_responses_included"] is False


def test_provenance_is_written_for_an_incomplete_result_too() -> None:
    document = analysis_of(package("allowed", warnings=True))["model_provenance"]
    assert [item["code"] for item in document["warnings"]] == ["MPZP_LLM_UNAVAILABLE"]
    assert document["present"] is True  # część wartości z modelu + ostrzeżenie o niepełnym odczycie


def test_a_degraded_result_without_model_values_still_records_the_warning() -> None:
    def only_warning(response: Any) -> Any:
        return response.model_copy(update={"warnings": [
            WarningMessage(code="MPZP_LLM_CANDIDATES_REJECTED", message="Bramki odrzuciły kandydatów (G3: 2).", severity="warning", source_name="mpzp")]})

    pkg = build_audit_package(_input(response_update=only_warning), LIMITS)
    document = json.loads(pkg.by_name("analysis.json").data)["model_provenance"]
    assert document["present"] is False and [w["code"] for w in document["warnings"]] == ["MPZP_LLM_CANDIDATES_REJECTED"]


# --- README, wersja, determinizm, weryfikator -------------------------------------------------------------------


def test_readme_explains_the_mark_the_rule_and_the_exporter_version() -> None:
    readme = package("allowed").by_name("README.md").data.decode("utf-8")
    assert MODEL_READING_MARK in readme and "nie jest interpretacją prawną" in readme
    assert "`model_provenance`" in readme and "`response_sha256`" in readme and "model_responses_included: false" in readme
    assert "redistribution: allowed" in readme and "`quote_omitted_reason`" in readme
    assert AUDIT_EXPORTER_VERSION == "audit-exporter/1.2.0" and AUDIT_EXPORTER_VERSION in readme
    assert json.loads(package("allowed").by_name("manifest.json").data)["exporter_version"] == AUDIT_EXPORTER_VERSION


def test_two_builds_with_model_values_are_byte_identical() -> None:
    assert _zip_bytes(package("forbidden")) == _zip_bytes(package("forbidden"))
    assert _zip_bytes(package("allowed")) == _zip_bytes(package("allowed"))
    assert _zip_bytes(package("allowed")) != _zip_bytes(package("forbidden"))


def test_the_offline_verifier_accepts_a_package_with_model_provenance(tmp_path: Any) -> None:
    for policy in ("allowed", "forbidden"):
        target = tmp_path / policy
        target.mkdir()
        for file in package(policy).files:
            path = target / file.name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(file.data)
        assert _load_script().main([str(target)]) == 0
