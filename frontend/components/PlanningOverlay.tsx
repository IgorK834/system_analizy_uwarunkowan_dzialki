"use client";

import { useEffect, useState } from "react";
import type maplibregl from "maplibre-gl";

import { LayerToggle, type LayerToggleItem } from "@/components/LayerToggle";
import {
  getKimpzpTileUrl,
  getPogWmsLayers,
  getPogWmsUrl,
} from "@/lib/config";
import {
  BUILDABLE_AREA_FILL_LAYER_ID,
  LAYER_LEGEND,
  MPZP_NATIVE_MAX_ZOOM,
  MPZP_WMS_LAYER_ID,
  MPZP_WMS_SOURCE_ID,
  NETWORK_LINE_LAYER_ID,
  OVERLAY_OPACITY,
  PARCEL_FILL_LAYER_ID,
  PLANNING_WMS_MAX_ZOOM,
  PLANNING_WMS_MIN_ZOOM,
  POG_WMS_LAYER_ID,
  POG_WMS_SOURCE_ID,
  PROTECTION_ZONE_FILL_LAYER_ID,
  RISK_FILL_LAYER_ID,
} from "@/lib/layerStyles";
import type { AnalyzeResponse } from "@/lib/types";

export type PlanningOverlayProps = {
  result?: AnalyzeResponse | null;
  map: maplibregl.Map | null;
};

// 256 px daje w publicznych usługach Geoportalu krótszy czas pierwszego
// renderu niż cięższe obrazy 512 px, mimo większej liczby małych requestów.
const WMS_TILE_SIZE = 256;
const VISIBILITY_STORAGE_KEY = "dzialki:planning-overlays:v1";

type OverlayPreferences = {
  mpzp: boolean;
  pog: boolean;
};

const DEFAULT_PREFERENCES: OverlayPreferences = {
  mpzp: true,
  pog: true,
};

const RESULT_FOREGROUND_LAYER_IDS = [
  RISK_FILL_LAYER_ID,
  PROTECTION_ZONE_FILL_LAYER_ID,
  NETWORK_LINE_LAYER_ID,
  BUILDABLE_AREA_FILL_LAYER_ID,
  PARCEL_FILL_LAYER_ID,
];

/**
 * Zamienia adres GetFeatureInfo (może zawierać querystring z parametrami
 * WMS) na czysty origin+path, żeby zbudować z niego bazowy adres GetMap.
 *
 * Założenie: discovery zwraca URL wskazujący na ten sam endpoint WMS, który
 * obsługuje też GetMap — sam querystring (jeśli jest) dotyczy konkretnego
 * zapytania GetFeatureInfo i nie ma zastosowania do rastra warstwy mapowej.
 */
function extractWmsBaseUrl(sourceUrl: string): string {
  const [base] = sourceUrl.split("?");
  return base;
}

function buildWmsTileUrl(baseUrl: string, layerNames: string): string {
  const params = new URLSearchParams({
    service: "WMS",
    version: "1.1.1",
    request: "GetMap",
    layers: layerNames,
    styles: "",
    format: "image/png",
    transparent: "true",
    srs: "EPSG:3857",
    width: String(WMS_TILE_SIZE),
    height: String(WMS_TILE_SIZE),
    bbox: "{bbox-epsg-3857}",
  });
  const separator = baseUrl.includes("?") ? "&" : "?";
  // URLSearchParams poprawnie koduje zwykłe wartości, ale MapLibre rozpoznaje
  // token bbox wyłącznie z literalnymi klamrami. Zakodowane %7B...%7D trafiało
  // bez podmiany do WMS, który zwracał dokument błędu XML zamiast obrazu PNG.
  const query = params
    .toString()
    .replace("%7Bbbox-epsg-3857%7D", "{bbox-epsg-3857}");
  return `${baseUrl}${separator}${query}`;
}

function addOrUpdateRasterLayer(
  map: maplibregl.Map,
  sourceId: string,
  layerId: string,
  tileUrl: string,
  nativeMaxZoom = PLANNING_WMS_MAX_ZOOM,
): void {
  if (map.getSource(sourceId)) return;
  map.addSource(sourceId, {
    type: "raster",
    tiles: [tileUrl],
    tileSize: WMS_TILE_SIZE,
    minzoom: PLANNING_WMS_MIN_ZOOM,
    maxzoom: nativeMaxZoom,
    scheme: "xyz",
  });
  const firstResultLayer = RESULT_FOREGROUND_LAYER_IDS.find((id) =>
    Boolean(map.getLayer(id)),
  );
  map.addLayer(
    {
      id: layerId,
      type: "raster",
      source: sourceId,
      minzoom: PLANNING_WMS_MIN_ZOOM,
      maxzoom: PLANNING_WMS_MAX_ZOOM,
      paint: {
        "raster-opacity": OVERLAY_OPACITY,
        "raster-fade-duration": 0,
      },
      layout: { visibility: "none" },
    },
    firstResultLayer,
  );
}

function removeRasterLayer(map: maplibregl.Map, sourceId: string, layerId: string): void {
  if (map.getLayer(layerId)) map.removeLayer(layerId);
  if (map.getSource(sourceId)) map.removeSource(sourceId);
}

function setLayerVisible(map: maplibregl.Map, layerId: string, visible: boolean): void {
  if (map.getLayer(layerId)) {
    map.setLayoutProperty(layerId, "visibility", visible ? "visible" : "none");
  }
}

