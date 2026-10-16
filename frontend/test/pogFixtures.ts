import { vi } from "vitest";

import type {
  PogActTileProperties,
  PogAreaSummary,
  PogCommuneCoverage,
  PogCoverage,
  PogCoverageArea,
  PogFeatureDetails,
  PogInspectorHit,
  PogOverlayTileProperties,
  PogTileState,
  PogZoneTileProperties,
} from "@/lib/types";

/** Zasięg aktu wydania (BK-406) — Sopot i sąsiedni projekt. */
export function buildCoverageArea(overrides: Partial<PogCoverageArea> = {}): PogCoverageArea {
  return {
    act_id: "PL.ZIPPZP.10011/226401-POG/1POG",
    teryt: "226401",
    legal_status: "binding",
    bounds: [18.5, 54.4, 18.6, 54.5],
    has_boundary: true,
    is_complete: true,
    incomplete_reasons: [],
    ...overrides,
  };
}

/** Metadane stanu krajowego POG zgodne z odpowiedzią GET /api/v1/map/pog/state. */
export function buildPogState(overrides: Partial<PogTileState> = {}): PogTileState {
  return {
    state_id: 42,
    source_id: "pog_app",
    kind: "import",
    created_at: "2026-09-28T00:00:00Z",
    is_current: true,
    act_count: 2,
    tile_url_template: "/api/v1/map/pog/states/42/{z}/{x}/{y}.mvt",
    editions_sources: [
      { edition: "binding", tile_url_template: "/api/v1/map/pog/states/42/{z}/{x}/{y}.mvt?edition=binding" },
      { edition: "project", tile_url_template: "/api/v1/map/pog/states/42/{z}/{x}/{y}.mvt?edition=project" },
    ],
    tile_schema: "pog-mvt/2",
    tile_format: "application/vnd.mapbox-vector-tile",
    layers: ["zones", "ouz", "downtown", "social_infrastructure_standard", "act_boundary"],
    layer_min_zoom: {
      zones: 11,
      ouz: 12,
      downtown: 12,
      social_infrastructure_standard: 12,
      act_boundary: 0,
    },
    text_attributes_min_zoom: 13,
    editions: ["all", "binding", "project"],
    default_edition: "all",
    min_zoom: 0,
    max_zoom: 18,
    extent: 4096,
    buffer: 64,
    bounds: [18.4, 54.3, 18.7, 54.6],
    acts_by_legal_status: { binding: 1, project: 1 },
    coverage_areas: [
      buildCoverageArea(),
      buildCoverageArea({
        act_id: "PL.ZIPPZP.99999/226401-POG/2POG",
        legal_status: "project",
        bounds: [18.55, 54.42, 18.62, 54.47],
      }),
    ],
    map_ready: true,
    versions_without_map_rows: 0,
    style_version: "2026.10.14-1",
    style_sha256: "b".repeat(64),
    attribution: "Rejestr Urbanistyczny (APP POG) — lokalny stan danych",
    legal_note: "Projekt aktu nie jest wiążący.",
    ...overrides,
  };
}

/** Atrybuty strefy z kafla MVT (realne wartości strefy 1POG-100SU Sopotu). */
export function buildPogZoneProperties(
  overrides: Partial<PogZoneTileProperties> = {},
): PogZoneTileProperties {
  return {
    feature_id: "PL.ZIPPZP.10011/226401-POG/1POG-100SU",
    feature_version: "20260819T010000",
    symbol: "SU",
    label: "strefa usługowa",
    designation: "100SU",
    legal_status: "binding",
    teryt: "226401",
    act_id: "PL.ZIPPZP.10011/226401-POG/1POG",
    zone_code: "SU",
    max_overground_floor_area_ratio: 0.9,
    max_building_height_m: 4,
    max_building_coverage_pct: 90,
    min_biologically_active_pct: 5,
    ...overrides,
  };
}

/** Atrybuty OUZ/OZS/OSDIS z kafla MVT. */
export function buildPogOverlayProperties(
  overrides: Partial<PogOverlayTileProperties> = {},
): PogOverlayTileProperties {
  return {
    feature_id: "PL.ZIPPZP.10011/226401-POG/1POG-OUZ1",
    feature_version: "20260819T010000",
    symbol: "OUZ1",
    label: "obszar uzupełnienia zabudowy",
    legal_status: "binding",
    teryt: "226401",
    act_id: "PL.ZIPPZP.10011/226401-POG/1POG",
    ...overrides,
  };
}

