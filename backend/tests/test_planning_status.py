"""BK-106: tabela decyzyjna statusu prawnego i pokrycia oraz aliasy.

Każdy wiersz tabeli opisuje obserwację źródła i oczekiwaną, niezależną parę
``legal_status``/``coverage_status`` wraz z dostępnością operacyjną. Test
tablicowy jest kryterium akceptacji BK-106.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.schemas.analyze import PogResult, PogStatusEvidence
from app.shared.planning_status import (
    COVERAGE_STATUS_VALUES,
    LEGAL_STATUS_LABELS_PL,
    LEGAL_STATUS_VALUES,
    ConfirmedPogStatus,
    PogStatusObservation,
    StatusEvidence,
    canonical_coverage_status,
    canonical_legal_status,
    inspire_status_uri,
    is_official_status_code,
    normalize_official_legal_status,
    pog_status_notes_pl,
    resolve_pog_status,
    upgrade_legacy_legal_status,
)

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
EARLIER = datetime(2026, 8, 19, 1, 0, tzinfo=timezone.utc)
INSPIRE = "http://inspire.ec.europa.eu/codelist/ProcessStepGeneralValue/"
LEGAL_FORCE = f"{INSPIRE}legalForce"
ADOPTION = f"{INSPIRE}adoption"
ELABORATION = f"{INSPIRE}elaboration"
OBSOLETE = f"{INSPIRE}obsolete"
RU = StatusEvidence("Rejestr Urbanistyczny", official=True, reference="https://rejestr-urbanistyczny.gov.pl/")
NO_ACT = StatusEvidence(
    "Urząd Gminy — pismo",
    official=True,
    reference="https://bip.example.gov.pl/pismo-brak-pog.pdf",
    confirmed_at=NOW,
)


def _obs(**kwargs) -> PogStatusObservation:
    kwargs.setdefault("source_responded", True)
    kwargs.setdefault("checked_at", NOW)
    return PogStatusObservation(**kwargs)


def _evidence(raw: str | None) -> StatusEvidence:
    return StatusEvidence(RU.source_name, official=True, reference=RU.reference, raw_value=raw)


# id, obserwacja, (legal, coverage, availability)
DECISION_TABLE = [
    (
        "binding+available",
        _obs(raw_legal_status=LEGAL_FORCE, legal_evidence=_evidence(LEGAL_FORCE), act_found=True,
             act_has_spatial_data=True, spatial_features_on_parcel=3, zones_cover_parcel=True,
             response_complete=True),
        ("binding", "available", "current"),
    ),
    (
        "project+available",
        _obs(raw_legal_status=ADOPTION, legal_evidence=_evidence(ADOPTION), act_found=True,
             act_has_spatial_data=True, spatial_features_on_parcel=2, zones_cover_parcel=True,
             response_complete=True),
        ("project", "available", "current"),
    ),
    (
        "in_progress+partial",
        _obs(raw_legal_status=ELABORATION, legal_evidence=_evidence(ELABORATION), act_found=True,
             act_has_spatial_data=True, spatial_features_on_parcel=1, zones_cover_parcel=False,
             response_complete=True),
        ("in_progress", "partial", "current"),
    ),
    (
        "binding+unknown (akt bez danych w odpowiedzi, brak dowodu zakresu)",
        _obs(raw_legal_status=LEGAL_FORCE, legal_evidence=_evidence(LEGAL_FORCE)),
        ("binding", "unknown", "current"),
    ),
    (
        "binding+act_without_spatial_data",
        _obs(raw_legal_status=LEGAL_FORCE, legal_evidence=_evidence(LEGAL_FORCE), act_found=True,
             act_has_spatial_data=False),
        ("binding", "act_without_spatial_data", "current"),
    ),
    (
        "binding+partial (dane aktu nie przecinają działki)",
        _obs(raw_legal_status=LEGAL_FORCE, legal_evidence=_evidence(LEGAL_FORCE), act_found=True,
             act_has_spatial_data=True),
        ("binding", "partial", "current"),
    ),
    (
        "binding+partial (niepełna paginacja)",
        _obs(raw_legal_status=LEGAL_FORCE, legal_evidence=_evidence(LEGAL_FORCE), act_found=True,
             act_has_spatial_data=True, spatial_features_on_parcel=4, zones_cover_parcel=True,
             response_complete=False),
        ("binding", "partial", "current"),
    ),
    (
        "superseded+available",
        _obs(raw_legal_status=OBSOLETE, legal_evidence=_evidence(OBSOLETE), act_found=True,
             act_has_spatial_data=True, spatial_features_on_parcel=1, zones_cover_parcel=True,
             response_complete=True),
        ("superseded", "available", "current"),
    ),
    (
        "unknown+available (geometria bez urzędowego kodu)",
        _obs(raw_legal_status=None, act_found=True, act_has_spatial_data=True,
             spatial_features_on_parcel=2, zones_cover_parcel=True, response_complete=True),
        ("unknown", "available", "current"),
    ),
    (
        "unknown (słowo 'uchwalony' nie jest kodem urzędowym)",
        _obs(raw_legal_status="uchwalony", legal_evidence=_evidence("uchwalony"), act_found=True,
             act_has_spatial_data=False),
        ("unknown", "act_without_spatial_data", "current"),
    ),
    (
        "unknown (kod z nieurzędowego źródła)",
        _obs(raw_legal_status=LEGAL_FORCE,
             legal_evidence=StatusEvidence("Serwis komercyjny", official=False, raw_value=LEGAL_FORCE),
             act_found=True, act_has_spatial_data=False),
        ("unknown", "act_without_spatial_data", "current"),
    ),
    (
        "pusty WFS",
        _obs(response_complete=False),
        ("unknown", "unknown", "current"),
    ),
    (
        "pusty WFS z pełną paginacją (nadal brak dowodu braku aktu)",
        _obs(response_complete=True),
        ("unknown", "unknown", "current"),
    ),
    (
        "pusty WFS + urzędowe potwierdzenie braku aktu",
        _obs(response_complete=True, no_act_evidence=NO_ACT),
        ("unknown", "no_act_confirmed", "current"),
    ),
    (
        "potwierdzenie braku aktu bez wskazania dokumentu",
        _obs(no_act_evidence=StatusEvidence("Urząd", official=True, reference=None)),
        ("unknown", "unknown", "current"),
    ),
    (
        "potwierdzenie braku aktu z nieurzędowego źródła",
        _obs(no_act_evidence=StatusEvidence("Forum", official=False, reference="https://x.example")),
        ("unknown", "unknown", "current"),
    ),
    (
        "timeout bez wcześniejszej wartości",
        _obs(source_responded=False),
        ("unknown", "unknown", "unavailable"),
    ),
    (
        "timeout z ostatnią potwierdzoną wartością",
        _obs(source_responded=False, previous=ConfirmedPogStatus(
            "binding", "available", EARLIER, legal_evidence=_evidence(LEGAL_FORCE))),
        ("binding", "available", "stale"),
    ),
    (
        "timeout z wcześniejszym projektem",
        _obs(source_responded=False, previous=ConfirmedPogStatus("project", "unknown", EARLIER)),
        ("project", "unknown", "stale"),
    ),
    (
        "timeout; wcześniejsza wartość nieustalona",
        _obs(source_responded=False, previous=ConfirmedPogStatus("unknown", "unknown", EARLIER)),
        ("unknown", "unknown", "unavailable"),
    ),
]


@pytest.mark.parametrize(
    ("observation", "expected"),
    [pytest.param(obs, expected, id=case_id) for case_id, obs, expected in DECISION_TABLE],
)
def test_decision_table(observation: PogStatusObservation, expected: tuple[str, str, str]) -> None:
    decision = resolve_pog_status(observation)

    assert (decision.legal_status, decision.coverage_status, decision.data_availability) == expected
    assert decision.legal_status in LEGAL_STATUS_VALUES
    assert decision.coverage_status in COVERAGE_STATUS_VALUES
    if decision.legal_status != "unknown" and decision.data_availability == "current":
        assert decision.legal_evidence is not None and decision.legal_evidence.official
    if decision.coverage_status == "no_act_confirmed":
        assert decision.coverage_evidence is NO_ACT
    if decision.data_availability == "stale":
        assert decision.confirmed_at == EARLIER
    # Kontrakt API akceptuje każdą decyzję tabeli (z dowodem dla binding).
    result = PogResult(
        legal_status=decision.legal_status,
        coverage_status=decision.coverage_status,
        data_availability=decision.data_availability,
        status_confirmed_at=decision.confirmed_at,
        legal_status_evidence=(
            PogStatusEvidence(
                source_name=decision.legal_evidence.source_name,
                official=decision.legal_evidence.official,
                reference=decision.legal_evidence.reference,
            )
            if decision.legal_evidence
            else None
        ),
        coverage_evidence=(
            PogStatusEvidence(
                source_name=decision.coverage_evidence.source_name,
                official=True,
                reference=decision.coverage_evidence.reference,
            )
            if decision.coverage_evidence
            else None
        ),
        touches_ouz_boundary=False,
    )
    assert result.status == result.legal_status


def test_decision_table_covers_every_canonical_value() -> None:
    decisions = [resolve_pog_status(obs) for _id, obs, _expected in DECISION_TABLE]
    assert {d.legal_status for d in decisions} == set(LEGAL_STATUS_VALUES)
    assert {d.coverage_status for d in decisions} == set(COVERAGE_STATUS_VALUES)
    assert {d.data_availability for d in decisions} == {"current", "stale", "unavailable"}


def test_empty_response_never_reports_no_act_or_binding() -> None:
    for complete in (False, True):
        decision = resolve_pog_status(_obs(response_complete=complete))
        assert decision.coverage_status != "no_act_confirmed"
        assert decision.legal_status == "unknown"
        assert "POG_EMPTY_RESPONSE_NOT_ABSENCE" in decision.reasons


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (LEGAL_FORCE, "binding"),
        ("legalForce", "binding"),
        ("prawnie wiążący lub realizowany", "binding"),
        ("APP.POG.SJ.PrawnieWiazacyLubRealizowany", "binding"),
        ("obowiązujący", "binding"),
        (ADOPTION, "project"),
        ("APP.POG.WTrakciePrzyjmowania", "project"),
        ("projekt", "project"),
        (ELABORATION, "in_progress"),
        ("APP.POG.OUZ.WOpracowaniu", "in_progress"),
        ("w trakcie sporządzania", "in_progress"),
        (OBSOLETE, "superseded"),
        ("nieaktualny", "superseded"),
        ("uchylony", "superseded"),
        ("uchwalony", "unknown"),
        ("adopted", "unknown"),
        ("binding", "unknown"),
        ("nie obowiązuje", "unknown"),
        ("", "unknown"),
        (None, "unknown"),
    ],
)
def test_official_code_mapping(raw: str | None, expected: str) -> None:
    assert normalize_official_legal_status(raw) == expected
    assert is_official_status_code(raw) is (expected != "unknown")


@pytest.mark.parametrize("status", ["binding", "project", "in_progress", "superseded"])
def test_inspire_uri_roundtrip(status: str) -> None:
    uri = inspire_status_uri(status)  # type: ignore[arg-type]
    assert uri is not None
    assert normalize_official_legal_status(uri) == status
    assert inspire_status_uri("unknown") is None


@pytest.mark.parametrize(
    ("legacy", "confirmed", "expected"),
    [
        ("adopted", True, "binding"),
        ("adopted", False, "unknown"),
        ("not_available", True, "unknown"),
        ("outdated", False, "superseded"),
        ("in_progress", False, "in_progress"),
        ("project", False, "project"),
        ("binding", False, "binding"),
        ("superseded", False, "superseded"),
        ("raster_only", False, "unknown"),
        (None, False, "unknown"),
    ],
)
def test_legacy_alias_upgrade(legacy: str | None, confirmed: bool, expected: str) -> None:
    assert upgrade_legacy_legal_status(legacy, confirmed=confirmed) == expected


def test_canonical_helpers_map_aliases_only() -> None:
    assert canonical_legal_status("outdated") == "superseded"
    assert canonical_legal_status("adopted") == "unknown"
    assert canonical_coverage_status("complete") == "available"
    assert canonical_coverage_status("covered") == "unknown"
    assert canonical_coverage_status("partial") == "partial"


# --- Kontrakt API: wsteczna zgodność i niezmienniki -------------------------


def _legacy_payload(**overrides) -> dict:
    payload = {
        "schema_version": "2.0",
        "legal_status": "adopted",
        "coverage_status": "complete",
        "status": "adopted",
        "touches_ouz_boundary": False,
    }
    payload.update(overrides)
    return payload


def test_legacy_adopted_without_confirmation_reads_as_unknown() -> None:
    result = PogResult.model_validate(_legacy_payload())
    assert result.legal_status == "unknown"
    assert result.coverage_status == "available"
    assert result.status == "unknown"
    assert result.legal_status_evidence is None


def test_legacy_adopted_with_pinned_release_reads_as_binding() -> None:
    result = PogResult.model_validate(
        _legacy_payload(
            act={"id": "pog-1", "version": "20260819T010000"},
            source={
                "source_id": "pog_app",
                "source_name": "POG_APP_LOCAL_POSTGIS",
                "artifact_sha256": "b" * 64,
                "data_release_id": 7,
                "fetched_at": "2026-08-19T01:00:00Z",
                "confidence": 1.0,
                "manual_review_required": False,
            },
        )
    )
    assert result.legal_status == "binding"
    assert result.legal_status_evidence is not None
    assert result.legal_status_evidence.reference == (
        f"data_release:7;sha256:{'b' * 64};act_version:20260819T010000"
    )
    assert result.status_confirmed_at == datetime(2026, 8, 19, 1, 0, tzinfo=timezone.utc)


def test_legacy_v1_with_official_raw_code_reads_as_binding() -> None:
    result = PogResult.model_validate(
        {
            "status": "adopted",
            "touches_ouz_boundary": False,
            "raw_attributes": {"app_metadata": {"raw_legal_status": LEGAL_FORCE}},
        }
    )
    assert result.legal_status == "binding"
    assert result.legal_status_evidence is not None
    assert result.legal_status_evidence.raw_value == LEGAL_FORCE


def test_legacy_v1_with_non_binding_raw_code_is_not_promoted() -> None:
    result = PogResult.model_validate(
        {
            "status": "adopted",
            "touches_ouz_boundary": False,
            "raw_attributes": {"app_metadata": {"raw_legal_status": ADOPTION}},
        }
    )
    assert result.legal_status == "unknown"


@pytest.mark.parametrize(
    ("legacy", "expected_legal", "expected_availability"),
    [
        ("not_available", "unknown", "current"),
        ("in_progress", "in_progress", "current"),
        ("unknown", "unknown", "unavailable"),
        ("outdated", "superseded", "current"),
    ],
)
def test_legacy_status_aliases_in_api_contract(
    legacy: str, expected_legal: str, expected_availability: str
) -> None:
    result = PogResult.model_validate({"status": legacy, "touches_ouz_boundary": False})
    assert result.legal_status == expected_legal
    assert result.data_availability == expected_availability


def test_binding_requires_official_evidence() -> None:
    with pytest.raises(ValueError, match="binding wymaga urzędowego"):
        PogResult(legal_status="binding", touches_ouz_boundary=False)
    with pytest.raises(ValueError, match="binding wymaga urzędowego"):
        PogResult(
            legal_status="binding",
            legal_status_evidence=PogStatusEvidence(source_name="X", official=False),
            touches_ouz_boundary=False,
        )


def test_no_act_confirmed_requires_official_reference() -> None:
    with pytest.raises(ValueError, match="no_act_confirmed"):
        PogResult(coverage_status="no_act_confirmed", touches_ouz_boundary=False)
    with pytest.raises(ValueError, match="no_act_confirmed"):
        PogResult(
            coverage_status="no_act_confirmed",
            coverage_evidence=PogStatusEvidence(source_name="Urząd", official=True),
            touches_ouz_boundary=False,
        )


def test_stale_requires_confirmation_date() -> None:
    with pytest.raises(ValueError, match="stale"):
        PogResult(data_availability="stale", touches_ouz_boundary=False)


def test_non_canonical_new_value_is_rejected() -> None:
    with pytest.raises(ValueError):
        PogResult(coverage_status="covered", touches_ouz_boundary=False)  # type: ignore[arg-type]


# --- Język prezentacji -------------------------------------------------------


@pytest.mark.parametrize("legal", ["project", "in_progress", "unknown"])
@pytest.mark.parametrize("coverage", list(COVERAGE_STATUS_VALUES))
@pytest.mark.parametrize("availability", ["current", "stale", "unavailable"])
def test_non_binding_language_never_says_in_force(
    legal: str, coverage: str, availability: str
) -> None:
    texts = [LEGAL_STATUS_LABELS_PL[legal], *pog_status_notes_pl(legal, coverage, availability, NOW)]
    joined = " ".join(texts).lower()
    assert "obowiązuj" not in joined
    assert "brak planu" not in joined.replace("braku planu", "")


@pytest.mark.parametrize("coverage", ["act_without_spatial_data", "unknown", "partial"])
def test_missing_geometry_is_explicitly_not_missing_plan(coverage: str) -> None:
    notes = pog_status_notes_pl("binding", coverage, "current")
    assert any("nie oznacza braku planu" in note for note in notes)


def test_stale_note_contains_confirmation_date() -> None:
    notes = pog_status_notes_pl("binding", "available", "stale", EARLIER)
    assert any("19.08.2026" in note for note in notes)
