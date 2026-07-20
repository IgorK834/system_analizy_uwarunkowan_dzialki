import { act, render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { MapView } from "@/components/MapView";

const mapState = vi.hoisted(() => ({
  constructor: vi.fn(),
  addControl: vi.fn(),
  on: vi.fn(),
  off: vi.fn(),
  remove: vi.fn(),
}));

vi.mock("maplibre-gl", () => {
  class MapMock {
    constructor(options: unknown) {
      mapState.constructor(options);
    }

    addControl = mapState.addControl;
    on = mapState.on;
    off = mapState.off;
    remove = mapState.remove;
  }

  class NavigationControlMock {}

  return {
    default: {
      Map: MapMock,
      NavigationControl: NavigationControlMock,
    },
  };
});

describe("MapView", () => {
  beforeEach(() => {
    for (const mock of Object.values(mapState)) mock.mockClear();
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

    const clickHandler = mapState.on.mock.calls.find(
      ([eventName]) => eventName === "click",
    )?.[1] as (event: { lngLat: { lng: number; lat: number } }) => void;
    act(() => clickHandler({ lngLat: { lng: 21.0122, lat: 52.2297 } }));
    expect(onMapClick).toHaveBeenCalledWith(21.0122, 52.2297);

    unmount();
    expect(mapState.off).toHaveBeenCalledWith("click", clickHandler);
    expect(mapState.remove).toHaveBeenCalledOnce();
  });

  it("aktualizuje callback bez ponownego tworzenia mapy", () => {
    const firstCallback = vi.fn();
    const secondCallback = vi.fn();
    const { rerender } = render(<MapView onMapClick={firstCallback} />);
    const clickHandler = mapState.on.mock.calls[0][1] as (event: {
      lngLat: { lng: number; lat: number };
    }) => void;

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

    const loadHandler = mapState.on.mock.calls.find(
      ([eventName]) => eventName === "load",
    )?.[1] as () => void;
    act(() => loadHandler());

    expect(onMapReady).toHaveBeenCalledOnce();
    expect(onMapReady).toHaveBeenCalledWith(
      expect.objectContaining({ on: expect.anything() }),
    );

    unmount();
    expect(mapState.off).toHaveBeenCalledWith("load", loadHandler);
  });
});
