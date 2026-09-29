/**
 * Warstwy MapLibre wektorowych kafli POG (BK-401/402/403).
 *
 * Źródło wektorowe jest dodawane raz, z URL-em przypiętym do `release_id`
 * wydania pobranego na starcie sesji mapy. Zmiana trybu tematycznego to wyłącznie
 * `setPaintProperty` (bez `addSource`, `setTiles`, `setData` ani żądań sieciowych),
 * a filtr statusu prawnego to `setFilter` na tych samych, już pobranych kaflach.
 */
import type maplibregl from "maplibre-gl";

import { getApiResourceUrl } from "@/lib/config";
import { buildPatternImage } from "@/lib/pogPatterns";
import {
  type PogThemeId,
  legalStatusPatternFilter,
  legalStatusPatternImage,
  patternImageId,
  themeById,
  themeFillColorExpression,
  themePatternImage,
  themePatternOpacityExpression,
} from "@/lib/pogThemes";
import {
  POG_NULL_STYLE,
  POG_UNKNOWN_ZONE,
  type PogOverlayId,
  legalStatusOpacityExpression,
  legalStatusPattern,
  legalStatusStyle,
  overlayStyle,
} from "@/lib/pogZones";
import type {
  PogFeatureLayer,
  PogInspectorHit,
  PogTileLayerName,
  PogTileRelease,
} from "@/lib/types";

export const POG_SOURCE_ID = "pog-mvt-source";

export const POG_LAYER_IDS = {
  zonesFill: "pog-zones-fill",
  zonesPattern: "pog-zones-pattern",
  zonesStatusPattern: "pog-zones-status-pattern",
  zonesOutlineBinding: "pog-zones-outline-binding",
  zonesOutlineNonBinding: "pog-zones-outline-nonbinding",
  ouzPattern: "pog-ouz-pattern",
  ouzLine: "pog-ouz-line",
  downtownPattern: "pog-downtown-pattern",
  downtownLine: "pog-downtown-line",
  socialPattern: "pog-social-infrastructure-standard-pattern",
  socialLine: "pog-social-infrastructure-standard-line",
  actBoundary: "pog-act-boundary-line",
} as const;

export type PogStatusFilter = "all" | "binding" | "non_binding";

export const POG_STATUS_FILTERS: readonly PogStatusFilter[] = ["all", "binding", "non_binding"];

const NON_BINDING_STATUSES = ["project", "in_progress"];

const OVERLAY_LAYERS: ReadonlyArray<{
  overlay: Exclude<PogOverlayId, "act_boundary">;
  sourceLayer: PogTileLayerName;
  pattern: string;
  line: string;
}> = [
  { overlay: "ouz", sourceLayer: "ouz", pattern: POG_LAYER_IDS.ouzPattern, line: POG_LAYER_IDS.ouzLine },
  {
    overlay: "downtown",
    sourceLayer: "downtown",
    pattern: POG_LAYER_IDS.downtownPattern,
    line: POG_LAYER_IDS.downtownLine,
  },
  {
    overlay: "social_infrastructure_standard",
    sourceLayer: "social_infrastructure_standard",
    pattern: POG_LAYER_IDS.socialPattern,
    line: POG_LAYER_IDS.socialLine,
  },
];

/** Warstwy w kolejności rysowania (od spodu). */
export const POG_LAYER_ORDER: readonly string[] = [
  POG_LAYER_IDS.zonesFill,
  POG_LAYER_IDS.zonesPattern,
  POG_LAYER_IDS.zonesStatusPattern,
  POG_LAYER_IDS.zonesOutlineBinding,
  POG_LAYER_IDS.zonesOutlineNonBinding,
  ...OVERLAY_LAYERS.flatMap((item) => [item.pattern, item.line]),
  POG_LAYER_IDS.actBoundary,
];

/**
 * Warstwy odpytywane przez inspektor (BK-404) → warstwa logiczna obiektu.
 * Wyłącznie warstwy wypełnień BK-401: strefy i OUZ/OZS/OSDIS (linie obrysu i
 * wzory statusu pokazują te same cechy, więc nie są odpytywane).
 */
export const POG_INSPECT_LAYERS: Readonly<Record<string, PogFeatureLayer>> = {
  [POG_LAYER_IDS.zonesFill]: "zones",
  [POG_LAYER_IDS.ouzPattern]: "ouz",
  [POG_LAYER_IDS.downtownPattern]: "downtown",
  [POG_LAYER_IDS.socialPattern]: "social_infrastructure_standard",
};

const INSPECT_ORDER: readonly PogFeatureLayer[] = [
  "zones",
  "ouz",
  "downtown",
  "social_infrastructure_standard",
];

