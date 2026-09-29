import { vi } from "vitest";

import type { PogTileRelease, PogZoneTileProperties } from "@/lib/types";

/** Metadane wydania POG zgodne z odpowiedzią GET /api/v1/map/pog/releases/active. */
export function buildPogRelease(overrides: Partial<PogTileRelease> = {}): PogTileRelease {
  return {
    release_id: 42,
    source_id: "pog_app",
    version_label: "pog-0123456789ab",
    published_at: "2026-09-28T00:00:00Z",
    is_active: true,
    artifact_sha256: "a".repeat(64),
    tile_url_template: "/api/v1/map/pog/releases/42/{z}/{x}/{y}.mvt",
    tile_schema: "pog-mvt/1",
    tile_format: "application/vnd.mapbox-vector-tile",
    layers: ["zones", "ouz", "downtown", "social_infrastructure_standard", "act_boundary"],
    editions: ["all", "binding", "project"],
    default_edition: "all",
    min_zoom: 0,
    max_zoom: 18,
    extent: 4096,
    buffer: 64,
    bounds: [18.4, 54.3, 18.7, 54.6],
    acts_by_legal_status: { binding: 1, project: 1 },
    style_version: "2026.09.28-1",
    style_sha256: "b".repeat(64),
    attribution: "Rejestr Urbanistyczny (APP POG) — lokalne wydanie danych",
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
    legal_status: "binding",
    teryt: "226401",
    act_id: "PL.ZIPPZP.10011/226401-POG/1POG",
    data_release_id: 42,
    zone_code: "SU",
    max_overground_floor_area_ratio: 0.9,
    max_building_height_m: 4,
    max_building_coverage_pct: 90,
    min_biologically_active_pct: 5,
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
    setFilter: vi.fn(),
  };
  return { map, sources, layers, images };
}
