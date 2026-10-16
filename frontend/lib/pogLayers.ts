/**
 * Warstwy MapLibre wektorowych kafli POG dla całego kraju (BK-401/402/403, ADR-026).
 *
 * Dane obowiązujące i projekty to **dwa osobne źródła wektorowe** (`binding` i `project` — osobne adresy kafli,
 * osobny cache i ETag), z identycznym zestawem warstw. Przełącznik „obowiązujące / projekty / oba” zmienia wyłącznie
 * widoczność warstw danego źródła — źródło nieużywane przez żadną widoczną warstwę nie pobiera kafli.
 *
 * Progi zoomu pochodzą z metadanych stanu (`layer_min_zoom`, jedno źródło prawdy: serwer):
 * granice aktów w kolorze statusu od z0 (przegląd krajowy, wypełnienie do z13), strefy od z11, OUZ/OZS/OSDIS od z12;
 * etykiety stref od z14, etykiety OUZ/OZS/OSDIS od z16. Zmiana trybu tematycznego to wyłącznie `setPaintProperty`.
 *
 * Źródła są dodawane raz, z URL-ami przypiętymi do `state_id` stanu pobranego na starcie sesji mapy.
 */
import type * as maplibregl from "maplibre-gl";

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
  POG_LEGAL_STATUS_STYLES,
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
  PogTileEdition,
  PogTileLayerName,
  PogTileState,
} from "@/lib/types";

export type PogEdition = Exclude<PogTileEdition, "all">;

/** Edycje danych jako osobne źródła; kolejność = kolejność rysowania (projekty nad obowiązującymi). */
export const POG_EDITIONS: readonly PogEdition[] = ["binding", "project"];

/** Źródło obowiązujących zachowuje dotychczasowy identyfikator; projekty mają własne. */
export const POG_SOURCE_ID = "pog-mvt-source";
export const POG_PROJECT_SOURCE_ID = "pog-mvt-project-source";

export function pogSourceId(edition: PogEdition): string {
  return edition === "binding" ? POG_SOURCE_ID : POG_PROJECT_SOURCE_ID;
}

export const POG_SOURCE_IDS: readonly string[] = POG_EDITIONS.map(pogSourceId);

/** Identyfikatory warstw źródła obowiązujących; warstwy projektów mają ten sam id z sufiksem edycji. */
export const POG_LAYER_IDS = {
  actFill: "pog-act-fill",
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
  zoneLabels: "pog-zone-labels",
  ouzLabels: "pog-ouz-labels",
  downtownLabels: "pog-downtown-labels",
  socialLabels: "pog-social-infrastructure-standard-labels",
} as const;

export type PogStatusFilter = "all" | "binding" | "non_binding";

export const POG_STATUS_FILTERS: readonly PogStatusFilter[] = ["all", "binding", "non_binding"];

/** Które edycje (źródła) są widoczne przy danym filtrze: „oba” = obowiązujące i projekty. */
export function visibleEditions(filter: PogStatusFilter): readonly PogEdition[] {
  if (filter === "binding") return ["binding"];
  if (filter === "non_binding") return ["project"];
  return POG_EDITIONS;
}

export function pogLayerId(base: string, edition: PogEdition): string {
  return edition === "binding" ? base : `${base}--${edition}`;
}

/** Nazwa bazowa warstwy (bez sufiksu edycji) i jej edycja. */
export function parsePogLayerId(id: string): { base: string; edition: PogEdition } {
  const marker = id.indexOf("--");
  if (marker < 0) return { base: id, edition: "binding" };
  return { base: id.slice(0, marker), edition: id.slice(marker + 2) as PogEdition };
}

