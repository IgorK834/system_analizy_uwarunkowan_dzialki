"""Kontrakt pełnego mapowania sześciu typów POG z oficjalnych fixtur RU."""

from dataclasses import replace
from pathlib import Path

import pytest

from app.modules.imports.domain.pog import (
    PogValidationError,
    pog_record_from_dict,
    pog_record_to_dict,
)
from app.modules.imports.infrastructure.pog.reader import (
    assemble_ru_pog_acts,
    merge_ru_pog_objects,
    parse_ru_app_feature_collection,
)


FIXTURES = Path(__file__).parent / "fixtures" / "ru"
TYPES = {
    "act": "AktPlanowaniaPrzestrzennego",
    "document": "DokumentFormalny",
    "osdis": "ObszarStandardowDostepnosciInfrastrukturySpolecznej",
    "ouz": "ObszarUzupelnieniaZabudowy",
    "ozs": "ObszarZabudowySrodmiejskiej",
    "zone": "StrefaPlanistyczna",
}


def _payload(name: str) -> bytes:
    return (FIXTURES / f"wfs_pog_getfeature_{name}.xml").read_bytes()


def test_real_bielsko_metadata_is_parsed_without_teryt_branch() -> None:
    objects = parse_ru_app_feature_collection(
        (FIXTURES / "wfs_pog_getfeature_246101.xml").read_bytes(),
        expected_type="AktPlanowaniaPrzestrzennego",
        source_reference="fixture",
    )
    act = objects.acts[0]
    assert act.teryt == "246101"
    assert act.name == "Plan ogólny Bielska-Białej"
    assert act.boundary is None  # przycięta fixture pozostaje bez dorobionej geometrii
    assert act.object_id and act.object_id.version_id == "20260901T095300"


def test_official_full_samples_cover_all_six_types_and_xlinks() -> None:
    parsed = {
        name: parse_ru_app_feature_collection(
            _payload(name), expected_type=feature_type, source_reference="fixture"
        )
        for name, feature_type in TYPES.items()
    }
    assert len(parsed["act"].acts) == 1
    assert len(parsed["document"].documents) == 1
    assert {parsed[name].features[0].feature_type for name in ("zone", "ouz", "ozs", "osdis")} == {
        "planning_zone", "ouz", "downtown_area", "social_infrastructure_standard"
    }
    zone = parsed["zone"].features[0]
    assert zone.act_reference and zone.act_reference.href.endswith("/1POG")
    assert zone.parameters and zone.parameters.values() == {
        "max_overground_floor_area_ratio": 0.9,
        "max_building_height_m": 4.0,
        "max_building_coverage_pct": 90.0,
        "min_biologically_active_pct": 5.0,
    }
    assert zone.primary_profiles[0].code == "KPT-MPZP-U"
    assert zone.primary_profiles[0].dictionary_source.endswith("/ontology/KPT")


def test_null_zero_and_polish_decimal_are_distinct() -> None:
    payload = _payload("zone").replace(b">0.9<", b">0,9<").replace(b">90.0<", b">0<")
    payload = payload.replace(
        b"<app-pog:minUdzialPowierzchniBiologicznieCzynnej>5.0</app-pog:minUdzialPowierzchniBiologicznieCzynnej>",
        b"",
    )
    zone = parse_ru_app_feature_collection(payload).features[0]
    assert zone.parameters
    assert zone.parameters.max_overground_floor_area_ratio.value == 0.9
    assert zone.parameters.max_building_coverage.value == 0
    assert zone.parameters.min_biologically_active is None


def test_missing_unit_foreign_namespace_and_bad_crs_are_rejected() -> None:
    missing_unit = _payload("zone").replace(b' uom="m"', b"")
    with pytest.raises(PogValidationError, match="uom"):
        parse_ru_app_feature_collection(missing_unit)
    foreign = _payload("zone").replace(
        b"https://www.gov.pl/static/zagospodarowanieprzestrzenne/schemas/app/3.0",
        b"https://invalid.example/app",
    )
    with pytest.raises(PogValidationError, match="namespace"):
        parse_ru_app_feature_collection(foreign)
    bad_crs = _payload("zone").replace(b"epsg.xml#2180", b"epsg.xml#4326")
    with pytest.raises(PogValidationError, match="EPSG:4326"):
        parse_ru_app_feature_collection(bad_crs)


def test_unknown_status_is_preserved_but_never_binding() -> None:
    payload = _payload("act").replace(b"legalForce", b"unmappedStatus").replace(
        "prawnie wiążący lub realizowany".encode(), b"status testowy"
    )
    act = parse_ru_app_feature_collection(payload).acts[0]
    assert act.legal_status == "not_available"
    assert act.raw_legal_status and act.raw_legal_status.endswith("/unmappedStatus")
    assert act.is_binding is False


def test_relations_and_osdis_survive_parse_json_roundtrip() -> None:
    osdis = _payload("osdis").replace(
        b"PL.ZIPPZP.10067/240203-POG/1POG",
        b"PL.ZIPPZP.10011/226401-POG/1POG",
    )
    parts = tuple(
        parse_ru_app_feature_collection(
            osdis if name == "osdis" else _payload(name),
            expected_type=feature_type,
            source_reference="fixture",
        )
        for name, feature_type in TYPES.items()
    )
    act = assemble_ru_pog_acts(merge_ru_pog_objects(parts))[0]
    assert {feature.feature_type for feature in act.features} == {
        "planning_zone", "ouz", "downtown_area", "social_infrastructure_standard"
    }
    assert len(act.documents) == 1
    restored = pog_record_from_dict(pog_record_to_dict(act))
    restored.validate()
    assert restored == act
    assert restored.features[-1].act_reference == act.features[-1].act_reference


def test_describe_feature_type_wrapper_and_official_schema_are_frozen() -> None:
    wrapper = (FIXTURES / "wfs_pog_describe_feature_type_3_0.xsd").read_text()
    schema = (FIXTURES / "planowaniePrzestrzenne_3_0.xsd").read_text()
    assert "planowaniePrzestrzenne_3_0.xsd" in wrapper
    for feature_type in TYPES.values():
        assert feature_type in schema
