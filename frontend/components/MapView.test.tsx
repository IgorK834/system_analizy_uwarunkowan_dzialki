import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MapView } from "@/components/MapView";
import {
  POG_INSPECT_LAYERS,
  POG_LAYER_IDS,
  POG_LAYER_ORDER,
  POG_PROJECT_SOURCE_ID,
  POG_SOURCE_ID,
  pogLayerId,
} from "@/lib/pogLayers";
import { POG_THEME_IDS, themeById, themeFillColorExpression } from "@/lib/pogThemes";
import { buildPogState, buildPogZoneProperties } from "@/test/pogFixtures";

const BINDING = (base: string) => pogLayerId(base, "binding");
const PROJECT = (base: string) => pogLayerId(base, "project");

const mapState = vi.hoisted(() => {
  const sources = new Map<string, Record<string, unknown>>();
  const layers = new Map<string, Record<string, unknown>>();
  const images = new Set<string>();
  const sourceApi = { setTiles: vi.fn(), setData: vi.fn(), setUrl: vi.fn() };
  return {
    sources,
    layers,
    images,
    sourceApi,
    constructor: vi.fn(),
    addControl: vi.fn(),
    on: vi.fn(),
    off: vi.fn(),
    remove: vi.fn(),
    getSource: vi.fn((id: string) => (sources.has(id) ? sourceApi : undefined)),
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
    queryRenderedFeatures: vi.fn(() => [] as unknown[]),
    getZoom: vi.fn(() => 13.5),
    configureWorker: vi.fn(),
    setWorkerUrl: vi.fn(),
  };
});

// MapLibre 6: adres workera ustawia osobny moduł (AU-010); tu sprawdzamy tylko kolejność wywołań.
vi.mock("@/lib/maplibreWorker", () => ({ configureMapLibreWorker: mapState.configureWorker }));

vi.mock("maplibre-gl", () => {
  class MapMock {
    constructor(options: unknown) {
      mapState.constructor(options);
    }

    addControl = mapState.addControl;
    on = mapState.on;
    off = mapState.off;
    remove = mapState.remove;
    getSource = mapState.getSource;
    addSource = mapState.addSource;
    removeSource = mapState.removeSource;
    getLayer = mapState.getLayer;
    addLayer = mapState.addLayer;
    removeLayer = mapState.removeLayer;
    hasImage = mapState.hasImage;
    addImage = mapState.addImage;
    setPaintProperty = mapState.setPaintProperty;
    setLayoutProperty = mapState.setLayoutProperty;
    setFilter = mapState.setFilter;
    queryRenderedFeatures = mapState.queryRenderedFeatures;
    getZoom = mapState.getZoom;
  }

  class NavigationControlMock {}

  // MapLibre 6 eksportuje wyłącznie nazwane symbole (bez eksportu domyślnego).
  return {
    Map: MapMock,
    NavigationControl: NavigationControlMock,
    setWorkerUrl: mapState.setWorkerUrl,
  };
});

function lastHandler(eventName: string) {
  return mapState.on.mock.calls.filter(([name]) => name === eventName).at(-1)?.[1] as (
    event?: unknown,
  ) => void;
}