export function PlanningOverlay({ result, map }: PlanningOverlayProps) {
  const [preferences, setPreferences] = useState(DEFAULT_PREFERENCES);
  const [preferencesReady, setPreferencesReady] = useState(false);
  const [currentZoom, setCurrentZoom] = useState<number | null>(null);

  const kimpzpTileUrl = getKimpzpTileUrl();
  const pogSource = result?.pog?.source ?? null;
  const resultPogWmsUrl =
    pogSource?.source_url && pogSource.source_name.toUpperCase().includes("WMS")
      ? pogSource.source_url
      : null;
  // Krajowa usługa jest stabilna przed i po analizie. Źródło z wyniku jest
  // fallbackiem dla wdrożeń bez skonfigurowanej nakładki krajowej.
  const pogWmsUrl = getPogWmsUrl() ?? resultPogWmsUrl;
  const pogWmsLayers = getPogWmsLayers();

  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(VISIBILITY_STORAGE_KEY);
      if (stored) {
        const parsed = JSON.parse(stored) as Partial<OverlayPreferences>;
        setPreferences({
          mpzp:
            typeof parsed.mpzp === "boolean"
              ? parsed.mpzp
              : DEFAULT_PREFERENCES.mpzp,
          pog:
            typeof parsed.pog === "boolean"
              ? parsed.pog
              : DEFAULT_PREFERENCES.pog,
        });
      }
    } catch {
      // Uszkodzone lub zablokowane localStorage nie może wyłączyć mapy.
    } finally {
      setPreferencesReady(true);
    }
  }, []);

  useEffect(() => {
    if (!map) return;
    const updateZoom = () => setCurrentZoom(map.getZoom());
    updateZoom();
    map.on("zoomend", updateZoom);
    return () => {
      map.off("zoomend", updateZoom);
    };
  }, [map]);

  useEffect(() => {
    if (!map) return;
    addOrUpdateRasterLayer(
      map,
      MPZP_WMS_SOURCE_ID,
      MPZP_WMS_LAYER_ID,
      kimpzpTileUrl,
      MPZP_NATIVE_MAX_ZOOM,
    );

    return () => {
      removeRasterLayer(map, MPZP_WMS_SOURCE_ID, MPZP_WMS_LAYER_ID);
    };
  }, [kimpzpTileUrl, map]);

  useEffect(() => {
    if (!map || !pogWmsUrl) return;
    const tileUrl = buildWmsTileUrl(extractWmsBaseUrl(pogWmsUrl), pogWmsLayers);
    addOrUpdateRasterLayer(map, POG_WMS_SOURCE_ID, POG_WMS_LAYER_ID, tileUrl);

    return () => {
      removeRasterLayer(map, POG_WMS_SOURCE_ID, POG_WMS_LAYER_ID);
    };
  }, [map, pogWmsLayers, pogWmsUrl]);

  useEffect(() => {
    if (!map) return;
    setLayerVisible(
      map,
      MPZP_WMS_LAYER_ID,
      preferencesReady && preferences.mpzp,
    );
  }, [map, preferences.mpzp, preferencesReady]);

  useEffect(() => {
    if (!map) return;
    setLayerVisible(
      map,
      POG_WMS_LAYER_ID,
      preferencesReady && preferences.pog && Boolean(pogWmsUrl),
    );
  }, [map, pogWmsUrl, preferences.pog, preferencesReady]);

  const updatePreference = (id: "mpzp" | "pog", checked: boolean) => {
    setPreferences((current) => {
      const updated = { ...current, [id]: checked };
      try {
        window.localStorage.setItem(
          VISIBILITY_STORAGE_KEY,
          JSON.stringify(updated),
        );
      } catch {
        // Przełącznik nadal działa w pamięci bieżącej sesji.
      }
      return updated;
    });
  };

  const items: LayerToggleItem[] = [
    {
      id: "mpzp_wms",
      label: LAYER_LEGEND.mpzp_wms.label,
      color: LAYER_LEGEND.mpzp_wms.color,
      checked: preferences.mpzp,
      disabled: false,
    },
    {
      id: "pog_wms",
      label: LAYER_LEGEND.pog_wms.label,
      color: LAYER_LEGEND.pog_wms.color,
      checked: Boolean(pogWmsUrl) && preferences.pog,
      disabled: !pogWmsUrl,
      disabledReason: !pogWmsUrl
        ? "Nakładka POG niedostępna — brak źródła WMS w wyniku i konfiguracji NEXT_PUBLIC_POG_WMS_URL."
        : undefined,
    },
  ];

  const anyEnabled =
    preferences.mpzp || (Boolean(pogWmsUrl) && preferences.pog);
  const waitingForZoom =
    anyEnabled && currentZoom !== null && currentZoom < PLANNING_WMS_MIN_ZOOM;
  const statusMessage =
    currentZoom === null
      ? "Nakładki zostaną uruchomione po załadowaniu mapy."
      : waitingForZoom
        ? `Przybliż mapę do poziomu ${PLANNING_WMS_MIN_ZOOM}, aby zobaczyć włączone nakładki.`
        : anyEnabled
          ? "Pobierane są wyłącznie kafelki widoczne w bieżącym obszarze mapy."
          : "Nakładki planistyczne są wyłączone.";

  return (
    <aside className="planning-overlay-control" aria-label="Nakładki planistyczne">
      <LayerToggle
        items={items}
        legendLabel="Nakładki planistyczne (WMS)"
        onChange={(id, checked) => {
          if (id === "mpzp_wms") updatePreference("mpzp", checked);
          if (id === "pog_wms") updatePreference("pog", checked);
        }}
      />
      <p className="planning-overlay-status" role="status">
        {statusMessage}
      </p>
    </aside>
  );
}
