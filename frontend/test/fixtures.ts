import type {
  AnalyzeResponse,
  PogResult,
  SectionQuality,
  SectionQualityKey,
  SectionQualityMatrix,
} from "@/lib/types";

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

const QUALITY_REFERENCE = "2026-09-20T09:30:00Z";

function qualityRow(
  section: SectionQualityKey,
  overrides: Partial<SectionQuality> = {},
): SectionQuality {
  return {
    section,
    report_section: "parcel",
    status: "available",
    source_id: null,
    source_name: null,
    fetched_at: null,
    data_release_id: null,
    source_version: null,
    manual_review_required: false,
    freshness: {
      state: "unknown",
      reason_code: "FRESHNESS_NO_POLICY",
      reference_at: QUALITY_REFERENCE,
      age_seconds: 60,
      max_age_days: null,
      basis: null,
    },
    policy_version: "quality-policy/1+abc123def456",
    reason_codes: [],
    ...overrides,
  };
}

/** Macierz jakości BK-504: 10 sekcji, legenda i sumy jak w odpowiedzi API. */
export function buildQualityMatrix(
  overrides: Partial<SectionQualityMatrix> = {},
): SectionQualityMatrix {
  const fresh = {
    state: "fresh" as const,
    reason_code: null,
    reference_at: QUALITY_REFERENCE,
    age_seconds: 60,
    max_age_days: 7,
    basis: "project_decision" as const,
  };
  return {
    schema_version: "1.0",
    policy_version: "quality-policy/1+abc123def456",
    reference_at: QUALITY_REFERENCE,
    origin: "stored",
    matrix_sha256: "a".repeat(64),
    sections: [
      qualityRow("parcel", {
        source_id: "uldk",
        source_name: "ULDK",
        fetched_at: "2026-09-20T09:29:00Z",
      }),
      qualityRow("mpzp", {
        status: "partial",
        manual_review_required: true,
        source_name: "MPZP wektor gminy",
        fetched_at: "2026-09-20T09:29:00Z",
        data_release_id: 7,
        reason_codes: ["MPZP_PARAMETER_CONFLICT"],
      }),
      qualityRow("pog", {
        source_id: "pog_app",
        source_name: "RU WFS APP",
        fetched_at: "2026-09-20T09:29:00Z",
        data_release_id: 12,
        source_version: "2026-09-19",
      }),
      qualityRow("pog_overlays", { source_id: "pog_app", source_name: "RU WFS APP" }),
      qualityRow("flood", {
        source_id: "isok",
        source_name: "ISOK WFS",
        fetched_at: "2026-09-20T09:29:00Z",
        freshness: fresh,
      }),
      qualityRow("nature", {
        status: "unavailable",
        manual_review_required: true,
        source_id: "gdos",
        source_name: "GDOŚ WFS",
        fetched_at: "2026-09-20T09:29:00Z",
        reason_codes: ["SERVICE_TIMEOUT"],
        freshness: fresh,
      }),
      qualityRow("terrain", {
        status: "no_coverage",
        source_id: "nmt",
        source_name: "NMT",
        fetched_at: "2026-08-01T10:00:00Z",
        reason_codes: ["NO_COVERAGE_SENTINEL", "FRESHNESS_OLDER_THAN_POLICY"],
        freshness: {
          ...fresh,
          state: "stale",
          reason_code: "FRESHNESS_OLDER_THAN_POLICY",
          age_seconds: 50 * 86_400,
        },
      }),
      qualityRow("utilities", {
        status: "partial",
        source_id: "kiut_wms",
        source_name: "KIUT (GUGiK)",
        fetched_at: "2026-09-20T09:29:00Z",
        reason_codes: ["KIUT_PREVIEW_ONLY"],
      }),
      qualityRow("transport", {
        status: "out_of_scope",
        reason_codes: ["NO_SOURCE_CONTRACT"],
        freshness: {
          state: "unknown",
          reason_code: "FRESHNESS_NO_SOURCE",
          reference_at: QUALITY_REFERENCE,
          age_seconds: null,
          max_age_days: null,
          basis: null,
        },
      }),
      qualityRow("mpzp_pog_relation", {
        status: "unknown",
        reason_codes: ["COMPATIBILITY_NOT_ASSESSED", "DERIVED_SECTION"],
      }),
    ],
    legend: {
      statuses: [
        { id: "available", label: "sprawdzono", description: "wynik zgodny z kontraktem źródła" },
        { id: "partial", label: "częściowo", description: "wynik niepełny" },
        { id: "no_coverage", label: "brak pokrycia źródła", description: "to nie jest błąd źródła" },
        { id: "unavailable", label: "źródło niedostępne", description: "próba pobrania nie powiodła się" },
        { id: "error", label: "błąd sprawdzenia", description: "nieoczekiwany błąd" },
        { id: "unknown", label: "nieustalone", description: "brak zapisanego wyniku" },
        { id: "out_of_scope", label: "poza zakresem", description: "brak kontraktu źródła" },
        { id: "awaiting_input", label: "oczekuje na dane użytkownika", description: "analiza wstrzymana" },
      ],
      freshness: [
        { id: "fresh", label: "aktualne wg reguły", description: "wiek w regule źródła" },
        { id: "stale", label: "starsze niż reguła", description: "wiek przekracza regułę" },
        { id: "unknown", label: "świeżość nieustalona", description: "brak reguły albo czasu" },
      ],
      reasons: [
        { code: "MPZP_PARAMETER_CONFLICT", label: "sprzeczne wartości parametrów w uchwale" },
        { code: "SERVICE_TIMEOUT", label: "przekroczono limit czasu usługi" },
        { code: "NO_COVERAGE_SENTINEL", label: "usługa potwierdziła brak pokrycia obszaru" },
        { code: "FRESHNESS_OLDER_THAN_POLICY", label: "dane starsze niż reguła wieku źródła" },
        { code: "KIUT_PREVIEW_ONLY", label: "podgląd nie jest geometrią sieci" },
        { code: "NO_SOURCE_CONTRACT", label: "brak potwierdzonego kontraktu źródła danych (BK-305)" },
        { code: "COMPATIBILITY_NOT_ASSESSED", label: "nie wykonano oceny relacji MPZP–POG" },
        { code: "DERIVED_SECTION", label: "sekcja wyliczona z innych sekcji — bez własnego źródła" },
      ],
    },
    ...overrides,
  };
}
