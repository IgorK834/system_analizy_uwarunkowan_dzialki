import { beforeEach, describe, expect, it, vi } from "vitest";
import type * as maplibregl from "maplibre-gl";

import {
  POG_ACT_FILL_MAX_ZOOM,
  POG_LABEL_FONT,
  POG_LAYER_IDS,
  POG_LAYER_ORDER,
  POG_OVERLAY_LABEL_MIN_ZOOM,
  POG_PROJECT_SOURCE_ID,
  POG_SOURCE_ID,
  POG_ZONE_LABEL_MIN_ZOOM,
  actStatusFillColorExpression,
  addPogLayers,
  applyPogStatusFilter,
  applyPogTheme,
  layerFilter,
  overlayImageId,
  parsePogLayerId,
  pogInspectorHits,
  pogLayerId,
  pogTileUrl,
  presentInspectLayers,
  removePogLayers,
  visibleEditions,
} from "@/lib/pogLayers";
import { buildPatternImage, patternCovers, PATTERN_ALPHA, PATTERN_SIZE } from "@/lib/pogPatterns";
import { legalStatusPatternImage, themeById, themeFillColorExpression } from "@/lib/pogThemes";
import { POG_NULL_STYLE } from "@/lib/pogZones";
import {
  buildPogOverlayProperties,
  buildPogState,
  buildPogZoneProperties,
  createPogMapMock,
} from "@/test/pogFixtures";

const BINDING = (base: string) => pogLayerId(base, "binding");
const PROJECT = (base: string) => pogLayerId(base, "project");

type Layer = Record<string, unknown> & { layout?: Record<string, unknown>; paint?: Record<string, unknown> };

