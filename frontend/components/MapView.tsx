"use client";

import { useEffect, useRef, useState } from "react";
import maplibregl, { type MapMouseEvent } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

import {
  OSM_RASTER_STYLE,
  POLAND_CENTER,
  POLAND_ZOOM,
} from "@/lib/mapStyle";
import {
  POG_INTERACTIVE_LAYERS,
  POG_LAYER_IDS,
  type PogStatusFilter,
  addPogLayers,
  applyPogStatusFilter,
  applyPogTheme,
  pogZoneFromFeatures,
  removePogLayers,
} from "@/lib/pogLayers";
import { DEFAULT_POG_THEME, type PogThemeId } from "@/lib/pogThemes";
import type { PogTileRelease, PogZoneTileProperties } from "@/lib/types";

export type MapViewProps = {
  onMapClick: (lon: number, lat: number) => void;
  /**
   * Wywoływane po pełnym załadowaniu stylu mapy z instancją maplibregl.Map.
   * Komponenty nakładające dodatkowe warstwy (ResultPanel, PreviewOverlays)
   * nie tworzą własnej instancji mapy — otrzymują tę samą instancję przez
   * współdzielony stan w page.tsx, a nie przez bezpośrednią manipulację DOM.
   */
  onMapReady?: (map: maplibregl.Map) => void;
  /**
   * Wydanie POG przypięte na starcie sesji (BK-401). Źródło wektorowe jest
   * tworzone raz dla tego wydania; zmiana trybu nie zmienia jego URL-a.
   */
  pogRelease?: PogTileRelease | null;
  /** Tryb tematyczny POG (BK-402) — przełączany wyłącznie przez `setPaintProperty`. */
  pogTheme?: PogThemeId;
  /** Filtr projekt / akt wiążący na już pobranych kaflach. */
  pogStatusFilter?: PogStatusFilter;
  /** Atrybuty klikniętej strefy z kafla MVT (albo `null` poza strefą). */
  onPogFeatureClick?: (zone: PogZoneTileProperties | null) => void;
};

export function MapView({
  onMapClick,
  onMapReady,
  pogRelease = null,
  pogTheme = DEFAULT_POG_THEME,
  pogStatusFilter = "all",
  onPogFeatureClick,
}: MapViewProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const onMapClickRef = useRef(onMapClick);
  const onMapReadyRef = useRef(onMapReady);
  const onPogFeatureClickRef = useRef(onPogFeatureClick);
  const pogThemeRef = useRef(pogTheme);
  const pogStatusFilterRef = useRef(pogStatusFilter);
  const mapRemovedRef = useRef(false);
  const [readyMap, setReadyMap] = useState<maplibregl.Map | null>(null);

  useEffect(() => {
    onMapClickRef.current = onMapClick;
  }, [onMapClick]);

  useEffect(() => {
    onMapReadyRef.current = onMapReady;
  }, [onMapReady]);

  useEffect(() => {
    onPogFeatureClickRef.current = onPogFeatureClick;
  }, [onPogFeatureClick]);

  useEffect(() => {
    if (!containerRef.current) return;

    mapRemovedRef.current = false;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: OSM_RASTER_STYLE,
      center: POLAND_CENTER,
      zoom: POLAND_ZOOM,
      // Wprost utrwalamy zachowanie ważne dla wolnych WMS-ów: kafle ze
      // wcześniejszego poziomu zoom mają być anulowane, a backend wykrywa
      // rozłączenie i przerywa odpowiadające im zapytanie upstream.
      cancelPendingTileRequestsWhileZooming: true,
    });
    map.addControl(new maplibregl.NavigationControl(), "top-right");

    const handleClick = (event: MapMouseEvent) => {
      const pogCallback = onPogFeatureClickRef.current;
      if (pogCallback && map.getLayer(POG_LAYER_IDS.zonesFill)) {
        const features = map.queryRenderedFeatures(event.point, {
          layers: [...POG_INTERACTIVE_LAYERS],
        });
        pogCallback(pogZoneFromFeatures(features));
      }
      onMapClickRef.current(event.lngLat.lng, event.lngLat.lat);
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

  // Źródło kafli POG powstaje raz na (mapę, wydanie). Po remoncie mapy warstwy
  // są odtwarzane z bieżącym trybem i filtrem — wybór użytkownika nie ginie.
  useEffect(() => {
    if (!readyMap || !pogRelease) return;
    addPogLayers(readyMap, pogRelease, pogThemeRef.current, pogStatusFilterRef.current);
    return () => {
      if (!mapRemovedRef.current) removePogLayers(readyMap);
    };
  }, [readyMap, pogRelease]);

  const hasPogLayers = Boolean(readyMap && pogRelease);

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
