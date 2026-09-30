"""Macierz kompletności i świeżości sekcji analizy (BK-504, ADR-011).

Dla każdej sekcji analizy (także pustej) powstaje ``SectionQuality``: status
według kontraktu źródła, źródło z katalogu, czas pobrania, wydanie i wersja,
flaga ręcznej weryfikacji oraz świeżość. Macierz jest wystawiana raz — przy
zapisie analizy — i zapisywana w ``analyses.section_quality``; raport, API i UI
czytają ten zapis, więc ocena historyczna nie zmienia się z upływem czasu.

Zasady:

- **status** wynika z kontraktu sekcji (``RiskSectionResult.status``,
  ``TerrainResult.status``, ``coverage_status`` POG, ``UtilitiesPreviewResult``…),
  a nie ze świeżości. Brak pokrycia (``no_coverage``), niedostępność
  (``unavailable``) i błąd (``error``) są odrębne; ręczna weryfikacja to flaga;
- **świeżość** liczona jest względem ``analyzed_at`` i reguły *źródła* z katalogu
  (``freshness_policy``). Źródło bez reguły daje ``unknown`` — nie istnieje
  globalny TTL. ``fetched_at`` to czas pobrania, nie wejścia aktu w życie;
- dodatkowy wiek na dzień eksportu jest liczony osobno w raporcie i nie wchodzi
  do zapisu ani do ``matrix_sha256``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Final, Literal

from app.core.data_sources import (
    QUALITY_POLICY_SCHEMA,
    CatalogError,
    DataSourceCatalog,
    get_catalog,
)
from app.modules.reporting.domain.sections import QUALITY_SECTIONS
from app.schemas.analyze import AnalyzeResponse, PogResult, RiskSectionResult
from app.schemas.source import (
    FreshnessAssessment,
    SectionKey,
    SectionQuality,
    SectionQualityMatrix,
    SourceMetadata,
)
from app.services.risks import risk_sections_from_snapshot
from app.services.terrain import terrain_from_snapshot
from app.shared.data_quality import (
    FRESHNESS_FUTURE_TIME,
    FRESHNESS_INVALID_TIME,
    FRESHNESS_NO_SOURCE,
    FRESHNESS_OLDER_THAN_POLICY,
    FreshnessRule,
    FreshnessVerdict,
    SectionQualityStatus,
    evaluate_freshness,
    unknown_freshness,
)

logger = logging.getLogger(__name__)

NO_CATALOG_POLICY_VERSION: Final[str] = f"{QUALITY_POLICY_SCHEMA}+no-catalog"
LEGACY_RECONSTRUCTED: Final[str] = "LEGACY_QUALITY_RECONSTRUCTED"

# Zgodność wsteczna: zapisy sprzed ``source_id`` w SourceMetadata rozpoznajemy po
# nazwie. Nowe wyniki niosą identyfikator z katalogu i tej tabeli nie używają.
_LEGACY_SOURCE_NAME_TO_ID: Final[dict[str, str]] = {
    "uldk": "uldk",
    "kimpzp": "kimpzp",
    "isok": "isok",
    "gdos": "gdos",
    "nmt": "nmt",
    "nmt_wcs": "nmt_wcs",
    "kiut": "kiut_gesut",
    "kiut (gugik)": "kiut_wms",
    "pog_app_vector": "pog_app",
}
_NON_CATALOG_SOURCE_NAMES: Final[frozenset[str]] = frozenset({"manual_user_input", "cache"})
_REVIEW_CODES: Final[frozenset[str]] = frozenset(
    {
        "MPZP_REVIEW_REQUIRED",
        "MPZP_MANUAL_ZONE",
        "MPZP_MANUAL_ZONE_REQUIRED",
        "POG_REVIEW_REQUIRED",
        "COMPATIBILITY_REVIEW_REQUIRED",
        "SOURCE_RECORD_MISSING",
    }
)
_TIME_PROBLEM_CODES: Final[frozenset[str]] = frozenset(
    {FRESHNESS_OLDER_THAN_POLICY, FRESHNESS_FUTURE_TIME, FRESHNESS_INVALID_TIME}
)


@dataclass
class _Draft:
    """Wynik kontraktowy sekcji przed oceną świeżości."""

    status: SectionQualityStatus
    source: SourceMetadata | None = None
    manual: bool = False
    codes: list[str] = field(default_factory=list)


def _load_catalog() -> DataSourceCatalog | None:
    try:
        return get_catalog()
    except CatalogError:
        # Docs są montowane do obrazu; brak katalogu nie może blokować zapisu
        # analizy. Ocena dostaje wersję ``no-catalog`` i świeżość ``unknown``.
        logger.warning("section_quality_catalog_unavailable")
        return None


def build_section_quality(
    response: AnalyzeResponse,
    *,
    reference_at: datetime | None = None,
    catalog: DataSourceCatalog | None = None,
    origin: Literal["stored", "reconstructed"] = "stored",
) -> SectionQualityMatrix:
    """Wystawia macierz jakości sekcji dla wyniku analizy.

    ``reference_at`` (domyślnie ``response.analyzed_at``) jest punktem odniesienia
    świeżości. Czas bez strefy jest interpretowany jako UTC. ``catalog`` można
    wstrzyknąć w testach; domyślnie używany jest katalog repozytorium.
    """
    active_catalog = catalog if catalog is not None else _load_catalog()
    reference = _aware(reference_at or response.analyzed_at)
    policy_version = (
        active_catalog.quality_policy_version() if active_catalog else NO_CATALOG_POLICY_VERSION
    )
    drafts = _drafts(response)
    sections = [
        _finish(
            spec.key,  # type: ignore[arg-type]
            spec.report_section,
            drafts[spec.key],
            reference,
            active_catalog,
            policy_version,
            origin,
        )
        for spec in QUALITY_SECTIONS
    ]
    matrix = SectionQualityMatrix(
        policy_version=policy_version,
        reference_at=reference,
        origin=origin,
        sections=sections,
        matrix_sha256="0" * 64,
    )
    return matrix.model_copy(update={"matrix_sha256": matrix.compute_sha256()})


def with_section_quality(
    response: AnalyzeResponse, *, catalog: DataSourceCatalog | None = None
) -> AnalyzeResponse:
    """Odpowiedź z wystawioną macierzą (istniejąca macierz nie jest nadpisywana)."""
    if response.section_quality is not None:
        return response
    return response.model_copy(
        update={"section_quality": build_section_quality(response, catalog=catalog)}
    )


def quality_to_snapshot(matrix: SectionQualityMatrix) -> dict[str, Any]:
    """Zapis JSONB: bez legendy (jest wyliczana przy odczycie z bieżących etykiet)."""
    return matrix.model_dump(mode="json", exclude={"legend"})


def quality_from_snapshot(
    raw: dict[str, Any] | None,
    response: AnalyzeResponse,
    analyzed_at: datetime,
) -> SectionQualityMatrix:
    """Odczyt zapisanej macierzy albo — dla zapisu sprzed BK-504 — jej odtworzenie.

    Odtworzona macierz ma ``origin='reconstructed'`` i kod
    ``LEGACY_QUALITY_RECONSTRUCTED``; nie jest zapisywana i nie udaje oceny
    z chwili analizy (używa bieżącej polityki).
    """
    if raw is not None:
        return SectionQualityMatrix.model_validate(raw)
    return build_section_quality(
        response, reference_at=analyzed_at, origin="reconstructed"
    )


# --- Ocena jednej sekcji ---------------------------------------------------------


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _finish(
    key: SectionKey,
    report_section: str,
    draft: _Draft,
    reference: datetime,
    catalog: DataSourceCatalog | None,
    policy_version: str,
    origin: str,
) -> SectionQuality:
    source = draft.source
    source_id, source_codes = resolve_source_id(source, catalog)
    rule: FreshnessRule | None = catalog.freshness_rule(source_id) if catalog else None
    if source is None:
        verdict: FreshnessVerdict = unknown_freshness(FRESHNESS_NO_SOURCE)
    else:
        verdict = evaluate_freshness(source.fetched_at, reference, rule)

    manual = draft.manual or bool(source and source.manual_review_required)
    codes = list(draft.codes)
    if manual and draft.status in {"available", "partial"} and not _REVIEW_CODES & set(codes):
        codes.append("SOURCE_REVIEW_REQUIRED")
    codes.extend(code for code in source_codes if code not in codes)
    if verdict.reason_code in _TIME_PROBLEM_CODES and verdict.reason_code not in codes:
        codes.append(verdict.reason_code)
    if origin == "reconstructed":
        codes.append(LEGACY_RECONSTRUCTED)

    return SectionQuality(
        section=key,
        report_section=report_section,
        status=draft.status,
        source_id=source_id,
        source_name=source.source_name if source else None,
        fetched_at=source.fetched_at if source else None,
        data_release_id=source.data_release_id if source else None,
        source_version=source.source_version if source else None,
        manual_review_required=manual,
        freshness=FreshnessAssessment(
            state=verdict.state,
            reason_code=verdict.reason_code,
            reference_at=reference,
            age_seconds=verdict.age_seconds,
            max_age_days=verdict.max_age_days,
            basis=verdict.basis,
        ),
        policy_version=policy_version,
        reason_codes=codes,
    )


def resolve_source_id(
    source: SourceMetadata | None, catalog: DataSourceCatalog | None
) -> tuple[str | None, list[str]]:
    """Identyfikator źródła z katalogu (albo jawny brak) i kody wyjaśniające."""
    if source is None:
        return None, []
    name = source.source_name.strip().casefold()
    if source.source_id:
        if catalog is not None and catalog.find(source.source_id) is None:
            return source.source_id, ["SOURCE_NOT_IN_CATALOG"]
        return source.source_id, []
    if name in _NON_CATALOG_SOURCE_NAMES:
        return None, []
    legacy = _LEGACY_SOURCE_NAME_TO_ID.get(name)
    if legacy is not None:
        return legacy, []
    return None, ["SOURCE_ID_UNRESOLVED"]


# --- Kontrakty sekcji ----------------------------------------------------------------


def _drafts(response: AnalyzeResponse) -> dict[str, _Draft]:
    pog_status, pog_codes = _pog_status(response.pog)
    risk = _risk_by_section(response)
    return {
        "parcel": _parcel(response),
        "mpzp": _mpzp(response),
        "pog": _pog(response.pog, pog_status, pog_codes),
        "pog_overlays": _pog(response.pog, pog_status, pog_codes),
        "flood": _risk(risk["flood"]),
        "nature": _risk(risk["nature"]),
        "terrain": _terrain(response),
        "utilities": _utilities(response),
        "transport": _Draft("out_of_scope", None, False, ["NO_SOURCE_CONTRACT"]),
        "mpzp_pog_relation": _relation(response.pog),
    }


def _parcel(response: AnalyzeResponse) -> _Draft:
    parcel = response.parcel
    if parcel is None:
        return _Draft("unknown", None, False, ["PARCEL_GEOMETRY_MISSING"])
    codes = ["GEOMETRY_REPAIRED"] if parcel.metrics.geometry_repaired else []
    source: SourceMetadata | None = parcel.source
    if source.source_name.casefold() == "cache" and source.source_id is None:
        # Zastępczy wpis odczytu z bazy: nie jest źródłem geometrii.
        return _Draft("available", None, True, [*codes, "SOURCE_RECORD_MISSING"])
    return _Draft("available", source, False, codes)


def _mpzp(response: AnalyzeResponse) -> _Draft:
    zones = response.mpzp_zones
    if response.manual_zone_required:
        return _Draft("awaiting_input", None, True, ["MPZP_MANUAL_ZONE_REQUIRED"])
    if not zones:
        return _Draft("unknown", None, False, ["MPZP_NOT_DETERMINED"])
    partial_codes: list[str] = []
    if any(zone.assignment_method == "manual_user_input" for zone in zones):
        partial_codes.append("MPZP_MANUAL_ZONE")
    if any(zone.assignment_method == "document_candidate" for zone in zones):
        partial_codes.append("MPZP_DOCUMENT_CANDIDATE")
    if any(zone.assignment_method == "legacy" for zone in zones):
        partial_codes.append("MPZP_LEGACY_SNAPSHOT")
    if any(parameter.conflict_group_id for zone in zones for parameter in zone.parameters):
        partial_codes.append("MPZP_PARAMETER_CONFLICT")
    if any(zone.intersection_pct is None and not zone.touches_boundary for zone in zones):
        partial_codes.append("MPZP_SHARE_UNDETERMINED")
    review = any(zone.manual_review_required or zone.source.manual_review_required for zone in zones)
    codes = list(partial_codes)
    if review and not partial_codes:
        codes.append("MPZP_REVIEW_REQUIRED")
    status: SectionQualityStatus = "partial" if partial_codes else "available"
    # Źródło sekcji to źródło pierwszej strefy; strefy jednej analizy pochodzą z
    # jednego przypiętego wydania (``mpzp_as_of``).
    return _Draft(status, zones[0].source, review, codes)


def _pog_status(pog: PogResult | None) -> tuple[SectionQualityStatus, list[str]]:
    if pog is None:
        return "unknown", ["POG_RESULT_MISSING"]
    if pog.data_availability == "unavailable":
        return "unavailable", ["POG_SOURCE_UNAVAILABLE"]
    if pog.coverage_status == "unknown":
        return "unknown", ["POG_COVERAGE_UNKNOWN"]
    if pog.coverage_status == "act_without_spatial_data":
        return "partial", ["POG_ACT_WITHOUT_SPATIAL_DATA"]
    if pog.coverage_status == "partial":
        return "partial", ["POG_COVERAGE_PARTIAL"]
    if pog.data_availability == "stale":
        return "partial", ["POG_DATA_STALE"]
    if pog.coverage_status == "no_act_confirmed":
        return "available", ["POG_NO_ACT_CONFIRMED"]
    return "available", []


def _pog(
    pog: PogResult | None, status: SectionQualityStatus, codes: list[str]
) -> _Draft:
    if pog is None:
        return _Draft(status, None, False, list(codes))
    manual = pog.manual_review_required
    result_codes = list(codes)
    if manual:
        result_codes.append("POG_REVIEW_REQUIRED")
    return _Draft(status, pog.source, manual, result_codes)


def _risk_by_section(response: AnalyzeResponse) -> dict[str, RiskSectionResult]:
    sections = {section.section: section for section in response.risk_sections}
    for section in risk_sections_from_snapshot(None):
        sections.setdefault(section.section, section)
    return sections


def _risk(section: RiskSectionResult) -> _Draft:
    codes = [section.reason_code] if section.reason_code else []
    if section.status == "unknown" and not codes:
        codes.append("RISK_SECTION_NOT_RECORDED")
    return _Draft(section.status, section.source, False, codes)


def _terrain(response: AnalyzeResponse) -> _Draft:
    terrain = response.terrain or terrain_from_snapshot(None)
    codes = [terrain.reason_code] if terrain.reason_code else []
    status: SectionQualityStatus = terrain.status
    relief = terrain.relief
    if status == "available" and relief is not None and relief.status != "available":
        status = "partial"
        codes.append("TERRAIN_RELIEF_NOT_AVAILABLE")
        if relief.reason_code:
            codes.append(relief.reason_code)
    return _Draft(status, terrain.source, False, codes)


def _utilities(response: AnalyzeResponse) -> _Draft:
    preview = response.utilities_preview
    if preview is None:
        return _Draft("unknown", None, False, ["KIUT_COVERAGE_NOT_CHECKED"])
    if preview.coverage_status == "covered":
        return _Draft("partial", preview.source, False, ["KIUT_PREVIEW_ONLY"])
    if preview.coverage_status == "not_covered":
        return _Draft("no_coverage", preview.source, False, ["KIUT_COUNTY_NOT_PUBLISHED"])
    return _Draft("unknown", preview.source, False, ["KIUT_COVERAGE_UNKNOWN"])


def _relation(pog: PogResult | None) -> _Draft:
    assessment = pog.compatibility_assessment if pog else None
    if assessment is None:
        return _Draft("unknown", None, False, ["COMPATIBILITY_NOT_ASSESSED", "DERIVED_SECTION"])
    codes: list[str] = []
    # ``unknown`` = nie ustalono relacji; ``not_applicable`` = ocenę wykonano i
    # wynikiem jest „nie dotyczy” (np. projekt POG) — status ``available`` z powodem.
    status: SectionQualityStatus = "unknown" if assessment.status == "unknown" else "available"
    if assessment.status in {"unknown", "not_applicable"}:
        codes.append(assessment.reason_code)
    if assessment.manual_review_required:
        codes.append("COMPATIBILITY_REVIEW_REQUIRED")
    codes.append("DERIVED_SECTION")
    return _Draft(status, None, assessment.manual_review_required, codes)