/** Pełny URL kafli z metadanych wydania — jedyne miejsce budowania źródła. */
export function pogTileUrl(release: PogTileRelease): string {
  return getApiResourceUrl(release.tile_url_template);
}

function statusFilterExpression(filter: PogStatusFilter): unknown[] | null {
  if (filter === "binding") return ["==", ["get", "legal_status"], "binding"];
  if (filter === "non_binding") {
    return ["in", ["get", "legal_status"], ["literal", NON_BINDING_STATUSES]];
  }
  return null;
}

const BASE_FILTERS: Readonly<Record<string, unknown[]>> = {
  [POG_LAYER_IDS.zonesOutlineBinding]: ["==", ["get", "legal_status"], "binding"],
  [POG_LAYER_IDS.zonesOutlineNonBinding]: ["!=", ["get", "legal_status"], "binding"],
  [POG_LAYER_IDS.zonesStatusPattern]: legalStatusPatternFilter(),
};

/** Filtr warstwy: stały filtr warstwy (np. obrys projektu) ∧ filtr statusu. */
export function layerFilter(layerId: string, filter: PogStatusFilter): unknown[] | null {
  const parts = [BASE_FILTERS[layerId], statusFilterExpression(filter)].filter(
    (item): item is unknown[] => Array.isArray(item),
  );
  if (parts.length === 0) return null;
  return parts.length === 1 ? parts[0] : ["all", ...parts];
}

function ensureImages(map: maplibregl.Map): void {
  const images: Array<[string, ReturnType<typeof buildPatternImage>]> = [
    [patternImageId(POG_NULL_STYLE.pattern), buildPatternImage(POG_NULL_STYLE.pattern, POG_NULL_STYLE.outline)],
    [patternImageId(POG_UNKNOWN_ZONE.pattern), buildPatternImage(POG_UNKNOWN_ZONE.pattern, POG_UNKNOWN_ZONE.outline)],
    [legalStatusPatternImage(), buildPatternImage(legalStatusPattern().pattern, legalStatusPattern().outline)],
    ...OVERLAY_LAYERS.map(({ overlay }): [string, ReturnType<typeof buildPatternImage>] => {
      const style = overlayStyle(overlay);
      return [overlayImageId(overlay), buildPatternImage(style.pattern ?? "diagonal-lines", style.outline)];
    }),
  ];
  for (const [id, image] of images) {
    if (!map.hasImage(id)) map.addImage(id, image);
  }
}

export function overlayImageId(overlay: PogOverlayId): string {
  return `pog-overlay-${overlay}`;
}

function withFilter<T extends { id: string }>(layer: T, filter: PogStatusFilter): T {
  const expression = layerFilter(layer.id, filter);
  return (expression ? { ...layer, filter: expression } : layer) as T;
}

/** Dodaje źródło i warstwy POG (idempotentnie) z trybem i filtrem statusu. */
export function addPogLayers(
  map: maplibregl.Map,
  release: PogTileRelease,
  themeId: PogThemeId,
  filter: PogStatusFilter,
): void {
  ensureImages(map);
  if (!map.getSource(POG_SOURCE_ID)) {
    map.addSource(POG_SOURCE_ID, {
      type: "vector",
      tiles: [pogTileUrl(release)],
      minzoom: release.min_zoom,
      maxzoom: release.max_zoom,
      ...(release.bounds ? { bounds: release.bounds } : {}),
      attribution: release.attribution,
    });
  }
  const theme = themeById(themeId);
  const nonBinding = legalStatusStyle("project");
  const layers: maplibregl.LayerSpecification[] = [
    {
      id: POG_LAYER_IDS.zonesFill,
      type: "fill",
      source: POG_SOURCE_ID,
      "source-layer": "zones",
      paint: {
        "fill-color": themeFillColorExpression(theme) as never,
        "fill-opacity": legalStatusOpacityExpression() as never,
      },
    },
    {
      id: POG_LAYER_IDS.zonesPattern,
      type: "fill",
      source: POG_SOURCE_ID,
      "source-layer": "zones",
      paint: {
        "fill-pattern": themePatternImage(theme),
        "fill-opacity": themePatternOpacityExpression(theme) as never,
      },
    },
    {
      // BK-406: projekt ma oprócz słabszego krycia i obrysu przerywanego także
      // własny wzór — rozróżnienie nie zależy wyłącznie od percepcji barwy.
      id: POG_LAYER_IDS.zonesStatusPattern,
      type: "fill",
      source: POG_SOURCE_ID,
      "source-layer": "zones",
      paint: { "fill-pattern": legalStatusPatternImage() },
    },
    {
      id: POG_LAYER_IDS.zonesOutlineBinding,
      type: "line",
      source: POG_SOURCE_ID,
      "source-layer": "zones",
      paint: { "line-color": "#3d3d3d", "line-width": 0.8 },
    },
    {
      id: POG_LAYER_IDS.zonesOutlineNonBinding,
      type: "line",
      source: POG_SOURCE_ID,
      "source-layer": "zones",
      paint: {
        "line-color": "#3d3d3d",
        "line-width": 1.2,
        "line-dasharray": nonBinding.line_dasharray ?? [2, 2],
      },
    },
    ...OVERLAY_LAYERS.flatMap(({ overlay, sourceLayer, pattern, line }) => {
      const style = overlayStyle(overlay);
      return [
        {
          id: pattern,
          type: "fill",
          source: POG_SOURCE_ID,
          "source-layer": sourceLayer,
          paint: { "fill-pattern": overlayImageId(overlay) },
        },
        {
          id: line,
          type: "line",
          source: POG_SOURCE_ID,
          "source-layer": sourceLayer,
          paint: {
            "line-color": style.outline,
            "line-width": style.line_width,
            ...(style.line_dasharray ? { "line-dasharray": style.line_dasharray } : {}),
          },
        },
      ] as maplibregl.LayerSpecification[];
    }),
    {
      id: POG_LAYER_IDS.actBoundary,
      type: "line",
      source: POG_SOURCE_ID,
      "source-layer": "act_boundary",
      paint: {
        "line-color": overlayStyle("act_boundary").outline,
        "line-width": overlayStyle("act_boundary").line_width,
      },
    },
  ];
  for (const layer of layers) {
    if (!map.getLayer(layer.id)) map.addLayer(withFilter(layer, filter));
  }
}