const OVERLAY_LAYERS: ReadonlyArray<{
  overlay: Exclude<PogOverlayId, "act_boundary">;
  sourceLayer: Exclude<PogTileLayerName, "zones" | "act_boundary">;
  pattern: string;
  line: string;
  labels: string;
}> = [
  {
    overlay: "ouz",
    sourceLayer: "ouz",
    pattern: POG_LAYER_IDS.ouzPattern,
    line: POG_LAYER_IDS.ouzLine,
    labels: POG_LAYER_IDS.ouzLabels,
  },
  {
    overlay: "downtown",
    sourceLayer: "downtown",
    pattern: POG_LAYER_IDS.downtownPattern,
    line: POG_LAYER_IDS.downtownLine,
    labels: POG_LAYER_IDS.downtownLabels,
  },
  {
    overlay: "social_infrastructure_standard",
    sourceLayer: "social_infrastructure_standard",
    pattern: POG_LAYER_IDS.socialPattern,
    line: POG_LAYER_IDS.socialLine,
    labels: POG_LAYER_IDS.socialLabels,
  },
];

/** Etykiety OUZ/OZS/OSDIS pojawiają się od z16 (jak we wzorcu UX); strefy od z14. */
export const POG_ZONE_LABEL_MIN_ZOOM = 14;
export const POG_OVERLAY_LABEL_MIN_ZOOM = 16;
/** Wypełnienie granic aktów w kolorze statusu znika przy z13 — wtedy rysowane są strefy. */
export const POG_ACT_FILL_MAX_ZOOM = 13;
export const POG_LABEL_FONT = "Open Sans Semibold";

/** Warstwy jednej edycji w kolejności rysowania (od spodu). */
const EDITION_LAYER_BASES: readonly string[] = [
  POG_LAYER_IDS.actFill,
  POG_LAYER_IDS.zonesFill,
  POG_LAYER_IDS.zonesPattern,
  POG_LAYER_IDS.zonesStatusPattern,
  POG_LAYER_IDS.zonesOutlineBinding,
  POG_LAYER_IDS.zonesOutlineNonBinding,
  ...OVERLAY_LAYERS.flatMap((item) => [item.pattern, item.line]),
  POG_LAYER_IDS.actBoundary,
  POG_LAYER_IDS.zoneLabels,
  ...OVERLAY_LAYERS.map((item) => item.labels),
];

/** Warstwy w kolejności rysowania (od spodu): najpierw wszystkie obowiązujące, potem projekty. */
export const POG_LAYER_ORDER: readonly string[] = POG_EDITIONS.flatMap((edition) =>
  EDITION_LAYER_BASES.map((base) => pogLayerId(base, edition)),
);

/**
 * Warstwy odpytywane przez inspektor (BK-404) → warstwa logiczna obiektu. Wypełnienia stref i nakładek oraz — przy
 * małym zoomie, gdy stref jeszcze nie ma w kaflach — wypełnienie granicy aktu (informacja o akcie i jego statusie).
 */
export const POG_INSPECT_LAYERS: Readonly<Record<string, PogFeatureLayer>> = Object.fromEntries(
  POG_EDITIONS.flatMap((edition) => [
    [pogLayerId(POG_LAYER_IDS.zonesFill, edition), "zones"],
    [pogLayerId(POG_LAYER_IDS.ouzPattern, edition), "ouz"],
    [pogLayerId(POG_LAYER_IDS.downtownPattern, edition), "downtown"],
    [pogLayerId(POG_LAYER_IDS.socialPattern, edition), "social_infrastructure_standard"],
    [pogLayerId(POG_LAYER_IDS.actFill, edition), "act_boundary"],
  ]),
) as Record<string, PogFeatureLayer>;

const INSPECT_ORDER: readonly PogFeatureLayer[] = [
  "zones",
  "ouz",
  "downtown",
  "social_infrastructure_standard",
  "act_boundary",
];

/** Pełny URL kafli edycji ze stanu — jedyne miejsce budowania źródła. */
export function pogTileUrl(state: PogTileState, edition: PogEdition = "binding"): string {
  const source = state.editions_sources?.find((item) => item.edition === edition);
  const template = source?.tile_url_template ?? `${state.tile_url_template}?edition=${edition}`;
  return getApiResourceUrl(template);
}

