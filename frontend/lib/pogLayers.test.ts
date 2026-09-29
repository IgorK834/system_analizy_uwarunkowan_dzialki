import { beforeEach, describe, expect, it, vi } from "vitest";
import type maplibregl from "maplibre-gl";

import {
  POG_LAYER_IDS,
  POG_LAYER_ORDER,
  POG_SOURCE_ID,
  addPogLayers,
  applyPogStatusFilter,
  applyPogTheme,
  layerFilter,
  overlayImageId,
  pogTileUrl,
  pogZoneFromFeatures,
  removePogLayers,
} from "@/lib/pogLayers";
import { buildPatternImage, patternCovers, PATTERN_ALPHA, PATTERN_SIZE } from "@/lib/pogPatterns";
import { themeById, themeFillColorExpression } from "@/lib/pogThemes";
import { POG_NULL_STYLE } from "@/lib/pogZones";
import { buildPogRelease, createPogMapMock } from "@/test/pogFixtures";

describe("pogLayers", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://api.example.test/");
  });

  it("dodaje jedno źródło wektorowe z URL-em przypiętym do wydania i wszystkie warstwy", () => {
    const { map, sources, layers } = createPogMapMock();
    const release = buildPogRelease();
    addPogLayers(map as unknown as maplibregl.Map, release, "zones", "all");

    expect(pogTileUrl(release)).toBe(
      "http://api.example.test/api/v1/map/pog/releases/42/{z}/{x}/{y}.mvt",
    );
    expect(sources.get(POG_SOURCE_ID)).toMatchObject({
      type: "vector",
      tiles: [pogTileUrl(release)],
      minzoom: 0,
      maxzoom: 18,
      bounds: release.bounds,
    });
    expect([...layers.keys()]).toEqual([...POG_LAYER_ORDER]);
    expect(layers.get(POG_LAYER_IDS.zonesFill)).toMatchObject({
      "source-layer": "zones",
      paint: { "fill-color": themeFillColorExpression(themeById("zones")) },
    });
    expect(layers.get(POG_LAYER_IDS.zonesOutlineNonBinding)).toMatchObject({
      filter: ["!=", ["get", "legal_status"], "binding"],
    });
    expect(layers.get(POG_LAYER_IDS.ouzPattern)).toMatchObject({
      "source-layer": "ouz",
      paint: { "fill-pattern": overlayImageId("ouz") },
    });

    addPogLayers(map as unknown as maplibregl.Map, release, "height", "binding");
    expect(map.addSource).toHaveBeenCalledOnce();
    expect(map.addImage).toHaveBeenCalledTimes(5);
  });

  it("bez zasięgu wydania nie ustawia bounds", () => {
    const { map, sources } = createPogMapMock();
    addPogLayers(map as unknown as maplibregl.Map, buildPogRelease({ bounds: null }), "zones", "all");
    expect(sources.get(POG_SOURCE_ID)).not.toHaveProperty("bounds");
  });

  it("zmiana trybu to wyłącznie setPaintProperty", () => {
    const { map } = createPogMapMock();
    applyPogTheme(map as unknown as maplibregl.Map, "height");
    expect(map.setPaintProperty).not.toHaveBeenCalled();

    addPogLayers(map as unknown as maplibregl.Map, buildPogRelease(), "zones", "all");
    map.addSource.mockClear();
    applyPogTheme(map as unknown as maplibregl.Map, "height");
    expect(map.setPaintProperty).toHaveBeenCalledWith(
      POG_LAYER_IDS.zonesFill,
      "fill-color",
      themeFillColorExpression(themeById("height")),
    );
    expect(map.setPaintProperty).toHaveBeenCalledWith(
      POG_LAYER_IDS.zonesPattern,
      "fill-pattern",
      "pog-pattern-diagonal-hatch",
    );
    expect(map.addSource).not.toHaveBeenCalled();
    expect(map.removeSource).not.toHaveBeenCalled();
  });

  it("filtr statusu łączy się ze stałym filtrem obrysów", () => {
    expect(layerFilter(POG_LAYER_IDS.zonesFill, "all")).toBeNull();
    expect(layerFilter(POG_LAYER_IDS.zonesFill, "binding")).toEqual([
      "==",
      ["get", "legal_status"],
      "binding",
    ]);
    expect(layerFilter(POG_LAYER_IDS.zonesFill, "non_binding")).toEqual([
      "in",
      ["get", "legal_status"],
      ["literal", ["project", "in_progress"]],
    ]);
    expect(layerFilter(POG_LAYER_IDS.zonesOutlineBinding, "non_binding")).toEqual([
      "all",
      ["==", ["get", "legal_status"], "binding"],
      ["in", ["get", "legal_status"], ["literal", ["project", "in_progress"]]],
    ]);

    const { map } = createPogMapMock();
    applyPogStatusFilter(map as unknown as maplibregl.Map, "binding");
    expect(map.setFilter).not.toHaveBeenCalled();
    addPogLayers(map as unknown as maplibregl.Map, buildPogRelease(), "zones", "all");
    applyPogStatusFilter(map as unknown as maplibregl.Map, "binding");
    expect(map.setFilter).toHaveBeenCalledTimes(POG_LAYER_ORDER.length);
  });

  it("usuwa warstwy i źródło oraz czyta atrybuty klikniętej strefy", () => {
    const { map, layers, sources } = createPogMapMock();
    addPogLayers(map as unknown as maplibregl.Map, buildPogRelease(), "zones", "all");
    removePogLayers(map as unknown as maplibregl.Map);
    expect(layers.size).toBe(0);
    expect(sources.size).toBe(0);
    removePogLayers(map as unknown as maplibregl.Map);

    expect(pogZoneFromFeatures([])).toBeNull();
    expect(
      pogZoneFromFeatures([
        { layer: { id: "other" }, properties: { zone_code: "SW" } },
        { layer: { id: POG_LAYER_IDS.zonesFill }, properties: { zone_code: "SU" } },
      ]),
    ).toEqual({ zone_code: "SU" });
  });
});

describe("pogPatterns", () => {
  it("generuje odrębne wzory w kolorze obrysu", () => {
    const patterns = ["diagonal-hatch", "cross-hatch", "cross-lines", "dots"] as const;
    const signatures = patterns.map((pattern) =>
      Array.from({ length: PATTERN_SIZE * PATTERN_SIZE }, (_, index) =>
        patternCovers(pattern, index % PATTERN_SIZE, Math.floor(index / PATTERN_SIZE)) ? 1 : 0,
      ).join(""),
    );
    expect(new Set(signatures).size).toBe(patterns.length);
    expect(patternCovers("nieznany" as never, 0, 0)).toBe(false);

    const image = buildPatternImage("diagonal-hatch", POG_NULL_STYLE.outline);
    expect(image.width).toBe(PATTERN_SIZE);
    expect(image.data).toHaveLength(PATTERN_SIZE * PATTERN_SIZE * 4);
    expect(Array.from(image.data.slice(0, 4))).toEqual([0x7a, 0x7a, 0x7a, PATTERN_ALPHA]);
    expect(image.data[7]).toBe(0);
  });
});
