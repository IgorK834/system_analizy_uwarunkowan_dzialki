import type { AnalyzeResponse } from "@/lib/types";

export function buildAnalyzeResponse(
  overrides: Partial<AnalyzeResponse> = {},
): AnalyzeResponse {
  return {
    analysis_id: 42,
    status: "complete",
    analyzed_at: "2026-07-16T10:00:00Z",
    parcel: null,
    mpzp_zones: [],
    pog: null,
    infrastructure: [],
    risks: [],
    buildable_area_sqm: null,
    manual_zone_required: false,
    warnings: [],
    sources: [],
    ...overrides,
  };
}
