import type {
  SourceMetadata,
  TerrainAspectResult,
  TerrainReliefResult,
  TerrainResult,
} from "@/lib/types";

/**
 * Fixture'y sekcji NMT zgodne z realną odpowiedzią `POST /analyze` scenariusza
 * końcowego BK-301/302 (kontrolny kwadrat Warszawy: Hmin 112,3 m, Hmax 115,7 m).
 * Profil jest skrócony do kilku próbek — z jedną luką NoData.
 */
export const NMT_SOURCE: SourceMetadata = {
  source_id: "nmt",
  source_version: null,
  artifact_sha256: "912ed836be23cb99ed3899129b8b9fd5f6e8b2f99cbb5e4aa04ea97f509043ec",
  data_release_id: null,
  act_version: null,
  source_name: "NMT",
  source_url: "https://services.gugik.gov.pl/nmt/?request=GetMinMaxByPolygon",
  fetched_at: "2026-09-28T10:15:53.683479Z",
  response_status: 200,
  confidence: 0.9,
  manual_review_required: false,
};

export const WCS_SOURCE: SourceMetadata = {
  source_id: "nmt_wcs",
  source_version: "DTM_PL-KRON86-NH_TIFF (WCS 2.0.1)",
  artifact_sha256: "603a9ddd93e210544ee26ea31fdd941bd49af28fc7f70c61c777021e70b276ec",
  data_release_id: null,
  act_version: null,
  source_name: "NMT_WCS",
  source_url:
    "https://mapy.geoportal.gov.pl/wss/service/PZGIK/NMT/GRID1/WCS/DigitalTerrainModelFormatTIFF?request=GetCoverage",
  fetched_at: "2026-09-28T10:00:00Z",
  response_status: 200,
  confidence: 0.9,
  manual_review_required: false,
};

export const DISPERSED_ASPECT: TerrainAspectResult = {
  status: "dispersed",
  mean_azimuth_deg: 90.9641,
  resultant_length: 0.1971,
  dominant_direction: null,
  sector_shares_pct: { N: 17.71, NE: 13.41, E: 15.05, SE: 18.04, S: 13.55, SW: 7.65, W: 7.19, NW: 7.38 },
  non_flat_share_pct: 63.52,
  flat_threshold_pct: 2,
};