const NON_BINDING_STATUSES = ["project", "in_progress"];

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

/** Filtr warstwy: stały filtr warstwy (np. obrys projektu) ∧ filtr statusu (używany w testach i wyrażeniach). */
export function layerFilter(layerId: string, filter: PogStatusFilter): unknown[] | null {
  const parts = [BASE_FILTERS[parsePogLayerId(layerId).base], statusFilterExpression(filter)].filter(
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

/** Kolor przeglądu krajowego wg statusu prawnego aktu (wyrażenie `match` z artefaktu prezentacji). */
export function actStatusFillColorExpression(): unknown[] {
  const unknown = legalStatusStyle("unknown");
  return [
    "match",
    ["get", "legal_status"],
    ...POG_LEGAL_STATUS_STYLES.filter((item) => item.status !== "unknown").flatMap((item) => [
      item.status,
      item.overview_color,
    ]),
    unknown.overview_color,
  ];
}

function minZoomOf(state: PogTileState, layer: PogTileLayerName): number {
  return state.layer_min_zoom?.[layer] ?? 0;
}

function textField(sourceLayer: "zones" | "ouz" | "downtown" | "social_infrastructure_standard", fallback: string): unknown[] {
  return sourceLayer === "zones"
    ? ["coalesce", ["get", "designation"], ["get", "symbol"], ["get", "zone_code"]]
    : ["coalesce", ["get", "symbol"], fallback];
}

function buildEditionLayers(
  state: PogTileState,
  edition: PogEdition,
  themeId: PogThemeId,
): maplibregl.LayerSpecification[] {
  const source = pogSourceId(edition);
  const id = (base: string) => pogLayerId(base, edition);
  const nonBinding = legalStatusStyle("project");
  const theme = themeById(themeId);
  const layers: maplibregl.LayerSpecification[] = [
    {
      // Przegląd krajowy: granica aktu w kolorze statusu prawnego, widoczna od najmniejszego zoomu.
      id: id(POG_LAYER_IDS.actFill),
      type: "fill",
      source,
      "source-layer": "act_boundary",
      maxzoom: POG_ACT_FILL_MAX_ZOOM,
      paint: {
        "fill-color": actStatusFillColorExpression() as never,
        "fill-opacity": ["interpolate", ["linear"], ["zoom"], 5, 0.5, 11, 0.4, POG_ACT_FILL_MAX_ZOOM, 0.05] as never,
      },
    },
    {
      id: id(POG_LAYER_IDS.zonesFill),
      type: "fill",
      source,
      "source-layer": "zones",
      minzoom: minZoomOf(state, "zones"),
      paint: {
        "fill-color": themeFillColorExpression(theme) as never,
        "fill-opacity": legalStatusOpacityExpression() as never,
      },
    },
    {
      id: id(POG_LAYER_IDS.zonesPattern),
      type: "fill",
      source,
      "source-layer": "zones",
      minzoom: minZoomOf(state, "zones"),
      paint: {
        "fill-pattern": themePatternImage(theme),
        "fill-opacity": themePatternOpacityExpression(theme) as never,
      },
    },
    {
      // BK-406: projekt ma oprócz słabszego krycia i obrysu przerywanego także własny wzór.
      id: id(POG_LAYER_IDS.zonesStatusPattern),
      type: "fill",
      source,
      "source-layer": "zones",
      minzoom: minZoomOf(state, "zones"),
      filter: BASE_FILTERS[POG_LAYER_IDS.zonesStatusPattern] as never,
      paint: { "fill-pattern": legalStatusPatternImage() },
    },
    {
      id: id(POG_LAYER_IDS.zonesOutlineBinding),
      type: "line",
      source,
      "source-layer": "zones",
      minzoom: minZoomOf(state, "zones"),
      filter: BASE_FILTERS[POG_LAYER_IDS.zonesOutlineBinding] as never,
      paint: { "line-color": "#3d3d3d", "line-width": 0.8 },
    },
    {
      id: id(POG_LAYER_IDS.zonesOutlineNonBinding),
      type: "line",
      source,
      "source-layer": "zones",
      minzoom: minZoomOf(state, "zones"),
      filter: BASE_FILTERS[POG_LAYER_IDS.zonesOutlineNonBinding] as never,
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
          id: id(pattern),
          type: "fill",
          source,
          "source-layer": sourceLayer,
          minzoom: minZoomOf(state, sourceLayer),
          paint: { "fill-pattern": overlayImageId(overlay) },
        },
        {
          id: id(line),
          type: "line",
          source,
          "source-layer": sourceLayer,
          minzoom: minZoomOf(state, sourceLayer),
          paint: {
            "line-color": style.outline,
            "line-width": style.line_width,
            ...(style.line_dasharray ? { "line-dasharray": style.line_dasharray } : {}),
          },
        },
      ] as maplibregl.LayerSpecification[];
    }),
    {
      id: id(POG_LAYER_IDS.actBoundary),
      type: "line",
      source,
      "source-layer": "act_boundary",
      paint: {
        "line-color": overlayStyle("act_boundary").outline,
        "line-width": ["interpolate", ["linear"], ["zoom"], 5, 0.6, 12, overlayStyle("act_boundary").line_width] as never,
      },
    },
    {
      id: id(POG_LAYER_IDS.zoneLabels),
      type: "symbol",
      source,
      "source-layer": "zones",
      minzoom: POG_ZONE_LABEL_MIN_ZOOM,
      layout: {
        "text-field": textField("zones", "") as never,
        "text-font": [POG_LABEL_FONT],
        "text-size": ["interpolate", ["linear"], ["zoom"], POG_ZONE_LABEL_MIN_ZOOM, 10, 18, 14] as never,
        "text-allow-overlap": false,
        "text-padding": 2,
      },
      paint: { "text-color": "#1d1d1d", "text-halo-color": "#ffffff", "text-halo-width": 1.4 },
    },
    ...OVERLAY_LAYERS.map(({ overlay, sourceLayer, labels }): maplibregl.LayerSpecification => ({
      id: id(labels),
      type: "symbol",
      source,
      "source-layer": sourceLayer,
      minzoom: Math.max(POG_OVERLAY_LABEL_MIN_ZOOM, minZoomOf(state, sourceLayer)),
      layout: {
        "text-field": textField(sourceLayer, overlayStyle(overlay).short_label) as never,
        "text-font": [POG_LABEL_FONT],
        "text-size": 11,
        "text-allow-overlap": false,
      },
      paint: {
        "text-color": overlayStyle(overlay).outline,
        "text-halo-color": "#ffffff",
        "text-halo-width": 1.4,
      },
    })),
  ];
  return layers;
}

