"use client";

import { useCallback, useEffect, useState } from "react";
import type * as maplibregl from "maplibre-gl";

import { POG_SOURCE_ID } from "@/lib/pogLayers";
import {
  INITIAL_TILE_ACTIVITY,
  type LonLatBounds,
  type PogTileActivity,
} from "@/lib/pogLayerState";

type SourceEvent = {
  sourceId?: string;
  isSourceLoaded?: boolean;
  tile?: unknown;
  error?: { message?: string; status?: number } | null;
};

/** Zdarzenie `error` MapLibre 6: `ErrorEvent` z identyfikatorem źródła dopisywanym przy błędach kafli. */
type SourceErrorEvent = maplibregl.ErrorEvent & SourceEvent & { error: { message: string; status?: number } };

function viewportOf(map: maplibregl.Map): LonLatBounds {
  const bounds = map.getBounds();
  return [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()];
}

function sameActivity(a: PogTileActivity, b: PogTileActivity): boolean {
  return (
    a.sourceLoaded === b.sourceLoaded &&
    a.anyTileLoaded === b.anyTileLoaded &&
    a.tileErrors === b.tileErrors &&
    a.tileLimitHit === b.tileLimitHit
  );
}

/**
 * Aktywność źródła kafli POG ze zdarzeń MapLibre (BK-406): ładowanie,
 * wczytane kafle, błędy (także 413 — limit kafla) i okno mapy do oceny
 * pokrycia metadanymi wydania. Stan jest resetowany dla każdego wydania.
 *
 * `retry` zeruje liczniki błędów i ponownie pobiera kafle w widoku przez
 * `refreshTiles` — już wyświetlone dane pozostają na mapie do czasu nadejścia
 * nowych, więc ponowienie nie usuwa ostatnich danych.
 */
export function usePogTileActivity(map: maplibregl.Map | null, releaseId: number | null) {
  const [tiles, setTiles] = useState<PogTileActivity>(INITIAL_TILE_ACTIVITY);
  const [viewport, setViewport] = useState<LonLatBounds | null>(null);

  useEffect(() => {
    setTiles(INITIAL_TILE_ACTIVITY);
    if (!map || releaseId === null) return;

    const update = (next: (current: PogTileActivity) => PogTileActivity) =>
      setTiles((current) => {
        const value = next(current);
        return sameActivity(current, value) ? current : value;
      });
    const onLoading = (event: SourceEvent) => {
      if (event.sourceId !== POG_SOURCE_ID) return;
      update((current) => ({ ...current, sourceLoaded: false }));
    };
    const onData = (event: SourceEvent) => {
      if (event.sourceId !== POG_SOURCE_ID) return;
      update((current) => ({
        ...current,
        sourceLoaded: event.isSourceLoaded ?? current.sourceLoaded,
        anyTileLoaded: current.anyTileLoaded || Boolean(event.tile),
      }));
    };
    const onError = (event: SourceErrorEvent) => {
      if (event.sourceId !== POG_SOURCE_ID) return;
      update((current) => ({
        ...current,
        sourceLoaded: event.isSourceLoaded ?? current.sourceLoaded,
        tileErrors: current.tileErrors + 1,
        tileLimitHit: current.tileLimitHit || event.error?.status === 413,
      }));
    };
    const onMove = () => setViewport(viewportOf(map));

    onMove();
    map.on("sourcedataloading", onLoading);
    map.on("sourcedata", onData);
    map.on("error", onError);
    map.on("moveend", onMove);
    return () => {
      map.off("sourcedataloading", onLoading);
      map.off("sourcedata", onData);
      map.off("error", onError);
      map.off("moveend", onMove);
    };
  }, [map, releaseId]);

  const retry = useCallback(() => {
    setTiles((current) => ({ ...current, tileErrors: 0, tileLimitHit: false }));
    if (map?.getSource(POG_SOURCE_ID)) map.refreshTiles(POG_SOURCE_ID);
  }, [map]);

  return { tiles, viewport, retry };
}
