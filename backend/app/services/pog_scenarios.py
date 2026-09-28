"""Scenariusze POG/OUZ i informacyjna ocena relacji MPZP–POG (BK-205).

Ocena nie jest opinią prawną. Porównywane są wyłącznie pary stref, które
przecinają się powierzchniowo w obrębie działki (geometrie EPSG:2180). Para bez
geometrii obu stron (np. symbol MPZP podany ręcznie) nie jest rozstrzygana,
nawet jeżeli tabela reguł zna wynik dla tej kombinacji funkcji. Parametry
różnych stref nigdy nie są uśredniane ani łączone — status całości wynika z
jawnej reguły „najsłabszego ogniwa” opisanej w ``AGGREGATION_RULE``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Final, Literal, Sequence

from shapely import from_wkt
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from app.core.planning_compatibility import (
    COMPATIBILITY_INFORMATIONAL_NOTICE,
    RULE_SET_ID,
    RULE_SET_SOURCE,
    RULE_SET_VERSION,
    check_mpzp_pog_compatibility,
    normalize_mpzp_function,
)
from app.schemas.analyze import (
    CompatibilityAssessment,
    CompatibilitySource,
    CompatibilityStatus,
    CompatibilityZonePair,
    MpzpZoneResult,
    PogResult,
    PogZoneResult,
)
from app.schemas.source import WarningMessage
from app.services.ouz import OuzStatusResult
from app.shared.planning_status import (
    NO_GEOMETRY_IS_NOT_NO_PLAN_PL,
    LegalStatus,
    canonical_legal_status,
)

# Data jest punktem odniesienia opisu okresu przejściowego reformy i pozostaje
# w jednym miejscu, aby aktualizacja po zmianie prawa nie wymagała modyfikacji
# rozgałęzień domenowych ani testów opartych na przypadkowym litera­le tekstowym.
PLANNING_REFORM_REGISTER_DUTY_DATE: Final[date] = date(2026, 1, 1)
PLANNING_REFORM_REGISTER_DUTY_DATE_LABEL: Final[str] = "1 stycznia 2026 r."

LEGAL_INFORMATION_DISCLAIMER: Final[str] = (
    "Analiza ma charakter informacyjny i nie stanowi decyzji urzędowej ani "
    "administracyjnej, ani porady prawnej."
)

# Przecięcie par stref o polu nie większym niż ta tolerancja jest stycznością
# granic, nie wspólną częścią działki (ta sama tolerancja co w BK-202).
PAIR_OVERLAP_TOLERANCE_SQM: Final[float] = 1e-6
# Udział działki, który pary muszą pokryć, aby całość mogła być ``compatible``.
PAIR_COVERAGE_TOLERANCE_PCT: Final[float] = 0.1

AGGREGATION_RULE: Final[str] = (
    "Najsłabsze ogniwo, bez uśredniania: incompatible, jeżeli którakolwiek para "
    "stref jest rozbieżna; w przeciwnym razie unknown, jeżeli którakolwiek para "
    "nie ma reguły lub danych; następnie uncertain; compatible wyłącznie wtedy, "
    "gdy wszystkie pary są compatible, są zidentyfikowane przestrzennie i razem "
    "pokrywają całą działkę. Parametry różnych stref nie są łączone."
)

PogScenarioStatus = LegalStatus


@dataclass(frozen=True)
class PogScenarioResult:
    """Scenariusz POG/OUZ z informacyjną oceną relacji MPZP–POG."""

    status: PogScenarioStatus
    assessment: CompatibilityAssessment
    ouz_status: OuzStatusResult
    manual_review_required: bool
    message: str
    legal_disclaimer: str
    warnings: list[WarningMessage] = field(default_factory=list)


def build_pog_scenario_result(
    mpzp_zones: Sequence[MpzpZoneResult],
    pog_result: PogResult | None,
    ouz_status: OuzStatusResult,
    *,
    as_of: date | datetime,
    parcel_area_sqm: float | None = None,
) -> PogScenarioResult:
    """Buduje scenariusz POG/OUZ bez kategorycznej porady prawnej.

    ``project`` i ``in_progress`` są prawidłowym scenariuszem okresu
    przejściowego, opisanym bez języka obowiązywania. ``superseded`` i
    ``unknown`` nie są interpretowane jako brak planu.
    """
    assessment, warnings = assess_mpzp_pog_compatibility(
        mpzp_zones, pog_result, as_of=as_of, parcel_area_sqm=parcel_area_sqm
    )
    status = canonical_legal_status(pog_result.legal_status if pog_result else None)
    if status in {"project", "in_progress"}:
        transition = (
            "Dostępny jest projekt POG; projekt nie jest aktem wiążącym."
            if status == "project"
            else "Procedura sporządzania POG jest w toku; ustalenia nie są wiążące."
        )
        message = (
            f"{transition} Trwa okres wdrażania reformy planowania; obowiązki "
            "rejestrowe są odnoszone do daty "
            f"{PLANNING_REFORM_REGISTER_DUTY_DATE_LABEL}."
        )
    else:
        message = assessment.rationale
    return PogScenarioResult(
        status=status,
        assessment=assessment,
        ouz_status=ouz_status,
        manual_review_required=(
            assessment.manual_review_required or ouz_status.manual_review_required
        ),
        message=f"{message} {LEGAL_INFORMATION_DISCLAIMER}",
        legal_disclaimer=LEGAL_INFORMATION_DISCLAIMER,
        warnings=warnings,
    )


def assess_mpzp_pog_compatibility(
    mpzp_zones: Sequence[MpzpZoneResult],
    pog: PogResult | None,
    *,
    as_of: date | datetime,
    parcel_area_sqm: float | None = None,
) -> tuple[CompatibilityAssessment, list[WarningMessage]]:
    """Ocena relacji MPZP–POG z jawnymi regułami, źródłami i parami stref.

    Ścieżki bez oceny par są rozdzielone: projekt/procedura POG i akt
    nieaktualny → ``not_applicable`` (nie ustanawiają obowiązku, z którym
    można porównać MPZP), potwierdzony brak aktu → ``not_applicable``, brak
    źródła albo niepotwierdzony status → ``unknown``.
    """
    legal_state_date = _legal_state_date(pog, as_of)
    sources = _sources(mpzp_zones, pog, legal_state_date)
    status = canonical_legal_status(pog.legal_status if pog else None)

    def early(
        result: CompatibilityStatus,
        reason_code: str,
        rationale: str,
        warnings: list[WarningMessage],
    ) -> tuple[CompatibilityAssessment, list[WarningMessage]]:
        return (
            CompatibilityAssessment(
                status=result,
                reason_code=reason_code,
                as_of=legal_state_date,
                rule_id=RULE_SET_ID,
                rule_version=RULE_SET_VERSION,
                aggregation=AGGREGATION_RULE,
                sources=sources,
                rationale=rationale,
                manual_review_required=True,
                zone_pairs=[],
                informational_notice=COMPATIBILITY_INFORMATIONAL_NOTICE,
            ),
            warnings,
        )

    if pog is None:
        return early(
            "unknown",
            "POG_SOURCE_MISSING",
            "Brak wyniku POG w analizie — nie ma źródła, z którym można porównać "
            f"MPZP. {NO_GEOMETRY_IS_NOT_NO_PLAN_PL}",
            [_scenario_unknown_warning()],
        )
    if pog.coverage_status == "no_act_confirmed":
        return early(
            "not_applicable",
            "POG_NO_ACT_CONFIRMED",
            "Źródło urzędowe potwierdza brak planu ogólnego dla działki; relacja "
            "MPZP–POG nie ma zastosowania.",
            [],
        )
    if status == "project":
        return early(
            "not_applicable",
            "POG_PROJECT_NOT_BINDING",
            "Dostępny jest wyłącznie projekt POG. Projekt nie ustanawia obowiązku, "
            "więc relacji z MPZP nie ocenia się jako zgodności ani niezgodności.",
            [_transitional_warning()],
        )
    if status == "in_progress":
        return early(
            "not_applicable",
            "POG_PROCEDURE_IN_PROGRESS",
            "Procedura sporządzania POG jest w toku; brak aktu, który ustanawiałby "
            "obowiązek do porównania z MPZP.",
            [_transitional_warning()],
        )
    if status == "superseded":
        return early(
            "not_applicable",
            "POG_SUPERSEDED",
            "Akt POG w danych jest nieaktualny; ocena relacji z MPZP wymaga aktu, "
            "który go zastąpił, i nie jest wykonywana na akcie nieaktualnym.",
            [_scenario_unknown_warning()],
        )
    if status != "binding":
        return early(
            "unknown",
            "POG_STATUS_UNKNOWN",
            "Nie udało się potwierdzić statusu prawnego POG w źródle urzędowym. "
            f"{NO_GEOMETRY_IS_NOT_NO_PLAN_PL}",
            [_scenario_unknown_warning()],
        )

    positive_mpzp = [zone for zone in mpzp_zones if not zone.touches_boundary]
    if not positive_mpzp:
        return early(
            "unknown",
            "MPZP_ZONES_MISSING",
            "POG obowiązuje, ale analiza nie ustaliła strefy MPZP; brak danych MPZP "
            "nie oznacza braku ograniczeń.",
            [_input_incomplete_warning()],
        )
    if not pog.zones:
        return early(
            "unknown",
            "POG_ZONES_MISSING",
            "POG obowiązuje, ale analiza nie ustaliła stref planistycznych POG na "
            "działce (brak danych przestrzennych).",
            [_input_incomplete_warning()],
        )

    pairs, overlaps = _zone_pairs(positive_mpzp, pog.zones, legal_state_date, parcel_area_sqm)
    if not pairs:
        return early(
            "unknown",
            "NO_SPATIAL_PAIRS",
            "Wydzielenia MPZP i strefy POG nie mają wspólnej części działki — "
            "nie ma pary stref do oceny.",
            [_input_incomplete_warning()],
        )

    result = _aggregate(pairs)
    reason_code = "PAIRS_EVALUATED"
    coverage_note = ""
    if result == "compatible" and not _pairs_cover_parcel(
        overlaps, parcel_area_sqm, pog
    ):
        result = "uncertain"
        reason_code = "PAIRS_PARTIAL_COVERAGE"
        coverage_note = (
            " Pary zgodne z tabelą nie pokrywają całej działki (albo pokrycie POG "
            "jest niepełne), więc całości nie oceniono jako compatible."
        )
    warnings = _pair_warnings(pairs, result)
    spatial_count = sum(1 for pair in pairs if pair.spatially_identified)
    rationale = (
        f"Oceniono {len(pairs)} par(y) stref MPZP × POG, w tym "
        f"{spatial_count} zidentyfikowanych przestrzennie w EPSG:2180. "
        f"{_status_summary(pairs)} Wynik całości ({result}) wynika z reguły "
        f"agregacji najsłabszego ogniwa.{coverage_note}"
    )
    return (
        CompatibilityAssessment(
            status=result,
            reason_code=reason_code,
            as_of=legal_state_date,
            rule_id=RULE_SET_ID,
            rule_version=RULE_SET_VERSION,
            aggregation=AGGREGATION_RULE,
            sources=sources,
            rationale=rationale,
            manual_review_required=(
                result != "compatible" or any(pair.manual_review_required for pair in pairs)
            ),
            zone_pairs=pairs,
            informational_notice=COMPATIBILITY_INFORMATIONAL_NOTICE,
        ),
        warnings,
    )


def _zone_pairs(
    mpzp_zones: Sequence[MpzpZoneResult],
    pog_zones: Sequence[PogZoneResult],
    as_of: date,
    parcel_area_sqm: float | None,
) -> tuple[list[CompatibilityZonePair], list[BaseGeometry]]:
    pairs: list[CompatibilityZonePair] = []
    overlaps: list[BaseGeometry] = []
    pog_geometries = {zone.id: _geometry(zone.intersection_wkt) for zone in pog_zones}
    for mpzp_zone in mpzp_zones:
        mpzp_geometry = _geometry(mpzp_zone.intersection_wkt)
        function = normalize_mpzp_function(mpzp_zone.primary_use)
        for pog_zone in pog_zones:
            pog_geometry = pog_geometries[pog_zone.id]
            overlap_area: float | None = None
            spatial = mpzp_geometry is not None and pog_geometry is not None
            if spatial:
                assert mpzp_geometry is not None and pog_geometry is not None
                overlap = mpzp_geometry.intersection(pog_geometry)
                if overlap.area <= PAIR_OVERLAP_TOLERANCE_SQM:
                    continue
                overlaps.append(overlap)
                overlap_area = overlap.area
            pairs.append(
                _pair(
                    mpzp_zone,
                    pog_zone,
                    function.value if function is not None else None,
                    spatial=spatial,
                    overlap_area=overlap_area,
                    parcel_area_sqm=parcel_area_sqm,
                    as_of=as_of,
                )
            )
    return pairs, overlaps


def _pair(
    mpzp_zone: MpzpZoneResult,
    pog_zone: PogZoneResult,
    function: str | None,
    *,
    spatial: bool,
    overlap_area: float | None,
    parcel_area_sqm: float | None,
    as_of: date,
) -> CompatibilityZonePair:
    base = {
        "mpzp_zone_symbol": mpzp_zone.zone_symbol,
        "mpzp_zone_id": mpzp_zone.zone_id,
        "mpzp_assignment_method": mpzp_zone.assignment_method,
        "mpzp_function": function,
        "pog_zone_id": pog_zone.id,
        "pog_zone_symbol": pog_zone.symbol,
        "pog_zone_type": pog_zone.type,
        "spatially_identified": spatial,
        "overlap_area_sqm": overlap_area,
        "overlap_pct": (
            min(100.0, overlap_area / parcel_area_sqm * 100.0)
            if overlap_area is not None and parcel_area_sqm
            else None
        ),
        "as_of": as_of,
    }
    label = f"{mpzp_zone.zone_symbol} × {pog_zone.symbol or pog_zone.type}"
    if function is None:
        return CompatibilityZonePair(
            **base,
            status="unknown",
            rationale=(
                f"{label}: funkcja strefy MPZP nie jest znormalizowana do katalogu "
                "reguł, więc żadna reguła nie ma zastosowania."
            ),
            manual_review_required=True,
        )
    rule = check_mpzp_pog_compatibility(function, pog_zone.type)
    if rule.result == "unknown":
        return CompatibilityZonePair(
            **base,
            status="unknown",
            rationale=f"{label}: {rule.reasoning}",
            manual_review_required=True,
        )
    rule_fields = {
        "rule_result": rule.result,
        "rule_id": rule.rule_id,
        "rule_version": rule.rule_version,
        "source": rule.rule_source,
    }
    if not spatial:
        return CompatibilityZonePair(
            **base,
            **rule_fields,
            status="uncertain",
            rationale=(
                f"{label}: reguła {rule.rule_id} daje „{rule.result}”, ale para nie "
                "jest zidentyfikowana przestrzennie (brak geometrii strefy — "
                f"przypisanie MPZP: {mpzp_zone.assignment_method}); nie rozstrzygnięto."
            ),
            manual_review_required=True,
        )
    return CompatibilityZonePair(
        **base,
        **rule_fields,
        status=rule.result,
        rationale=f"{label}: {rule.reasoning}",
        manual_review_required=(
            rule.result != "compatible" or mpzp_zone.manual_review_required
        ),
    )


def _aggregate(pairs: Sequence[CompatibilityZonePair]) -> CompatibilityStatus:
    statuses = {pair.status for pair in pairs}
    for candidate in ("incompatible", "unknown", "uncertain"):
        if candidate in statuses:
            return candidate  # type: ignore[return-value]
    return "compatible"


def _pairs_cover_parcel(
    overlaps: Sequence[BaseGeometry],
    parcel_area_sqm: float | None,
    pog: PogResult,
) -> bool:
    if not parcel_area_sqm or pog.coverage_status != "available" or not overlaps:
        return False
    covered_pct = unary_union(list(overlaps)).area / parcel_area_sqm * 100.0
    return covered_pct >= 100.0 - PAIR_COVERAGE_TOLERANCE_PCT


def _status_summary(pairs: Sequence[CompatibilityZonePair]) -> str:
    labels = {
        "compatible": "bez wskazanej rozbieżności",
        "incompatible": "potencjalnie rozbieżne",
        "uncertain": "nierozstrzygnięte",
        "unknown": "bez reguły lub danych",
        "not_applicable": "nie dotyczy",
    }
    counts: dict[str, int] = {}
    for pair in pairs:
        counts[pair.status] = counts.get(pair.status, 0) + 1
    return "Pary: " + ", ".join(
        f"{labels[status]} — {count}" for status, count in sorted(counts.items())
    ) + "."


def _pair_warnings(
    pairs: Sequence[CompatibilityZonePair], result: CompatibilityStatus
) -> list[WarningMessage]:
    warnings: list[WarningMessage] = []
    if result == "incompatible":
        warnings.append(
            _warning(
                "MPZP_POG_POTENTIAL_DIVERGENCE",
                "Tabela reguł wskazuje potencjalną rozbieżność funkcji co najmniej "
                "jednej pary stref MPZP i POG. To analiza informacyjna — wymaga "
                "weryfikacji ustaleń obu aktów.",
                source_name="planning_compatibility",
            )
        )
    if any(not pair.spatially_identified for pair in pairs):
        warnings.append(
            _warning(
                "MPZP_POG_PAIRS_NOT_SPATIAL",
                "Część par stref MPZP i POG nie jest zidentyfikowana przestrzennie "
                "(brak geometrii strefy); takich par nie rozstrzygnięto.",
                source_name="planning_compatibility",
            )
        )
    if any(pair.status == "unknown" for pair in pairs):
        warnings.append(
            _warning(
                "MPZP_POG_COMPATIBILITY_UNKNOWN",
                "Nie można automatycznie ustalić relacji części par stref MPZP i "
                "POG; wynik wymaga ręcznej weryfikacji.",
                source_name="planning_compatibility",
            )
        )
    return warnings


def _legal_state_date(pog: PogResult | None, fallback: date | datetime) -> date:
    """Data stanu prawnego: potwierdzenie statusu POG, inaczej chwila analizy."""
    if pog is not None and pog.status_confirmed_at is not None:
        return pog.status_confirmed_at.date()
    if pog is not None and pog.source is not None and pog.source.fetched_at is not None:
        return pog.source.fetched_at.date()
    return fallback.date() if isinstance(fallback, datetime) else fallback


def _sources(
    mpzp_zones: Sequence[MpzpZoneResult],
    pog: PogResult | None,
    as_of: date,
) -> list[CompatibilitySource]:
    sources = [
        CompatibilitySource(
            kind="rule_set",
            label="Tabela reguł funkcja MPZP × strefa POG",
            reference=RULE_SET_SOURCE,
            version=RULE_SET_VERSION,
            as_of=as_of,
        )
    ]
    if pog is not None:
        act = pog.act
        sources.append(
            CompatibilitySource(
                kind="pog",
                label=f"POG — {act.title or act.id}" if act else "POG",
                reference=(
                    (act.card_url if act and act.card_url_verified else None)
                    or (act.gml_url if act and act.gml_url_verified else None)
                    or (pog.source.source_url if pog.source else None)
                ),
                version=(act.act_version or act.version) if act else None,
                as_of=as_of,
            )
        )
    seen: set[tuple[str | None, str | None]] = set()
    for zone in mpzp_zones:
        selection = zone.manual_selection
        key = (zone.act_identifier or (selection.plan_id if selection else None), zone.act_version)
        if key in seen:
            continue
        seen.add(key)
        sources.append(
            CompatibilitySource(
                kind="mpzp",
                label=f"MPZP — {key[0] or zone.zone_symbol} ({zone.assignment_method})",
                reference=(
                    zone.document_url
                    or (selection.document_url if selection else None)
                    or zone.source.source_url
                ),
                version=(
                    zone.act_version
                    or (selection.document_sha256 if selection else None)
                    or zone.source.artifact_sha256
                ),
                as_of=zone.source.fetched_at.date() if zone.source.fetched_at else None,
            )
        )
    return sources


def _geometry(wkt: str | None) -> BaseGeometry | None:
    if not wkt:
        return None
    geometry = from_wkt(wkt)
    return None if geometry.is_empty else geometry


def _transitional_warning() -> WarningMessage:
    return _warning(
        "POG_TRANSITIONAL_STATUS",
        "Akt POG jest projektem albo w trakcie sporządzania; sprawdź aktualne dokumenty gminy.",
    )


def _scenario_unknown_warning() -> WarningMessage:
    return _warning(
        "POG_SCENARIO_UNKNOWN",
        "Brak potwierdzonego, obowiązującego POG wymaga ręcznej weryfikacji w Rejestrze Urbanistycznym.",
        severity="error",
    )


def _input_incomplete_warning() -> WarningMessage:
    return _warning(
        "MPZP_POG_INPUT_INCOMPLETE",
        "Ocena relacji MPZP–POG wymaga ustalonych stref obu aktów na działce.",
    )


def _warning(
    code: str,
    message: str,
    *,
    severity: Literal["info", "warning", "error"] = "warning",
    source_name: str = "pog",
) -> WarningMessage:
    return WarningMessage(
        code=code,
        message=message,
        severity=severity,
        source_name=source_name,
    )
