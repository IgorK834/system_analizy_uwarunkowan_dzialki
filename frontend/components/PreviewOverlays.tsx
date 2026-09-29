"use client";

import { useEffect, useMemo, useState } from "react";
import type maplibregl from "maplibre-gl";

import { LayerAvailabilityNote } from "@/components/LayerAvailabilityNote";
import { LayerToggle, type LayerToggleItem } from "@/components/LayerToggle";
import { getPreviewSources } from "@/lib/api";
import { getApiResourceUrl } from "@/lib/config";
import {
  BUILDABLE_AREA_FILL_LAYER_ID,
  KIUT_DISPLAY_MIN_ZOOM,
  KIUT_OVERLAY_OPACITY,
  KIUT_WMS_LAYER_ID,
  KIUT_WMS_SOURCE_ID,
  LAYER_LEGEND,
  MPZP_WMS_LAYER_ID,
  MPZP_WMS_SOURCE_ID,
  NETWORK_LINE_LAYER_ID,
  OVERLAY_OPACITY,
  PARCEL_FILL_LAYER_ID,
  PLANNING_WMS_MAX_ZOOM,
  POG_WMS_LAYER_ID,
  POG_WMS_SOURCE_ID,
  PROTECTION_ZONE_FILL_LAYER_ID,
  RISK_FILL_LAYER_ID,
} from "@/lib/layerStyles";
import { coverageStatusLabel, legalStatusShort } from "@/lib/pogStatus";
import type {
  AnalyzeResponse,
  LayerState,
  PreviewSource,
  PreviewSourceKey,
} from "@/lib/types";

export type PreviewOverlaysProps = {
  result?: AnalyzeResponse | null;
  map: maplibregl.Map | null;
};

const VISIBILITY_STORAGE_KEY = "dzialki:preview-overlays:v2";
const LEGACY_VISIBILITY_STORAGE_KEYS = [
  "dzialki:preview-overlays:v1",
  "dzialki:planning-overlays:v1",
];

type OverlayPreferences = Record<PreviewSourceKey, boolean>;

const DEFAULT_PREFERENCES: OverlayPreferences = {
  mpzp: true,
  pog: true,
  kiut: false,
};

const SOURCE_LAYER_IDS: Record<
  PreviewSourceKey,
  { sourceId: string; layerId: string }
> = {
  mpzp: { sourceId: MPZP_WMS_SOURCE_ID, layerId: MPZP_WMS_LAYER_ID },
  pog: { sourceId: POG_WMS_SOURCE_ID, layerId: POG_WMS_LAYER_ID },
  kiut: { sourceId: KIUT_WMS_SOURCE_ID, layerId: KIUT_WMS_LAYER_ID },
};

const RESULT_FOREGROUND_LAYER_IDS = [
  RISK_FILL_LAYER_ID,
  PROTECTION_ZONE_FILL_LAYER_ID,
  NETWORK_LINE_LAYER_ID,
  BUILDABLE_AREA_FILL_LAYER_ID,
  PARCEL_FILL_LAYER_ID,
];

function addRasterLayer(
  map: maplibregl.Map,
  source: PreviewSource,
  visible: boolean,
): void {
  const { sourceId, layerId } = SOURCE_LAYER_IDS[source.source_key];
  if (!map.getSource(sourceId)) {
    map.addSource(sourceId, {
      type: "raster",
      tiles: [getApiResourceUrl(source.tile_url_template)],
      tileSize: source.tile_size,
      minzoom: source.min_zoom,
      maxzoom: source.max_zoom,
      scheme: "xyz",
      attribution: source.attribution,
    });
  }
  if (map.getLayer(layerId)) return;

  const firstResultLayer = RESULT_FOREGROUND_LAYER_IDS.find((id) =>
    Boolean(map.getLayer(id)),
  );
  const opacity =
    source.source_key === "kiut" ? KIUT_OVERLAY_OPACITY : OVERLAY_OPACITY;
  const resampling = source.source_key === "kiut" ? "nearest" : "linear";

  map.addLayer(
    {
      id: layerId,
      type: "raster",
      source: sourceId,
      minzoom: source.min_zoom,
      maxzoom: PLANNING_WMS_MAX_ZOOM,
      paint: {
        "raster-opacity": opacity,
        "raster-resampling": resampling,
        "raster-fade-duration": 0,
      },
      layout: { visibility: visible ? "visible" : "none" },
    },
    firstResultLayer,
  );
}

