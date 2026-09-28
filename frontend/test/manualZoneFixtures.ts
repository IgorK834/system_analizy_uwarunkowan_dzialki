import type {
  AnalyzeResponse,
  CompatibilityAssessment,
  CompatibilityZonePair,
  ManualZoneContext,
  MpzpZoneResult,
  PreviewSource,
} from "@/lib/types";
import { buildAnalyzeResponse } from "@/test/fixtures";

export const PINNED_SHA = "b".repeat(64);

export const MPZP_PREVIEW_SOURCE: PreviewSource = {
  source_key: "mpzp",
  label: "Miejscowe plany zagospodarowania przestrzennego",
  attribution: "KIMPZP, GUGiK",
  min_zoom: 11,
  max_zoom: 18,
  tile_size: 256,
  tile_url_template: "/api/v1/map/tiles/mpzp/{z}/{x}/{y}.png",
  legal_note: "Podgląd poglądowy.",
  info_url: "https://mapy.geoportal.gov.pl/",
  catalog_status: "production",
};

/** Działka ok. 70 × 70 m w Krakowie (WGS84). */
export const PARCEL_FEATURE = {
  type: "Feature",
  geometry: {
    type: "MultiPolygon",
    coordinates: [
      [
        [
          [19.9449, 50.0646],
          [19.9459, 50.0646],
          [19.9459, 50.0652],
          [19.9449, 50.0652],
          [19.9449, 50.0646],
        ],
      ],
    ],
  },
  properties: { layer: "parcel" },
};

export function manualZoneContext(overrides: Partial<ManualZoneContext> = {}): ManualZoneContext {
  return {
    plan_id: "MPZP/2020/1",
    candidate_zone_symbols: ["230_U", "231_MN"],
    document_status: "pinned",
    document: {
      requested_url: "https://bip.krakow.pl/uchwala.pdf",
      requested_url_verified: true,
      media_type: "application/pdf",
      filename: "uchwala.pdf",
      sha256: PINNED_SHA,
      size_bytes: 1024,
      fetched_at: "2026-09-25T08:00:00Z",
      document_version_id: 5,
      preview_path: "/analyze/77/pending-document",
    },
    raster_preview_source_key: "mpzp",
    symbol_max_length: 20,
    symbol_allowed_pattern: "^[A-Za-z0-9ĄąĆćĘęŁłŃńÓóŚśŹźŻż._/-]+$",
    notice:
      "Symbol podany ręcznie nie ustala udziału strefy w powierzchni działki (pozostaje nieustalony).",
    ...overrides,
  };
}

export function waitingResponse(overrides: Partial<AnalyzeResponse> = {}): AnalyzeResponse {
  return buildAnalyzeResponse({
    analysis_id: 77,
    status: "waiting_for_user_input",
    manual_zone_required: true,
    manual_zone_context: manualZoneContext(),
    parcel: {
      parcel_identifier: "126101_1.0001.77",
      geometry_geojson: PARCEL_FEATURE,
      metrics: {
        area_sqm: 4900,
        area_ha: 0.49,
        perimeter_m: 280,
        is_valid: true,
        geometry_repaired: false,
      },
      source: {
        source_name: "ULDK",
        source_url: null,
        fetched_at: null,
        response_status: null,
        confidence: 1,
        manual_review_required: false,
      },
      buildable_area_geojson: null,
    },
    ...overrides,
  });
}

