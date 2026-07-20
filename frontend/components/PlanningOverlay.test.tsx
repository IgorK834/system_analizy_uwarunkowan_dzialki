import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type maplibregl from "maplibre-gl";

import { PlanningOverlay } from "@/components/PlanningOverlay";
import { buildAnalyzeResponse } from "@/test/fixtures";

function createMapMock() {
  const sources = new Set<string>();
  const layers = new Map<string, { layout?: Record<string, unknown> }>();
  const handlers = new Map<string, () => void>();
  let zoom = 6;

  const map = {
    getSource: vi.fn((id: string) => (sources.has(id) ? {} : undefined)),
    addSource: vi.fn((id: string) => {
      sources.add(id);
    }),
    removeSource: vi.fn((id: string) => {
      sources.delete(id);
    }),
    getLayer: vi.fn((id: string) => layers.get(id)),
    addLayer: vi.fn((layer: { id: string; layout?: Record<string, unknown> }) => {
      layers.set(layer.id, { layout: layer.layout });
    }),
    removeLayer: vi.fn((id: string) => {
      layers.delete(id);
    }),
    setLayoutProperty: vi.fn(),
    getZoom: vi.fn(() => zoom),
    on: vi.fn((eventName: string, handler: () => void) => {
      handlers.set(eventName, handler);
    }),
    off: vi.fn((eventName: string, handler: () => void) => {
      if (handlers.get(eventName) === handler) handlers.delete(eventName);
    }),
    triggerZoom: (nextZoom: number) => {
      zoom = nextZoom;
      handlers.get("zoomend")?.();
    },
  };

  return map as unknown as maplibregl.Map & typeof map;
}