/** Dodaje źródła i warstwy POG (idempotentnie) z trybem i filtrem edycji; niewidoczna edycja nie pobiera kafli. */
export function addPogLayers(
  map: maplibregl.Map,
  state: PogTileState,
  themeId: PogThemeId,
  filter: PogStatusFilter,
): void {
  ensureImages(map);
  const visible = new Set(visibleEditions(filter));
  for (const edition of POG_EDITIONS) {
    const sourceId = pogSourceId(edition);
    if (!map.getSource(sourceId)) {
      map.addSource(sourceId, {
        type: "vector",
        tiles: [pogTileUrl(state, edition)],
        minzoom: state.min_zoom,
        maxzoom: state.max_zoom,
        ...(state.bounds ? { bounds: state.bounds } : {}),
        attribution: state.attribution,
      });
    }
    for (const layer of buildEditionLayers(state, edition, themeId)) {
      if (map.getLayer(layer.id)) continue;
      map.addLayer({
        ...layer,
        layout: {
          ...(layer.layout ?? {}),
          visibility: visible.has(edition) ? "visible" : "none",
        } as never,
      } as maplibregl.LayerSpecification);
    }
  }
}

/** BK-402: zmiana trybu to wyłącznie `setPaintProperty` na istniejących warstwach (obu edycji). */
export function applyPogTheme(map: maplibregl.Map, themeId: PogThemeId): void {
  const theme = themeById(themeId);
  for (const edition of POG_EDITIONS) {
    const fill = pogLayerId(POG_LAYER_IDS.zonesFill, edition);
    if (!map.getLayer(fill)) continue;
    map.setPaintProperty(fill, "fill-color", themeFillColorExpression(theme) as never);
    const pattern = pogLayerId(POG_LAYER_IDS.zonesPattern, edition);
    map.setPaintProperty(pattern, "fill-pattern", themePatternImage(theme));
    map.setPaintProperty(pattern, "fill-opacity", themePatternOpacityExpression(theme) as never);
  }
}