/** BK-402: zmiana trybu to wyłącznie `setPaintProperty` na istniejących warstwach. */
export function applyPogTheme(map: maplibregl.Map, themeId: PogThemeId): void {
  if (!map.getLayer(POG_LAYER_IDS.zonesFill)) return;
  const theme = themeById(themeId);
  map.setPaintProperty(POG_LAYER_IDS.zonesFill, "fill-color", themeFillColorExpression(theme));
  map.setPaintProperty(POG_LAYER_IDS.zonesPattern, "fill-pattern", themePatternImage(theme));
  map.setPaintProperty(
    POG_LAYER_IDS.zonesPattern,
    "fill-opacity",
    themePatternOpacityExpression(theme),
  );
}

/** Filtr projekt / akt wiążący na już pobranych kaflach (bez nowego źródła). */
export function applyPogStatusFilter(map: maplibregl.Map, filter: PogStatusFilter): void {
  for (const layerId of POG_LAYER_ORDER) {
    if (map.getLayer(layerId)) map.setFilter(layerId, layerFilter(layerId, filter) as never);
  }
}

export function removePogLayers(map: maplibregl.Map): void {
  for (const layerId of [...POG_LAYER_ORDER].reverse()) {
    if (map.getLayer(layerId)) map.removeLayer(layerId);
  }
  if (map.getSource(POG_SOURCE_ID)) map.removeSource(POG_SOURCE_ID);
}

type RenderedFeature = {
  id?: string | number;
  layer?: { id?: string };
  properties?: Record<string, unknown> | null;
};

/**
 * Trafienia inspektora z `queryRenderedFeatures` (BK-404): tylko warstwy POG,
 * bez duplikatów tej samej cechy z sąsiednich kafli (ten sam identyfikator MVT
 * = klucz wiersza wydania, a zapasowo `feature_id`), strefy przed nakładkami.
 */
export function pogInspectorHits(features: ReadonlyArray<RenderedFeature>): PogInspectorHit[] {
  const hits = new Map<string, PogInspectorHit>();
  for (const feature of features) {
    const layer = feature.layer?.id ? POG_INSPECT_LAYERS[feature.layer.id] : undefined;
    const properties = feature.properties;
    if (!layer || !properties) continue;
    const featurePk =
      typeof feature.id === "number" && Number.isFinite(feature.id) ? feature.id : null;
    const identity = featurePk ?? String(properties.feature_id ?? "");
    const key = `${layer}:${properties.data_release_id ?? ""}:${identity}`;
    if (hits.has(key)) continue;
    hits.set(key, { key, layer, featurePk, properties } as PogInspectorHit);
  }
  return [...hits.values()].sort(
    (a, b) => INSPECT_ORDER.indexOf(a.layer) - INSPECT_ORDER.indexOf(b.layer),
  );
}

/** Identyfikatory warstw odpytywanych przez inspektor, które są na mapie. */
export function presentInspectLayers(map: Pick<maplibregl.Map, "getLayer">): string[] {
  return Object.keys(POG_INSPECT_LAYERS).filter((layerId) => Boolean(map.getLayer(layerId)));
}
