/**
 * Scenariusz końcowy BK-401–406 na prawdziwej stronie: page.tsx + MapView +
 * PogMapPanel + PogFeatureInspector + PogAreaSummary + adaptery stylu i stanu
 * warstwy. Atrapą są wyłącznie MapLibre (brak WebGL w jsdom) i klient HTTP.
 * Test sprawdza przypięcie URL-i kafli do stanu krajowego (dwa źródła:
 * obowiązujące i projekty), pięć trybów bez żądań, przełącznik
 * obowiązujące/projekty (widoczność warstw), zachowanie trybu po remoncie
 * mapy, inspektor obiektów bez uruchamiania analizy (BK-404), stany warstwy z
 * awarią kafla i danymi nieaktualnymi (BK-406) oraz panel pokrycia importu
 * (issue 301).
 */
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import HomePage from "@/app/page";
import { MapView, type MapViewProps } from "@/components/MapView";
import {
  analyzeParcel,
  getCurrentPogTileState,
  getPogAreaSummary,
  getPogCommuneState,
  getPogCoverage,
  getPogFeatureDetails,
  getPreviewSources,
} from "@/lib/api";
import { POG_LAYER_IDS, POG_PROJECT_SOURCE_ID, POG_SOURCE_ID, pogLayerId } from "@/lib/pogLayers";
import { POG_THEMES, themeById, themeFillColorExpression } from "@/lib/pogThemes";
import {
  buildPogAreaSummary,
  buildPogCoverage,
  buildPogFeatureDetails,
  buildPogOverlayProperties,
  buildPogState,
  buildPogZoneProperties,
} from "@/test/pogFixtures";

const BINDING = (base: string) => pogLayerId(base, "binding");
const PROJECT = (base: string) => pogLayerId(base, "project");

const mapState = vi.hoisted(() => {
  const sources = new Map<string, Record<string, unknown>>();
  const layers = new Map<string, Record<string, unknown>>();
  const images = new Set<string>();
  const handlers: Array<[string, (event?: unknown) => void]> = [];
  return {
    sources,
    layers,
    images,
    handlers,
    instances: 0,
    addSource: vi.fn((id: string, source: Record<string, unknown>) => {
      sources.set(id, source);
    }),
    setPaintProperty: vi.fn(),
    setLayoutProperty: vi.fn(),
    setFilter: vi.fn(),
    queryRenderedFeatures: vi.fn(() => [] as unknown[]),
    refreshTiles: vi.fn(),
  };
});

vi.mock("maplibre-gl", () => {
  class MapMock {
    constructor() {
      mapState.instances += 1;
    }
    addControl = vi.fn();
    on = (name: string, handler: (event?: unknown) => void) => {
      mapState.handlers.push([name, handler]);
    };
    off = vi.fn();
    remove = vi.fn();
    getSource = (id: string) => mapState.sources.get(id);
    addSource = mapState.addSource;
    removeSource = (id: string) => mapState.sources.delete(id);
    getLayer = (id: string) => mapState.layers.get(id);
    addLayer = (layer: Record<string, unknown>) => {
      mapState.layers.set(String(layer.id), layer);
    };
    removeLayer = (id: string) => mapState.layers.delete(id);
    hasImage = (id: string) => mapState.images.has(id);
    addImage = (id: string) => {
      mapState.images.add(id);
    };
    setPaintProperty = mapState.setPaintProperty;
    setLayoutProperty = mapState.setLayoutProperty;
    setFilter = mapState.setFilter;
    queryRenderedFeatures = mapState.queryRenderedFeatures;
    getZoom = () => 14;
    getBounds = () => ({
      getWest: () => 18.52,
      getSouth: () => 54.42,
      getEast: () => 18.58,
      getNorth: () => 54.46,
    });
    refreshTiles = mapState.refreshTiles;
    fitBounds = vi.fn();
    flyTo = vi.fn();
  }
  // MapLibre 6 eksportuje wyłącznie nazwane symbole (bez eksportu domyślnego).
  return { Map: MapMock, NavigationControl: class {}, setWorkerUrl: vi.fn() };
});

vi.mock("@/components/MapViewLoader", () => ({
  MapViewLoader: (props: MapViewProps) => <MapView {...props} />,
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    analyzeParcel: vi.fn(),
    getPreviewSources: vi.fn(),
    getCurrentPogTileState: vi.fn(),
    getPogFeatureDetails: vi.fn(),
    getPogAreaSummary: vi.fn(),
    getPogCoverage: vi.fn(),
    getPogCommuneState: vi.fn(),
    resumeAnalysis: vi.fn(),
  };
});

function fire(eventName: string, event?: unknown) {
  const handler = mapState.handlers.filter(([name]) => name === eventName).at(-1)?.[1];
  act(() => handler?.(event));
}