function removeRasterLayer(
  map: maplibregl.Map,
  sourceKey: PreviewSourceKey,
): void {
  const { sourceId, layerId } = SOURCE_LAYER_IDS[sourceKey];
  if (map.getLayer(layerId)) map.removeLayer(layerId);
  if (map.getSource(sourceId)) map.removeSource(sourceId);
}

function setLayerVisible(
  map: maplibregl.Map,
  sourceKey: PreviewSourceKey,
  visible: boolean,
): void {
  const { layerId } = SOURCE_LAYER_IDS[sourceKey];
  if (map.getLayer(layerId)) {
    map.setLayoutProperty(layerId, "visibility", visible ? "visible" : "none");
  }
}

function mpzpStatus(result?: AnalyzeResponse | null): string {
  if (!result) return "Analiza parametrów: jeszcze niewykonana.";
  if (result.mpzp_zones.length > 0) return "Analiza parametrów: dostępna.";
  if (result.manual_zone_required) return "Analiza parametrów: wymaga symbolu strefy.";
  return "Analiza parametrów: niedostępna.";
}

function pogStatus(result?: AnalyzeResponse | null): string {
  const pog = result?.pog;
  if (!pog) return "Status aktu w gminie: nieustalony.";
  return `Status aktu w gminie: ${legalStatusShort(pog.legal_status)}; ${coverageStatusLabel(
    pog.coverage_status,
  )}.`;
}

function kiutCoverageStatus(result?: AnalyzeResponse | null): string {
  if (!result) {
    return "Pokrycie powiatu: jeszcze niesprawdzone. Analiza odległości do sieci jest niedostępna bez umowy z powiatem.";
  }
  const preview = result.utilities_preview;
  if (!preview) {
    return "Pokrycie powiatu: nie udało się sprawdzić w tym snapshotcie. Pusty podgląd nie oznacza braku sieci.";
  }

  const county = preview.county_name ? ` (${preview.county_name})` : "";
  const labels = {
    covered: `Pokrycie powiatu${county}: publikuje dane GESUT w KIUT.`,
    not_covered: `Pokrycie powiatu${county}: KIUT nie potwierdził publikacji danych GESUT.`,
    unknown: `Pokrycie powiatu${county}: nie udało się sprawdzić.`,
  };
  return `${labels[preview.coverage_status]} ${preview.note}`;
}

function kiutStatus(
  result: AnalyzeResponse | null | undefined,
  autoEnabled: boolean,
  currentZoom: number | null,
  enabled: boolean,
  source: PreviewSource | undefined,
): string {
  const coverage = kiutCoverageStatus(result);
  if (autoEnabled) {
    return (
      `Włączono podgląd uzbrojenia, ponieważ powiat publikuje dane. ` +
      `Przybliż do poziomu ${KIUT_DISPLAY_MIN_ZOOM}. ${coverage}`
    );
  }
  return coverage + zoomNote(source, currentZoom, enabled);
}

function zoomNote(
  source: PreviewSource | undefined,
  currentZoom: number | null,
  enabled: boolean,
): string {
  if (!source || !enabled || currentZoom === null) return "";
  const displayMinZoom =
    source.source_key === "kiut" ? KIUT_DISPLAY_MIN_ZOOM : source.min_zoom;
  return currentZoom < displayMinZoom
    ? ` Przybliż do poziomu ${displayMinZoom}, aby zobaczyć tę warstwę.`
    : "";
}

type PreferenceSnapshot = {
  preferences: OverlayPreferences;
  kiutExplicit: boolean;
};

