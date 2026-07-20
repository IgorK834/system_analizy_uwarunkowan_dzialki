/**
 * Konfiguracja kolorów, identyfikatorów źródeł/warstw i opisów legendy dla
 * warstw mapowych dodawanych przez ResultPanel, LayerToggle i PlanningOverlay.
 *
 * Kolory i identyfikatory są scentralizowane tutaj (nie jako inline hex w
 * JSX), żeby uniknąć rozjazdu między komponentem, który dodaje warstwę do
 * MapLibre, i komponentem, który renderuje jej legendę.
 */

// --- Działka (obrys z ULDK) ---
// Kolor zbliżony do palety --accent z globals.css, ale z wyższym kontrastem
// na warstwie rastrowej OSM, żeby obrys był czytelny na różnych podłożach.
export const PARCEL_FILL_COLOR = "#176c4b";
export const PARCEL_LINE_COLOR = "#0d5137";
export const PARCEL_FILL_OPACITY = 0.12;
export const PARCEL_LINE_WIDTH = 2.5;

export const PARCEL_SOURCE_ID = "parcel-source";
export const PARCEL_FILL_LAYER_ID = "parcel-fill-layer";
export const PARCEL_LINE_LAYER_ID = "parcel-line-layer";

// --- Obszar zabudowy (techniczne przybliżenie po odsunięciu od granicy) ---
// Odrębny odcień (pomarańcz), żeby wizualnie odróżnić przybliżenie techniczne
// od pewnego obrysu działki — zgodne z zasadą rozróżniania danych
// pewnych/częściowych z context.md.
export const BUILDABLE_AREA_FILL_COLOR = "#c96a1f";
export const BUILDABLE_AREA_LINE_COLOR = "#8a4813";
export const BUILDABLE_AREA_FILL_OPACITY = 0.18;
export const BUILDABLE_AREA_LINE_WIDTH = 2;
export const BUILDABLE_AREA_LINE_DASH: [number, number] = [2, 1.5];

export const BUILDABLE_AREA_SOURCE_ID = "buildable-area-source";
export const BUILDABLE_AREA_FILL_LAYER_ID = "buildable-area-fill-layer";
export const BUILDABLE_AREA_LINE_LAYER_ID = "buildable-area-line-layer";

// --- Sieci uzbrojenia terenu ---
export const NETWORK_COLOR = "#1769aa";
export const NETWORK_LINE_WIDTH = 3;
export const NETWORK_SOURCE_ID = "network-source";
export const NETWORK_FILL_LAYER_ID = "network-fill-layer";
export const NETWORK_LINE_LAYER_ID = "network-line-layer";

// --- Techniczne strefy ochronne sieci ---
export const PROTECTION_ZONE_FILL_COLOR = "#c43d3d";
export const PROTECTION_ZONE_LINE_COLOR = "#8f2525";
export const PROTECTION_ZONE_FILL_OPACITY = 0.22;
export const PROTECTION_ZONE_LINE_WIDTH = 1.5;
export const PROTECTION_ZONE_SOURCE_ID = "protection-zone-source";
export const PROTECTION_ZONE_FILL_LAYER_ID = "protection-zone-fill-layer";
export const PROTECTION_ZONE_LINE_LAYER_ID = "protection-zone-line-layer";

// --- Ryzyka środowiskowe i przestrzenne ---
export const RISK_FILL_COLOR = "#7b3fb3";
export const RISK_LINE_COLOR = "#54277d";
export const RISK_FILL_OPACITY = 0.2;
export const RISK_LINE_WIDTH = 2;
export const RISK_SOURCE_ID = "risk-source";
export const RISK_FILL_LAYER_ID = "risk-fill-layer";
export const RISK_LINE_LAYER_ID = "risk-line-layer";

// --- Nakładka WMS MPZP (KIMPZP) ---
export const MPZP_WMS_SOURCE_ID = "mpzp-wms-source";
export const MPZP_WMS_LAYER_ID = "mpzp-wms-layer";

// --- Nakładka WMS POG (per gmina, adres z odpowiedzi analizy) ---
export const POG_WMS_SOURCE_ID = "pog-wms-source";
export const POG_WMS_LAYER_ID = "pog-wms-layer";

// Nakładki planistyczne są domyślnie włączone, ale MapLibre nie pobiera ich
// kafli przed osiągnięciem tej skali. Chroni to publiczne WMS-y przed serią
// zapytań dla widoku całej Polski, a użytkownik dostaje szczegóły dopiero przy
// skali, na której plan jest czytelny.
export const PLANNING_WMS_MIN_ZOOM = 11;
// Backend przechowuje natywne kafle do z18. Przy większym zbliżeniu MapLibre
// skaluje już pobrany kafel zamiast generować nowy wariant WMS dla każdego
// kolejnego zoomu, co istotnie poprawia współczynnik trafień cache.
export const MPZP_NATIVE_MAX_ZOOM = 18;
export const PLANNING_WMS_MAX_ZOOM = 22;

// Nakładki rastrowe WMS muszą być półprzezroczyste, żeby nie przykrywały
// całkowicie podkładu OSM i warstw wektorowych działki/obszaru zabudowy.
export const OVERLAY_OPACITY = 0.55;

export type LayerId =
  | "parcel"
  | "buildable_area"
  | "networks"
  | "protection_zones"
  | "risks"
  | "mpzp_wms"
  | "pog_wms";

export type LayerLegendEntry = {
  id: LayerId;
  label: string;
  color: string;
};

// Legenda pokazywana obok przełączników w LayerToggle — jedno miejsce prawdy
// dla nazw domenowych i kolorów, zgodnie z wymogiem context.md, żeby każda
// warstwa mapowa miała nazwę domenową i kolor legendy.
export const LAYER_LEGEND: Record<LayerId, LayerLegendEntry> = {
  parcel: { id: "parcel", label: "Obrys działki", color: PARCEL_FILL_COLOR },
  buildable_area: {
    id: "buildable_area",
    label: "Obszar zabudowy (przybliżenie techniczne)",
    color: BUILDABLE_AREA_FILL_COLOR,
  },
  networks: {
    id: "networks",
    label: "Sieci uzbrojenia terenu",
    color: NETWORK_COLOR,
  },
  protection_zones: {
    id: "protection_zones",
    label: "Strefy ochronne sieci",
    color: PROTECTION_ZONE_FILL_COLOR,
  },
  risks: {
    id: "risks",
    label: "Ryzyka i formy ochrony",
    color: RISK_FILL_COLOR,
  },
  mpzp_wms: {
    id: "mpzp_wms",
    label: "MPZP (nakładka WMS, KIMPZP)",
    color: "#2a5db0",
  },
  pog_wms: {
    id: "pog_wms",
    label: "Plan Ogólny Gminy (nakładka WMS)",
    color: "#7a3fae",
  },
};
