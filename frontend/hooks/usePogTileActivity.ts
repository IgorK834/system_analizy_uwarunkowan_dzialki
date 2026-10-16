"use client";

import { useCallback, useEffect, useState } from "react";
import type * as maplibregl from "maplibre-gl";

import { POG_SOURCE_IDS } from "@/lib/pogLayers";
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

const isPogSource = (sourceId: string | undefined): boolean =>
  sourceId !== undefined && POG_SOURCE_IDS.includes(sourceId);

/**
 * Aktywność źródeł kafli POG (obowiązujące i projekty) ze zdarzeń MapLibre (BK-406): ładowanie, wczytane kafle,
 * błędy (także 413 — limit kafla) i okno mapy do oceny pokrycia metadanymi stanu. Stan jest resetowany dla każdego
 * stanu krajowego.
 *
 * `retry` zeruje liczniki błędów i ponownie pobiera kafle w widoku przez `refreshTiles` — już wyświetlone dane
 * pozostają na mapie do czasu nadejścia nowych, więc ponowienie nie usuwa ostatnich danych.
 */
export function usePogTileActivity(map: maplibregl.Map | null, stateId: number | null) {
  const [tiles, setTiles] = useState<PogTileActivity>(INITIAL_TILE_ACTIVITY);
  const [viewport, setViewport] = useState<LonLatBounds | null>(null);

  useEffect(() => {
    setTiles(INITIAL_TILE_ACTIVITY);
    if (!map || stateId === null) return;

    const update = (next: (current: PogTileActivity) => PogTileActivity) =>
      setTiles((current) => {
        const value = next(current);
        return sameActivity(current, value) ? current : value;
      });
    // Źródło nieużywane przez widoczną warstwę (np. projekty przy filtrze „obowiązujące”) nie wysyła zdarzeń,
    // więc „wczytane” oznacza: wszystkie źródła, które zgłosiły aktywność, nie mają oczekujących żądań.
    const loaded = new Map<string, boolean>();
    const allLoaded = () => [...loaded.values()].every(Boolean);
    const onLoading = (event: SourceEvent) => {
      if (!isPogSource(event.sourceId)) return;
      loaded.set(String(event.sourceId), false);
      update((current) => ({ ...current, sourceLoaded: false }));
    };
    const onData = (event: SourceEvent) => {
      if (!isPogSource(event.sourceId)) return;
      loaded.set(String(event.sourceId), event.isSourceLoaded ?? loaded.get(String(event.sourceId)) ?? false);
      update((current) => ({
        ...current,
        sourceLoaded: allLoaded(),
        anyTileLoaded: current.anyTileLoaded || Boolean(event.tile),
      }));
    };
    const onError = (event: SourceErrorEvent) => {
      if (!isPogSource(event.sourceId)) return;
      if (event.isSourceLoaded !== undefined) loaded.set(String(event.sourceId), event.isSourceLoaded);
      update((current) => ({
        ...current,
        sourceLoaded: loaded.size > 0 ? allLoaded() : current.sourceLoaded,
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
  }, [map, stateId]);

  const retry = useCallback(() => {
    setTiles((current) => ({ ...current, tileErrors: 0, tileLimitHit: false }));
    if (!map) return;
    for (const sourceId of POG_SOURCE_IDS) {
      if (map.getSource(sourceId)) map.refreshTiles(sourceId);
    }
  }, [map]);

  return { tiles, viewport, retry };
}
