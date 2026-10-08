import { act, renderHook } from "@testing-library/react";
import type * as maplibregl from "maplibre-gl";
import { describe, expect, it, vi } from "vitest";

import { usePogTileActivity } from "@/hooks/usePogTileActivity";
import { POG_SOURCE_ID } from "@/lib/pogLayers";
import { INITIAL_TILE_ACTIVITY } from "@/lib/pogLayerState";

function createMap() {
  const handlers = new Map<string, Array<(event?: unknown) => void>>();
  let bounds = [18.5, 54.4, 18.6, 54.5];
  const map = {
    on: vi.fn((name: string, handler: (event?: unknown) => void) => {
      handlers.set(name, [...(handlers.get(name) ?? []), handler]);
    }),
    off: vi.fn((name: string, handler: (event?: unknown) => void) => {
      handlers.set(name, (handlers.get(name) ?? []).filter((item) => item !== handler));
    }),
    getBounds: vi.fn(() => ({
      getWest: () => bounds[0],
      getSouth: () => bounds[1],
      getEast: () => bounds[2],
      getNorth: () => bounds[3],
    })),
    getSource: vi.fn((id: string) => (id === POG_SOURCE_ID ? {} : undefined)),
    refreshTiles: vi.fn(),
  };
  const fire = (name: string, event?: unknown) =>
    act(() => (handlers.get(name) ?? []).forEach((handler) => handler(event)));
  const move = (next: number[]) => {
    bounds = next;
    fire("moveend");
  };
  return { map, handlers, fire, move };
}

describe("usePogTileActivity", () => {
  it("śledzi ładowanie, wczytane kafle, błędy i 413 wyłącznie dla źródła POG", () => {
    const { map, fire, move } = createMap();
    const { result } = renderHook(() =>
      usePogTileActivity(map as unknown as maplibregl.Map, 42),
    );
    expect(result.current.tiles).toEqual(INITIAL_TILE_ACTIVITY);
    expect(result.current.viewport).toEqual([18.5, 54.4, 18.6, 54.5]);

    fire("sourcedata", { sourceId: "osm", isSourceLoaded: true, tile: {} });
    fire("error", { sourceId: "pog-wms-source", error: { status: 500 } });
    expect(result.current.tiles).toEqual(INITIAL_TILE_ACTIVITY);

    fire("sourcedataloading", { sourceId: POG_SOURCE_ID });
    fire("sourcedata", { sourceId: POG_SOURCE_ID, isSourceLoaded: false, tile: {} });
    expect(result.current.tiles).toMatchObject({ sourceLoaded: false, anyTileLoaded: true });
    fire("sourcedata", { sourceId: POG_SOURCE_ID, isSourceLoaded: true });
    expect(result.current.tiles.sourceLoaded).toBe(true);
    const stable = result.current.tiles;
    fire("sourcedata", { sourceId: POG_SOURCE_ID, isSourceLoaded: true });
    // Brak zmiany → ten sam obiekt (bez zbędnego renderu strony).
    expect(result.current.tiles).toBe(stable);

    fire("error", { sourceId: POG_SOURCE_ID, error: { status: 503 } });
    fire("error", { sourceId: POG_SOURCE_ID, error: { status: 413 } });
    expect(result.current.tiles).toMatchObject({ tileErrors: 2, tileLimitHit: true });

    move([-30, 40, -29, 41]);
    expect(result.current.viewport).toEqual([-30, 40, -29, 41]);

    act(() => result.current.retry());
    expect(result.current.tiles).toMatchObject({ tileErrors: 0, tileLimitHit: false });
    expect(map.refreshTiles).toHaveBeenCalledWith(POG_SOURCE_ID);
  });

  it("resetuje stan dla nowego wydania i odpina zdarzenia", () => {
    const { map, fire, handlers } = createMap();
    const { result, rerender, unmount } = renderHook(
      ({ release }) => usePogTileActivity(map as unknown as maplibregl.Map, release),
      { initialProps: { release: 42 as number | null } },
    );
    fire("error", { sourceId: POG_SOURCE_ID, error: null });
    expect(result.current.tiles.tileErrors).toBe(1);
    rerender({ release: 43 });
    expect(result.current.tiles).toEqual(INITIAL_TILE_ACTIVITY);
    rerender({ release: null });
    expect([...handlers.values()].every((list) => list.length === 0)).toBe(true);
    unmount();
  });

  it("bez mapy nie subskrybuje, a ponowienie bez źródła nie odświeża kafli", () => {
    const { result } = renderHook(() => usePogTileActivity(null, 42));
    act(() => result.current.retry());
    expect(result.current.tiles).toEqual(INITIAL_TILE_ACTIVITY);

    const { map } = createMap();
    map.getSource.mockReturnValue(undefined);
    const withMap = renderHook(() => usePogTileActivity(map as unknown as maplibregl.Map, 42));
    act(() => withMap.result.current.retry());
    expect(map.refreshTiles).not.toHaveBeenCalled();
  });
});