export function buildRelief(overrides: Partial<TerrainReliefResult> = {}): TerrainReliefResult {
  return {
    schema_version: "1.0",
    algorithm_version: "horn1981-3x3-v1",
    slope_classes_version: "slope-classes-pl-v1",
    status: "available",
    reason_code: null,
    resolution_m: 1,
    parcel_pixel_count: 10000,
    valid_pixel_count: 10000,
    nodata_pixel_count: 0,
    valid_area_share_pct: 100,
    min_height_m: 105.19,
    max_height_m: 116.22,
    mean_height_m: 114.26,
    slope: {
      mean_deg: 4.2218,
      median_deg: 1.4745,
      p90_deg: 9.0969,
      max_deg: 78.4418,
      mean_pct: 9.5422,
      median_pct: 2.574,
      p90_pct: 16.0119,
      max_pct: 488.9744,
    },
    slope_classes: [
      { class_id: "flat", label: "płaski (< 2%)", min_pct: 0, max_pct: 2, pixel_count: 3648, area_sqm: 3648, share_pct: 36.48 },
      { class_id: "gentle", label: "łagodny (2–5%)", min_pct: 2, max_pct: 5, pixel_count: 3858, area_sqm: 3858, share_pct: 38.58 },
      { class_id: "moderate", label: "umiarkowany (5–10%)", min_pct: 5, max_pct: 10, pixel_count: 1128, area_sqm: 1128, share_pct: 11.28 },
      { class_id: "strong", label: "znaczny (10–15%)", min_pct: 10, max_pct: 15, pixel_count: 316, area_sqm: 316, share_pct: 3.16 },
      { class_id: "steep", label: "stromy (15–30%)", min_pct: 15, max_pct: 30, pixel_count: 435, area_sqm: 435, share_pct: 4.35 },
      { class_id: "very_steep", label: "bardzo stromy (≥ 30%)", min_pct: 30, max_pct: null, pixel_count: 615, area_sqm: 615, share_pct: 6.15 },
    ],
    aspect: DISPERSED_ASPECT,
    profile: {
      method: "parcel_long_axis_through_rectangle_center",
      crs: "EPSG:2180",
      start: [637000, 486050],
      end: [637100, 486050],
      length_m: 100,
      step_m: 25,
      interpolation: "bilinear",
      samples: [
        { distance_m: 0, x: 637000, y: 486050, height_m: 115.069, inside_parcel: true },
        { distance_m: 25, x: 637025, y: 486050, height_m: 116.161, inside_parcel: true },
        { distance_m: 50, x: 637050, y: 486050, height_m: null, inside_parcel: true },
        { distance_m: 75, x: 637075, y: 486050, height_m: 114.6, inside_parcel: true },
        { distance_m: 100, x: 637100, y: 486050, height_m: 114.222, inside_parcel: true },
      ],
      line_geojson: null,
    },
    raster: {
      coverage_id: "DTM_PL-KRON86-NH_TIFF",
      crs: "EPSG:2180",
      resolution_m: 1,
      width_px: 105,
      height_px: 105,
      bbox: [636997.343266, 485997.66941, 637102.343266, 486102.66941],
      buffer_m: 2,
      size_bytes: 44591,
      nodata_value: null,
      nodata_policy: "GDAL_NODATA, NaN oraz niezadeklarowane 0.0 poza zasięgiem danych",
      vertical_datum: "PL-KRON86-NH",
      gdal_version: "GDAL 3.10.3, released 2025/04/01",
    },
    source: WCS_SOURCE,
    warnings: [
      "Wysokości z rastra WCS 1 m (105,19–116,22 m) różnią się od usługi GetMinMaxByPolygon (112,30–115,70 m) o więcej niż 1 m.",
    ],
    ...overrides,
  };
}

export function buildTerrain(overrides: Partial<TerrainResult> = {}): TerrainResult {
  return {
    schema_version: "1.0",
    status: "available",
    reason_code: null,
    min_height_m: 112.3,
    max_height_m: 115.7,
    height_difference_m: 3.4,
    grid_size_m: 4,
    sampled_points: 676,
    source: NMT_SOURCE,
    warnings: [],
    relief: buildRelief(),
    ...overrides,
  };
}

const EMPTY_MEASUREMENT = {
  min_height_m: null,
  max_height_m: null,
  height_difference_m: null,
} as const;

export const NO_COVERAGE_TERRAIN = buildTerrain({
  status: "no_coverage",
  reason_code: "NO_COVERAGE_SENTINEL",
  ...EMPTY_MEASUREMENT,
  warnings: ["NMT nie ma danych wysokościowych dla obszaru działki."],
  relief: null,
});

export const TIMEOUT_TERRAIN = buildTerrain({
  status: "unavailable",
  reason_code: "SERVICE_TIMEOUT",
  ...EMPTY_MEASUREMENT,
  grid_size_m: null,
  sampled_points: null,
  source: { ...NMT_SOURCE, response_status: null, confidence: 0, manual_review_required: true },
  relief: buildRelief({
    status: "unavailable",
    reason_code: "RASTER_TOO_LARGE",
    resolution_m: null,
    parcel_pixel_count: null,
    valid_pixel_count: null,
    nodata_pixel_count: null,
    valid_area_share_pct: null,
    min_height_m: null,
    max_height_m: null,
    mean_height_m: null,
    slope: null,
    slope_classes: [],
    aspect: null,
    profile: null,
    raster: null,
    source: { ...WCS_SOURCE, confidence: 0, manual_review_required: true },
    warnings: [],
  }),
});

export const FLAT_TERRAIN = buildTerrain({
  min_height_m: 101.2,
  max_height_m: 101.2,
  height_difference_m: 0,
  relief: buildRelief({
    aspect: {
      status: "flat",
      mean_azimuth_deg: null,
      resultant_length: null,
      dominant_direction: null,
      sector_shares_pct: {},
      non_flat_share_pct: 0,
      flat_threshold_pct: 2,
    },
  }),
});