export function manualZone(overrides: Partial<MpzpZoneResult> = {}): MpzpZoneResult {
  return {
    zone_symbol: "230_U",
    primary_use: null,
    supplementary_use: null,
    max_building_height_m: 12,
    max_floors: null,
    min_biologically_active_pct: null,
    max_floor_area_ratio: null,
    min_floor_area_ratio: null,
    max_building_coverage_pct: null,
    intersection_area_sqm: null,
    intersection_pct: null,
    is_dominant: false,
    assignment_method: "manual_user_input",
    manual_review_required: true,
    parameters: [
      {
        name: "max_building_height_m",
        normalized_value: 12,
        raw_value: "12 m",
        unit: "m",
        evidence_text: "maksymalna wysokość zabudowy: 12 m",
        page_number: 2,
        segment_id: "p2-s1",
        legal_unit_id: 3,
        document_sha256: PINNED_SHA,
        document_version_id: 5,
        parser_version: "mpzp-parser/2.0",
        extraction_method: "pdf_text",
        confidence: 0.9,
        conflict_group_id: null,
        manual_review_required: true,
      },
    ],
    manual_selection: {
      entered_symbol: "230_U",
      plan_id: "MPZP/2020/1",
      candidate_zone_symbols: ["230_U", "231_MN"],
      symbol_in_candidates: true,
      document_url: "https://bip.krakow.pl/uchwala.pdf",
      document_sha256: PINNED_SHA,
      document_version_id: 5,
      document_fetched_at: "2026-09-25T08:00:00Z",
      document_pinned: true,
      selected_at: "2026-09-25T09:00:00Z",
    },
    source: {
      source_name: "manual_user_input",
      source_url: "https://bip.krakow.pl/uchwala.pdf",
      fetched_at: "2026-09-25T08:00:00Z",
      response_status: 200,
      confidence: 0.5,
      manual_review_required: true,
    },
    ...overrides,
  };
}

export function pair(overrides: Partial<CompatibilityZonePair> = {}): CompatibilityZonePair {
  return {
    mpzp_zone_symbol: "1MN",
    mpzp_zone_id: "plan:1MN",
    mpzp_assignment_method: "vector_intersection",
    mpzp_function: "single_family_housing",
    pog_zone_id: "pog:sj",
    pog_zone_symbol: "SJ",
    pog_zone_type: "SJ",
    spatially_identified: true,
    overlap_area_sqm: 600,
    overlap_pct: 60,
    status: "compatible",
    rule_result: "compatible",
    rule_id: "mpzp-pog-function-table:single_family_housing:SJ",
    rule_version: "1.0",
    source: "Jawna tabela reguł",
    as_of: "2026-09-20",
    rationale: "1MN × SJ: zabudowa jednorodzinna odpowiada profilowi strefy.",
    manual_review_required: false,
    ...overrides,
  };
}

export function assessment(overrides: Partial<CompatibilityAssessment> = {}): CompatibilityAssessment {
  return {
    schema_version: "1.0",
    status: "incompatible",
    reason_code: "PAIRS_EVALUATED",
    as_of: "2026-09-20",
    rule_id: "mpzp-pog-function-table",
    rule_version: "1.0",
    aggregation: "Najsłabsze ogniwo, bez uśredniania.",
    sources: [
      { kind: "rule_set", label: "Tabela reguł", reference: "tabela", version: "1.0", as_of: "2026-09-20" },
      { kind: "pog", label: "POG — Plan ogólny", reference: null, version: "v1", as_of: null },
    ],
    rationale: "Oceniono 2 par(y) stref MPZP × POG.",
    manual_review_required: true,
    zone_pairs: [
      pair(),
      pair({
        mpzp_zone_symbol: "2P",
        mpzp_zone_id: "plan:2P",
        mpzp_function: "production",
        pog_zone_id: "pog:sn",
        pog_zone_symbol: "SN",
        pog_zone_type: "SN",
        overlap_area_sqm: 400,
        overlap_pct: 40,
        status: "incompatible",
        rule_result: "incompatible",
        rule_id: "mpzp-pog-function-table:production:SN",
        rationale: "2P × SN: zabudowa produkcyjna narusza funkcję zieleni.",
        manual_review_required: true,
      }),
    ],
    informational_notice:
      "Ocena relacji MPZP–POG jest analizą informacyjną opartą na jawnej tabeli reguł systemu. Nie jest opinią prawną i nie przesądza o prawnej możliwości zabudowy działki.",
    legacy_evidence: null,
    ...overrides,
  };
}