function readPreferences(): PreferenceSnapshot {
  const current = window.localStorage.getItem(VISIBILITY_STORAGE_KEY);
  const legacy = LEGACY_VISIBILITY_STORAGE_KEYS.map((key) =>
    window.localStorage.getItem(key),
  ).find(Boolean);
  const stored = current ?? legacy;
  if (!stored) {
    return { preferences: DEFAULT_PREFERENCES, kiutExplicit: false };
  }

  const parsed = JSON.parse(stored) as Partial<OverlayPreferences>;
  const kiutExplicit = typeof parsed.kiut === "boolean";
  const preferences: OverlayPreferences = {
    mpzp:
      typeof parsed.mpzp === "boolean" ? parsed.mpzp : DEFAULT_PREFERENCES.mpzp,
    pog: typeof parsed.pog === "boolean" ? parsed.pog : DEFAULT_PREFERENCES.pog,
    kiut: kiutExplicit ? parsed.kiut === true : DEFAULT_PREFERENCES.kiut,
  };
  if (!current && legacy) {
    // Migracja v1 nie może wymyślić jawnej preferencji KIUT — inaczej
    // automatyczne włączenie po analizie nigdy by nie zadziałało.
    window.localStorage.setItem(
      VISIBILITY_STORAGE_KEY,
      JSON.stringify({ mpzp: preferences.mpzp, pog: preferences.pog }),
    );
  }
  return { preferences, kiutExplicit };
}

