"""Kontrakt pełnego mapowania sześciu typów POG z oficjalnych fixtur RU."""

from dataclasses import replace
from pathlib import Path

import pytest

from app.modules.imports.domain.pog import (
    PogValidationError,
    parse_app_reference,
    pog_record_from_dict,
    pog_record_to_dict,
)
from app.modules.imports.infrastructure.pog.reader import (
    assemble_ru_pog_acts,
    canonical_record_sha256,
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
    assert act.legal_status == "unknown"
    assert act.raw_legal_status and act.raw_legal_status.endswith("/unmappedStatus")
    assert act.is_binding is False


def test_official_legal_force_code_maps_to_binding() -> None:
    act = parse_ru_app_feature_collection(_payload("act")).acts[0]
    assert act.legal_status == "binding"
    assert act.raw_legal_status and act.raw_legal_status.endswith("/legalForce")
    act.validate()
    assert act.is_binding is True


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
    assert [(doc.object_id.local_id, doc.resolution_status) for doc in act.documents] == [
        ("1", "resolved"),
        ("XXIV.300.2026", "unavailable"),
    ]
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


APP = "https://www.gov.pl/zagospodarowanieprzestrzenne/app"
DOC_TEMPLATE = (
    '<wfs:member><app-pog:DokumentFormalny gml:id="{gml_id}">'
    '<gml:identifier codeSpace="{app}">{app}/DokumentFormalny/PL.ZIPPZP.10011/226401-POG/{local}{version_path}</gml:identifier>'
    '<app-pog:idIIP><app-pog:Identyfikator><app-pog:przestrzenNazw>PL.ZIPPZP.10011/226401-POG</app-pog:przestrzenNazw>'
    '<app-pog:lokalnyId>{local}</app-pog:lokalnyId>{version_xml}</app-pog:Identyfikator></app-pog:idIIP>'
    '<app-pog:tytul>{title}</app-pog:tytul>'
    '<app-pog:dataWejsciaWZycie>2026-08-19</app-pog:dataWejsciaWZycie>{repeal}'
    '<app-pog:lacze>{link}</app-pog:lacze>'
    '<app-pog:uchwala xlink:href="{app}/AktPlanowaniaPrzestrzennego/PL.ZIPPZP.10011/226401-POG/1POG{act_version}"/>'
    '</app-pog:DokumentFormalny></wfs:member>'
)


def _documents_payload(*docs: dict[str, str]) -> bytes:
    """Zbudowane z realnej odpowiedzi RU: zmieniane są tylko pola dokumentów."""
    base = _payload("document").decode()
    head, _, _tail = base.partition("<wfs:member>")
    members = "".join(
        DOC_TEMPLATE.format(
            app=APP,
            gml_id=f"doc_{index}",
            local=doc["local"],
            version_path=f"/{doc['version']}" if doc.get("version") else "",
            version_xml=(
                f"<app-pog:wersjaId>{doc['version']}</app-pog:wersjaId>" if doc.get("version") else ""
            ),
            title=doc.get("title", "UCHWAŁA NR XXIV/300/2026"),
            repeal=(
                f"<app-pog:dataUchylenia>{doc['repeal']}</app-pog:dataUchylenia>"
                if doc.get("repeal") else ""
            ),
            link=doc.get("link", "https://bip.sopot.pl/uchwala.pdf"),
            act_version=f"/{doc['act_version']}" if doc.get("act_version") else "",
        )
        for index, doc in enumerate(docs)
    )
    return f"{head}{members}</wfs:FeatureCollection>".encode()


def _act_with_document_refs(*hrefs: str) -> bytes:
    act = _payload("act").decode()
    start = act.index("<app-pog:dokumentPrzystepujacy")
    end = act.index("<app-pog:wydzielenie")
    refs = "".join(f'<app-pog:dokumentUchwalajacy xlink:href="{href}"/>' for href in hrefs)
    return (act[:start] + refs + act[end:]).encode()


def _assemble(act_payload: bytes, documents_payload: bytes):
    parts = (
        parse_ru_app_feature_collection(act_payload, expected_type=TYPES["act"]),
        parse_ru_app_feature_collection(documents_payload, expected_type=TYPES["document"]),
    )
    return assemble_ru_pog_acts(merge_ru_pog_objects(parts))[0]


def test_act_version_metadata_is_parsed_from_official_sample() -> None:
    act = parse_ru_app_feature_collection(_payload("act")).acts[0]
    assert act.publication_id == (
        f"{APP}/AktPlanowaniaPrzestrzennego/PL.ZIPPZP.10011/226401-POG/1POG/20260819T010000"
    )
    assert act.version_started_at is not None
    assert act.version_started_at.isoformat() == "2026-08-19T01:00:00+00:00"
    assert act.valid_from is not None and act.valid_from.isoformat() == "2026-08-19"
    assert act.valid_to is None
    assert act.catalog_resource_identifier == (
        f"{APP}/AktPlanowaniaPrzestrzennego/PL.ZIPPZP.10011/226401-POG/"
    )


def test_document_record_fields_and_canonical_sha() -> None:
    document = parse_ru_app_feature_collection(_payload("document")).documents[0]
    assert document.relation == "przystapienie"
    assert document.short_name == "POG Sopot - przystąpienie"
    assert document.document_date is not None and document.document_date.isoformat() == "2024-04-23"
    assert document.effective_date is not None
    assert document.link == "https://bip.sopot.pl/m,287,plan-ogolny.html"
    assert document.link_verified is True
    assert document.record_sha256 and len(document.record_sha256) == 64
    # Ta sama treść z innym prefiksem przestrzeni nazw daje ten sam SHA.
    renamed = _payload("document").replace(b"app-pog:", b"app:").replace(b"xmlns:app-pog=", b"xmlns:app=")
    assert parse_ru_app_feature_collection(renamed).documents[0].record_sha256 == document.record_sha256
    changed = _payload("document").replace("przystąpienia".encode(), b"przystapienia")
    assert parse_ru_app_feature_collection(changed).documents[0].record_sha256 != document.record_sha256


def test_same_title_documents_with_different_versions_are_not_merged() -> None:
    title = "UCHWAŁA NR XXIV/300/2026 RADY MIASTA SOPOTU"
    documents = _documents_payload(
        {"local": "XXIV.300.2026", "version": "20260101T000000", "title": title},
        {"local": "XXIV.300.2026", "version": "20260819T010000", "title": title},
    )
    act = _assemble(
        _act_with_document_refs(
            f"{APP}/DokumentFormalny/PL.ZIPPZP.10011/226401-POG/XXIV.300.2026/20260819T010000"
        ),
        documents,
    )
    by_version = {doc.object_id.version_id: doc for doc in act.documents}
    assert set(by_version) == {"20260101T000000", "20260819T010000"}
    assert by_version["20260819T010000"].resolution_status == "resolved"
    # Starsza wersja o tym samym tytule nie jest scalana ani podpinana jako rozstrzygnięta.
    assert by_version["20260101T000000"].resolution_status == "unresolved"
    assert "inną wersję" in (by_version["20260101T000000"].resolution_note or "")
    assert by_version["20260101T000000"].record_sha256 != by_version["20260819T010000"].record_sha256


def test_unversioned_reference_to_many_document_versions_is_unresolved() -> None:
    documents = _documents_payload(
        {"local": "XXIV.300.2026", "version": "20260101T000000"},
        {"local": "XXIV.300.2026", "version": "20260819T010000"},
    )
    act = _assemble(
        _act_with_document_refs(f"{APP}/DokumentFormalny/PL.ZIPPZP.10011/226401-POG/XXIV.300.2026"),
        documents,
    )
    assert {doc.resolution_status for doc in act.documents} == {"unresolved"}
    assert all("wielu wersji" in (doc.resolution_note or "") for doc in act.documents)


def test_document_pointing_to_other_act_version_stays_unresolved() -> None:
    documents = _documents_payload(
        {"local": "Z.1.2027", "act_version": "20270101T000000", "title": "Zmiana POG"}
    )
    act = _assemble(_act_with_document_refs(), documents)
    (document,) = act.documents
    assert document.resolution_status == "unresolved"
    assert "20270101T000000" in (document.resolution_note or "")


def test_repealed_and_non_https_documents_keep_their_flags() -> None:
    documents = _documents_payload(
        {"local": "U.1.2025", "repeal": "2026-08-19", "link": "http://bip.sopot.pl/stara.pdf"}
    )
    act = _assemble(_act_with_document_refs(), documents)
    (document,) = act.documents
    assert document.resolution_status == "resolved"
    assert document.repeal_date is not None
    assert document.link_verified is False


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        (f"{APP}/AktPlanowaniaPrzestrzennego/PL.ZIPPZP.10011/226401-POG/1POG",
         ("AktPlanowaniaPrzestrzennego", "PL.ZIPPZP.10011/226401-POG", "1POG", None)),
        (f"{APP}/StrefaPlanistyczna/PL.ZIPPZP.10011/226401-POG/1POG-1SW/20260819T010000",
         ("StrefaPlanistyczna", "PL.ZIPPZP.10011/226401-POG", "1POG-1SW", "20260819T010000")),
        ("https://example.com/app/Akt/x/y/z", None),
        (f"{APP}/AktPlanowaniaPrzestrzennego/PL.ZIPPZP.10011", None),
        (None, None),
    ],
)
def test_app_reference_parser(href, expected) -> None:
    ref = parse_app_reference(href)
    if expected is None:
        assert ref is None
    else:
        assert ref is not None
        assert (ref.object_type, ref.namespace, ref.local_id, ref.version_id) == expected


def test_feature_with_suffix_collision_is_not_attached_to_act() -> None:
    zone = _payload("zone").replace(b"226401-POG/1POG\"", b"226401-POG/11POG\"")
    parts = (
        parse_ru_app_feature_collection(_payload("act"), expected_type=TYPES["act"]),
        parse_ru_app_feature_collection(zone, expected_type=TYPES["zone"]),
    )
    act = assemble_ru_pog_acts(merge_ru_pog_objects(parts))[0]
    assert act.features == ()


def test_canonical_sha_helper_is_stable_for_equal_elements() -> None:
    from xml.etree import ElementTree

    first = ElementTree.fromstring('<a xmlns="urn:x" b="1" c="2"><d>t</d></a>')
    second = ElementTree.fromstring('<p:a xmlns:p="urn:x" c="2" b="1"><p:d>t</p:d></p:a>')
    assert canonical_record_sha256(first) == canonical_record_sha256(second)