/** Przełącznik obowiązujące / projekty / oba: widoczność warstw źródeł, bez zmiany ich definicji. */
export function applyPogStatusFilter(map: maplibregl.Map, filter: PogStatusFilter): void {
  const visible = new Set(visibleEditions(filter));
  for (const edition of POG_EDITIONS) {
    for (const base of EDITION_LAYER_BASES) {
      const layerId = pogLayerId(base, edition);
      if (map.getLayer(layerId)) {
        map.setLayoutProperty(layerId, "visibility", visible.has(edition) ? "visible" : "none");
      }
    }
  }
}

export function removePogLayers(map: maplibregl.Map): void {
  for (const layerId of [...POG_LAYER_ORDER].reverse()) {
    if (map.getLayer(layerId)) map.removeLayer(layerId);
  }
  for (const sourceId of POG_SOURCE_IDS) {
    if (map.getSource(sourceId)) map.removeSource(sourceId);
  }
}

type RenderedFeature = {
  id?: string | number;
  layer?: { id?: string };
  properties?: Record<string, unknown> | null;
};

/**
 * Trafienia inspektora z `queryRenderedFeatures` (BK-404): tylko warstwy POG, bez duplikatów tej samej cechy z
 * sąsiednich kafli (ten sam identyfikator MVT = klucz wiersza w tabeli mapy, a zapasowo `feature_id`), strefy przed
 * nakładkami, a granica aktu na końcu. Stan krajowy jest jeden na sesję mapy, więc klucz go nie zawiera.
 */
export function pogInspectorHits(features: ReadonlyArray<RenderedFeature>): PogInspectorHit[] {
  const hits = new Map<string, PogInspectorHit>();
  for (const feature of features) {
    const layer = feature.layer?.id ? POG_INSPECT_LAYERS[feature.layer.id] : undefined;
    const properties = feature.properties;
    if (!layer || !properties) continue;
    const featurePk =
      typeof feature.id === "number" && Number.isFinite(feature.id) ? feature.id : null;
    const identity = featurePk ?? String(properties.feature_id ?? properties.act_id ?? "");
    const key = `${layer}:${identity}`;
    if (hits.has(key)) continue;
    hits.set(key, { key, layer, featurePk, properties } as PogInspectorHit);
  }
  return [...hits.values()].sort(
    (a, b) => INSPECT_ORDER.indexOf(a.layer) - INSPECT_ORDER.indexOf(b.layer),
  );
}

/** Identyfikatory warstw odpytywanych przez inspektor, które są na mapie (widoczne i niewidoczne — filtruje MapLibre). */
export function presentInspectLayers(map: Pick<maplibregl.Map, "getLayer">): string[] {
  return Object.keys(POG_INSPECT_LAYERS).filter((layerId) => Boolean(map.getLayer(layerId)));
}