export function PreviewOverlays({ result, map }: PreviewOverlaysProps) {
  const [preferences, setPreferences] = useState(DEFAULT_PREFERENCES);
  const [preferencesReady, setPreferencesReady] = useState(false);
  const [kiutExplicit, setKiutExplicit] = useState(false);
  const [kiutAutoEnabled, setKiutAutoEnabled] = useState(false);
  const [sources, setSources] = useState<PreviewSource[]>([]);
  const [sourcesState, setSourcesState] = useState<
    "loading" | "ready" | "error"
  >("loading");
  const [currentZoom, setCurrentZoom] = useState<number | null>(null);
  // BK-406: błędy kafli WMS ze zdarzeń MapLibre — warstwa z błędami jest
  // „niepełna”, a nie pusta z powodu braku planu lub sieci.
  const [tileErrors, setTileErrors] = useState<Partial<Record<PreviewSourceKey, number>>>({});

  const sourcesByKey = useMemo(
    () =>
      Object.fromEntries(
        sources.map((source) => [source.source_key, source]),
      ) as Partial<Record<PreviewSourceKey, PreviewSource>>,
    [sources],
  );

  useEffect(() => {
    const controller = new AbortController();
    setSourcesState("loading");
    void getPreviewSources({ signal: controller.signal })
      .then((loadedSources) => {
        if (controller.signal.aborted) return;
        setSources(loadedSources);
        setSourcesState("ready");
      })
      .catch(() => {
        if (controller.signal.aborted) return;
        setSources([]);
        setSourcesState("error");
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    try {
      const loaded = readPreferences();
      setPreferences(loaded.preferences);
      setKiutExplicit(loaded.kiutExplicit);
    } catch {
      setPreferences(DEFAULT_PREFERENCES);
      setKiutExplicit(false);
    } finally {
      setPreferencesReady(true);
    }
  }, []);

  useEffect(() => {
    if (!preferencesReady || kiutExplicit || kiutAutoEnabled) return;
    if (result?.utilities_preview?.coverage_status !== "covered") return;
    setKiutAutoEnabled(true);
    setPreferences((current) =>
      current.kiut ? current : { ...current, kiut: true },
    );
  }, [kiutAutoEnabled, kiutExplicit, preferencesReady, result]);

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
    const onError = (event: { sourceId?: string }) => {
      const sourceKey = (Object.keys(SOURCE_LAYER_IDS) as PreviewSourceKey[]).find(
        (key) => SOURCE_LAYER_IDS[key].sourceId === event.sourceId,
      );
      if (!sourceKey) return;
      setTileErrors((current) => ({ ...current, [sourceKey]: (current[sourceKey] ?? 0) + 1 }));
    };
    map.on("error", onError);
    return () => {
      map.off("error", onError);
    };
  }, [map]);

  useEffect(() => {
    if (!map) return;
    const mounted: PreviewSourceKey[] = [];
    for (const sourceKey of ["mpzp", "pog"] as const) {
      const source = sourcesByKey[sourceKey];
      if (!source) continue;
      addRasterLayer(
        map,
        source,
        preferencesReady && preferences[sourceKey],
      );
      mounted.push(sourceKey);
    }
    return () => {
      for (const sourceKey of mounted.reverse()) {
        removeRasterLayer(map, sourceKey);
      }
    };
  }, [map, preferencesReady, sourcesByKey]);

  useEffect(() => {
    if (!map || !preferencesReady) return;
    setLayerVisible(map, "mpzp", preferences.mpzp);
    setLayerVisible(map, "pog", preferences.pog);
  }, [map, preferences.mpzp, preferences.pog, preferencesReady, sourcesByKey]);

  const kiutShouldBeMounted =
    preferencesReady &&
    preferences.kiut &&
    currentZoom !== null &&
    currentZoom >= KIUT_DISPLAY_MIN_ZOOM;

  useEffect(() => {
    const source = sourcesByKey.kiut;
    if (!map || !source || !kiutShouldBeMounted) return;
    addRasterLayer(map, source, true);
    return () => removeRasterLayer(map, "kiut");
  }, [kiutShouldBeMounted, map, sourcesByKey]);

  const updatePreference = (sourceKey: PreviewSourceKey, checked: boolean) => {
    if (sourceKey === "kiut") {
      setKiutExplicit(true);
      setKiutAutoEnabled(false);
    }
    setPreferences((current) => {
      const updated = { ...current, [sourceKey]: checked };
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

  const unavailableReason = (sourceKey: PreviewSourceKey) => {
    if (sourcesState === "loading") return "Trwa ładowanie konfiguracji warstwy.";
    if (sourcesState === "error") {
      return "Konfiguracja warstw jest chwilowo niedostępna; mapa podstawowa nadal działa.";
    }
    if (!sourcesByKey[sourceKey]) return "Backend nie udostępnił tej warstwy.";
    return undefined;
  };

  const layerState = (sourceKey: PreviewSourceKey): LayerState => {
    if (sourcesState === "loading") return "loading";
    if (sourcesState === "error" || !sourcesByKey[sourceKey]) return "error";
    return tileErrors[sourceKey] ? "partial" : "available";
  };

  const items: LayerToggleItem[] = [
    {
      id: "kiut_wms",
      label: LAYER_LEGEND.kiut_wms.label,
      color: LAYER_LEGEND.kiut_wms.color,
      checked: preferences.kiut,
      disabled: !sourcesByKey.kiut,
      disabledReason: unavailableReason("kiut"),
      state: layerState("kiut"),
      status: kiutStatus(
        result,
        kiutAutoEnabled,
        currentZoom,
        preferences.kiut,
        sourcesByKey.kiut,
      ),
    },
    {
      id: "mpzp_wms",
      label: LAYER_LEGEND.mpzp_wms.label,
      color: LAYER_LEGEND.mpzp_wms.color,
      checked: preferences.mpzp,
      disabled: !sourcesByKey.mpzp,
      disabledReason: unavailableReason("mpzp"),
      state: layerState("mpzp"),
      status:
        `Podgląd krajowy (KIMPZP). ${mpzpStatus(result)}` +
        zoomNote(sourcesByKey.mpzp, currentZoom, preferences.mpzp),
    },
    {
      id: "pog_wms",
      label: LAYER_LEGEND.pog_wms.label,
      color: LAYER_LEGEND.pog_wms.color,
      checked: preferences.pog,
      disabled: !sourcesByKey.pog,
      disabledReason: unavailableReason("pog"),
      state: layerState("pog"),
      status:
        `Podgląd krajowy (PlanyOgolneGmin). ${pogStatus(result)}` +
        zoomNote(sourcesByKey.pog, currentZoom, preferences.pog),
    },
  ];

  return (
    <aside className="planning-overlay-control" aria-label="Warstwy podglądowe">
      <LayerToggle
        items={items}
        legendLabel="Warstwy podglądowe (WMS)"
        onChange={(id, checked) => {
          if (id === "mpzp_wms") updatePreference("mpzp", checked);
          if (id === "pog_wms") updatePreference("pog", checked);
          if (id === "kiut_wms") updatePreference("kiut", checked);
        }}
      />
      <p className="planning-overlay-status" role="status">
        {sourcesState === "error"
          ? "Nie udało się pobrać konfiguracji podglądów. Mapa podstawowa pozostaje dostępna."
          : "Kafle są pobierane wyłącznie dla włączonych warstw i bieżącego widoku mapy."}
      </p>
      <LayerAvailabilityNote sources={sources} compact />
    </aside>
  );
}
