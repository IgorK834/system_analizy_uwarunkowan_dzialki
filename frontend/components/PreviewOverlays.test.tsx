import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type maplibregl from "maplibre-gl";

import { PreviewOverlays } from "@/components/PreviewOverlays";
import { getPreviewSources } from "@/lib/api";
import { KIUT_OVERLAY_OPACITY, OVERLAY_OPACITY } from "@/lib/layerStyles";
import type { PreviewSource } from "@/lib/types";
import { buildAnalyzeResponse } from "@/test/fixtures";

vi.mock("@/lib/api", () => ({ getPreviewSources: vi.fn() }));

const getPreviewSourcesMock = vi.mocked(getPreviewSources);

const PREVIEW_SOURCES: PreviewSource[] = [
  {
    source_key: "mpzp",
    label: "Miejscowe plany zagospodarowania przestrzennego",
    attribution: "KIMPZP, GUGiK",
    min_zoom: 11,
    max_zoom: 18,
    tile_size: 256,
    tile_url_template: "/api/v1/map/tiles/mpzp/{z}/{x}/{y}.png",
    legal_note: "Kafle są buforowane do 24 godzin.",
    info_url: "https://info.example.test/mpzp",
    catalog_status: "production",
  },
  {
    source_key: "pog",
    label: "Plany ogólne gmin",
    attribution: "POG, GUGiK",
    min_zoom: 11,
    max_zoom: 18,
    tile_size: 256,
    tile_url_template: "/api/v1/map/tiles/pog/{z}/{x}/{y}.png",
    legal_note: "Kafle są buforowane do 24 godzin.",
    info_url: "https://info.example.test/pog",
    catalog_status: "production",
  },
  {
    source_key: "kiut",
    label: "Uzbrojenie terenu",
    attribution: "KIUT, GUGiK",
    min_zoom: 16,
    max_zoom: 20,
    tile_size: 512,
    tile_url_template: "/api/v1/map/tiles/kiut/{z}/{x}/{y}.png",
    legal_note: "Kafle są buforowane do 6 godzin.",
    info_url: "https://info.example.test/kiut",
    catalog_status: "production",
  },
];

function createMapMock() {
  const sources = new Set<string>();
  const layers = new Map<string, { layout?: Record<string, unknown> }>();
  const handlers = new Map<string, (event?: unknown) => void>();
  let zoom = 6;

  const map = {
    getSource: vi.fn((id: string) => (sources.has(id) ? {} : undefined)),
    addSource: vi.fn((id: string) => {
      sources.add(id);
    }),
    removeSource: vi.fn((id: string) => sources.delete(id)),
    getLayer: vi.fn((id: string) => layers.get(id)),
    addLayer: vi.fn((
      layer: { id: string; layout?: Record<string, unknown> },
      _beforeId?: string,
    ) => {
      layers.set(layer.id, { layout: layer.layout });
    }),
    removeLayer: vi.fn((id: string) => layers.delete(id)),
    setLayoutProperty: vi.fn(),
    getZoom: vi.fn(() => zoom),
    on: vi.fn((eventName: string, handler: (event?: unknown) => void) => {
      handlers.set(eventName, handler);
    }),
    off: vi.fn((eventName: string, handler: (event?: unknown) => void) => {
      if (handlers.get(eventName) === handler) handlers.delete(eventName);
    }),
    triggerZoom: (nextZoom: number) => {
      zoom = nextZoom;
      handlers.get("zoomend")?.();
    },
    seedLayer: (id: string) => {
      layers.set(id, {});
    },
    triggerTileError: (sourceId: string) => {
      handlers.get("error")?.({ sourceId, error: { status: 503 } });
    },
  };

  return map as unknown as maplibregl.Map & typeof map;
}

function coveredUtilitiesPreview() {
  return {
    coverage_status: "covered" as const,
    county_name: "powiat krakowski",
    layer_available: true,
    note: "Brak obiektów na podglądzie nie oznacza braku sieci.",
    source: {
      source_name: "KIUT (GUGiK)",
      source_url: "https://info.example.test/kiut",
      fetched_at: "2026-09-02T10:00:00Z",
      response_status: 200,
      confidence: 0.9,
      manual_review_required: false,
    },
  };
}