describe("MapView", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://api.example.test");
    for (const mock of Object.values(mapState)) {
      if (typeof mock === "function" && "mockClear" in mock) mock.mockClear();
    }
    mapState.sources.clear();
    mapState.layers.clear();
    mapState.images.clear();
    Object.values(mapState.sourceApi).forEach((mock) => mock.mockClear());
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
  });

  it("tworzy mapę, przekazuje kliknięcie i usuwa instancję", () => {
    const onMapClick = vi.fn();
    const { unmount } = render(<MapView onMapClick={onMapClick} />);

    expect(mapState.constructor).toHaveBeenCalledWith(
      expect.objectContaining({
        center: [19.5, 52.1],
        zoom: 6,
        cancelPendingTileRequestsWhileZooming: true,
      }),
    );
    expect(mapState.addControl).toHaveBeenCalledWith(expect.anything(), "top-right");
    // Etykiety stref (z14) wymagają glifów — hostowanych przez własną aplikację, nie przez zewnętrzny serwer czcionek.
    expect(mapState.constructor).toHaveBeenCalledWith(
      expect.objectContaining({
        style: expect.objectContaining({
          glyphs: `${window.location.origin}/map-fonts/{fontstack}/{range}.pbf`,
        }),
      }),
    );

    const clickHandler = lastHandler("click");
    act(() => clickHandler({ lngLat: { lng: 21.0122, lat: 52.2297 } }));
    expect(onMapClick).toHaveBeenCalledWith(21.0122, 52.2297);

    unmount();
    expect(mapState.off).toHaveBeenCalledWith("click", clickHandler);
    expect(mapState.remove).toHaveBeenCalledOnce();
  });

  it("ustawia adres workera MapLibre 6 przed utworzeniem mapy (AU-010)", () => {
    render(<MapView />);

    expect(mapState.configureWorker).toHaveBeenCalledOnce();
    expect(mapState.configureWorker.mock.invocationCallOrder[0]).toBeLessThan(
      mapState.constructor.mock.invocationCallOrder[0],
    );
  });

  it("aktualizuje callback bez ponownego tworzenia mapy", () => {
    const firstCallback = vi.fn();
    const secondCallback = vi.fn();
    const { rerender } = render(<MapView onMapClick={firstCallback} />);
    const clickHandler = lastHandler("click");

    rerender(<MapView onMapClick={secondCallback} />);
    act(() => clickHandler({ lngLat: { lng: 18, lat: 51 } }));

    expect(firstCallback).not.toHaveBeenCalled();
    expect(secondCallback).toHaveBeenCalledWith(18, 51);
    expect(mapState.constructor).toHaveBeenCalledOnce();
  });

  it("wywołuje onMapReady z instancją mapy po zdarzeniu load i odrejestrowuje handler przy odmontowaniu", () => {
    const onMapClick = vi.fn();
    const onMapReady = vi.fn();
    const { unmount } = render(
      <MapView onMapClick={onMapClick} onMapReady={onMapReady} />,
    );

    const loadHandler = lastHandler("load");
    act(() => loadHandler());

    expect(onMapReady).toHaveBeenCalledOnce();
    expect(onMapReady).toHaveBeenCalledWith(
      expect.objectContaining({ on: expect.anything() }),
    );
    expect(mapState.addSource).not.toHaveBeenCalled();

    unmount();
    expect(mapState.off).toHaveBeenCalledWith("load", loadHandler);
  });

  it("BK-402: pięć trybów zmienia tylko paint — 0 requestów, te same URL-e źródeł, bez addSource/setData", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const state = buildPogState();
    const { rerender } = render(
      <MapView onMapClick={vi.fn()} pogState={state} pogTheme="zones" />,
    );
    act(() => lastHandler("load")());

    // Dwa źródła: obowiązujące i projekty, oba przypięte do numeru stanu krajowego.
    expect(mapState.addSource).toHaveBeenCalledTimes(2);
    const sourcesBefore = JSON.stringify([...mapState.sources.entries()]);
    expect(mapState.sources.get(POG_SOURCE_ID)?.tiles).toEqual([
      "http://api.example.test/api/v1/map/pog/states/42/{z}/{x}/{y}.mvt?edition=binding",
    ]);
    expect(mapState.sources.get(POG_PROJECT_SOURCE_ID)?.tiles).toEqual([
      "http://api.example.test/api/v1/map/pog/states/42/{z}/{x}/{y}.mvt?edition=project",
    ]);
    const layerCount = mapState.addLayer.mock.calls.length;
    mapState.setPaintProperty.mockClear();

    for (const theme of [...POG_THEME_IDS.slice(1), POG_THEME_IDS[0]]) {
      rerender(<MapView onMapClick={vi.fn()} pogState={state} pogTheme={theme} />);
      expect(mapState.setPaintProperty).toHaveBeenLastCalledWith(
        PROJECT(POG_LAYER_IDS.zonesPattern),
        "fill-opacity",
        expect.any(Array),
      );
      for (const id of [BINDING, PROJECT]) {
        expect(mapState.setPaintProperty).toHaveBeenCalledWith(
          id(POG_LAYER_IDS.zonesFill),
          "fill-color",
          themeFillColorExpression(themeById(theme)),
        );
      }
    }

    expect(mapState.setPaintProperty).toHaveBeenCalledTimes(POG_THEME_IDS.length * 6);
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(mapState.addSource).toHaveBeenCalledTimes(2);
    expect(mapState.removeSource).not.toHaveBeenCalled();
    expect(mapState.addLayer).toHaveBeenCalledTimes(layerCount);
    expect(JSON.stringify([...mapState.sources.entries()])).toBe(sourcesBefore);
    expect(mapState.sourceApi.setTiles).not.toHaveBeenCalled();
    expect(mapState.sourceApi.setData).not.toHaveBeenCalled();
    expect(mapState.sourceApi.setUrl).not.toHaveBeenCalled();
    expect(mapState.constructor).toHaveBeenCalledOnce();
  });

  it("zachowuje wybrany tryb i filtr po remoncie mapy", () => {
    const state = buildPogState();
    const first = render(
      <MapView onMapClick={vi.fn()} pogState={state} pogTheme="height" pogStatusFilter="binding" />,
    );
    act(() => lastHandler("load")());
    first.unmount();
    expect(mapState.remove).toHaveBeenCalledOnce();
    mapState.sources.clear();
    mapState.layers.clear();
    mapState.addLayer.mockClear();

    render(
      <MapView onMapClick={vi.fn()} pogState={state} pogTheme="height" pogStatusFilter="binding" />,
    );
    act(() => lastHandler("load")());

    expect(mapState.layers.get(BINDING(POG_LAYER_IDS.zonesFill))).toMatchObject({
      paint: { "fill-color": themeFillColorExpression(themeById("height")) },
      layout: { visibility: "visible" },
    });
    // Filtr „tylko obowiązujące” ukrywa warstwy projektów (źródło projektów nie pobiera kafli).
    expect(mapState.layers.get(PROJECT(POG_LAYER_IDS.zonesFill))).toMatchObject({ layout: { visibility: "none" } });
    expect(mapState.addLayer).toHaveBeenCalledTimes(POG_LAYER_ORDER.length);
  });

  it("filtr statusu przełącza widoczność warstw źródeł, a kliknięcie zwraca wszystkie obiekty POG w punkcie (BK-404)", () => {
    const onMapClick = vi.fn();
    const onPogInspect = vi.fn();
    const state = buildPogState();
    const { rerender, unmount } = render(
      <MapView onMapClick={onMapClick} pogState={state} onPogInspect={onPogInspect} />,
    );
    act(() => lastHandler("load")());
    mapState.setLayoutProperty.mockClear();
    rerender(
      <MapView
        onMapClick={onMapClick}
        pogState={state}
        pogStatusFilter="non_binding"
        onPogInspect={onPogInspect}
      />,
    );
    expect(mapState.setLayoutProperty).toHaveBeenCalledWith(BINDING(POG_LAYER_IDS.zonesFill), "visibility", "none");
    expect(mapState.setLayoutProperty).toHaveBeenCalledWith(PROJECT(POG_LAYER_IDS.zonesFill), "visibility", "visible");
    expect(mapState.setFilter).not.toHaveBeenCalled();
    expect(mapState.addSource).toHaveBeenCalledTimes(2);

    const zone = buildPogZoneProperties();
    mapState.queryRenderedFeatures.mockReturnValueOnce([
      { id: 7, layer: { id: BINDING(POG_LAYER_IDS.zonesFill) }, properties: zone },
      { id: 7, layer: { id: BINDING(POG_LAYER_IDS.zonesFill) }, properties: zone },
    ]);
    act(() => lastHandler("click")({ point: { x: 1, y: 2 }, lngLat: { lng: 18.5, lat: 54.4 } }));
    expect(mapState.queryRenderedFeatures).toHaveBeenCalledWith(
      { x: 1, y: 2 },
      { layers: Object.keys(POG_INSPECT_LAYERS) },
    );
    expect(onPogInspect).toHaveBeenCalledWith({
      lon: 18.5,
      lat: 54.4,
      queried: true,
      stateId: 42,
      zoom: 13.5,
      hits: [{ key: "zones:7", layer: "zones", featurePk: 7, properties: zone }],
    });
    expect(onMapClick).toHaveBeenCalledWith(18.5, 54.4);

    rerender(<MapView onMapClick={onMapClick} pogState={null} onPogInspect={onPogInspect} />);
    expect(mapState.removeSource).toHaveBeenCalledWith(POG_SOURCE_ID);
    expect(mapState.removeSource).toHaveBeenCalledWith(POG_PROJECT_SOURCE_ID);
    unmount();
  });

  it("bez warstw POG kliknięcie nie odpytuje kafli i zgłasza „nie sprawdzono”", () => {
    const onPogInspect = vi.fn();
    render(<MapView onPogInspect={onPogInspect} />);
    act(() => lastHandler("load")());
    act(() => lastHandler("click")({ point: { x: 3, y: 4 }, lngLat: { lng: 21, lat: 52 } }));
    expect(mapState.queryRenderedFeatures).not.toHaveBeenCalled();
    expect(onPogInspect).toHaveBeenCalledWith({ lon: 21, lat: 52, queried: false, hits: [], stateId: null, zoom: 13.5 });
  });
});
