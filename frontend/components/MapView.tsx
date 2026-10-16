"use client";

import { useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import type { MapMouseEvent } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

import { configureMapLibreWorker } from "@/lib/maplibreWorker";
import {
  POLAND_CENTER,
  POLAND_ZOOM,
  mapStyleWithGlyphs,
} from "@/lib/mapStyle";
import {
  type PogStatusFilter,
  addPogLayers,
  applyPogStatusFilter,
  applyPogTheme,
  pogInspectorHits,
  presentInspectLayers,
  removePogLayers,
} from "@/lib/pogLayers";
import { DEFAULT_POG_THEME, type PogThemeId } from "@/lib/pogThemes";
import type { PogPointQuery, PogTileState } from "@/lib/types";

export type MapViewProps = {
  /** Surowe kliknięcie mapy (lon/lat). Nie uruchamia analizy — decyduje rodzic. */
  onMapClick?: (lon: number, lat: number) => void;
  /**
   * Wywoływane po pełnym załadowaniu stylu mapy z instancją maplibregl.Map.
   * Komponenty nakładające dodatkowe warstwy (ResultPanel, PreviewOverlays)
   * nie tworzą własnej instancji mapy — otrzymują tę samą instancję przez
   * współdzielony stan w page.tsx, a nie przez bezpośrednią manipulację DOM.
   */
  onMapReady?: (map: maplibregl.Map) => void;
  /**
   * Stan krajowy POG przypięty na starcie sesji (BK-401, ADR-026). Źródła wektorowe (obowiązujące i projekty) są
   * tworzone raz dla tego stanu; zmiana trybu ani filtra nie zmienia ich URL-i.
   */
  pogState?: PogTileState | null;
  /** Tryb tematyczny POG (BK-402) — przełączany wyłącznie przez `setPaintProperty`. */
  pogTheme?: PogThemeId;
  /** Filtr projekt / akt wiążący na już pobranych kaflach. */
  pogStatusFilter?: PogStatusFilter;
  /**
   * BK-404: wszystkie obiekty POG wyrenderowane w klikniętym punkcie (strefy i
   * OUZ/OZS/OSDIS) po deduplikacji cech z sąsiednich kafli. Wywoływane przy
   * każdym kliknięciu — także bez warstw POG (`queried = false`).
   */
  onPogInspect?: (query: PogPointQuery) => void;
};

export function MapView({
  onMapClick,
  onMapReady,
  pogState = null,
  pogTheme = DEFAULT_POG_THEME,
  pogStatusFilter = "all",
  onPogInspect,
}: MapViewProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const onMapClickRef = useRef(onMapClick);
  const onMapReadyRef = useRef(onMapReady);
  const onPogInspectRef = useRef(onPogInspect);
  const pogThemeRef = useRef(pogTheme);
  const pogStatusFilterRef = useRef(pogStatusFilter);
  const pogStateIdRef = useRef<number | null>(pogState?.state_id ?? null);
  const mapRemovedRef = useRef(false);
  const [readyMap, setReadyMap] = useState<maplibregl.Map | null>(null);

  useEffect(() => {
    onMapClickRef.current = onMapClick;
  }, [onMapClick]);

  useEffect(() => {
    onMapReadyRef.current = onMapReady;
  }, [onMapReady]);

  useEffect(() => {
    onPogInspectRef.current = onPogInspect;
  }, [onPogInspect]);

  useEffect(() => {
    if (!containerRef.current) return;

    mapRemovedRef.current = false;
    configureMapLibreWorker();
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: mapStyleWithGlyphs(window.location.origin),
      center: POLAND_CENTER,
      zoom: POLAND_ZOOM,
      // Wprost utrwalamy zachowanie ważne dla wolnych WMS-ów: kafle ze
      // wcześniejszego poziomu zoom mają być anulowane, a backend wykrywa
      // rozłączenie i przerywa odpowiadające im zapytanie upstream.
      cancelPendingTileRequestsWhileZooming: true,
    });
    map.addControl(new maplibregl.NavigationControl(), "top-right");

    const handleClick = (event: MapMouseEvent) => {
      const { lng: lon, lat } = event.lngLat;
      const inspect = onPogInspectRef.current;
      if (inspect) {
        // Tylko warstwy obiektów BK-401 obecne na mapie; brak warstw to „nie
        // sprawdzono”, a nie „brak obiektu”.
        const layers = presentInspectLayers(map);
        const features =
          layers.length > 0 ? map.queryRenderedFeatures(event.point, { layers }) : [];
        inspect({
          lon,
          lat,
          hits: pogInspectorHits(features),
          queried: layers.length > 0,
          stateId: pogStateIdRef.current,
          zoom: map.getZoom(),
        });
      }
      onMapClickRef.current?.(lon, lat);
    };
    const handleLoad = () => {
      setReadyMap(map);
      onMapReadyRef.current?.(map);
    };

    map.on("click", handleClick);
    map.on("load", handleLoad);

    return () => {
      map.off("click", handleClick);
      map.off("load", handleLoad);
      mapRemovedRef.current = true;
      setReadyMap(null);
      map.remove();
    };
  }, []);

  // Źródła kafli POG powstają raz na (mapę, stan krajowy). Po remoncie mapy warstwy
  // są odtwarzane z bieżącym trybem i filtrem — wybór użytkownika nie ginie.
  useEffect(() => {
    pogStateIdRef.current = pogState?.state_id ?? null;
    if (!readyMap || !pogState) return;
    addPogLayers(readyMap, pogState, pogThemeRef.current, pogStatusFilterRef.current);
    return () => {
      if (!mapRemovedRef.current) removePogLayers(readyMap);
    };
  }, [readyMap, pogState]);

  const hasPogLayers = Boolean(readyMap && pogState);

  useEffect(() => {
    pogThemeRef.current = pogTheme;
    if (readyMap && hasPogLayers) applyPogTheme(readyMap, pogTheme);
  }, [readyMap, hasPogLayers, pogTheme]);

  useEffect(() => {
    pogStatusFilterRef.current = pogStatusFilter;
    if (readyMap && hasPogLayers) applyPogStatusFilter(readyMap, pogStatusFilter);
  }, [readyMap, hasPogLayers, pogStatusFilter]);

  return (
    <div
      ref={containerRef}
      className="map-canvas"
      aria-label="Interaktywna mapa Polski"
    />
  );
}
