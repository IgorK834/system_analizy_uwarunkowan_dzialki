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
};

export function MapView({ onMapClick }: MapViewProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const onMapClickRef = useRef(onMapClick);

  useEffect(() => {
    onMapClickRef.current = onMapClick;
  }, [onMapClick]);

  useEffect(() => {
    if (!containerRef.current) return;

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: OSM_RASTER_STYLE,
      center: POLAND_CENTER,
      zoom: POLAND_ZOOM,
    });
    map.addControl(new maplibregl.NavigationControl(), "top-right");

    const handleClick = (event: MapMouseEvent) => {
      onMapClickRef.current(event.lngLat.lng, event.lngLat.lat);
    };

    map.on("click", handleClick);

    return () => {
      map.off("click", handleClick);
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