/** Trafienie inspektora po deduplikacji (klucz jak w `pogInspectorHits`). */
export function buildZoneHit(
  overrides: Partial<PogZoneTileProperties> = {},
  featurePk = 101,
): PogInspectorHit {
  const properties = buildPogZoneProperties(overrides);
  return {
    key: `zones:${featurePk}`,
    layer: "zones",
    featurePk,
    properties,
  };
}

export function buildOverlayHit(
  layer: "ouz" | "downtown" | "social_infrastructure_standard",
  overrides: Partial<PogOverlayTileProperties> = {},
  featurePk = 201,
): PogInspectorHit {
  const properties = buildPogOverlayProperties(overrides);
  return { key: `${layer}:${featurePk}`, layer, featurePk, properties };
}

/** Atrybuty granicy aktu z kafla (przegląd krajowy). */
export function buildActHit(overrides: Partial<PogActTileProperties> = {}, featurePk = 7): PogInspectorHit {
  const properties: PogActTileProperties = {
    act_id: "PL.ZIPPZP.10011/226401-POG/1POG",
    label: "Plan ogólny Miasta Sopotu",
    legal_status: "binding",
    teryt: "226401",
    act_version: "20260819T010000",
    resolution_number: "XV/123/2026",
    resolution_date: "2026-08-19",
    valid_from: "2026-09-01",
    effective_date: "2026-09-01",
    version_started_on: "2026-08-19",
    ...overrides,
  };
  return { key: `act_boundary:${featurePk}`, layer: "act_boundary", featurePk, properties };
}

/** Odpowiedź GET …/features/{feature_id} (szczegóły spoza kafla). */
export function buildPogFeatureDetails(
  overrides: Partial<PogFeatureDetails> = {},
): PogFeatureDetails {
  return {
    schema: "pog-inspector/1",
    tile_schema: "pog-mvt/1",
    state: {
      state_id: 42,
      kind: "import",
      created_at: "2026-09-28T00:00:00Z",
      is_current: true,
    },
    feature_pk: 101,
    feature_id: "PL.ZIPPZP.10011/226401-POG/1POG-100SU",
    feature_version: "20260819T010000",
    layer: "zones",
    feature_type: "planning_zone",
    symbol: "SU",
    designation: "100SU",
    label: "strefa usługowa — pełna etykieta z aktu",
    zone_code: "SU",
    source_zone_type: "strefa usługowa",
    parameters: {
      max_overground_floor_area_ratio: 0.9,
      max_building_height_m: 4,
      max_building_coverage_pct: 90,
      min_biologically_active_pct: 5,
    },
    parameters_informational: false,
    primary_profiles: [{ code: "U", label: "usługi", dictionary_source: "ProfilFunkcjonalnyKod" }],
    additional_profiles: [],
    act: {
      act_id: "PL.ZIPPZP.10011/226401-POG/1POG",
      act_version: "20260819T010000",
      name: "Plan ogólny Miasta Sopotu",
      teryt: "226401",
      legal_status: "binding",
      legal_status_code: "http://inspire.ec.europa.eu/codelist/ProcessStepGeneralValue/legalForce",
      resolution_number: "XV/123/2026",
      resolution_date: "2026-08-19",
      legal_valid_from: "2026-09-01",
      legal_valid_to: null,
      effective_date: "2026-09-01",
      version_started_on: "2026-08-19",
      official_journal: "Dziennik Urzędowy Województwa Pomorskiego",
      document_link: "https://dziennik.example.gov.pl/akt/123",
      ru_act_url: "https://rejestr-urbanistyczny.gov.pl/plany-ogolne/1735",
      publication_id: "pub-1",
      manual_review_required: false,
      act_version_id: 9,
      data_release_id: 42,
      release_label: "pog-0123456789ab",
      artifact_sha256: "a".repeat(64),
      content_hash: "c".repeat(64),
    },
    source_reference: "fixture",
    ...overrides,
  };
}