describe("Mapa analityczna POG — scenariusz końcowy", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://api.example.test");
    mapState.sources.clear();
    mapState.layers.clear();
    mapState.images.clear();
    mapState.handlers.length = 0;
    mapState.instances = 0;
    mapState.addSource.mockClear();
    mapState.setPaintProperty.mockClear();
    mapState.setLayoutProperty.mockClear();
    mapState.setFilter.mockClear();
    mapState.refreshTiles.mockClear();
    mapState.queryRenderedFeatures.mockReset().mockReturnValue([]);
    vi.mocked(getPogFeatureDetails).mockReset().mockResolvedValue(buildPogFeatureDetails());
    vi.mocked(getPogAreaSummary).mockReset().mockResolvedValue(buildPogAreaSummary());
    vi.mocked(getCurrentPogTileState).mockReset().mockResolvedValue(buildPogState());
    vi.mocked(getPogCoverage).mockReset().mockResolvedValue(buildPogCoverage());
    vi.mocked(getPogCommuneState).mockReset();
    vi.mocked(getPreviewSources).mockReset().mockResolvedValue([]);
    vi.mocked(analyzeParcel).mockReset().mockReturnValue(new Promise(() => undefined));
  });

  it("5 trybów klawiaturą i myszą: 0 requestów, te same URL-e źródeł, tylko setPaintProperty", async () => {
    const user = userEvent.setup();
    render(<HomePage />);
    fire("load");
    await screen.findByText(/Stan krajowy #42/);
    await waitFor(() => expect(mapState.addSource).toHaveBeenCalledTimes(2));
    const source = JSON.stringify([...mapState.sources.entries()]);
    expect(mapState.sources.get(POG_SOURCE_ID)?.tiles).toEqual([
      "http://api.example.test/api/v1/map/pog/states/42/{z}/{x}/{y}.mvt?edition=binding",
    ]);
    expect(mapState.sources.get(POG_PROJECT_SOURCE_ID)?.tiles).toEqual([
      "http://api.example.test/api/v1/map/pog/states/42/{z}/{x}/{y}.mvt?edition=project",
    ]);

    const fetchSpy = vi.spyOn(globalThis, "fetch");
    mapState.setPaintProperty.mockClear();
    const panel = screen.getByRole("group", { name: "Tryb mapy planu ogólnego" });
    for (const theme of POG_THEMES.slice(1)) {
      await user.click(within(panel).getByRole("radio", { name: new RegExp(theme.label) }));
      expect(mapState.setPaintProperty).toHaveBeenCalledWith(
        BINDING(POG_LAYER_IDS.zonesFill),
        "fill-color",
        themeFillColorExpression(themeById(theme.id)),
      );
      expect(screen.getByLabelText(`Legenda: ${theme.label}`)).toBeInTheDocument();
    }
    await user.keyboard("{ArrowDown}");
    expect(within(panel).getByRole("radio", { name: /Strefy planistyczne/ })).toBeChecked();

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(getCurrentPogTileState).toHaveBeenCalledOnce();
    expect(getPogCoverage).not.toHaveBeenCalled();
    expect(mapState.addSource).toHaveBeenCalledTimes(2);
    expect(JSON.stringify([...mapState.sources.entries()])).toBe(source);
    expect(analyzeParcel).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  it("BK-404: klik obiektu otwiera inspektor bez POST /analyze; przycisk wysyła dokładnie jedno żądanie", async () => {
    const user = userEvent.setup();
    render(<HomePage />);
    fire("load");
    await screen.findByText(/Stan krajowy #42/);
    fire("sourcedata", { sourceId: POG_SOURCE_ID, isSourceLoaded: true, tile: {} });

    await user.click(screen.getByRole("radio", { name: "Tylko akty obowiązujące" }));
    expect(mapState.setLayoutProperty).toHaveBeenCalledWith(BINDING(POG_LAYER_IDS.zonesFill), "visibility", "visible");
    expect(mapState.setLayoutProperty).toHaveBeenCalledWith(PROJECT(POG_LAYER_IDS.zonesFill), "visibility", "none");
    expect(mapState.addSource).toHaveBeenCalledTimes(2);
    await user.click(screen.getByRole("radio", { name: "Wszystkie akty" }));

    const zone = buildPogZoneProperties();
    const project = buildPogZoneProperties({
      feature_id: "PL.ZIPPZP.99999/226401-POG/2POG-1SJ",
      symbol: "SJ",
      designation: "1SJ",
      zone_code: "SJ",
      legal_status: "project",
      max_building_height_m: undefined,
    });
    const ouz = buildPogOverlayProperties();
    mapState.queryRenderedFeatures.mockReturnValueOnce([
      { id: 1, layer: { id: BINDING(POG_LAYER_IDS.zonesFill) }, properties: zone },
      { id: 1, layer: { id: BINDING(POG_LAYER_IDS.zonesFill) }, properties: zone },
      { id: 2, layer: { id: PROJECT(POG_LAYER_IDS.zonesFill) }, properties: project },
      { id: 3, layer: { id: BINDING(POG_LAYER_IDS.ouzPattern) }, properties: ouz },
      { id: 3, layer: { id: BINDING(POG_LAYER_IDS.ouzPattern) }, properties: ouz },
    ]);
    fire("click", { point: { x: 5, y: 5 }, lngLat: { lng: 18.538, lat: 54.456 } });

    const inspector = await screen.findByTestId("pog-inspector");
    const zones = within(inspector).getAllByTestId("pog-inspector-zone");
    expect(zones).toHaveLength(2);
    expect(within(zones[0]).getByTestId("pog-inspector-max_building_height_m")).toHaveTextContent("4 m");
    expect(within(zones[0]).getByTestId("pog-inspector-max_building_coverage_pct")).toHaveTextContent("90%");
    expect(within(zones[1]).getByTestId("pog-inspector-max_building_height_m")).toHaveTextContent(
      "brak wartości w danych",
    );
    expect(within(zones[1]).getByTestId("pog-inspector-badge")).toHaveTextContent("projekt / dane niewiążące");
    expect(within(inspector).getAllByTestId("pog-inspector-overlay")).toHaveLength(1);
    expect(within(inspector).getByText(/Dane z kafli mapy — stan krajowy #42/)).toBeInTheDocument();
    expect(await within(zones[0]).findByText(/Plan ogólny Miasta Sopotu/)).toBeInTheDocument();
    expect(getPogFeatureDetails).toHaveBeenCalledWith(42, zone.feature_id, expect.anything());
    expect(analyzeParcel).not.toHaveBeenCalled();

    // BK-405 z inspektora: gotowy agregat aktu — wykres i tabela z tymi samymi liczbami.
    await user.click(within(zones[0]).getByRole("button", { name: "Struktura stref aktu i gminy" }));
    const table = await within(zones[0]).findByTestId("pog-area-table");
    const shares = within(table).getAllByTestId("pog-area-cell-share").map((cell) => cell.textContent);
    const bars = within(zones[0]).getAllByTestId("pog-area-bar-value").map((bar) => bar.textContent);
    expect(shares).toEqual(["60,0%", "40,0%"]);
    expect(bars).toEqual(shares);
    expect(analyzeParcel).not.toHaveBeenCalled();

    await user.click(within(inspector).getByRole("button", { name: "Analizuj działkę w tym punkcie" }));
    expect(analyzeParcel).toHaveBeenCalledOnce();
    expect(analyzeParcel).toHaveBeenCalledWith(
      { method: "map", lon: 18.538, lat: 54.456 },
      expect.anything(),
    );
  });

  it("BK-406: edycja klawiaturą, awaria kafla i nieudane ponowienie — plakietka i data stale widoczne", async () => {
    const user = userEvent.setup();
    render(<HomePage />);
    fire("load");
    await screen.findByText(/Stan krajowy #42/);
    const state = () => screen.getByTestId("pog-layer-state");
    const badge = () => screen.getByTestId("pog-status-badge");
    expect(state()).toHaveTextContent("ładowanie");
    expect(badge()).toHaveTextContent("projekt / dane niewiążące");

    fire("sourcedata", { sourceId: POG_SOURCE_ID, isSourceLoaded: true, tile: {} });
    expect(state()).toHaveTextContent("dostępna");

    // Jawna zmiana edycji klawiaturą — te same źródłowe URL-e, tylko widoczność warstw.
    const edition = screen.getByRole("group", { name: "Edycja danych (status prawny aktu)" });
    within(edition).getByRole("radio", { name: "Wszystkie akty" }).focus();
    await user.keyboard("{ArrowDown}{ArrowDown}");
    expect(within(edition).getByRole("radio", { name: "Tylko projekty (niewiążące)" })).toBeChecked();
    expect(mapState.setLayoutProperty).toHaveBeenCalledWith(BINDING(POG_LAYER_IDS.zonesFill), "visibility", "none");
    expect(mapState.setLayoutProperty).toHaveBeenCalledWith(PROJECT(POG_LAYER_IDS.zonesFill), "visibility", "visible");
    expect(mapState.setFilter).not.toHaveBeenCalled();

    // Awaria kafla (np. 503 z serwera kafli): stan partial, plakietka zostaje.
    fire("error", { sourceId: POG_SOURCE_ID, error: { status: 503 } });
    expect(state()).toHaveTextContent("dane niepełne");
    expect(screen.getByText(/Część kafli POG nie została wczytana \(1\)/)).toBeInTheDocument();
    expect(badge()).toBeVisible();
    expect(document.body.textContent).not.toMatch(/(^|[^a-ząćęłńóśźż])(?<!≠ )brak planu/i);

    // Ponowienie: metadane stanu niedostępne → stale z datą i tym samym stanem.
    vi.mocked(getCurrentPogTileState).mockRejectedValueOnce(new Error("503"));
    await user.click(screen.getByRole("button", { name: "Ponów wczytanie warstwy" }));
    expect(mapState.refreshTiles).toHaveBeenCalledWith(POG_SOURCE_ID);
    expect(mapState.refreshTiles).toHaveBeenCalledWith(POG_PROJECT_SOURCE_ID);
    await waitFor(() => expect(state()).toHaveTextContent("dane nieaktualne"));
    expect(screen.getByTestId("pog-state-line")).toHaveTextContent(
      /Stan krajowy #42 \(import, 28\.09\.2026\).*ostatnio potwierdzone \d{2}\.\d{2}\.\d{4}, \d{2}:\d{2}/,
    );
    expect(badge()).toHaveTextContent("projekt / dane niewiążące");
    // Ostatnie dane nie zostały usunięte z mapy.
    expect(mapState.sources.has(POG_SOURCE_ID)).toBe(true);
    expect(mapState.addSource).toHaveBeenCalledTimes(2);
    expect(document.body.textContent).not.toMatch(/(^|[^a-ząćęłńóśźż])(?<!≠ )brak planu/i);
    expect(analyzeParcel).not.toHaveBeenCalled();
  });

  it("po remoncie mapy tryb i filtr są odtwarzane", async () => {
    const user = userEvent.setup();
    const first = render(<HomePage />);
    fire("load");
    await screen.findByText(/Stan krajowy #42/);
    await user.click(screen.getByRole("radio", { name: /Maksymalna wysokość zabudowy/ }));
    first.unmount();
    mapState.sources.clear();
    mapState.layers.clear();

    render(<HomePage />);
    fire("load");
    await waitFor(() =>
      expect(mapState.layers.get(BINDING(POG_LAYER_IDS.zonesFill))).toMatchObject({
        paint: { "fill-color": themeFillColorExpression(themeById("height")) },
      }),
    );
    expect(screen.getByRole("radio", { name: /Maksymalna wysokość zabudowy/ })).toBeChecked();
    expect(mapState.instances).toBe(2);
  });

  it("brak zaimportowanych gmin nie jest opisany jako brak planu", async () => {
    vi.mocked(getCurrentPogTileState).mockResolvedValue(null);
    render(<HomePage />);
    fire("load");
    expect(await screen.findByText(/Nie oznacza to braku planu ogólnego/)).toBeInTheDocument();
    expect(mapState.addSource).not.toHaveBeenCalled();
  });

  it("issue 301: panel pokrycia pobiera listę dopiero po rozwinięciu i rozróżnia stany gmin bez „brak planu”", async () => {
    const user = userEvent.setup();
    vi.mocked(getPogCommuneState).mockResolvedValue({
      terc: "246101",
      status: "in_ru_not_imported",
      catalog_synced_at: "2026-10-13T11:00:00Z",
      detail: "x",
    });
    render(<HomePage />);
    fire("load");
    await screen.findByText(/Stan krajowy #42/);
    expect(getPogCoverage).not.toHaveBeenCalled();

    await user.click(screen.getByText("Pokrycie importu planów ogólnych (gminy)"));
    const summary = await screen.findByTestId("pog-coverage-summary");
    expect(summary).toHaveTextContent("zaimportowane: 1; czekają na import: 2");
    const items = screen.getAllByTestId("pog-coverage-item");
    expect(items.map((item) => item.dataset.status)).toEqual(["imported", "in_ru_not_imported", "in_ru_not_imported"]);
    expect(items[0]).toHaveTextContent("Zaimportowana");
    expect(items[1]).toHaveTextContent("Brak lokalnych danych (≠ brak planu)");
    expect(items[1]).toHaveTextContent("zlecenie importu w kolejce");

    await user.type(screen.getByLabelText("Sprawdź gminę po kodzie TERC (6 cyfr)"), "246101");
    await user.click(screen.getByRole("button", { name: "Sprawdź" }));
    expect(await screen.findByTestId("pog-coverage-lookup")).toHaveTextContent(/Brak lokalnych danych \(≠ brak planu\)/);
    expect(getPogCommuneState).toHaveBeenCalledWith("246101");
    expect(document.body.textContent).not.toMatch(/(^|[^a-ząćęłńóśźż])(?<!≠ )brak planu(?! ogólnego| w gminie| —| w RU)/i);
    expect(analyzeParcel).not.toHaveBeenCalled();
  });
});