describe("PlanningOverlay", () => {
  let map: ReturnType<typeof createMapMock>;

  beforeEach(() => {
    map = createMapMock();
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.example.test");
    vi.stubEnv("NEXT_PUBLIC_KIMPZP_TILE_URL", "");
    vi.stubEnv("NEXT_PUBLIC_POG_WMS_URL", "");
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("dodaje domyślnie włączoną nakładkę MPZP przed analizą i pozwala ją wyłączyć", async () => {
    const user = userEvent.setup();

    render(<PlanningOverlay result={null} map={map} />);

    const mpzpToggle = screen.getByRole("switch", { name: /MPZP/ });
    expect(mpzpToggle).not.toBeDisabled();
    expect(mpzpToggle).toHaveAttribute("aria-checked", "true");
    expect(map.addSource).toHaveBeenCalledWith(
      "mpzp-wms-source",
      expect.objectContaining({
        type: "raster",
        tiles: [
          "https://api.example.test/api/v1/map/tiles/mpzp/{z}/{x}/{y}.png",
        ],
        minzoom: 11,
        maxzoom: 18,
        scheme: "xyz",
        tileSize: 256,
      }),
    );
    expect(map.addLayer).toHaveBeenCalledWith(
      expect.objectContaining({
        id: "mpzp-wms-layer",
        paint: expect.objectContaining({ "raster-fade-duration": 0 }),
      }),
      undefined,
    );

    await waitFor(() =>
      expect(map.setLayoutProperty).toHaveBeenCalledWith(
        "mpzp-wms-layer",
        "visibility",
        "visible",
      ),
    );

    await user.click(mpzpToggle);
    expect(map.setLayoutProperty).toHaveBeenCalledWith(
      "mpzp-wms-layer",
      "visibility",
      "none",
    );
    expect(window.localStorage.getItem("dzialki:planning-overlays:v1")).toContain(
      '"mpzp":false',
    );
  });

  it("nie pobiera warstwy przed progiem minzoom i informuje użytkownika o wymaganym przybliżeniu", () => {
    render(<PlanningOverlay result={null} map={map} />);

    expect(screen.getByText(/Przybliż mapę do poziomu 11/)).toBeVisible();
    expect(map.addLayer).toHaveBeenCalledWith(
      expect.objectContaining({
        id: "mpzp-wms-layer",
        minzoom: 11,
        maxzoom: 22,
      }),
      undefined,
    );

    act(() => map.triggerZoom(12));
    expect(
      screen.getByText(/wyłącznie kafelki widoczne w bieżącym obszarze mapy/),
    ).toBeVisible();
  });

  it("odtwarza wyłączenie nakładki z poprzedniej sesji", async () => {
    window.localStorage.setItem(
      "dzialki:planning-overlays:v1",
      JSON.stringify({ mpzp: false, pog: true }),
    );

    render(<PlanningOverlay result={null} map={map} />);

    await waitFor(() =>
      expect(screen.getByRole("switch", { name: /MPZP/ })).toHaveAttribute(
        "aria-checked",
        "false",
      ),
    );
    expect(map.setLayoutProperty).not.toHaveBeenCalledWith(
      "mpzp-wms-layer",
      "visibility",
      "visible",
    );
  });

  it("używa jawnie skonfigurowanego adresu CDN kafelków MPZP", () => {
    vi.stubEnv(
      "NEXT_PUBLIC_KIMPZP_TILE_URL",
      "https://tiles.example.test/mpzp/{z}/{x}/{y}.png",
    );

    render(<PlanningOverlay result={null} map={map} />);

    const mpzpToggle = screen.getByRole("switch", { name: /MPZP/ });
    expect(mpzpToggle).not.toBeDisabled();
    expect(map.addSource).toHaveBeenCalledWith(
      "mpzp-wms-source",
      expect.objectContaining({
        tiles: ["https://tiles.example.test/mpzp/{z}/{x}/{y}.png"],
      }),
    );
  });

  it("włącza przełącznik POG, gdy źródło wyniku jest usługą WMS", () => {
    const result = buildAnalyzeResponse({
      pog: {
        status: "adopted",
        planning_zone: "SJ",
        zone_type: "SJ",
        in_ouz: false,
        area_ratio: 0.5,
        in_downtown_area: false,
        uchwala_nr: null,
        uchwala_date: null,
        manual_review_required: false,
        conflict_with_mpzp: null,
        raw_attributes: null,
        ouz_intersection_area_sqm: null,
        ouz_intersection_pct: null,
        touches_ouz_boundary: false,
        source: {
          source_name: "POG_GMINA_WMS",
          source_url: "https://pog.example.test/wms?service=WMS&request=GetFeatureInfo",
          fetched_at: null,
          response_status: null,
          confidence: 0.6,
          manual_review_required: true,
        },
      },
    });

    render(<PlanningOverlay result={result} map={map} />);

    const pogToggle = screen.getByRole("switch", { name: /Plan Ogólny Gminy/ });
    expect(pogToggle).not.toBeDisabled();
    expect(map.addSource).toHaveBeenCalledWith(
      "pog-wms-source",
      expect.objectContaining({
        tiles: [expect.stringContaining("https://pog.example.test/wms?")],
      }),
    );
  });

  it("używa skonfigurowanej krajowej nakładki POG, gdy wynik nie zawiera źródła WMS", () => {
    vi.stubEnv("NEXT_PUBLIC_POG_WMS_URL", "https://wms.example.test/pog");
    vi.stubEnv("NEXT_PUBLIC_POG_WMS_LAYERS", "aktPlanowaniaprzestrzennego");

    render(<PlanningOverlay result={null} map={map} />);

    expect(
      screen.getByRole("switch", { name: /Plan Ogólny Gminy/ }),
    ).not.toBeDisabled();
    expect(map.addSource).toHaveBeenCalledWith(
      "pog-wms-source",
      expect.objectContaining({
        tiles: [expect.stringMatching(/version=1\.1\.1.*srs=EPSG%3A3857/)],
      }),
    );
  });

  it("wyłącza przełącznik POG z jasnym powodem, gdy źródło nie jest usługą WMS", () => {
    const result = buildAnalyzeResponse({
      pog: {
        status: "adopted",
        planning_zone: "SJ",
        zone_type: "SJ",
        in_ouz: false,
        area_ratio: 0.5,
        in_downtown_area: false,
        uchwala_nr: null,
        uchwala_date: null,
        manual_review_required: false,
        conflict_with_mpzp: null,
        raw_attributes: null,
        ouz_intersection_area_sqm: null,
        ouz_intersection_pct: null,
        touches_ouz_boundary: false,
        source: {
          source_name: "POG_GMINA_BIP",
          source_url: "https://bip.example.test/pog",
          fetched_at: null,
          response_status: null,
          confidence: 0.4,
          manual_review_required: true,
        },
      },
    });

    render(<PlanningOverlay result={result} map={map} />);

    const pogToggle = screen.getByRole("switch", { name: /Plan Ogólny Gminy/ });
    expect(pogToggle).toBeDisabled();
    expect(
      screen.getByText(/brak źródła WMS w wyniku i konfiguracji/),
    ).toBeVisible();
    expect(map.addSource).not.toHaveBeenCalledWith(
      "pog-wms-source",
      expect.anything(),
    );
  });

  it("wyłącza przełącznik POG, gdy pog jest null", () => {
    const result = buildAnalyzeResponse({ pog: null });

    render(<PlanningOverlay result={result} map={map} />);

    expect(screen.getByRole("switch", { name: /Plan Ogólny Gminy/ })).toBeDisabled();
  });
});