describe("PreviewOverlays", () => {
  let map: ReturnType<typeof createMapMock>;

  beforeEach(() => {
    map = createMapMock();
    window.localStorage.clear();
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.example.test");
    getPreviewSourcesMock.mockReset();
    getPreviewSourcesMock.mockResolvedValue(PREVIEW_SOURCES);
  });

  it("pobiera rejestr backendu i dodaje MPZP oraz POG bez adresów upstreamu", async () => {
    render(<PreviewOverlays result={null} map={map} />);

    await waitFor(() => expect(getPreviewSourcesMock).toHaveBeenCalledOnce());
    await waitFor(() => expect(map.addSource).toHaveBeenCalledTimes(2));
    expect(map.addSource).toHaveBeenCalledWith(
      "mpzp-wms-source",
      expect.objectContaining({
        tiles: [
          "https://api.example.test/api/v1/map/tiles/mpzp/{z}/{x}/{y}.png",
        ],
        minzoom: 11,
        maxzoom: 18,
        tileSize: 256,
      }),
    );
    expect(map.addSource).toHaveBeenCalledWith(
      "pog-wms-source",
      expect.objectContaining({
        tiles: ["https://api.example.test/api/v1/map/tiles/pog/{z}/{x}/{y}.png"],
      }),
    );
    expect(map.addSource).not.toHaveBeenCalledWith(
      "kiut-wms-source",
      expect.anything(),
    );
    expect(map.addLayer).toHaveBeenCalledWith(
      expect.objectContaining({
        id: "mpzp-wms-layer",
        paint: expect.objectContaining({
          "raster-opacity": OVERLAY_OPACITY,
          "raster-resampling": "linear",
        }),
      }),
      undefined,
    );
    expect(screen.getByRole("switch", { name: /MPZP/ })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.getByRole("switch", { name: /Plan Ogólny/ })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.getByRole("switch", { name: /Uzbrojenie/ })).toHaveAttribute(
      "aria-checked",
      "false",
    );
  });

  it("nie dodaje KIUT poniżej zoomu 17 i uruchamia je dopiero po decyzji użytkownika", async () => {
    const user = userEvent.setup();
    render(<PreviewOverlays result={null} map={map} />);
    const kiutToggle = await screen.findByRole("switch", { name: /Uzbrojenie/ });
    await waitFor(() => expect(kiutToggle).not.toBeDisabled());

    await user.click(kiutToggle);

    expect(screen.getByText(/Przybliż do poziomu 17/)).toBeVisible();
    expect(map.addSource).not.toHaveBeenCalledWith(
      "kiut-wms-source",
      expect.anything(),
    );
    expect(
      window.localStorage.getItem("dzialki:preview-overlays:v2"),
    ).toContain('"kiut":true');

    act(() => map.triggerZoom(16.9));
    expect(map.addSource).not.toHaveBeenCalledWith(
      "kiut-wms-source",
      expect.anything(),
    );

    act(() => map.triggerZoom(17.0));

    await waitFor(() =>
      expect(map.addSource).toHaveBeenCalledWith(
        "kiut-wms-source",
        expect.objectContaining({
          tiles: [
            "https://api.example.test/api/v1/map/tiles/kiut/{z}/{x}/{y}.png",
          ],
          minzoom: 16,
          maxzoom: 20,
          tileSize: 512,
        }),
      ),
    );
    expect(map.addLayer).toHaveBeenCalledWith(
      expect.objectContaining({
        id: "kiut-wms-layer",
        paint: {
          "raster-opacity": KIUT_OVERLAY_OPACITY,
          "raster-resampling": "nearest",
          "raster-fade-duration": 0,
        },
      }),
      undefined,
    );
    expect(screen.queryByText(/Przybliż do poziomu 19/)).not.toBeInTheDocument();
  });

  it("migruje preferencje v1, zachowując KIUT domyślnie wyłączone", async () => {
    window.localStorage.setItem(
      "dzialki:planning-overlays:v1",
      JSON.stringify({ mpzp: false, pog: true }),
    );

    render(<PreviewOverlays result={null} map={map} />);

    await waitFor(() =>
      expect(screen.getByRole("switch", { name: /MPZP/ })).toHaveAttribute(
        "aria-checked",
        "false",
      ),
    );
    expect(screen.getByRole("switch", { name: /Uzbrojenie/ })).toHaveAttribute(
      "aria-checked",
      "false",
    );
    expect(window.localStorage.getItem("dzialki:preview-overlays:v2")).toBe(
      JSON.stringify({ mpzp: false, pog: true }),
    );
  });

  it("układa KIUT nad MPZP i POG, ale pod warstwami wyniku", async () => {
    const user = userEvent.setup();
    map.seedLayer("parcel-fill-layer");
    render(<PreviewOverlays result={null} map={map} />);
    const kiutToggle = await screen.findByRole("switch", { name: /Uzbrojenie/ });
    await waitFor(() => expect(kiutToggle).not.toBeDisabled());
    await user.click(kiutToggle);
    act(() => map.triggerZoom(17));

    await waitFor(() => expect(map.addLayer).toHaveBeenCalledTimes(3));
    expect(
      map.addLayer.mock.calls.map(([layer]) => (layer as { id: string }).id),
    ).toEqual(["mpzp-wms-layer", "pog-wms-layer", "kiut-wms-layer"]);
    expect(map.addLayer.mock.calls.every((call) => call[1] === "parcel-fill-layer")).toBe(
      true,
    );
  });

  it("pokazuje status analizy MPZP, POG i jawne ograniczenie KIUT", async () => {
    const result = buildAnalyzeResponse({
      mpzp_zones: [
        {
          zone_symbol: "MN",
          primary_use: "mieszkaniowa",
          supplementary_use: null,
          max_building_height_m: null,
          max_floors: null,
          min_biologically_active_pct: null,
          max_floor_area_ratio: null,
          min_floor_area_ratio: null,
          max_building_coverage_pct: null,
          intersection_area_sqm: 100,
          intersection_pct: 100,
          is_dominant: true,
          source: {
            source_name: "MPZP",
            source_url: null,
            fetched_at: null,
            response_status: null,
            confidence: 1,
            manual_review_required: false,
          },
        },
      ],
      pog: {
        schema_version: "2.1",
        legal_status: "binding",
        coverage_status: "available",
        data_availability: "current",
        status_confirmed_at: "2026-09-24T10:00:00Z",
        legal_status_evidence: {
          source_name: "RU",
          official: true,
          reference: null,
          source_id: null,
          raw_value: "legalForce",
          confirmed_at: null,
        },
        coverage_evidence: null,
        act: null,
        zones: [],
        dominant_zone_id: null,
        ouz: [],
        downtown_areas: [],
        social_infrastructure_standard_areas: [],
        status: "binding",
        planning_zone: "SJ",
        zone_type: "SJ",
        in_ouz: false,
        area_ratio: 0.5,
        in_downtown_area: false,
        uchwala_nr: null,
        uchwala_date: null,
        manual_review_required: false,
        compatibility_assessment: null,
        raw_attributes: null,
        ouz_intersection_area_sqm: null,
        ouz_intersection_pct: null,
        touches_ouz_boundary: false,
        source: null,
      },
      utilities_preview: coveredUtilitiesPreview(),
    });

    render(<PreviewOverlays result={result} map={map} />);

    expect(await screen.findByText(/Analiza parametrów: dostępna/)).toBeVisible();
    expect(
      screen.getByText(/Status aktu w gminie: obowiązuje; dane przestrzenne dostępne/),
    ).toBeVisible();
    expect(
      screen.getByText(/Pokrycie powiatu \(powiat krakowski\): publikuje dane GESUT/),
    ).toBeVisible();
    expect(
      screen.getByText(/Brak obiektów na podglądzie nie oznacza braku sieci/),
    ).toBeVisible();
  });

  it("włącza KIUT po analizie, gdy powiat publikuje dane i nie było preferencji", async () => {
    render(
      <PreviewOverlays
        result={buildAnalyzeResponse({
          utilities_preview: coveredUtilitiesPreview(),
        })}
        map={map}
      />,
    );

    const kiutToggle = await screen.findByRole("switch", { name: /Uzbrojenie/ });
    await waitFor(() =>
      expect(kiutToggle).toHaveAttribute("aria-checked", "true"),
    );
    expect(
      screen.getByText(
        /Włączono podgląd uzbrojenia, ponieważ powiat publikuje dane/,
      ),
    ).toBeVisible();
    expect(screen.getByText(/Przybliż do poziomu 17/)).toBeVisible();
    expect(window.localStorage.getItem("dzialki:preview-overlays:v2")).toBeNull();
  });

  it("szanuje jawne wyłączenie KIUT mimo pokrycia powiatu", async () => {
    window.localStorage.setItem(
      "dzialki:preview-overlays:v2",
      JSON.stringify({ mpzp: true, pog: true, kiut: false }),
    );
    render(
      <PreviewOverlays
        result={buildAnalyzeResponse({
          utilities_preview: coveredUtilitiesPreview(),
        })}
        map={map}
      />,
    );

    const kiutToggle = await screen.findByRole("switch", { name: /Uzbrojenie/ });
    await waitFor(() => expect(kiutToggle).not.toBeDisabled());
    expect(kiutToggle).toHaveAttribute("aria-checked", "false");
    expect(
      screen.queryByText(/Włączono podgląd uzbrojenia/),
    ).not.toBeInTheDocument();
  });

  it.each([
    [
      "not_covered" as const,
      "KIUT nie potwierdził publikacji danych GESUT",
      "Pusty podgląd nie jest dowodem braku sieci.",
    ],
    [
      "unknown" as const,
      "nie udało się sprawdzić",
      "Pusty podgląd nie oznacza braku sieci.",
    ],
  ])("pokazuje stan pokrycia KIUT %s", async (coverageStatus, label, note) => {
    const result = buildAnalyzeResponse({
      utilities_preview: {
        coverage_status: coverageStatus,
        county_name: null,
        layer_available: false,
        note,
        source: {
          source_name: "KIUT (GUGiK)",
          source_url: "https://info.example.test/kiut",
          fetched_at: "2026-09-02T10:00:00Z",
          response_status: coverageStatus === "not_covered" ? 200 : null,
          confidence: coverageStatus === "not_covered" ? 0.9 : 0,
          manual_review_required: coverageStatus === "unknown",
        },
      },
    });

    render(<PreviewOverlays result={result} map={map} />);

    expect(await screen.findByText(new RegExp(label))).toBeVisible();
    expect(screen.getByText(new RegExp(note))).toBeVisible();
  });

  it("przy błędzie rejestru zachowuje mapę i stałą notę informacyjną", async () => {
    getPreviewSourcesMock.mockRejectedValue(new Error("503"));

    render(<PreviewOverlays result={null} map={map} />);

    expect(
      await screen.findByText(/Mapa podstawowa pozostaje dostępna/),
    ).toBeVisible();
    expect(map.addSource).not.toHaveBeenCalled();
    expect(map.removeSource).not.toHaveBeenCalled();
    expect(
      screen.getByRole("region", { name: "Informacja o warstwach podglądowych" }),
    ).toBeVisible();
    expect(
      screen.getByText("Informacja o źródłach i ograniczeniach"),
    ).toBeVisible();
    expect(screen.getByText(/Brak obiektów na mapie nie oznacza/)).toBeInTheDocument();
  });

  it("renderuje atrybucje i linki informacyjne także przy wyłączonych warstwach", async () => {
    render(<PreviewOverlays result={null} map={map} />);

    expect(
      screen.getByText("Informacja o źródłach i ograniczeniach"),
    ).toBeVisible();
    expect(
      await screen.findByRole("link", { name: "Uzbrojenie terenu" }),
    ).toHaveAttribute("href", "https://info.example.test/kiut");
    expect(screen.getByRole("link", { name: "Plany ogólne gmin" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Miejscowe plany/ })).toBeInTheDocument();
  });

  it("BK-406: każda warstwa WMS ma jawny stan — ładowanie, dostępna, niepełna po błędzie kafla", async () => {
    render(<PreviewOverlays result={null} map={map} />);
    const stateOf = (name: RegExp) =>
      within(screen.getByRole("switch", { name }).closest("li") as HTMLElement).getByText(
        (_, element) => element?.hasAttribute("data-layer-state") ?? false,
      );
    expect(stateOf(/MPZP/)).toHaveTextContent("ładowanie");
    await waitFor(() => expect(stateOf(/MPZP/)).toHaveTextContent("dostępna"));
    expect(stateOf(/Plan Ogólny Gminy/)).toHaveAttribute("data-layer-state", "available");

    act(() => map.triggerTileError("mpzp-wms-source"));
    act(() => map.triggerTileError("inne-zrodlo"));
    expect(stateOf(/MPZP/)).toHaveTextContent("dane niepełne");
    expect(stateOf(/Plan Ogólny Gminy/)).toHaveTextContent("dostępna");

    // Legenda stanów w nocie o źródłach — 6 stanów, bez „brak planu”.
    const legend = screen.getByText("Co oznacza stan warstwy?").closest("details") as HTMLElement;
    expect(within(legend).getAllByRole("definition")).toHaveLength(6);
    expect(legend.textContent).not.toMatch(/(^|[^a-ząćęłńóśźż])brak planu/i);
  });

  it("BK-406: awaria rejestru daje stan „awaria warstwy” dla podglądów", async () => {
    getPreviewSourcesMock.mockRejectedValueOnce(new Error("503"));
    render(<PreviewOverlays result={null} map={map} />);
    await waitFor(() =>
      expect(
        within(screen.getByRole("switch", { name: /MPZP/ }).closest("li") as HTMLElement).getByText(
          "awaria warstwy",
        ),
      ).toBeInTheDocument(),
    );
  });
});
