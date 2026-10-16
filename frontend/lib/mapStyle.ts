import type { StyleSpecification } from "maplibre-gl";

export const POLAND_CENTER: [number, number] = [19.5, 52.1];
export const POLAND_ZOOM = 6;

export const OSM_RASTER_STYLE: StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    },
  },
  layers: [
    {
      id: "osm",
      type: "raster",
      source: "osm",
    },
  ],
};

/** Ścieżka glifów serwowanych lokalnie z `public/map-fonts` (etykiety stref POG od z14). */
export const MAP_GLYPHS_PATH = "/map-fonts/{fontstack}/{range}.pbf";

/**
 * Styl bazowy z glifami hostowanymi przez własną aplikację. MapLibre pobiera glify w workerze, więc adres musi być
 * bezwzględny — budujemy go z origin strony; bez origin (SSR/testy) zwracany jest styl bez glifów.
 */
export function mapStyleWithGlyphs(origin: string | null | undefined): StyleSpecification {
  if (!origin) return OSM_RASTER_STYLE;
  return { ...OSM_RASTER_STYLE, glyphs: `${origin.replace(/\/$/, "")}${MAP_GLYPHS_PATH}` };
}
