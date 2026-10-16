"""Kompletność analizy i status nagłówkowy (AU-104, ADR-022): macierz warunków reguły ``completeness/1.1``.

Bazą jest zamrożona odpowiedź raportu (``multizone``): rzeczywisty kształt wyniku z MPZP z wektora, POG, ISOK, GDOŚ i NMT.
Każdy test zmienia jedną rzecz i sprawdza, czy reguła reaguje dokładnie tak, jak opisuje ADR-022.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest

from app.schemas.analyze import (
    AnalyzeResponse,
    MpzpStatus,
    RiskSectionResult,
    TerrainReliefResult,
    TerrainResult,
    UtilitiesPreviewResult,
)
from app.schemas.source import SourceMetadata, WarningMessage
from app.services.cache import RESULT_CONTRACT_VERSION
from app.services.completeness import (
    RULE_VERSION,
    build_completeness,
    completeness_from_snapshot,
    completeness_to_snapshot,
    with_completeness,
)
from app.services.pog_scenarios import assess_mpzp_pog_compatibility
from tests.report_map_reference import load_fixture


def _assessed(response: AnalyzeResponse) -> AnalyzeResponse:
    """Odpowiedź z oceną relacji MPZP–POG (ostatnia sekcja wymagana do kompletności)."""
    assert response.pog is not None
    assessment, _ = assess_mpzp_pog_compatibility(
        response.mpzp_zones, response.pog, as_of=datetime(2026, 10, 1, tzinfo=timezone.utc)
    )
    assessment = assessment.model_copy(update={"status": "compatible", "reason_code": "PAIRS_EVALUATED"})
    return response.model_copy(
        update={"pog": response.pog.model_copy(update={"compatibility_assessment": assessment})}
    )


def _without_conflicts(response: AnalyzeResponse) -> AnalyzeResponse:
    """Zamrożona odpowiedź ``multizone`` celowo ma sprzeczne parametry (do testów raportu); tu bez nich."""
    zones = [
        zone.model_copy(
            update={
                "parameters": [parameter.model_copy(update={"conflict_group_id": None}) for parameter in zone.parameters]
            }
        )
        for zone in response.mpzp_zones
    ]
    return response.model_copy(update={"mpzp_zones": zones})


def _base(**update: Any) -> AnalyzeResponse:
    response, _ = load_fixture("multizone")
    response = _assessed(_without_conflicts(response))
    return response.model_copy(update={"section_quality": None, "completeness": None, "headline": None, **update})


def _states(response: AnalyzeResponse) -> dict[str, str]:
    return {item.section: item.state for item in build_completeness(response).sections}


def _mpzp_status(status: str, reason: str = "TEST") -> MpzpStatus:
    return MpzpStatus(
        status=status,  # type: ignore[arg-type]
        reason_code=reason,
        label=status,
        message="komunikat",
        precedence_rule_version="mpzp-act-precedence/1.0",
    )


def _unavailable_source(section: str) -> RiskSectionResult:
    return RiskSectionResult(
        section=section,  # type: ignore[arg-type]
        status="unavailable",
        reason_code="TIMEOUT",
        source=None,
    )


# --- zakres i definicja complete -----------------------------------------------------------------------


def test_all_sections_in_scope_checked_gives_complete_with_the_scope_listed() -> None:
    completeness = build_completeness(_base())

    assert completeness.complete and completeness.rule_version == RULE_VERSION == "completeness/1.1"
    assert (completeness.checked, completeness.total) == (10, 10)
    # Od Task 23.2–23.3 droga i zabudowa sąsiednia mają kontrakt źródła (BDOT10k) i wchodzą do zakresu.
    assert {item.section for item in completeness.sections if item.state == "out_of_scope"} == {"utilities"}
    assert completeness.headline.startswith("Sprawdzono wszystkie 10 sekcji w zakresie analizy.")
    assert "Poza zakresem: uzbrojenie terenu." in completeness.headline


def test_header_status_is_derived_from_completeness_and_never_set_by_hand() -> None:
    complete = with_completeness(_base())
    assert complete.status == "complete" and complete.headline == complete.completeness.headline

    partial = with_completeness(_base(terrain=None))
    assert partial.status == "partial" and not partial.completeness.complete


def test_waiting_response_keeps_its_own_status() -> None:
    waiting = with_completeness(
        _base(status="waiting_for_user_input", manual_zone_required=True, mpzp_zones=[]), update_status=False
    )

    assert waiting.status == "waiting_for_user_input" and not waiting.completeness.complete
    assert _states(waiting)["mpzp"] == "missing"


def test_existing_completeness_is_not_recomputed() -> None:
    first = with_completeness(_base())
    second = with_completeness(first.model_copy(update={"terrain": None}))

    assert second.completeness is first.completeness and second.status == "complete"


@pytest.mark.parametrize(
    ("label", "update", "missing_section"),
    [
        ("parcel_geometry_missing", {"parcel": None}, "parcel"),
        ("pog_missing", {"pog": None}, "pog"),
        ("terrain_missing", {"terrain": None}, "terrain"),
    ],
)
def test_every_required_section_that_is_not_confirmed_blocks_complete(
    label: str, update: dict[str, Any], missing_section: str
) -> None:
    completeness = build_completeness(_base(**update))

    assert not completeness.complete, label
    assert _states(_base(**update))[missing_section] == "missing"
    assert completeness.checked == completeness.total - sum(
        1 for item in completeness.sections if item.state == "missing"
    )
    assert "brak: " in completeness.headline


@pytest.mark.parametrize("section", ["flood", "nature"])
@pytest.mark.parametrize("status", ["unavailable", "no_coverage", "unknown"])
def test_environment_sections_must_be_available(section: str, status: str) -> None:
    base = _base()
    risks = [
        item.model_copy(update={"status": status, "reason_code": "X"}) if item.section == section else item
        for item in base.risk_sections
    ]

    response = base.model_copy(update={"risk_sections": risks})

    assert _states(response)[section] == "missing" and not build_completeness(response).complete


def test_no_coverage_or_error_of_a_source_is_never_read_as_no_restriction() -> None:
    response = _base()
    flood = next(item for item in response.risk_sections if item.section == "flood")
    covered = response.model_copy(
        update={
            "risk_sections": [
                flood.model_copy(update={"status": "no_coverage", "reason_code": "OUTSIDE"})
                if item.section == "flood"
                else item
                for item in response.risk_sections
            ]
        }
    )

    completeness = build_completeness(covered)

    assert not completeness.complete
    assert next(item for item in completeness.sections if item.section == "flood").quality_status == "no_coverage"


def test_pog_with_incomplete_coverage_is_missing_and_a_binding_status_does_not_matter_for_completeness() -> None:
    base = _base()
    assert base.pog is not None
    partial_pog = base.model_copy(update={"pog": base.pog.model_copy(update={"coverage_status": "partial"})})
    project_pog = base.model_copy(update={"pog": base.pog.model_copy(update={"legal_status": "project"})})

    assert _states(partial_pog)["pog"] == "missing" and not build_completeness(partial_pog).complete
    # Status prawny jest osobnym faktem: dane sprawdzone => complete, z zastrzeżeniem w ``notes``.
    completeness = build_completeness(project_pog)
    assert completeness.complete
    assert any("nie jest (jeszcze) aktem wiążącym" in note for note in completeness.notes)


def test_unassessed_mpzp_pog_relation_blocks_complete_when_there_is_a_zone_to_compare() -> None:
    base = _base()
    assert base.pog is not None
    unassessed = base.model_copy(update={"pog": base.pog.model_copy(update={"compatibility_assessment": None})})

    assert _states(unassessed)["mpzp_pog_relation"] == "missing"
    assert not build_completeness(unassessed).complete


def test_terrain_with_unavailable_relief_is_not_fully_checked() -> None:
    base = _base()
    assert base.terrain is not None
    relief = TerrainReliefResult(
        algorithm_version="terrain-relief/1", slope_classes_version="slope-classes/1",
        status="unavailable", reason_code="WCS_TIMEOUT", source=base.terrain.source,
    )
    broken = base.terrain.model_copy(update={"relief": relief})

    response = base.model_copy(update={"terrain": broken})

    section = next(item for item in build_completeness(response).sections if item.section == "terrain")
    assert section.state == "missing" and section.quality_status == "partial"


# --- MPZP: akt + dokument albo wektor, nie ręczny symbol ---------------------------------------------


def test_vector_zones_are_checked_with_the_vector_basis_and_no_note() -> None:
    completeness = build_completeness(_base())
    mpzp = next(item for item in completeness.sections if item.section == "mpzp")

    assert mpzp.state == "checked" and mpzp.basis == "vector_intersection"
    assert not any("document_candidate" in note for note in completeness.notes)


def test_mpzp_from_act_and_document_is_enough_for_complete_but_says_so() -> None:
    """Sedno AU-104: w konfiguracji domyślnej nie ma lokalnego wektora — akt i dokument wystarczają."""
    base = _base()
    candidates = [zone.model_copy(update={"assignment_method": "document_candidate", "intersection_pct": None}) for zone in base.mpzp_zones]

    completeness = build_completeness(base.model_copy(update={"mpzp_zones": candidates}))
    mpzp = next(item for item in completeness.sections if item.section == "mpzp")

    assert mpzp.state == "checked" and mpzp.basis == "document_candidate"
    assert completeness.complete
    assert any("assignment_method=document_candidate" in note and "bez lokalnego wektora" in note for note in completeness.notes)
    assert "Do weryfikacji ręcznej: MPZP" in completeness.headline  # kandydat z dokumentu pozostaje do weryfikacji


@pytest.mark.parametrize("method", ["manual_user_input", "legacy"])
def test_symbol_given_by_the_user_or_a_legacy_snapshot_is_not_a_system_finding(method: str) -> None:
    base = _base()
    zones = [zone.model_copy(update={"assignment_method": method}) for zone in base.mpzp_zones]

    assert _states(base.model_copy(update={"mpzp_zones": zones}))["mpzp"] == "missing"


def test_mixed_assignment_methods_use_the_weakest_basis() -> None:
    base = _base()
    assert len(base.mpzp_zones) >= 2
    zones = [base.mpzp_zones[0], *[zone.model_copy(update={"assignment_method": "document_candidate"}) for zone in base.mpzp_zones[1:]]]

    mpzp = next(item for item in build_completeness(base.model_copy(update={"mpzp_zones": zones})).sections if item.section == "mpzp")

    assert mpzp.state == "checked" and mpzp.basis == "document_candidate"


def test_mpzp_waiting_for_the_zone_symbol_is_missing() -> None:
    response = _base(manual_zone_required=True, mpzp_zones=[])

    assert _states(response)["mpzp"] == "missing"


@pytest.mark.parametrize(
    ("status", "state"),
    [
        ("no_match", "checked"),  # kanały odpowiedziały i nie wskazały planu
        ("available", "missing"),  # akt jest, ale strefa nie została ustalona (MPZP_ACT_WITHOUT_ZONE)
        ("ambiguous", "missing"),
        ("unavailable", "missing"),
        ("no_coverage", "missing"),
        ("unknown", "missing"),
    ],
)
def test_mpzp_without_zones_follows_the_disjoint_state(status: str, state: str) -> None:
    response = _base(mpzp_zones=[], mpzp_status=_mpzp_status(status, f"REASON_{status.upper()}"))

    mpzp = next(item for item in build_completeness(response).sections if item.section == "mpzp")

    assert mpzp.state == state
    if status == "no_match":
        assert mpzp.basis == "no_match"
    elif status != "available":
        assert f"REASON_{status.upper()}" in mpzp.reason_codes


def test_no_match_never_comes_from_a_source_error_and_is_explained_as_not_a_proof_of_absence() -> None:
    base = _base()
    assert base.pog is not None and base.pog.compatibility_assessment is not None
    # Bez strefy MPZP ocena relacji kończy się ``unknown`` (MPZP_ZONES_MISSING) — tak jak w orkiestratorze.
    unknown = base.pog.compatibility_assessment.model_copy(
        update={"status": "unknown", "reason_code": "MPZP_ZONES_MISSING"}
    )
    response = base.model_copy(
        update={
            "mpzp_zones": [],
            "mpzp_status": _mpzp_status("no_match", "MPZP_NO_ACT_AT_PARCEL"),
            "pog": base.pog.model_copy(update={"compatibility_assessment": unknown}),
        }
    )

    completeness = build_completeness(response)

    assert completeness.complete
    assert any("nie jest dowód braku planu" in note for note in completeness.notes)
    relation = next(item for item in completeness.sections if item.section == "mpzp_pog_relation")
    assert relation.state == "out_of_scope"  # nie ma strefy MPZP do porównania z POG


def test_relation_is_out_of_scope_for_a_zone_without_geometry_but_missing_for_a_vector_zone() -> None:
    """Para MPZP–POG jest przestrzenna: strefa z aktu i dokumentu nie ma geometrii (``PAIRS_NOT_SPATIAL``)."""
    base = _base()
    assert base.pog is not None and base.pog.compatibility_assessment is not None
    unknown = base.pog.compatibility_assessment.model_copy(update={"status": "unknown", "reason_code": "PAIRS_EVALUATED"})
    pog = base.pog.model_copy(update={"compatibility_assessment": unknown})
    candidates = [zone.model_copy(update={"assignment_method": "document_candidate"}) for zone in base.mpzp_zones]

    document = build_completeness(base.model_copy(update={"pog": pog, "mpzp_zones": candidates}))
    vector = build_completeness(base.model_copy(update={"pog": pog}))

    relation = next(item for item in document.sections if item.section == "mpzp_pog_relation")
    assert relation.state == "out_of_scope" and "MPZP_WITHOUT_GEOMETRY" in relation.reason_codes
    assert document.complete and (document.checked, document.total) == (9, 9)
    assert "relacja MPZP–POG" in document.headline  # poza zakresem, ale wymieniona
    assert _states(base.model_copy(update={"pog": pog}))["mpzp_pog_relation"] == "missing" and not vector.complete


def test_relation_stays_missing_when_the_pog_input_itself_is_not_checked() -> None:
    base = _base()
    assert base.pog is not None and base.pog.compatibility_assessment is not None
    unknown = base.pog.compatibility_assessment.model_copy(update={"status": "unknown", "reason_code": "POG_STATUS_UNKNOWN"})
    pog = base.pog.model_copy(update={"coverage_status": "partial", "compatibility_assessment": unknown})
    candidates = [zone.model_copy(update={"assignment_method": "document_candidate"}) for zone in base.mpzp_zones]

    states = _states(base.model_copy(update={"pog": pog, "mpzp_zones": candidates}))

    assert states["pog"] == "missing" and states["mpzp_pog_relation"] == "missing"


def test_snapshot_without_a_status_and_without_zones_is_not_a_confirmed_absence() -> None:
    """Zapis sprzed AU-103 (brak ``mpzp_status``): brak stref nie oznacza braku planu."""
    response = _base(mpzp_zones=[], mpzp_status=None)

    assert _states(response)["mpzp"] == "missing"


@pytest.mark.parametrize("code", ["MPZP_PARTIAL_COVERAGE", "MPZP_ZONES_OVERLAP"])
def test_vector_zones_that_do_not_cover_the_parcel_or_overlap_are_not_a_confirmed_finding(code: str) -> None:
    base = _base()
    warning = WarningMessage(code=code, message="x", severity="warning", source_name="mpzp")

    mpzp = next(
        item
        for item in build_completeness(base.model_copy(update={"warnings": [*base.warnings, warning]})).sections
        if item.section == "mpzp"
    )

    assert mpzp.state == "missing" and code in mpzp.reason_codes


@pytest.mark.parametrize("code", ["MPZP_LLM_UNAVAILABLE", "MPZP_LLM_CANDIDATES_REJECTED"])
def test_degraded_model_path_is_never_a_full_result(code: str) -> None:
    base = _base()
    warning = WarningMessage(code=code, message="x", severity="warning", source_name="mpzp")

    response = base.model_copy(update={"warnings": [*base.warnings, warning]})

    assert _states(response)["mpzp"] == "missing" and not build_completeness(response).complete


def test_document_candidates_do_not_inherit_the_vector_coverage_warnings() -> None:
    """Ostrzeżenie o pokryciu dotyczy wektora; kandydat z dokumentu nie ma geometrii, więc go nie dotyczy."""
    base = _base()
    candidates = [zone.model_copy(update={"assignment_method": "document_candidate"}) for zone in base.mpzp_zones]
    warning = WarningMessage(code="MPZP_PARTIAL_COVERAGE", message="x", severity="warning", source_name="mpzp")

    response = base.model_copy(update={"mpzp_zones": candidates, "warnings": [*base.warnings, warning]})

    assert _states(response)["mpzp"] == "checked"


def test_unresolved_parameter_conflict_is_not_a_checked_mpzp_section() -> None:
    """Dwie sprzeczne wartości w uchwale bez wyboru parsera: strefa jest, ale parametr czeka na człowieka."""
    response, _ = load_fixture("multizone")
    conflicted = _assessed(response).model_copy(update={"section_quality": None, "completeness": None})
    assert any(parameter.conflict_group_id for zone in conflicted.mpzp_zones for parameter in zone.parameters)

    completeness = build_completeness(conflicted)
    mpzp = next(item for item in completeness.sections if item.section == "mpzp")

    assert mpzp.state == "missing" and mpzp.reason_codes[0] == "MPZP_PARAMETER_CONFLICT"
    assert not completeness.complete and with_completeness(conflicted).status == "partial"


# --- poza zakresem ---------------------------------------------------------------------------------------


def test_utilities_are_out_of_scope_whatever_their_data_say_while_transport_and_buildings_are_in_scope() -> None:
    base = _base()
    preview = UtilitiesPreviewResult.model_validate(base.utilities_preview.model_dump()) if base.utilities_preview else None
    for variant in (base, base.model_copy(update={"utilities_preview": None}), base.model_copy(update={"utilities_preview": preview})):
        states = _states(variant)
        assert states["utilities"] == "out_of_scope"
        assert states["transport"] == "checked" and states["buildings"] == "checked"

    completeness = build_completeness(base)
    utilities = next(item for item in completeness.sections if item.section == "utilities")
    assert "KIUT_PREVIEW_ONLY" in utilities.reason_codes
    assert completeness.total == 11 - 1


@pytest.mark.parametrize("section", ["road_context", "neighbour_buildings"])
@pytest.mark.parametrize("status", ["no_coverage", "unavailable", "unknown"])
def test_bdot_sections_without_a_result_are_missing_never_checked(section: str, status: str) -> None:
    """Brak pokrycia, błąd źródła i zapis sprzed sekcji nie są sprawdzeniem (brak danych ≠ brak drogi/budynków)."""
    base = _base()
    changed = getattr(base, section).model_copy(update={"status": status, "reason_code": "TEST"})
    key = "transport" if section == "road_context" else "buildings"

    completeness = build_completeness(base.model_copy(update={section: changed}))

    assert not completeness.complete and _states(base.model_copy(update={section: changed}))[key] == "missing"
    assert f"brak: {'droga i dostęp' if key == 'transport' else 'zabudowa sąsiednia'}" in completeness.headline


def test_empty_bounded_search_with_full_coverage_is_a_checked_road_section_but_partial_coverage_is_not() -> None:
    base = _base()
    empty = base.road_context.model_copy(
        update={"status": "no_match", "reason_code": "SEARCH_RADIUS_EXHAUSTED", "nearest": None, "search_area_within_coverage": True}
    )
    uncovered = empty.model_copy(update={"search_area_within_coverage": False})

    assert _states(base.model_copy(update={"road_context": empty}))["transport"] == "checked"
    assert _states(base.model_copy(update={"road_context": uncovered}))["transport"] == "missing"


def test_out_of_scope_sections_are_listed_not_silently_dropped() -> None:
    completeness = build_completeness(_base())

    assert [item.section for item in completeness.sections] == [
        "parcel", "mpzp", "pog", "pog_overlays", "flood", "nature", "terrain", "utilities", "transport", "buildings",
        "mpzp_pog_relation",
    ]
    assert all(item.label and item.short_label for item in completeness.sections)


# --- komunikat nagłówkowy -----------------------------------------------------------------------------------


def test_headline_counts_checked_over_in_scope_and_lists_what_is_missing() -> None:
    response = _base(terrain=None, mpzp_zones=[], mpzp_status=_mpzp_status("unavailable", "MPZP_SOURCE_UNAVAILABLE"))

    completeness = build_completeness(response)

    assert completeness.headline.startswith(f"{completeness.checked} z {completeness.total} sekcji sprawdzono; brak: ")
    assert "MPZP" in completeness.headline and "teren (NMT)" in completeness.headline
    assert (completeness.checked, completeness.total) == (8, 10)


def test_headline_is_the_same_text_in_the_response_and_in_the_completeness() -> None:
    response = with_completeness(_base(terrain=None))

    assert response.headline == response.completeness.headline


# --- zapis, odczyt i wersje --------------------------------------------------------------------------------


def test_snapshot_roundtrip_preserves_the_assessment_made_at_analysis_time() -> None:
    response = _base()
    completeness = build_completeness(response)

    snapshot = completeness_to_snapshot(completeness)
    json.dumps(snapshot)
    restored = completeness_from_snapshot(snapshot, response.model_copy(update={"terrain": None}))

    assert restored.model_dump(exclude={"origin"}) == completeness.model_dump(exclude={"origin"})
    assert restored.origin == "stored" and "origin" not in snapshot
    assert restored.complete  # późniejsza zmiana odpowiedzi nie przelicza zapisanej oceny


def test_record_from_before_au104_is_reconstructed_and_labelled() -> None:
    reconstructed = completeness_from_snapshot(None, _base())

    assert reconstructed.origin == "reconstructed" and reconstructed.rule_version == RULE_VERSION


def test_result_contract_version_changes_the_cache_signature() -> None:
    assert "+completeness-v1.1" in RESULT_CONTRACT_VERSION and "+road-v1.0" in RESULT_CONTRACT_VERSION
    assert "+buildings-v1.0" in RESULT_CONTRACT_VERSION and "+quality-v1.1" in RESULT_CONTRACT_VERSION
    assert RESULT_CONTRACT_VERSION.endswith("completeness-v1.1")


def test_matrix_from_the_response_is_reused_instead_of_being_assessed_twice() -> None:
    base = _base()
    matrix = build_completeness(base)  # wystawia macierz wewnętrznie
    from app.services.section_quality import build_section_quality

    explicit = build_completeness(base, matrix=build_section_quality(base))

    assert explicit.model_dump() == matrix.model_dump()
    assert isinstance(base.sources[0], SourceMetadata) and TerrainResult.__name__
