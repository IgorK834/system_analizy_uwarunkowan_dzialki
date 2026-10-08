"""Kontrakt pewności: pasmo i artefakt kalibracji w evidence parsera i API (PV3-09)."""

from __future__ import annotations

import asyncio
import json

import pytest

from app.modules.planning.domain import evidence_confidence as ec
from app.schemas import analyze as analyze_schemas
from app.schemas import mpzp as mpzp_schemas
from app.schemas.source import SourceMetadata
from app.services.mpzp_zones import apply_parser_zone, unassigned_share_zone
from scripts import evaluate_mpzp_parser as ev
from tests.parcel_fixtures_config import find_repo_root

ARTIFACT = ec.default_artifact()
FIXTURES = find_repo_root() / "backend/tests/fixtures/mpzp_evaluation"
SOURCE = SourceMetadata(source_name="MPZP_BIP", source_url=None, confidence=0.9, manual_review_required=False)


def _parse(doc: str, symbols: list[str], mode: str) -> mpzp_schemas.MpzpParseResult:
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    loaded = ev.load_document(FIXTURES, manifest["documents"][doc])
    return asyncio.run(ev.run_parser(loaded, symbols, mode))


@pytest.mark.parametrize("mode", ["legacy", "blocks"])
def test_numeric_values_carry_a_calibrated_band_and_the_artifact_id(mode: str) -> None:
    result = _parse("bielsko_viii_187_2024", ["230_U", "230_UMW"], mode)
    numeric = [p for z in result.zones for p in z.parameters if isinstance(p.normalized_value, (int, float))]
    assert numeric
    for parameter in numeric:
        assert parameter.confidence_calibration == ARTIFACT.calibration_id
        assert parameter.confidence_band == ec.band_for(parameter.confidence, ARTIFACT)
        features = ec.ConfidenceFeatures.from_dict(parameter.confidence_features or {})
        assert features.extraction_method == "pdf_text" and features.quote_verified is True
        below = parameter.confidence < ARTIFACT.review_threshold
        assert parameter.manual_review_required or not (below or parameter.value_kind == "conflict")
    descriptive = [p for z in result.zones for p in z.parameters if isinstance(p.normalized_value, str)]
    assert descriptive and all(p.confidence_band is None and p.confidence_calibration is None for p in descriptive)
    assert all(p.confidence <= ARTIFACT.uncalibrated_cap for p in descriptive)


def test_ocr_document_values_carry_ocr_features_and_lower_confidence() -> None:
    ocr = _parse("stare_miasto_xliv_305_2002", ["146 MN"], "legacy")
    text = _parse("bielsko_viii_187_2024", ["230_U"], "legacy")
    ocr_values = [p for z in ocr.zones for p in z.parameters if isinstance(p.normalized_value, (int, float))]
    assert ocr_values and all(p.confidence_features["extraction_method"] == "ocr" for p in ocr_values)
    best_text = max(p.confidence for z in text.zones for p in z.parameters if isinstance(p.normalized_value, (int, float)))
    assert max(p.confidence for p in ocr_values) < best_text
    assert all(p.manual_review_required for p in ocr_values)  # skan: ręczna weryfikacja (próg z pomiaru)


def test_band_and_calibration_flow_to_the_api_evidence() -> None:
    parameter = mpzp_schemas.MpzpParameter(
        name="max_building_height_m", normalized_value=9.0, unit="m", raw_value="9 m", source_text="wysokość 9 m",
        page_number=2, confidence=0.97, manual_review_required=False, confidence_band="high",
        confidence_calibration=ARTIFACT.calibration_id,
    )
    zone, _skipped, _conflicts = apply_parser_zone(
        unassigned_share_zone("MN", SOURCE), mpzp_schemas.MpzpZoneResult(zone_symbol="MN", parameters=[parameter])
    )
    (evidence,) = zone.parameters
    assert evidence.confidence_band == "high" and evidence.confidence_calibration == ARTIFACT.calibration_id
    assert evidence.model_dump(mode="json")["confidence_band"] == "high"


def test_snapshot_before_the_calibration_has_no_band() -> None:
    old = analyze_schemas.MpzpParameterEvidence.model_validate({"name": "max_intensity", "confidence": 0.85})
    assert old.confidence_band is None and old.confidence_calibration is None
    with pytest.raises(ValueError):
        analyze_schemas.MpzpParameterEvidence.model_validate({"name": "x", "confidence": 0.5, "confidence_band": "bardzo wysokie"})


def test_the_contract_version_changed_so_old_confidence_is_not_served_from_cache() -> None:
    from app.services import cache

    # Co najmniej 2.6 (PV3-21); 2.7 (AU-004) nadal wyklucza wyniki 2.4 i starsze z cache.
    version = analyze_schemas.MPZP_RESULT_SCHEMA_VERSION
    assert tuple(map(int, version.split("."))) >= (2, 6) and f"mpzp-v{version}" in cache.RESULT_CONTRACT_VERSION


def test_values_from_a_language_model_are_never_verified_or_high_band() -> None:
    features = ec.ConfidenceFeatures(scope_kind="zone_section", origin=ec.ORIGIN_LLM, strategy="comparative", extraction_method="pdf_text")
    scored = ec.score(features, ARTIFACT)
    assert scored.manual_review_required and scored.band != "high" and scored.calibrated is False
    # ``verified`` jest zakazanym statusem przeglądu dla każdego wyniku silnika (kontrakt ewaluatora)
    from scripts.mpzp_eval_engines import FORBIDDEN_REVIEW_STATUSES

    assert "verified" in FORBIDDEN_REVIEW_STATUSES
