"use client";

import { useEffect, useRef } from "react";
import maplibregl, { type MapMouseEvent } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

import {
  OSM_RASTER_STYLE,
  POLAND_CENTER,
  POLAND_ZOOM,
} from "@/lib/mapStyle";

export type MapViewProps = {
  onMapClick: (lon: number, lat: number) => void;
  /**
   * Wywoływane po pełnym załadowaniu stylu mapy z instancją maplibregl.Map.
   * Komponenty nakładające dodatkowe warstwy (ResultPanel, PlanningOverlay)
   * nie tworzą własnej instancji mapy — otrzymują tę samą instancję przez
   * współdzielony stan w page.tsx, a nie przez bezpośrednią manipulację DOM.
   */
  onMapReady?: (map: maplibregl.Map) => void;
};

export function MapView({ onMapClick, onMapReady }: MapViewProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const onMapClickRef = useRef(onMapClick);
  const onMapReadyRef = useRef(onMapReady);

  useEffect(() => {
    onMapClickRef.current = onMapClick;
  }, [onMapClick]);

  useEffect(() => {
    onMapReadyRef.current = onMapReady;
  }, [onMapReady]);

  useEffect(() => {
    if (!containerRef.current) return;

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
      onMapClickRef.current(event.lngLat.lng, event.lngLat.lat);
    };
    const handleLoad = () => {
      onMapReadyRef.current?.(map);
    };

    map.on("click", handleClick);
    map.on("load", handleLoad);

    return () => {
      map.off("click", handleClick);
      map.off("load", handleLoad);
      map.remove();
    };
  }, []);

  return (
    <div
      ref={containerRef}
      className="map-canvas"
      aria-label="Interaktywna mapa Polski"
    />
  );
}