/** Agregat stref aktu 1 km² podzielonego 60/40 (BK-405). */
export function buildPogAreaSummary(overrides: Partial<PogAreaSummary> = {}): PogAreaSummary {
  return {
    schema: "pog-area-summary/2",
    state_id: 42,
    computed_in_state_id: 42,
    state_is_current: true,
    release_id: 7,
    release_label: "pog-0123456789ab",
    artifact_sha256: "a".repeat(64),
    scope: "act",
    act_id: "PL.ZIPPZP.10011/226401-POG/1POG",
    act_version: "20260819T010000",
    teryt: "226401",
    edition: null,
    legal_status: "binding",
    act_ids: ["PL.ZIPPZP.10011/226401-POG/1POG"],
    act_count: 1,
    denominator_area_sqm: 1_000_000,
    denominator_area_sqkm: 1,
    denominator_source: "act_boundary",
    zones_area_sqm: 1_000_000,
    zones_area_sqkm: 1,
    missing_area_sqm: 0,
    missing_area_sqkm: 0,
    overlap_area_sqm: 0,
    outside_area_sqm: 0,
    deduplicated_area_sqm: null,
    share_sum_pct: 100,
    share_tolerance_pct: 0.1,
    area_tolerance_sqm: 500,
    zone_count: 2,
    is_complete: true,
    incomplete_reasons: [],
    zones: [
      { zone_code: "SW", area_sqm: 600_000, area_sqkm: 0.6, share_pct: 60, zone_count: 1 },
      { zone_code: "SU", area_sqm: 400_000, area_sqkm: 0.4, share_pct: 40, zone_count: 1 },
    ],
    method_version: "pog-aggregates/1",
    computed_at: "2026-09-29T10:00:00Z",
    ...overrides,
  };
}

/** Atrapa mapy MapLibre rejestrująca źródła, warstwy i obrazy wzorów. */
export function createPogMapMock() {
  const sources = new Map<string, Record<string, unknown>>();
  const layers = new Map<string, Record<string, unknown>>();
  const images = new Set<string>();
  const map = {
    getSource: vi.fn((id: string) => sources.get(id)),
    addSource: vi.fn((id: string, source: Record<string, unknown>) => {
      sources.set(id, source);
    }),
    removeSource: vi.fn((id: string) => sources.delete(id)),
    getLayer: vi.fn((id: string) => layers.get(id)),
    addLayer: vi.fn((layer: Record<string, unknown>) => {
      layers.set(String(layer.id), layer);
    }),
    removeLayer: vi.fn((id: string) => layers.delete(id)),
    hasImage: vi.fn((id: string) => images.has(id)),
    addImage: vi.fn((id: string) => {
      images.add(id);
    }),
    setPaintProperty: vi.fn(),
    setLayoutProperty: vi.fn(),
    setFilter: vi.fn(),
  };
  return { map, sources, layers, images };
}

/** Gmina z listy pokrycia krajowego (issue 301). */
export function buildCommuneCoverage(overrides: Partial<PogCommuneCoverage> = {}): PogCommuneCoverage {
  return {
    terc: "226401",
    name: "Sopot",
    district: "Sopot",
    voivodeship: "pomorskie",
    status: "imported",
    ru_acts: [
      {
        published_act_id: 1735,
        status_text: "prawnie wiążący lub realizowany",
        legal_status: "binding",
        publish_date: "2026-08-19",
        valid_from: "2026-09-01",
      },
    ],
    imported_acts: [
      {
        act_identifier: "PL.ZIPPZP.10011/226401-POG/1POG",
        act_version: "20260819T010000",
        legal_status: "binding",
        name: "Plan ogólny Miasta Sopotu",
        state_id: 42,
        imported_at: "2026-09-28T10:00:00Z",
        fetched_at: "2026-09-28T09:58:00Z",
        release_id: 7,
        release_label: "pog-national-1",
        content_hash: "d".repeat(64),
        ru_published_act_id: 1735,
        up_to_date: true,
        zone_count: 12,
        overlay_count: 3,
      },
    ],
    import_job: null,
    ...overrides,
  };
}

export function buildPogCoverage(overrides: Partial<PogCoverage> = {}): PogCoverage {
  return {
    source_id: "pog_app",
    checked_at: "2026-10-13T12:00:00Z",
    catalog_synced_at: "2026-10-13T11:00:00Z",
    catalog_fresh: true,
    summary: {
      ru_communes: 3,
      imported_communes: 1,
      in_ru_not_imported: 2,
      imported_without_ru_entry: 0,
      outdated_versions: 0,
      state_id: 42,
    },
    communes: [
      buildCommuneCoverage(),
      buildCommuneCoverage({
        terc: "246101",
        name: "Bielsko-Biała",
        district: "Bielsko-Biała",
        voivodeship: "śląskie",
        status: "in_ru_not_imported",
        imported_acts: [],
        import_job: {
          job_id: 5,
          status: "queued",
          attempts: 0,
          requested_at: "2026-10-13T11:30:00Z",
          finished_at: null,
          last_error: null,
        },
      }),
      buildCommuneCoverage({
        terc: "126101",
        name: "Kraków",
        district: "Kraków",
        voivodeship: "małopolskie",
        status: "in_ru_not_imported",
        imported_acts: [],
      }),
    ],
    note: "Stan wynika z wykazu RU.",
    ...overrides,
  };
}
