import type { AnalyzeResponse, PogResult } from "@/lib/types";

export function buildAnalyzeResponse(
  overrides: Partial<AnalyzeResponse> = {},
): AnalyzeResponse {
  return {
    analysis_id: 42,
    access_token: "token-42",
    status: "complete",
    analyzed_at: "2026-07-16T10:00:00Z",
    parcel: null,
    mpzp_zones: [],
    pog: null,
    infrastructure: [],
    utilities_preview: null,
    risks: [],
    buildable_area_sqm: null,
    manual_zone_required: false,
    warnings: [],
    sources: [],
    ...overrides,
  };
}

export const LEGAL_FORCE =
  "http://inspire.ec.europa.eu/codelist/ProcessStepGeneralValue/legalForce";

/** Minimalny wynik POG v2.1 z jawnie ustawionym statusem i pokryciem. */
export function buildPogResult(overrides: Partial<PogResult> = {}): PogResult {
  const legal = overrides.legal_status ?? "unknown";
  return {
    schema_version: "2.1",
    legal_status: legal,
    coverage_status: "unknown",
    data_availability: "current",
    status_confirmed_at: "2026-09-24T10:00:00Z",
    legal_status_evidence:
      legal === "unknown"
        ? null
        : {
            source_name: "Rejestr Urbanistyczny (lokalne wydanie)",
            official: true,
            reference: "data_release:1",
            source_id: "pog_app",
            raw_value: LEGAL_FORCE,
            confirmed_at: "2026-09-24T10:00:00Z",
          },
    coverage_evidence: null,
    act: null,
    zones: [],
    dominant_zone_id: null,
    ouz: [],
    downtown_areas: [],
    social_infrastructure_standard_areas: [],
    status: legal,
    planning_zone: null,
    zone_type: null,
    in_ouz: false,
    area_ratio: null,
    in_downtown_area: false,
    uchwala_nr: null,
    uchwala_date: null,
    manual_review_required: legal !== "binding",
    compatibility_assessment: null,
    raw_attributes: null,
    ouz_intersection_area_sqm: null,
    ouz_intersection_pct: null,
    touches_ouz_boundary: false,
    source: null,
    ...overrides,
  };
}
