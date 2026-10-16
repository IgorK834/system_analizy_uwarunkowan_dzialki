import { describe, expect, it } from "vitest";

import {
  MAP_GLYPHS_PATH,
  OSM_RASTER_STYLE,
  POLAND_CENTER,
  POLAND_ZOOM,
  mapStyleWithGlyphs,
} from "@/lib/mapStyle";

describe("styl mapy", () => {
  it("używa kafli OSM z atrybucją i startuje nad Polską", () => {
    expect(POLAND_CENTER).toEqual([19.5, 52.1]);
    expect(POLAND_ZOOM).toBe(6);
    expect(OSM_RASTER_STYLE.sources.osm).toMatchObject({
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: expect.stringContaining("OpenStreetMap"),
    });
    expect(OSM_RASTER_STYLE.layers[0]).toMatchObject({
      id: "osm",
      type: "raster",
      source: "osm",
    });
  });

  it("dodaje glify z własnej aplikacji (bezwzględny adres) albo zwraca styl bazowy bez origin", () => {
    expect(MAP_GLYPHS_PATH).toBe("/map-fonts/{fontstack}/{range}.pbf");
    expect(mapStyleWithGlyphs("https://app.example.pl/").glyphs).toBe(
      "https://app.example.pl/map-fonts/{fontstack}/{range}.pbf",
    );
    expect(mapStyleWithGlyphs(null)).toBe(OSM_RASTER_STYLE);
    expect(OSM_RASTER_STYLE.glyphs).toBeUndefined();
  });
});
