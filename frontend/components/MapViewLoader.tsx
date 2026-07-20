"use client";

import dynamic from "next/dynamic";

import type { MapViewProps } from "@/components/MapView";

const ClientMapView = dynamic(
  () => import("@/components/MapView").then((module) => module.MapView),
  {
    ssr: false,
    loading: () => <div className="map-loading">Ładowanie mapy…</div>,
  },
);

export function MapViewLoader(props: MapViewProps) {
  return <ClientMapView {...props} />;
}
