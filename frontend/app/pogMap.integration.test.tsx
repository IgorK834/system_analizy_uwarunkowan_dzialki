/**
 * Scenariusz końcowy BK-401–403 na prawdziwej stronie: page.tsx + MapView +
 * PogMapPanel + adaptery stylu. Atrapą są wyłącznie MapLibre (brak WebGL w
 * jsdom) i klient HTTP. Test sprawdza przypięcie URL-a kafli do wydania, pięć
 * trybów bez żadnego żądania sieciowego, filtr projekt/wiążący, zachowanie
 * trybu po remoncie mapy i atrybuty klikniętej strefy z kafla.
 */
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import HomePage from "@/app/page";
import { MapView, type MapViewProps } from "@/components/MapView";
import { analyzeParcel, getActivePogTileRelease, getPreviewSources } from "@/lib/api";
import { POG_LAYER_IDS, POG_SOURCE_ID } from "@/lib/pogLayers";
import { POG_THEMES, themeById, themeFillColorExpression } from "@/lib/pogThemes";
import { buildPogRelease, buildPogZoneProperties } from "@/test/pogFixtures";

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
    setFilter: vi.fn(),
    queryRenderedFeatures: vi.fn(() => [] as unknown[]),
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
    setFilter = mapState.setFilter;
    queryRenderedFeatures = mapState.queryRenderedFeatures;
    getZoom = () => 14;
    setLayoutProperty = vi.fn();
    fitBounds = vi.fn();
    flyTo = vi.fn();
  }
  return { default: { Map: MapMock, NavigationControl: class {} } };
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
    getActivePogTileRelease: vi.fn(),
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
    mapState.setFilter.mockClear();
    vi.mocked(getActivePogTileRelease).mockReset().mockResolvedValue(buildPogRelease());
    vi.mocked(getPreviewSources).mockReset().mockResolvedValue([]);
    vi.mocked(analyzeParcel).mockReset().mockReturnValue(new Promise(() => undefined));
  });

  it("5 trybów klawiaturą i myszą: 0 requestów, ten sam URL źródła, tylko setPaintProperty", async () => {
    const user = userEvent.setup();
    render(<HomePage />);
    fire("load");
    await screen.findByText(/Wydanie pog-0123456789ab \(#42\)/);
    await waitFor(() => expect(mapState.addSource).toHaveBeenCalledOnce());
    const source = JSON.stringify(mapState.sources.get(POG_SOURCE_ID));
    expect(mapState.sources.get(POG_SOURCE_ID)?.tiles).toEqual([
      "http://api.example.test/api/v1/map/pog/releases/42/{z}/{x}/{y}.mvt",
    ]);

    const fetchSpy = vi.spyOn(globalThis, "fetch");
    mapState.setPaintProperty.mockClear();
    const panel = screen.getByRole("group", { name: "Tryb mapy planu ogólnego" });
    for (const theme of POG_THEMES.slice(1)) {
      await user.click(within(panel).getByRole("radio", { name: new RegExp(theme.label) }));
      expect(mapState.setPaintProperty).toHaveBeenCalledWith(
        POG_LAYER_IDS.zonesFill,
        "fill-color",
        themeFillColorExpression(themeById(theme.id)),
      );
      expect(screen.getByLabelText(`Legenda: ${theme.label}`)).toBeInTheDocument();
    }
    await user.keyboard("{ArrowDown}");
    expect(within(panel).getByRole("radio", { name: /Strefy planistyczne/ })).toBeChecked();

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(getActivePogTileRelease).toHaveBeenCalledOnce();
    expect(mapState.addSource).toHaveBeenCalledOnce();
    expect(JSON.stringify(mapState.sources.get(POG_SOURCE_ID))).toBe(source);
    expect(analyzeParcel).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  it("filtr projekt/wiążący i atrybuty klikniętej strefy z kafla", async () => {
    const user = userEvent.setup();
    render(<HomePage />);
    fire("load");
    await screen.findByText(/Wydanie zawiera projekty/);

    await user.click(screen.getByRole("radio", { name: "Tylko akty obowiązujące" }));
    expect(mapState.setFilter).toHaveBeenCalledWith(POG_LAYER_IDS.zonesFill, [
      "==",
      ["get", "legal_status"],
      "binding",
    ]);
    expect(mapState.addSource).toHaveBeenCalledOnce();

    mapState.queryRenderedFeatures.mockReturnValueOnce([
      { layer: { id: POG_LAYER_IDS.zonesFill }, properties: buildPogZoneProperties() },
    ]);
    fire("click", { point: { x: 5, y: 5 }, lngLat: { lng: 18.538, lat: 54.456 } });
    const selected = await screen.findByLabelText("Wybrana strefa z mapy POG");
    expect(within(selected).getByTestId("pog-selected-max_building_height_m")).toHaveTextContent("4 m");
    expect(within(selected).getByTestId("pog-selected-max_building_coverage_pct")).toHaveTextContent("90%");
  });

  it("po remoncie mapy tryb i filtr są odtwarzane, a mapa działa bez wydania RU", async () => {
    const user = userEvent.setup();
    const first = render(<HomePage />);
    fire("load");
    await screen.findByText(/Wydanie pog-0123456789ab/);
    await user.click(screen.getByRole("radio", { name: /Maksymalna wysokość zabudowy/ }));
    first.unmount();
    mapState.sources.clear();
    mapState.layers.clear();

    render(<HomePage />);
    fire("load");
    await waitFor(() =>
      expect(mapState.layers.get(POG_LAYER_IDS.zonesFill)).toMatchObject({
        paint: { "fill-color": themeFillColorExpression(themeById("height")) },
      }),
    );
    expect(screen.getByRole("radio", { name: /Maksymalna wysokość zabudowy/ })).toBeChecked();
    expect(mapState.instances).toBe(2);
  });

  it("brak lokalnego wydania nie jest opisany jako brak planu", async () => {
    vi.mocked(getActivePogTileRelease).mockResolvedValue(null);
    render(<HomePage />);
    fire("load");
    expect(await screen.findByText(/Nie oznacza to braku planu ogólnego/)).toBeInTheDocument();
    expect(mapState.addSource).not.toHaveBeenCalled();
  });
});
