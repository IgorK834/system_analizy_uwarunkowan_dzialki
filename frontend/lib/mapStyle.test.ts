import { describe, expect, it } from "vitest";

import {
  OSM_RASTER_STYLE,
  POLAND_CENTER,
  POLAND_ZOOM,
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
});