describe("pogLayers", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://api.example.test/");
  });

  it("dodaje dwa źródła wektorowe (obowiązujące i projekty) przypięte do stanu krajowego i wszystkie warstwy", () => {
    const { map, sources, layers } = createPogMapMock();
    const state = buildPogState();
    addPogLayers(map as unknown as maplibregl.Map, state, "zones", "all");

    expect(pogTileUrl(state)).toBe(
      "http://api.example.test/api/v1/map/pog/states/42/{z}/{x}/{y}.mvt?edition=binding",
    );
    expect(pogTileUrl(state, "project")).toBe(
      "http://api.example.test/api/v1/map/pog/states/42/{z}/{x}/{y}.mvt?edition=project",
    );
    expect(sources.get(POG_SOURCE_ID)).toMatchObject({
      type: "vector",
      tiles: [pogTileUrl(state, "binding")],
      minzoom: 0,
      maxzoom: 18,
      bounds: state.bounds,
    });
    expect(sources.get(POG_PROJECT_SOURCE_ID)).toMatchObject({ tiles: [pogTileUrl(state, "project")] });
    expect([...layers.keys()]).toEqual([...POG_LAYER_ORDER]);
    expect(layers.get(BINDING(POG_LAYER_IDS.zonesFill))).toMatchObject({
      source: POG_SOURCE_ID,
      "source-layer": "zones",
      paint: { "fill-color": themeFillColorExpression(themeById("zones")) },
    });
    expect(layers.get(PROJECT(POG_LAYER_IDS.zonesFill))).toMatchObject({
      source: POG_PROJECT_SOURCE_ID,
      "source-layer": "zones",
    });
    expect(layers.get(BINDING(POG_LAYER_IDS.zonesOutlineNonBinding))).toMatchObject({
      filter: ["!=", ["get", "legal_status"], "binding"],
    });
    expect(layers.get(BINDING(POG_LAYER_IDS.ouzPattern))).toMatchObject({
      "source-layer": "ouz",
      paint: { "fill-pattern": overlayImageId("ouz") },
    });
    // BK-406: projekt ma własny wzór (poza kryciem i obrysem przerywanym).
    expect(layers.get(BINDING(POG_LAYER_IDS.zonesStatusPattern))).toMatchObject({
      "source-layer": "zones",
      paint: { "fill-pattern": legalStatusPatternImage() },
      filter: ["in", ["get", "legal_status"], ["literal", ["project", "in_progress"]]],
    });
    expect(legalStatusPatternImage()).toBe("pog-pattern-horizontal-lines");

    addPogLayers(map as unknown as maplibregl.Map, state, "height", "binding");
    expect(map.addSource).toHaveBeenCalledTimes(2);
    expect(map.addImage).toHaveBeenCalledTimes(6);
  });

  it("progi zoomu pochodzą ze stanu: przegląd krajowy z0–13, strefy z11, OUZ/OZS/OSDIS z12, etykiety z14/z16", () => {
    const { map, layers } = createPogMapMock();
    addPogLayers(map as unknown as maplibregl.Map, buildPogState(), "zones", "all");
    const layer = (base: string) => layers.get(BINDING(base)) as Layer;

    expect(layer(POG_LAYER_IDS.actFill)).toMatchObject({ "source-layer": "act_boundary", maxzoom: POG_ACT_FILL_MAX_ZOOM });
    expect(layer(POG_LAYER_IDS.actFill).minzoom).toBeUndefined();
    expect(layer(POG_LAYER_IDS.zonesFill).minzoom).toBe(11);
    expect(layer(POG_LAYER_IDS.ouzPattern).minzoom).toBe(12);
    expect(layer(POG_LAYER_IDS.zoneLabels).minzoom).toBe(POG_ZONE_LABEL_MIN_ZOOM);
    expect(POG_ZONE_LABEL_MIN_ZOOM).toBe(14);
    expect(layer(POG_LAYER_IDS.ouzLabels).minzoom).toBe(POG_OVERLAY_LABEL_MIN_ZOOM);
    expect(POG_OVERLAY_LABEL_MIN_ZOOM).toBe(16);
    expect(layer(POG_LAYER_IDS.zoneLabels).layout).toMatchObject({ "text-font": [POG_LABEL_FONT] });

    // Zmiana progów po stronie serwera przesuwa warstwy bez zmiany kodu klienta.
    const other = createPogMapMock();
    addPogLayers(
      other.map as unknown as maplibregl.Map,
      buildPogState({
        layer_min_zoom: { zones: 9, ouz: 10, downtown: 10, social_infrastructure_standard: 10, act_boundary: 0 },
      }),
      "zones",
      "all",
    );
    expect((other.layers.get(BINDING(POG_LAYER_IDS.zonesFill)) as Layer).minzoom).toBe(9);
    expect((other.layers.get(BINDING(POG_LAYER_IDS.ouzPattern)) as Layer).minzoom).toBe(10);
  });

  it("wypełnienie przeglądu ma kolor statusu prawnego i każdy status ma inny kolor", () => {
    const expression = actStatusFillColorExpression() as unknown[];
    expect(expression.slice(0, 2)).toEqual(["match", ["get", "legal_status"]]);
    const labels = expression.slice(2, -1).filter((_, index) => index % 2 === 0);
    const colors = expression.slice(2, -1).filter((_, index) => index % 2 === 1);
    expect(labels).toEqual(expect.arrayContaining(["binding", "project", "in_progress"]));
    expect(new Set(colors).size).toBe(colors.length);
    expect(typeof expression.at(-1)).toBe("string");
  });

  it("bez zasięgu stanu nie ustawia bounds", () => {
    const { map, sources } = createPogMapMock();
    addPogLayers(map as unknown as maplibregl.Map, buildPogState({ bounds: null }), "zones", "all");
    expect(sources.get(POG_SOURCE_ID)).not.toHaveProperty("bounds");
  });

  it("zmiana trybu to wyłącznie setPaintProperty na obu edycjach", () => {
    const { map } = createPogMapMock();
    applyPogTheme(map as unknown as maplibregl.Map, "height");
    expect(map.setPaintProperty).not.toHaveBeenCalled();

    addPogLayers(map as unknown as maplibregl.Map, buildPogState(), "zones", "all");
    map.addSource.mockClear();
    applyPogTheme(map as unknown as maplibregl.Map, "height");
    for (const id of [BINDING, PROJECT]) {
      expect(map.setPaintProperty).toHaveBeenCalledWith(
        id(POG_LAYER_IDS.zonesFill),
        "fill-color",
        themeFillColorExpression(themeById("height")),
      );
      expect(map.setPaintProperty).toHaveBeenCalledWith(
        id(POG_LAYER_IDS.zonesPattern),
        "fill-pattern",
        "pog-pattern-diagonal-hatch",
      );
    }
    expect(map.addSource).not.toHaveBeenCalled();
    expect(map.removeSource).not.toHaveBeenCalled();
  });

  it("przełącznik obowiązujące / projekty / oba zmienia tylko widoczność warstw źródła", () => {
    expect(visibleEditions("all")).toEqual(["binding", "project"]);
    expect(visibleEditions("binding")).toEqual(["binding"]);
    expect(visibleEditions("non_binding")).toEqual(["project"]);
    expect(parsePogLayerId(PROJECT(POG_LAYER_IDS.zonesFill))).toEqual({
      base: POG_LAYER_IDS.zonesFill,
      edition: "project",
    });
    expect(parsePogLayerId(POG_LAYER_IDS.zonesFill)).toEqual({ base: POG_LAYER_IDS.zonesFill, edition: "binding" });

    const { map, layers } = createPogMapMock();
    applyPogStatusFilter(map as unknown as maplibregl.Map, "binding");
    expect(map.setLayoutProperty).not.toHaveBeenCalled();

    addPogLayers(map as unknown as maplibregl.Map, buildPogState(), "zones", "binding");
    const visibility = (id: string) => (layers.get(id) as Layer).layout?.visibility;
    expect(visibility(BINDING(POG_LAYER_IDS.zonesFill))).toBe("visible");
    expect(visibility(PROJECT(POG_LAYER_IDS.zonesFill))).toBe("none");

    map.addSource.mockClear();
    applyPogStatusFilter(map as unknown as maplibregl.Map, "non_binding");
    expect(map.setLayoutProperty).toHaveBeenCalledWith(BINDING(POG_LAYER_IDS.zonesFill), "visibility", "none");
    expect(map.setLayoutProperty).toHaveBeenCalledWith(PROJECT(POG_LAYER_IDS.zonesFill), "visibility", "visible");
    expect(map.setLayoutProperty).toHaveBeenCalledTimes(POG_LAYER_ORDER.length);
    expect(map.addSource).not.toHaveBeenCalled();
    expect(map.setFilter).not.toHaveBeenCalled();
  });

  it("filtr statusu łączy się ze stałym filtrem obrysów (wyrażenia warstw)", () => {
    expect(layerFilter(POG_LAYER_IDS.zonesFill, "all")).toBeNull();
    expect(layerFilter(POG_LAYER_IDS.zonesFill, "binding")).toEqual(["==", ["get", "legal_status"], "binding"]);
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
    expect(layerFilter(PROJECT(POG_LAYER_IDS.zonesOutlineBinding), "all")).toEqual([
      "==",
      ["get", "legal_status"],
      "binding",
    ]);
  });

  it("usuwa warstwy i źródła obu edycji", () => {
    const { map, layers, sources } = createPogMapMock();
    addPogLayers(map as unknown as maplibregl.Map, buildPogState(), "zones", "all");
    expect(presentInspectLayers(map as unknown as maplibregl.Map)).toEqual(
      expect.arrayContaining([
        BINDING(POG_LAYER_IDS.zonesFill),
        BINDING(POG_LAYER_IDS.ouzPattern),
        BINDING(POG_LAYER_IDS.downtownPattern),
        BINDING(POG_LAYER_IDS.socialPattern),
        BINDING(POG_LAYER_IDS.actFill),
        PROJECT(POG_LAYER_IDS.zonesFill),
      ]),
    );
    removePogLayers(map as unknown as maplibregl.Map);
    expect(layers.size).toBe(0);
    expect(sources.size).toBe(0);
    expect(presentInspectLayers(map as unknown as maplibregl.Map)).toEqual([]);
    removePogLayers(map as unknown as maplibregl.Map);
  });

  it("BK-404: trafienia inspektora bez duplikatów z sąsiednich kafli, strefy przed nakładkami, granica aktu na końcu", () => {
    const su = buildPogZoneProperties();
    const sj = buildPogZoneProperties({ feature_id: "PL/2POG-1SJ", zone_code: "SJ", legal_status: "project" });
    const ouz = buildPogOverlayProperties();
    const act = { act_id: su.act_id, legal_status: "binding", label: "Plan ogólny" };
    const hits = pogInspectorHits([
      { id: 7, layer: { id: BINDING(POG_LAYER_IDS.actFill) }, properties: act },
      { id: 201, layer: { id: BINDING(POG_LAYER_IDS.ouzPattern) }, properties: ouz },
      { id: 101, layer: { id: BINDING(POG_LAYER_IDS.zonesFill) }, properties: su },
      // Ta sama cecha z sąsiedniego kafla (ten sam identyfikator MVT).
      { id: 101, layer: { id: BINDING(POG_LAYER_IDS.zonesFill) }, properties: su },
      { id: 102, layer: { id: PROJECT(POG_LAYER_IDS.zonesFill) }, properties: sj },
      // Warstwy spoza inspektora i cechy bez atrybutów są pomijane.
      { id: 101, layer: { id: BINDING(POG_LAYER_IDS.zonesOutlineBinding) }, properties: su },
      { id: 999, layer: { id: "osm-tiles" }, properties: { name: "x" } },
      { id: 5, layer: { id: BINDING(POG_LAYER_IDS.downtownPattern) }, properties: null },
      // Bez identyfikatora MVT deduplikacja używa feature_id.
      { layer: { id: BINDING(POG_LAYER_IDS.socialPattern) }, properties: { ...ouz, feature_id: "OSDIS-1" } },
      { layer: { id: BINDING(POG_LAYER_IDS.socialPattern) }, properties: { ...ouz, feature_id: "OSDIS-1" } },
    ]);
    expect(hits.map((hit) => [hit.layer, hit.featurePk, hit.key])).toEqual([
      ["zones", 101, "zones:101"],
      ["zones", 102, "zones:102"],
      ["ouz", 201, "ouz:201"],
      ["social_infrastructure_standard", null, "social_infrastructure_standard:OSDIS-1"],
      ["act_boundary", 7, "act_boundary:7"],
    ]);
    expect(pogInspectorHits([])).toEqual([]);
  });
});

describe("pogPatterns", () => {
  it("generuje odrębne wzory w kolorze obrysu", () => {
    const patterns = ["diagonal-hatch", "cross-hatch", "cross-lines", "dots", "horizontal-lines"] as const;
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
