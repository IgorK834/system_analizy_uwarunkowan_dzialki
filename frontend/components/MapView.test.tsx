import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MapView } from "@/components/MapView";
import { POG_LAYER_IDS, POG_LAYER_ORDER, POG_SOURCE_ID } from "@/lib/pogLayers";
import { POG_THEME_IDS, themeById, themeFillColorExpression } from "@/lib/pogThemes";
import { buildPogRelease, buildPogZoneProperties } from "@/test/pogFixtures";

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
    setFilter: vi.fn(),
    queryRenderedFeatures: vi.fn(() => [] as unknown[]),
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
    setFilter = mapState.setFilter;
    queryRenderedFeatures = mapState.queryRenderedFeatures;
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

  it("BK-402: pięć trybów zmienia tylko paint — 0 requestów, ten sam URL źródła, bez addSource/setData", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const release = buildPogRelease();
    const { rerender } = render(
      <MapView onMapClick={vi.fn()} pogRelease={release} pogTheme="zones" />,
    );
    act(() => lastHandler("load")());

    expect(mapState.addSource).toHaveBeenCalledOnce();
    const sourceBefore = JSON.stringify(mapState.sources.get(POG_SOURCE_ID));
    expect(mapState.sources.get(POG_SOURCE_ID)?.tiles).toEqual([
      "http://api.example.test/api/v1/map/pog/releases/42/{z}/{x}/{y}.mvt",
    ]);
    const layerCount = mapState.addLayer.mock.calls.length;
    mapState.setPaintProperty.mockClear();

    for (const theme of [...POG_THEME_IDS.slice(1), POG_THEME_IDS[0]]) {
      rerender(<MapView onMapClick={vi.fn()} pogRelease={release} pogTheme={theme} />);
      expect(mapState.setPaintProperty).toHaveBeenLastCalledWith(
        POG_LAYER_IDS.zonesPattern,
        "fill-opacity",
        expect.any(Array),
      );
      expect(mapState.setPaintProperty).toHaveBeenCalledWith(
        POG_LAYER_IDS.zonesFill,
        "fill-color",
        themeFillColorExpression(themeById(theme)),
      );
    }

    expect(mapState.setPaintProperty).toHaveBeenCalledTimes(POG_THEME_IDS.length * 3);
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(mapState.addSource).toHaveBeenCalledOnce();
    expect(mapState.removeSource).not.toHaveBeenCalled();
    expect(mapState.addLayer).toHaveBeenCalledTimes(layerCount);
    expect(JSON.stringify(mapState.sources.get(POG_SOURCE_ID))).toBe(sourceBefore);
    expect(mapState.sourceApi.setTiles).not.toHaveBeenCalled();
    expect(mapState.sourceApi.setData).not.toHaveBeenCalled();
    expect(mapState.sourceApi.setUrl).not.toHaveBeenCalled();
    expect(mapState.constructor).toHaveBeenCalledOnce();
  });

  it("zachowuje wybrany tryb i filtr po remoncie mapy", () => {
    const release = buildPogRelease();
    const first = render(
      <MapView onMapClick={vi.fn()} pogRelease={release} pogTheme="height" pogStatusFilter="binding" />,
    );
    act(() => lastHandler("load")());
    first.unmount();
    expect(mapState.remove).toHaveBeenCalledOnce();
    mapState.sources.clear();
    mapState.layers.clear();
    mapState.addLayer.mockClear();

    render(
      <MapView onMapClick={vi.fn()} pogRelease={release} pogTheme="height" pogStatusFilter="binding" />,
    );
    act(() => lastHandler("load")());

    const zonesFill = mapState.layers.get(POG_LAYER_IDS.zonesFill);
    expect(zonesFill).toMatchObject({
      paint: { "fill-color": themeFillColorExpression(themeById("height")) },
      filter: ["==", ["get", "legal_status"], "binding"],
    });
    expect(mapState.addLayer).toHaveBeenCalledTimes(POG_LAYER_ORDER.length);
  });

  it("filtr statusu używa setFilter, a kliknięcie zwraca wszystkie obiekty POG w punkcie (BK-404)", () => {
    const onMapClick = vi.fn();
    const onPogInspect = vi.fn();
    const release = buildPogRelease();
    const { rerender, unmount } = render(
      <MapView onMapClick={onMapClick} pogRelease={release} onPogInspect={onPogInspect} />,
    );
    act(() => lastHandler("load")());
    mapState.setFilter.mockClear();
    rerender(
      <MapView
        onMapClick={onMapClick}
        pogRelease={release}
        pogStatusFilter="non_binding"
        onPogInspect={onPogInspect}
      />,
    );
    expect(mapState.setFilter).toHaveBeenCalledWith(POG_LAYER_IDS.zonesFill, [
      "in",
      ["get", "legal_status"],
      ["literal", ["project", "in_progress"]],
    ]);
    expect(mapState.addSource).toHaveBeenCalledOnce();

    const zone = buildPogZoneProperties();
    mapState.queryRenderedFeatures.mockReturnValueOnce([
      { id: 7, layer: { id: POG_LAYER_IDS.zonesFill }, properties: zone },
      { id: 7, layer: { id: POG_LAYER_IDS.zonesFill }, properties: zone },
    ]);
    act(() => lastHandler("click")({ point: { x: 1, y: 2 }, lngLat: { lng: 18.5, lat: 54.4 } }));
    expect(mapState.queryRenderedFeatures).toHaveBeenCalledWith(
      { x: 1, y: 2 },
      {
        layers: [
          POG_LAYER_IDS.zonesFill,
          POG_LAYER_IDS.ouzPattern,
          POG_LAYER_IDS.downtownPattern,
          POG_LAYER_IDS.socialPattern,
        ],
      },
    );
    expect(onPogInspect).toHaveBeenCalledWith({
      lon: 18.5,
      lat: 54.4,
      queried: true,
      hits: [{ key: "zones:42:7", layer: "zones", featurePk: 7, properties: zone }],
    });
    expect(onMapClick).toHaveBeenCalledWith(18.5, 54.4);

    rerender(<MapView onMapClick={onMapClick} pogRelease={null} onPogInspect={onPogInspect} />);
    expect(mapState.removeSource).toHaveBeenCalledWith(POG_SOURCE_ID);
    unmount();
  });

  it("bez warstw POG kliknięcie nie odpytuje kafli i zgłasza „nie sprawdzono”", () => {
    const onPogInspect = vi.fn();
    render(<MapView onPogInspect={onPogInspect} />);
    act(() => lastHandler("load")());
    act(() => lastHandler("click")({ point: { x: 3, y: 4 }, lngLat: { lng: 21, lat: 52 } }));
    expect(mapState.queryRenderedFeatures).not.toHaveBeenCalled();
    expect(onPogInspect).toHaveBeenCalledWith({ lon: 21, lat: 52, queried: false, hits: [] });
  });
});
